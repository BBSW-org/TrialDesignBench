from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.conftest import FIXTURE_TASK_ID
from trialdesignbench import agents, environment
from trialdesignbench.build import BuildOptions, build_tasks
from trialdesignbench.run import (
    AgentRequest,
    RunError,
    fill_allowed_hosts,
    parse_agent_pairs,
    plan_run,
    regrade_command,
)
from trialdesignbench.schema import RunManifest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

API_ENV = {
    "ANTHROPIC_API_KEY": "sk-test",
    "OPENAI_API_KEY": "sk-test",
    "XAI_API_KEY": "sk-test",
}


@pytest.fixture
def tasks_dir(dataset_dir: Path, tmp_path: Path) -> Path:
    out = tmp_path / "tasks"
    build_tasks(dataset_dir, out, options=BuildOptions(image="tdb-env:test"))
    return out


def plan(tasks_dir: Path, tmp_path: Path, requests: list[AgentRequest], **kw: object):  # type: ignore[no-untyped-def]
    kw.setdefault("env", API_ENV)
    return plan_run(
        tasks_dir,
        requests,
        jobs_dir=tmp_path / "jobs",
        job_name="j1",
        dry_run=True,
        **kw,  # type: ignore[arg-type]
    )


def load_job(p) -> dict:  # type: ignore[no-untyped-def]
    text = p.job_yaml.read_text()
    return json.loads(
        "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    )


def task_policy(p) -> tuple[list[str], list[str]]:  # type: ignore[no-untyped-def]
    """(agent-phase hosts, setup baseline hosts) of the first benchmark task."""
    task = next(
        Path(t["path"])
        for t in load_job(p)["tasks"]
        if Path(t["path"]).name != "network-canary"
    )
    config = tomllib.loads((task / "task.toml").read_text())
    assert config["agent"]["network_mode"] == "allowlist"
    return config["agent"]["allowed_hosts"], config["environment"]["allowed_hosts"]


def test_dry_run_job_yaml(tasks_dir: Path, tmp_path: Path) -> None:
    p = plan(
        tasks_dir,
        tmp_path,
        [AgentRequest("claude-code", "anthropic/claude-opus-5")],
        n_attempts=3,
        n_concurrent=2,
    )
    job = load_job(p)
    assert p.job_yaml == (tmp_path / "jobs" / "j1" / "job.yaml").resolve()
    assert job["job_name"] == "j1"
    assert job["n_attempts"] == 3
    assert job["n_concurrent_trials"] == 2
    assert job["environment"] == {"type": "docker"}
    (agent,) = job["agents"]
    assert agent["name"] == "claude-code"
    assert agent["model_name"] == "anthropic/claude-opus-5"
    kwargs = agent["kwargs"]
    assert kwargs["version"] == environment.PINS.claude_code_version
    assert kwargs["disallowed_tools"] == "WebSearch,WebFetch"
    assert kwargs["config"] == {"permissions": {"deny": ["WebSearch", "WebFetch"]}}
    assert agent["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert agent["env"]["DISABLE_TELEMETRY"] == "1"
    assert "mcp_servers" not in agent
    assert (
        "extra_allowed_hosts" not in agent
        and "extra_allowed_hosts" not in job["environment"]
    )
    assert (
        "--allow-agent-host" not in p.command
        and "--allow-environment-host" not in p.command
    )
    assert p.command == ["harbor", "run", "-c", str(p.job_yaml)]

    # Task copies live beside the job dir with the allowlist filled in.
    (task,) = job["tasks"]
    assert Path(task["path"]).parent == (tmp_path / "jobs" / "j1.tasks").resolve()
    # Claude Code is preinstalled, so setup needs no extra hosts.
    assert task_policy(p) == (["api.anthropic.com"], ["api.anthropic.com"])
    # Source tasks are untouched.
    src = tomllib.loads((tasks_dir / FIXTURE_TASK_ID / "task.toml").read_text())
    assert src["environment"]["allowed_hosts"] == []
    assert src["agent"]["allowed_hosts"] == []

    manifest = RunManifest.model_validate_json((p.job_dir / "tdb-run.json").read_text())
    assert manifest.auth_mode == "api"
    assert manifest.network_policy.agent_allowed_hosts == ("api.anthropic.com",)
    assert manifest.network_policy.environment_allowed_hosts == ("api.anthropic.com",)
    assert manifest.agents[0].setup_hosts == ()
    assert manifest.network_policy.disabled_tools == {
        "claude-code": ("WebSearch", "WebFetch")
    }
    assert manifest.agents[0].env_keys  # names only
    assert "sk-test" not in (p.job_dir / "tdb-run.json").read_text()
    assert "sk-test" not in p.job_yaml.read_text()


def test_codex_web_search_disabled(tasks_dir: Path, tmp_path: Path) -> None:
    p = plan(tasks_dir, tmp_path, [AgentRequest("codex", "openai/gpt-5.5")])
    agent = load_job(p)["agents"][0]
    assert agent["kwargs"] == {
        "version": environment.PINS.codex_version,
        "web_search": "disabled",
    }


def test_grok_build_two_phase_policy(tasks_dir: Path, tmp_path: Path) -> None:
    p = plan(tasks_dir, tmp_path, [AgentRequest("grok-build", "xai/grok-4.7")])
    agent = load_job(p)["agents"][0]
    assert agent["kwargs"] == {
        "version": environment.PINS.grok_build_version,
        "disable_web_search": True,
        "grok_config": {"features": {"web_fetch": False}},
    }
    assert agent["env"] == {}
    # Harbor installs grok during setup: apt mirrors and x.ai are reachable
    # then, only the model API during agent.run().
    agent_hosts, setup = task_policy(p)
    assert agent_hosts == ["api.x.ai"]
    assert setup == sorted(["api.x.ai", *agents.get_profile("grok-build").setup_hosts])
    assert "x.ai" in setup and "archive.ubuntu.com" in setup
    network = p.manifest.network_policy
    assert network.agent_allowed_hosts == ("api.x.ai",)
    assert set(network.environment_allowed_hosts) == set(setup)
    assert network.disabled_tools == {"grok-build": ("web_search", "web_fetch")}
    assert p.manifest.agents[0].setup_hosts
    assert any("unverified" in w for w in p.warnings)
    assert "sk-test" not in p.job_yaml.read_text()


@pytest.mark.parametrize(
    ("model", "host", "key"),
    [
        ("anthropic/claude-opus-5", "api.anthropic.com", "ANTHROPIC_API_KEY"),
        ("openai/gpt-5.5", "api.openai.com", "OPENAI_API_KEY"),
        ("xai/grok-4.7", "api.x.ai", "XAI_API_KEY"),
    ],
)
def test_opencode_providers(
    tasks_dir: Path, tmp_path: Path, model: str, host: str, key: str
) -> None:
    p = plan(tasks_dir, tmp_path, [AgentRequest("opencode", model)])
    agent = load_job(p)["agents"][0]
    config = agent["kwargs"]["opencode_config"]
    assert config["permission"] == {"webfetch": "deny", "websearch": "deny"}
    assert "*" not in config["permission"]  # a wildcard could re-allow them
    assert json.loads(agent["env"]["OPENCODE_PERMISSION"]) == config["permission"]
    assert agent["env"]["OPENCODE_DISABLE_MODELS_FETCH"] == "1"
    assert agent["env"]["OPENCODE_MODELS_PATH"] == environment.OPENCODE_MODELS_PATH
    agent_hosts, setup = task_policy(p)
    assert agent_hosts == [host]
    assert "registry.npmjs.org" in setup and host in setup
    with pytest.raises(RunError, match=key):
        plan(
            tasks_dir,
            tmp_path,
            [AgentRequest("opencode", model)],
            env={k: v for k, v in API_ENV.items() if k != key},
        )


def test_matrix_uses_host_union_with_warning(tasks_dir: Path, tmp_path: Path) -> None:
    requests = parse_agent_pairs(["claude-code", "codex"], ["anthropic/a", "openai/b"])
    p = plan(tasks_dir, tmp_path, requests)
    assert [a["name"] for a in load_job(p)["agents"]] == ["claude-code", "codex"]
    assert any("union" in w for w in p.warnings)
    hosts = ["api.anthropic.com", "api.openai.com"]
    assert task_policy(p) == (hosts, hosts)

    requests = parse_agent_pairs(
        ["claude-code", "grok-build"], ["anthropic/a", "xai/b"]
    )
    p = plan(tasks_dir, tmp_path, requests)
    agent_hosts, setup = task_policy(p)
    assert agent_hosts == ["api.anthropic.com", "api.x.ai"]
    assert set(agent_hosts) < set(setup) and "x.ai" in setup


@pytest.mark.parametrize(
    ("agent", "env", "match"),
    [
        ("claude-code", {"ANTHROPIC_API_KEY": "k"}, None),
        ("codex", {"ANTHROPIC_API_KEY": "k"}, "OPENAI_API_KEY"),
        ("claude-code", {}, "ANTHROPIC_API_KEY"),
        ("grok-build", {"ANTHROPIC_API_KEY": "k"}, "XAI_API_KEY"),
    ],
)
def test_api_auth_validation(
    tasks_dir: Path, tmp_path: Path, agent: str, env: dict[str, str], match: str | None
) -> None:
    provider = agents.get_profile(agent).providers[0]
    req = [AgentRequest(agent, f"{provider}/x")]
    if match is None:
        plan(tasks_dir, tmp_path, req, env=env)
    else:
        with pytest.raises(RunError, match=match):
            plan(tasks_dir, tmp_path, req, env=env)


def test_subscription_auth(
    tasks_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = [AgentRequest("claude-code", "anthropic/x")]
    with pytest.raises(RunError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        plan(
            tasks_dir,
            tmp_path,
            req,
            auth="subscription",
            env={"ANTHROPIC_API_KEY": "k"},
        )
    p = plan(
        tasks_dir,
        tmp_path,
        req,
        auth="subscription",
        env={"ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN": "tok"},
    )
    job = load_job(p)
    assert job["agents"][0]["env"]["CLAUDE_FORCE_OAUTH"] == "1"
    assert job["n_concurrent_trials"] == 1
    assert "tok" not in p.job_yaml.read_text()

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    codex = [AgentRequest("codex", "openai/x")]
    with pytest.raises(RunError, match="auth.json"):
        plan(
            tasks_dir,
            tmp_path,
            codex,
            auth="subscription",
            env={"ANTHROPIC_API_KEY": "k"},
        )
    auth_json = tmp_path / "auth.json"
    auth_json.write_text("{}")
    p = plan(
        tasks_dir,
        tmp_path,
        codex,
        auth="subscription",
        env={"ANTHROPIC_API_KEY": "k", "CODEX_AUTH_JSON_PATH": str(auth_json)},
    )
    assert load_job(p)["agents"][0]["env"] == {"CODEX_FORCE_AUTH_JSON": "1"}
    assert any("unverified" in w for w in p.warnings)


def test_judge_key_required(tasks_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(RunError, match="judge"):
        plan(
            tasks_dir,
            tmp_path,
            [AgentRequest("codex", "openai/x")],
            env={"OPENAI_API_KEY": "k"},
        )


def test_agent_version_must_match_image(tasks_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(RunError, match="image ships"):
        plan(tasks_dir, tmp_path, [AgentRequest("claude-code", "anthropic/x", "0.0.1")])


@pytest.mark.parametrize("agent", sorted(agents.REFUSED_AGENTS))
def test_refused_agents_explain_why(
    tasks_dir: Path, tmp_path: Path, agent: str
) -> None:
    with pytest.raises(RunError, match="not supported") as info:
        plan(tasks_dir, tmp_path, [AgentRequest(agent, "google/x")])
    assert agents.REFUSED_AGENTS[agent] in str(info.value)


def test_unlisted_agent_refused(tasks_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(RunError, match="Supported agents: claude-code, codex"):
        plan(tasks_dir, tmp_path, [AgentRequest("aider", "openai/x")])


@pytest.mark.parametrize(
    ("agent", "model", "match"),
    [
        ("claude-code", "openai/gpt-5.5", "cannot use 'openai' models"),
        ("codex", "gpt-5.5", "must be <provider>/<model>"),
        ("opencode", "google/gemini-3.8-flash", "cannot use 'google' models"),
    ],
)
def test_model_provider_must_match_agent(
    tasks_dir: Path, tmp_path: Path, agent: str, model: str, match: str
) -> None:
    with pytest.raises(RunError, match=match):
        plan(tasks_dir, tmp_path, [AgentRequest(agent, model)])


@pytest.mark.parametrize("agent", ["grok-build", "opencode"])
def test_subscription_only_where_harbor_supports_it(
    tasks_dir: Path, tmp_path: Path, agent: str
) -> None:
    provider = agents.get_profile(agent).providers[0]
    with pytest.raises(RunError, match="only --auth api"):
        plan(
            tasks_dir,
            tmp_path,
            [AgentRequest(agent, f"{provider}/x")],
            auth="subscription",
        )


def test_pinned_version_for_setup_installed_agent(
    tasks_dir: Path, tmp_path: Path
) -> None:
    with pytest.raises(RunError, match="tdb pins"):
        plan(tasks_dir, tmp_path, [AgentRequest("opencode", "anthropic/x", "0.0.1")])


def test_canary_is_first_task(tasks_dir: Path, tmp_path: Path) -> None:
    p = plan(
        tasks_dir, tmp_path, [AgentRequest("claude-code", "anthropic/x")], canary=True
    )
    tasks = load_job(p)["tasks"]
    assert Path(tasks[0]["path"]).name == "network-canary"
    config = tomllib.loads((Path(tasks[0]["path"]) / "task.toml").read_text())
    assert config["agent"]["allowed_hosts"] == ["api.anthropic.com"]
    assert config["environment"]["allowed_hosts"] == ["api.anthropic.com"]
    assert "clinicaltrials.gov" in config["environment"]["healthcheck"]["command"]
    assert p.manifest.network_policy.canary


def test_canary_uses_setup_hosts(tasks_dir: Path, tmp_path: Path) -> None:
    p = plan(
        tasks_dir, tmp_path, [AgentRequest("grok-build", "xai/grok-4.7")], canary=True
    )
    canary = Path(load_job(p)["tasks"][0]["path"])
    config = tomllib.loads((canary / "task.toml").read_text())
    assert config["agent"]["allowed_hosts"] == ["api.x.ai"]
    assert "x.ai" in config["environment"]["allowed_hosts"]
    # Real agents do not run the agent-phase probe.
    assert (canary / "solution" / "solve.sh").read_text().strip().endswith("echo OK")


def test_pair_parsing() -> None:
    with pytest.raises(RunError):
        parse_agent_pairs(["claude-code"], [])
    with pytest.raises(RunError):
        parse_agent_pairs(["claude-code", "codex"], ["a", "b"], ["1"])


def test_fill_allowed_hosts_requires_markers() -> None:
    with pytest.raises(RunError, match="found 0"):
        fill_allowed_hosts("[environment]\nallowed_hosts = []\n", ["x"], ["x"])
    # Tasks built by tdb 1.0.0 only have the agent marker, in [environment].
    old = "[environment]\nallowed_hosts = []  # tdb:agent-allowed-hosts\n"
    with pytest.raises(RunError, match="rebuild the tasks"):
        fill_allowed_hosts(old, ["x"], ["x"])


def test_regrade_command(tmp_path: Path) -> None:
    cmd = regrade_command(tmp_path / "job", tmp_path / "tasks", job_name="r1")
    assert cmd[:3] == ["harbor", "job", "regrade"]
    assert "-p" in cmd and "--job-name" in cmd


def test_job_yaml_parses_with_harbor_jobconfig(tasks_dir: Path, tmp_path: Path) -> None:
    harbor_config = pytest.importorskip(
        "harbor.models.job.config", reason="harbor not installed"
    )
    p = plan(tasks_dir, tmp_path, [AgentRequest("claude-code", "anthropic/x")])
    config = harbor_config.JobConfig.model_validate(load_job(p))
    assert config.agents[0].kwargs["disallowed_tools"] == "WebSearch,WebFetch"


@pytest.mark.parametrize("profile", agents.AGENTS, ids=lambda a: a.name)
def test_agent_kwargs_pass_harbor_preflight(
    tasks_dir: Path, tmp_path: Path, profile: agents.AgentProfile
) -> None:
    """Harbor rejects unknown or invalid kwargs before any trial starts."""
    harbor_config = pytest.importorskip(
        "harbor.models.job.config", reason="harbor not installed"
    )
    factory = pytest.importorskip("harbor.agents.factory")
    model = f"{profile.providers[0]}/x"
    p = plan(tasks_dir, tmp_path, [AgentRequest(profile.name, model)])
    config = harbor_config.JobConfig.model_validate(load_job(p))
    factory.AgentFactory.run_preflight(config.agents[0])
