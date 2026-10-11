"""Generate a Harbor `job.yaml`, resolve auth, and invoke `harbor run`.

Harbor is driven through files only: this module writes task copies and a
job config, runs the `harbor` CLI, and records a `tdb-run.json` manifest. It
never imports Harbor. Per-agent settings (hosts, credentials, closed-book
kwargs) come from `trialdesignbench.agents`.

Layout for `--jobs-dir J --job-name N`:

    J/N/                job directory owned by Harbor
      job.yaml          generated config (`harbor run -c`)
      tdb-run.json      RunManifest
    J/N.tasks/          task copies with the agent allowlist filled in
      harbor-plugin/    tdb_harbor_agents.py, the agent classes Harbor imports

Task copies live beside the job directory because Harbor deletes any
subdirectory of a job without a `result.json` when a job is resumed.

Agents are launched through Harbor's `import_path`, not its agent `name`:
`job.yaml` names a class in `tdb_harbor_agents`, a copy of
`trialdesignbench/harbor_agents.py` (Harbor's adapters with a file-based
instruction transport, see that module), and `harbor` runs with the copy's
directory on `PYTHONPATH`. The copy keeps the job runnable from any Harbor
install, and its digest is recorded in the manifest.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trialdesignbench import agents, environment
from trialdesignbench.agents import HOST_TABLE_VERSION, AgentError, AuthMode
from trialdesignbench.build import (
    AGENT_HOSTS_MARKER,
    BUILD_MANIFEST,
    ENVIRONMENT_HOSTS_MARKER,
)
from trialdesignbench.canary import write_canary_task
from trialdesignbench.judge import DEFAULT_JUDGE_MODEL, judge_backend
from trialdesignbench.provenance import (
    digest_tree,
    docker_image_digest,
    docker_image_labels,
    git_sha,
    harbor_version,
    package_version,
    sha256_file,
    utc_now,
)
from trialdesignbench.schema import AgentSpec, HarborPlugin, NetworkPolicy, RunManifest

PLUGIN_DIR = "harbor-plugin"
"""Subdirectory of the task copies holding the Harbor plugin module."""


class RunError(RuntimeError):
    """Raised before launch when a run cannot be configured safely."""


@dataclass(frozen=True)
class AgentRequest:
    agent: str
    model: str
    version: str | None = None
    effort: str | None = None
    """Reasoning effort level; `None` or `agents.DEFAULT_EFFORT` keep the
    harness default."""


def parse_agent_pairs(
    agents: Sequence[str],
    models: Sequence[str],
    versions: Sequence[str] = (),
    efforts: Sequence[str] = (),
) -> list[AgentRequest]:
    """Pair repeated `--agent`/`--model` with optional versions and efforts.

    `--effort` is given once for every agent, or once per `--agent`.
    """
    if not agents:
        raise RunError("at least one --agent is required")
    if len(agents) != len(models):
        raise RunError("--agent and --model must be given the same number of times")
    if versions and len(versions) != len(agents):
        raise RunError("--agent-version must be given once per --agent, or not at all")
    if len(efforts) == 1:
        efforts = tuple(efforts) * len(agents)
    elif efforts and len(efforts) != len(agents):
        raise RunError(
            "--effort must be given once (for every --agent), once per --agent, "
            "or not at all"
        )
    return [
        AgentRequest(
            a,
            m,
            versions[i] if versions else None,
            efforts[i] if efforts else None,
        )
        for i, (a, m) in enumerate(zip(agents, models, strict=True))
    ]


def agent_config(
    request: AgentRequest,
    *,
    auth: AuthMode,
    skills: Sequence[str],
    env: Mapping[str, str],
) -> tuple[dict[str, Any], AgentSpec]:
    """Harbor `AgentConfig` dict plus its provenance record."""
    try:
        profile = agents.get_profile(request.agent)
        provider = agents.model_provider(profile, request.model)
        hosts = agents.api_hosts(profile, auth, provider)
        auth_env = agents.resolve_auth(profile, auth, provider, env)
        effort_kwargs = agents.effort_kwargs(profile, request.effort)
    except AgentError as exc:
        raise RunError(str(exc)) from exc
    version = request.version or profile.version
    if version != profile.version:
        if profile.preinstalled:
            raise RunError(
                f"{request.agent} version {version} requested but the image ships "
                f"{profile.version}; rebuild the image instead of installing at "
                "trial time"
            )
        raise RunError(
            f"{request.agent} version {version} requested but tdb pins "
            f"{profile.version} (ImagePins.{profile.version_pin}); change the pin "
            "and rebuild the image"
        )
    kwargs: dict[str, Any] = {
        "version": version,
        **copy.deepcopy(dict(profile.kwargs)),
        **effort_kwargs,
    }
    agent_env = {**profile.env, **auth_env}
    # `import_path`, never `name`: Harbor resolves a valid `name` first and
    # would launch its own adapter instead of the plugin class.
    config: dict[str, Any] = {
        "import_path": agents.import_path(profile),
        "model_name": request.model,
        "kwargs": kwargs,
        "env": agent_env,
        "skills": list(skills),
    }
    spec = AgentSpec(
        agent=request.agent,
        model=request.model,
        agent_version=version,
        effort=effort_kwargs.get(profile.effort.kwarg),
        kwargs=copy.deepcopy(kwargs),
        env_keys=tuple(sorted(agent_env)),
        allowed_hosts=hosts,
        setup_hosts=profile.setup_hosts,
        import_path=agents.import_path(profile),
    )
    return config, spec


def _marker_line(marker: str) -> re.Pattern[str]:
    return re.compile(
        r"^allowed_hosts = \[[^\]\n]*\]  " + re.escape(marker) + r"$", re.MULTILINE
    )


def fill_allowed_hosts(
    task_toml: str, agent_hosts: Sequence[str], environment_hosts: Sequence[str]
) -> str:
    """Fill the two allowlist placeholders written by `tdb build`.

    `agent_hosts` go to `[agent]` (during `agent.run()`), `environment_hosts`
    to the `[environment]` baseline (during agent setup).
    """
    new = task_toml
    for marker, hosts in (
        (AGENT_HOSTS_MARKER, agent_hosts),
        (ENVIRONMENT_HOSTS_MARKER, environment_hosts),
    ):
        replacement = f"allowed_hosts = {json.dumps(list(hosts))}  {marker}"
        new, n = _marker_line(marker).subn(replacement, new)
        if n != 1:
            raise RunError(
                f"expected exactly one `{marker}` line in task.toml, found {n}; "
                "rebuild the tasks with this version of `tdb build`"
            )
    parsed = tomllib.loads(new)
    agent = parsed.get("agent", {})
    if agent.get("network_mode") != "allowlist" or agent.get("allowed_hosts") != list(
        agent_hosts
    ):
        raise RunError("agent allowlist placeholder is not in the [agent] table")
    if parsed.get("environment", {}).get("allowed_hosts") != list(environment_hosts):
        raise RunError(
            "environment allowlist placeholder is not in the [environment] table"
        )
    return new


def task_dirs(tasks_dir: Path) -> list[Path]:
    """Harbor task directories under `tasks_dir`."""
    return sorted(p for p in tasks_dir.iterdir() if (p / "task.toml").is_file())


def materialize_tasks(
    tasks_dir: Path,
    dest: Path,
    agent_hosts: Sequence[str],
    environment_hosts: Sequence[str],
    task_ids: Sequence[str] | None,
) -> list[Path]:
    """Copy tasks beside the job dir and fill both allowlists."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    available = {p.name: p for p in task_dirs(tasks_dir)}
    wanted = list(task_ids) if task_ids else sorted(available)
    unknown = sorted(set(wanted) - set(available))
    if unknown:
        raise RunError(f"unknown task ids: {unknown}")
    if not wanted:
        raise RunError(f"no Harbor tasks found in {tasks_dir}")
    out = []
    for name in wanted:
        target = dest / name
        shutil.copytree(available[name], target)
        toml_path = target / "task.toml"
        toml_path.write_text(
            fill_allowed_hosts(toml_path.read_text(), agent_hosts, environment_hosts)
        )
        out.append(target)
    manifest = tasks_dir / BUILD_MANIFEST
    if manifest.is_file():
        shutil.copyfile(manifest, dest / BUILD_MANIFEST)
    return out


def install_harbor_plugin(tasks_copy: Path) -> HarborPlugin:
    """Copy `trialdesignbench/harbor_agents.py` beside the task copies.

    The copy is the module `job.yaml` imports (`agents.HARBOR_PLUGIN_MODULE`);
    `harbor_env` puts its directory on `PYTHONPATH`, so the `harbor` process
    finds it whether or not Harbor shares a Python environment with tdb.
    """
    source = Path(__file__).with_name("harbor_agents.py")
    plugin_dir = tasks_copy / PLUGIN_DIR
    plugin_dir.mkdir(parents=True, exist_ok=True)
    target = plugin_dir / f"{agents.HARBOR_PLUGIN_MODULE}.py"
    shutil.copyfile(source, target)
    return HarborPlugin(
        module=agents.HARBOR_PLUGIN_MODULE,
        path=str(target),
        sha256=sha256_file(target),
    )


def harbor_env(plugin: HarborPlugin, env: Mapping[str, str]) -> dict[str, str]:
    """Environment overrides for the `harbor` process: the plugin directory
    first on `PYTHONPATH`."""
    plugin_dir = str(Path(plugin.path).parent)
    existing = env.get("PYTHONPATH")
    return {
        "PYTHONPATH": f"{plugin_dir}{os.pathsep}{existing}" if existing else plugin_dir
    }


def read_build_manifest(tasks_dir: Path) -> dict[str, Any]:
    """Load `tdb-build.json` from a tasks directory."""
    path = tasks_dir / BUILD_MANIFEST
    if not path.is_file():
        raise RunError(f"{path} not found; build tasks with `tdb build` first")
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def check_image(image: str | None, requests: Sequence[AgentRequest]) -> list[str]:
    """Problems with the local image (empty list when it matches the pins)."""
    if image is None:
        return []
    labels = docker_image_labels(image)
    if labels is None:
        return [
            f"image {image} not found locally; build it with `tdb env build` or pull it"
        ]
    problems = []
    for req in requests:
        profile = agents.get_profile(req.agent)
        want = req.version or profile.version
        have = labels.get(profile.image_label)
        if have != want:
            what = "ships" if profile.preinstalled else "was built for"
            problems.append(
                f"image {image} {what} {req.agent} {have}, requested {want}"
            )
    grader = labels.get(f"{environment.LABEL_PREFIX}.version")
    if grader != package_version():
        problems.append(
            f"image {image} has grader {grader}, this package is {package_version()}"
        )
    return problems


def dump_job_yaml(config: Mapping[str, Any]) -> str:
    """Serialize as JSON, which is valid YAML, so no YAML dependency is needed."""
    header = (
        f"# Generated by trialdesignbench {package_version()} (`tdb run`).\n"
        "# JSON syntax is valid YAML; Harbor reads this with `harbor run -c`.\n"
    )
    return header + json.dumps(config, indent=2) + "\n"


@dataclass
class RunPlan:
    job_dir: Path
    job_yaml: Path
    tasks_copy: Path
    command: list[str]
    config: dict[str, Any]
    manifest: RunManifest
    env: dict[str, str] = field(default_factory=dict)
    """Environment overrides for `command` (the plugin's `PYTHONPATH`)."""
    warnings: list[str] = field(default_factory=list)


def plan_run(
    tasks_dir: Path,
    requests: Sequence[AgentRequest],
    *,
    auth: AuthMode = "api",
    n_attempts: int = 1,
    n_concurrent: int | None = None,
    skills: Sequence[str] = (),
    job_name: str | None = None,
    jobs_dir: Path = Path("jobs"),
    task_ids: Sequence[str] | None = None,
    canary: bool = False,
    yes: bool = False,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
) -> RunPlan:
    """Validate inputs, write task copies, `job.yaml`, and `tdb-run.json`."""
    env = dict(os.environ if env is None else env)
    tasks_dir = tasks_dir.resolve()
    build = read_build_manifest(tasks_dir)
    if n_concurrent is None:
        n_concurrent = 1 if auth == "subscription" else 2
    judge = judge_backend(build.get("judge_model") or DEFAULT_JUDGE_MODEL)
    if not env.get(judge.key_env):
        raise RunError(f"the rubric judge in the verifier requires {judge.key_env}")
    configs, specs = [], []
    for req in requests:
        agent_cfg, spec = agent_config(req, auth=auth, skills=skills, env=env)
        configs.append(agent_cfg)
        specs.append(spec)
    agent_hosts = sorted({h for s in specs for h in s.allowed_hosts})
    environment_hosts = sorted(
        {*agent_hosts, *(h for s in specs for h in s.setup_hosts)}
    )
    setup_hosts = sorted(set(environment_hosts) - set(agent_hosts))
    warnings = []
    for req in requests:
        verified, note = agents.hosts_verified(agents.get_profile(req.agent), auth)
        if not verified:
            warnings.append(
                f"host list for {req.agent} with --auth {auth} is unverified: {note}"
            )
    for spec in specs:
        if spec.effort is None:
            warnings.append(
                f"reasoning effort for {spec.agent} not set: trials run at the "
                "harness default, which depends on the model and CLI version and "
                "is not recorded. Pass --effort to pin it."
            )
    if len({(s.allowed_hosts, s.setup_hosts) for s in specs}) > 1:
        warnings.append(
            "matrix agents need different hosts; every trial gets the union "
            f"(agent phase {agent_hosts}, setup {setup_hosts}). Run one job per "
            "agent for per-agent allowlists."
        )
    image = build.get("image")
    if not dry_run:
        problems = check_image(image, requests)
        if problems:
            raise RunError("; ".join(problems))

    name = job_name or utc_now().strftime("%Y-%m-%d__%H-%M-%S")
    jobs_dir = jobs_dir.resolve()
    job_dir = jobs_dir / name
    if (job_dir / "config.json").exists():
        raise RunError(f"{job_dir} already holds a Harbor job; pick another --job-name")
    tasks_copy = jobs_dir / f"{name}.tasks"
    task_paths = materialize_tasks(
        tasks_dir, tasks_copy, agent_hosts, environment_hosts, task_ids
    )
    plugin = install_harbor_plugin(tasks_copy)
    if canary:
        if image is None:
            raise RunError("--canary needs tasks built with a prebuilt --image")
        task_paths.insert(
            0,
            write_canary_task(
                tasks_copy,
                image=image,
                agent_hosts=agent_hosts,
                setup_hosts=setup_hosts,
            ),
        )

    config: dict[str, Any] = {
        "job_name": name,
        "jobs_dir": str(jobs_dir),
        "n_attempts": n_attempts,
        "n_concurrent_trials": n_concurrent,
        "environment": {"type": "docker"},
        "agents": configs,
        "tasks": [{"path": str(p)} for p in task_paths],
    }
    job_dir.mkdir(parents=True, exist_ok=True)
    job_yaml = job_dir / "job.yaml"
    job_yaml.write_text(dump_job_yaml(config))
    command = ["harbor", "run", "-c", str(job_yaml)]
    if yes:
        command.append("--yes")
    manifest = RunManifest(
        package_version=package_version(),
        harbor_version=None if dry_run else harbor_version(),
        job_name=name,
        job_dir=str(job_dir),
        dataset_digest=build.get("dataset_digest"),
        tasks_digest=digest_tree(tasks_dir),
        image_ref=image,
        image_digest=None if (dry_run or image is None) else docker_image_digest(image),
        agents=tuple(specs),
        auth_mode=auth,
        skills=tuple(skills),
        judge_model=build.get("judge_model"),
        n_attempts=n_attempts,
        n_concurrent=n_concurrent,
        network_policy=NetworkPolicy(
            host_table_version=HOST_TABLE_VERSION,
            agent_network_mode="allowlist",
            agent_allowed_hosts=tuple(agent_hosts),
            environment_network_mode="allowlist",
            environment_allowed_hosts=tuple(environment_hosts),
            verifier_network_mode="allowlist",
            verifier_allowed_hosts=(judge.api_host,),
            disabled_tools={
                s.agent: agents.get_profile(s.agent).disabled_tools for s in specs
            },
            canary=canary,
        ),
        repo_git_sha=git_sha(Path(__file__).parent),
        command=tuple(command),
        harbor_plugin=plugin,
        started_at=utc_now(),
    )
    write_manifest(job_dir, manifest)
    return RunPlan(
        job_dir,
        job_yaml,
        tasks_copy,
        command,
        config,
        manifest,
        env=harbor_env(plugin, env),
        warnings=warnings,
    )


def write_manifest(job_dir: Path, manifest: RunManifest) -> None:
    """Write `tdb-run.json`."""
    (job_dir / "tdb-run.json").write_text(manifest.model_dump_json(indent=2) + "\n")


def execute(plan: RunPlan) -> int:
    """Run `harbor run -c job.yaml` and record the exit code."""
    if shutil.which("harbor") is None:
        raise RunError(
            "`harbor` not found; install `trialdesignbench[harbor]` (Python 3.12+)"
        )
    proc = subprocess.run(
        plan.command, check=False, env={**os.environ, **plan.env} if plan.env else None
    )
    finished = plan.manifest.model_copy(
        update={"finished_at": utc_now(), "exit_code": proc.returncode}
    )
    write_manifest(plan.job_dir, finished)
    return proc.returncode


def regrade_command(
    job_dir: Path,
    tasks_dir: Path,
    *,
    job_name: str | None = None,
    jobs_dir: Path | None = None,
    n_concurrent: int | None = None,
) -> list[str]:
    """`harbor job regrade` command for a recorded job."""
    command = [
        "harbor",
        "job",
        "regrade",
        str(job_dir.resolve()),
        "-p",
        str(tasks_dir.resolve()),
    ]
    if jobs_dir is not None:
        command += ["-o", str(jobs_dir.resolve())]
    if job_name is not None:
        command += ["--job-name", job_name]
    if n_concurrent is not None:
        command += ["-n", str(n_concurrent)]
    return command
