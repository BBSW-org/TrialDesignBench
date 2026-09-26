"""Generate a Harbor `job.yaml`, resolve auth, and invoke `harbor run`.

Harbor is driven through files only: this module writes task copies and a
job config, runs the `harbor` CLI, and records a `tdb-run.json` manifest. It
never imports Harbor.

Layout for `--jobs-dir J --job-name N`:

    J/N/                job directory owned by Harbor
      job.yaml          generated config (`harbor run -c`)
      tdb-run.json      RunManifest
    J/N.tasks/          task copies with the agent allowlist filled in

Task copies live beside the job directory because Harbor deletes any
subdirectory of a job without a `result.json` when a job is resumed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from trialdesignbench import environment
from trialdesignbench.build import AGENT_HOSTS_MARKER, BUILD_MANIFEST, JUDGE_API_HOST
from trialdesignbench.canary import write_canary_task
from trialdesignbench.provenance import (
    digest_tree,
    docker_image_digest,
    docker_image_labels,
    git_sha,
    harbor_version,
    package_version,
    utc_now,
)
from trialdesignbench.schema import AgentSpec, NetworkPolicy, RunManifest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

AuthMode = Literal["api", "subscription"]

HOST_TABLE_VERSION = "1"


@dataclass(frozen=True)
class HostEntry:
    hosts: tuple[str, ...]
    verified: bool
    note: str = ""


# Exact hostnames only (no wildcards) so the policy stays portable. Entries
# marked unverified are starting points; confirm with `tdb env check --canary`
# and a smoke run before relying on them.
AGENT_HOSTS: Mapping[tuple[str, AuthMode], HostEntry] = {
    ("claude-code", "api"): HostEntry(("api.anthropic.com",), verified=True),
    ("claude-code", "subscription"): HostEntry(
        ("api.anthropic.com",),
        verified=False,
        note="add any OAuth token refresh host observed in a smoke run",
    ),
    ("codex", "api"): HostEntry(("api.openai.com",), verified=True),
    ("codex", "subscription"): HostEntry(
        ("chatgpt.com", "auth.openai.com"),
        verified=False,
        note="auth.openai.com is the expected token refresh host; confirm by smoke test",
    ),
}

UNSUPPORTED_AGENTS: Mapping[str, str] = {
    "gemini-cli": (
        "Harbor's Gemini CLI adapter writes ~/.gemini/settings.json itself and "
        "accepts no native config, so server-side web tools cannot be disabled"
    ),
}

DISABLED_TOOLS: Mapping[str, tuple[str, ...]] = {
    "claude-code": ("WebSearch", "WebFetch"),
    "codex": ("web_search",),
}

CLAUDE_CODE_ENV = {
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}

API_KEY_ENV = {"claude-code": "ANTHROPIC_API_KEY", "codex": "OPENAI_API_KEY"}
JUDGE_KEY_ENV = "ANTHROPIC_API_KEY"


class RunError(RuntimeError):
    """Raised before launch when a run cannot be configured safely."""


@dataclass(frozen=True)
class AgentRequest:
    agent: str
    model: str
    version: str | None = None


def parse_agent_pairs(
    agents: Sequence[str], models: Sequence[str], versions: Sequence[str] = ()
) -> list[AgentRequest]:
    """Pair repeated `--agent`/`--model` (and optional versions)."""
    if not agents:
        raise RunError("at least one --agent is required")
    if len(agents) != len(models):
        raise RunError("--agent and --model must be given the same number of times")
    if versions and len(versions) != len(agents):
        raise RunError("--agent-version must be given once per --agent, or not at all")
    return [
        AgentRequest(a, m, versions[i] if versions else None)
        for i, (a, m) in enumerate(zip(agents, models))
    ]


def allowed_hosts(agent: str, auth: AuthMode) -> HostEntry:
    if agent in UNSUPPORTED_AGENTS:
        raise RunError(f"{agent} is not supported: {UNSUPPORTED_AGENTS[agent]}")
    try:
        return AGENT_HOSTS[(agent, auth)]
    except KeyError as exc:
        raise RunError(f"no network host table entry for agent {agent!r}") from exc


def resolve_auth(agent: str, auth: AuthMode, env: Mapping[str, str]) -> dict[str, str]:
    """Validate credentials and return the agent env flags for this mode."""
    if auth == "api":
        key = API_KEY_ENV[agent]
        if not env.get(key):
            raise RunError(f"--auth api for {agent} requires {key} in the environment")
        return {}
    if agent == "claude-code":
        if not env.get("CLAUDE_CODE_OAUTH_TOKEN"):
            raise RunError(
                "--auth subscription for claude-code requires CLAUDE_CODE_OAUTH_TOKEN "
                "(create one with `claude setup-token`)"
            )
        return {"CLAUDE_FORCE_OAUTH": "1"}
    if agent == "codex":
        explicit = env.get("CODEX_AUTH_JSON_PATH")
        path = Path(explicit) if explicit else Path.home() / ".codex" / "auth.json"
        if not path.is_file():
            raise RunError(
                f"--auth subscription for codex requires a ChatGPT auth.json at {path} "
                "(run `codex login`) or CODEX_AUTH_JSON_PATH"
            )
        return {"CODEX_FORCE_AUTH_JSON": "1"}
    raise RunError(f"unsupported agent {agent!r}")


def agent_config(
    request: AgentRequest,
    *,
    auth: AuthMode,
    skills: Sequence[str],
    env: Mapping[str, str],
) -> tuple[dict[str, Any], AgentSpec]:
    """Harbor `AgentConfig` dict plus its provenance record."""
    entry = allowed_hosts(request.agent, auth)
    shipped = environment.agent_versions()[request.agent]
    version = request.version or shipped
    if version != shipped:
        raise RunError(
            f"{request.agent} version {version} requested but the image ships "
            f"{shipped}; rebuild the image instead of installing at trial time"
        )
    agent_env = resolve_auth(request.agent, auth, env)
    kwargs: dict[str, Any] = {"version": version}
    if request.agent == "claude-code":
        denied = list(DISABLED_TOOLS["claude-code"])
        kwargs["disallowed_tools"] = ",".join(denied)
        kwargs["config"] = {"permissions": {"deny": denied}}
        agent_env = {**CLAUDE_CODE_ENV, **agent_env}
    elif request.agent == "codex":
        kwargs["web_search"] = "disabled"
    config: dict[str, Any] = {
        "name": request.agent,
        "model_name": request.model,
        "kwargs": kwargs,
        "env": agent_env,
        "skills": list(skills),
    }
    spec = AgentSpec(
        agent=request.agent,
        model=request.model,
        agent_version=version,
        kwargs=kwargs,
        env_keys=tuple(sorted(agent_env)),
        allowed_hosts=entry.hosts,
    )
    return config, spec


_MARKER_LINE = re.compile(
    r"^allowed_hosts = \[[^\]\n]*\]  " + re.escape(AGENT_HOSTS_MARKER) + r"$",
    re.MULTILINE,
)


def fill_allowed_hosts(task_toml: str, hosts: Sequence[str]) -> str:
    """Replace the agent allowlist placeholder written by `tdb build`."""
    replacement = f"allowed_hosts = {json.dumps(list(hosts))}  {AGENT_HOSTS_MARKER}"
    new, n = _MARKER_LINE.subn(replacement, task_toml)
    if n != 1:
        raise RunError(
            f"expected exactly one `{AGENT_HOSTS_MARKER}` line in task.toml, found {n}"
        )
    parsed = tomllib.loads(new)
    if parsed["environment"].get("allowed_hosts") != list(hosts):
        raise RunError("allowed_hosts placeholder is not in the [environment] table")
    return new


def task_dirs(tasks_dir: Path) -> list[Path]:
    """Harbor task directories under `tasks_dir`."""
    return sorted(p for p in tasks_dir.iterdir() if (p / "task.toml").is_file())


def materialize_tasks(
    tasks_dir: Path, dest: Path, hosts: Sequence[str], task_ids: Sequence[str] | None
) -> list[Path]:
    """Copy tasks beside the job dir and fill the agent allowlist."""
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
        toml_path.write_text(fill_allowed_hosts(toml_path.read_text(), hosts))
        out.append(target)
    manifest = tasks_dir / BUILD_MANIFEST
    if manifest.is_file():
        shutil.copyfile(manifest, dest / BUILD_MANIFEST)
    return out


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
    prefix = environment.LABEL_PREFIX
    keys = {"claude-code": "claude-code-version", "codex": "codex-version"}
    for req in requests:
        want = req.version or environment.agent_versions()[req.agent]
        have = labels.get(f"{prefix}.{keys[req.agent]}")
        if have != want:
            problems.append(f"image {image} ships {req.agent} {have}, requested {want}")
    if labels.get(f"{prefix}.version") != package_version():
        problems.append(
            f"image {image} has grader {labels.get(f'{prefix}.version')}, "
            f"this package is {package_version()}"
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
    if not env.get(JUDGE_KEY_ENV):
        raise RunError(f"the rubric judge in the verifier requires {JUDGE_KEY_ENV}")
    configs, specs = [], []
    for req in requests:
        agent_cfg, spec = agent_config(req, auth=auth, skills=skills, env=env)
        configs.append(agent_cfg)
        specs.append(spec)
    hosts = sorted({h for s in specs for h in s.allowed_hosts})
    warnings = []
    for req in requests:
        entry = allowed_hosts(req.agent, auth)
        if not entry.verified:
            warnings.append(
                f"host list for {req.agent} with --auth {auth} is unverified: {entry.note}"
            )
    if len({s.allowed_hosts for s in specs}) > 1:
        warnings.append(
            "matrix agents need different API hosts; every trial gets the union "
            f"{hosts}. Run one job per agent for per-agent allowlists."
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
    task_paths = materialize_tasks(tasks_dir, tasks_copy, hosts, task_ids)
    if canary:
        if image is None:
            raise RunError("--canary needs tasks built with a prebuilt --image")
        task_paths.insert(
            0, write_canary_task(tasks_copy, image=image, allowed_hosts=hosts)
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
            agent_allowed_hosts=tuple(hosts),
            verifier_network_mode="allowlist",
            verifier_allowed_hosts=(JUDGE_API_HOST,),
            disabled_tools={s.agent: DISABLED_TOOLS[s.agent] for s in specs},
            canary=canary,
        ),
        repo_git_sha=git_sha(Path(__file__).parent),
        command=tuple(command),
        started_at=utc_now(),
    )
    write_manifest(job_dir, manifest)
    return RunPlan(job_dir, job_yaml, tasks_copy, command, config, manifest, warnings)


def write_manifest(job_dir: Path, manifest: RunManifest) -> None:
    """Write `tdb-run.json`."""
    (job_dir / "tdb-run.json").write_text(manifest.model_dump_json(indent=2) + "\n")


def execute(plan: RunPlan) -> int:
    """Run `harbor run -c job.yaml` and record the exit code."""
    if shutil.which("harbor") is None:
        raise RunError(
            "`harbor` not found; install `trialdesignbench[harbor]` (Python 3.12+)"
        )
    proc = subprocess.run(plan.command, check=False)
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
