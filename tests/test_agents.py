from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from trialdesignbench import agents, environment
from trialdesignbench.canary import AGENT_RESULT_PATH, RESULT_PATH, write_canary_task
from trialdesignbench.grade import WEB_TOOL_NAMES

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
PROFILES = pytest.mark.parametrize("profile", agents.AGENTS, ids=lambda a: a.name)


@PROFILES
def test_profile_is_consistent(profile: agents.AgentProfile) -> None:
    assert profile.version_pin in {f.name for f in fields(environment.ImagePins)}
    assert profile.providers and set(profile.providers) <= set(agents.PROVIDERS)
    assert profile.disabled_tools, "every agent must disable its web tools"
    # Setup hosts exist exactly when Harbor installs the CLI during setup.
    assert bool(profile.setup_hosts) != profile.preinstalled
    for host in (
        *profile.setup_hosts,
        *(agents.PROVIDERS[p].host for p in profile.providers),
    ):
        assert "*" not in host and "/" not in host
    assert f"{profile.image_label.removeprefix('org.trialdesignbench.')}=" in (
        environment.dockerfile_text()
    )
    effort = profile.effort
    assert effort.levels and set(effort.levels) <= set(agents.EFFORT_LEVELS)
    # Ordered lowest to highest, like the canonical list.
    assert effort.levels == tuple(
        lv for lv in agents.EFFORT_LEVELS if lv in effort.levels
    )
    assert agents.DEFAULT_EFFORT not in effort.levels
    # The closed-book kwargs never set the effort; --effort owns that kwarg.
    assert effort.kwarg not in profile.kwargs
    assert effort.how and effort.note


@PROFILES
def test_effort_kwargs(profile: agents.AgentProfile) -> None:
    assert agents.effort_kwargs(profile, None) == {}
    assert agents.effort_kwargs(profile, agents.DEFAULT_EFFORT) == {}
    for level in profile.effort.levels:
        kwargs = agents.effort_kwargs(profile, level)
        assert kwargs == {profile.effort.kwarg: level}
        assert agents.effort_from_kwargs(profile.name, kwargs) == level
    with pytest.raises(agents.AgentError, match="does not accept --effort 'bogus'"):
        agents.effort_kwargs(profile, "bogus")
    assert agents.effort_from_kwargs(profile.name, {"version": "1"}) is None
    assert agents.effort_from_kwargs("aider", {"reasoning_effort": "high"}) is None


def test_effort_levels_match_harbor_and_the_clis() -> None:
    """Levels verified against Harbor 0.23.0 schemas and the pinned CLIs.

    `claude --effort` (2.1.283) lists low..max and drops anything else with
    a warning; the OpenAI API enumerates none..max for `reasoning.effort`;
    grok 1.0.40 documents none..max as canonical levels; OpenCode variants
    are the catalog's `reasoning_options` effort values.
    """
    levels = {a.name: a.effort.levels for a in agents.AGENTS}
    assert levels["claude-code"] == ("low", "medium", "high", "xhigh", "max")
    assert levels["codex"] == levels["grok-build"] == agents.EFFORT_LEVELS
    assert levels["opencode"] == agents.EFFORT_LEVELS
    kwargs = {a.name: a.effort.kwarg for a in agents.AGENTS}
    assert kwargs == {
        "claude-code": "reasoning_effort",
        "codex": "reasoning_effort",
        "grok-build": "reasoning_effort",
        "opencode": "variant",
    }
    with pytest.raises(agents.AgentError, match="low, medium, high, xhigh, max"):
        agents.effort_kwargs(agents.get_profile("claude-code"), "minimal")


@PROFILES
def test_grader_recognizes_disabled_web_tools(profile: agents.AgentProfile) -> None:
    """The trajectory scan must flag any web tool tdb tries to disable."""
    for tool in profile.disabled_tools:
        assert tool.lower() in WEB_TOOL_NAMES


def test_registry_names() -> None:
    names = agents.supported_names()
    assert len(set(names)) == len(names)
    assert not set(names) & set(agents.REFUSED_AGENTS)
    with pytest.raises(agents.AgentError, match="Supported agents"):
        agents.get_profile("gemini-cli")


def test_grok_policy_file_matches_profile() -> None:
    policy = tomllib.loads(
        (ROOT / "src/trialdesignbench/environment/grok-requirements.toml").read_text()
    )
    kwargs = agents.get_profile("grok-build").kwargs
    assert policy["disable_web_search"] is kwargs["disable_web_search"] is True
    assert policy["features"]["web_fetch"] is False
    assert kwargs["grok_config"]["features"]["web_fetch"] is False


def test_codex_subscription_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex = agents.get_profile("codex")
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(agents.AgentError, match=r"\.codex/auth\.json"):
        agents.resolve_auth(codex, "subscription", "openai", {})
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "auth.json").write_text("{}")
    assert agents.resolve_auth(codex, "subscription", "openai", {}) == {
        "CODEX_FORCE_AUTH_JSON": "1"
    }


def test_docs_list_every_agent() -> None:
    """docs/articles/agents.md must stay in sync with the registry."""
    text = (ROOT / "docs/articles/agents.md").read_text()
    for profile in agents.AGENTS:
        row = re.search(rf"^\| `{re.escape(profile.name)}` \|.*$", text, re.MULTILINE)
        assert row, f"{profile.name} missing from the supported agents table"
        assert profile.version in row.group(0)
    for name in agents.REFUSED_AGENTS:
        assert f"`{name}`" in text
    readme = (ROOT / "README.md").read_text()
    for profile in agents.AGENTS:
        assert f"`{profile.name}`" in readme


def test_canary_probes_both_phases(tmp_path: Path) -> None:
    task = write_canary_task(
        tmp_path,
        image="img",
        agent_hosts=["api.x.ai"],
        setup_hosts=["x.ai", "api.x.ai"],
        agent_probe=True,
    )
    config = tomllib.loads((task / "task.toml").read_text())
    assert config["agent"]["allowed_hosts"] == ["api.x.ai"]
    assert config["environment"]["allowed_hosts"] == ["api.x.ai", "x.ai"]
    # Setup probe (healthcheck): blocked URLs fail, model API connects.
    health = config["environment"]["healthcheck"]["command"]
    assert RESULT_PATH in health and "clinicaltrials.gov" in health
    # Agent-phase probe (oracle): install hosts must be blocked as well.
    solve = (task / "solution" / "solve.sh").read_text()
    assert AGENT_RESULT_PATH in solve and "https://x.ai" in solve
    assert "https://api.x.ai" not in solve.split("for host in")[0]
    verify = (task / "tests" / "test.sh").read_text()
    phases = json.loads(
        re.search(r"phases = json\.loads\('(.*)'\)", verify).group(1)  # type: ignore[union-attr]
    )
    assert phases == {"setup": RESULT_PATH, "agent": AGENT_RESULT_PATH}


def test_canary_without_agent_probe(tmp_path: Path) -> None:
    task = write_canary_task(tmp_path, image="img", agent_hosts=["api.anthropic.com"])
    assert "echo OK" in (task / "solution" / "solve.sh").read_text()
    assert AGENT_RESULT_PATH not in (task / "tests" / "test.sh").read_text()


def _run_verifier(task: Path, tmp_path: Path, results: dict[str, object]) -> dict:
    """Execute the canary verifier's Python with local paths."""
    script = (task / "tests" / "test.sh").read_text()
    code = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    out = tmp_path / "verifier"
    out.mkdir(parents=True)
    code = code.replace('"/logs/verifier"', repr(str(out)))
    for phase, path in (("setup", RESULT_PATH), ("agent", AGENT_RESULT_PATH)):
        local = tmp_path / f"{phase}.json"
        code = code.replace(path, str(local))
        if phase in results:
            local.write_text(json.dumps(results[phase]))
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True)
    verdict: dict = json.loads((out / "canary.json").read_text())
    reward = json.loads((out / "reward.json").read_text())["reward"]
    assert reward == (0.0 if verdict["reasons"] else 1.0)
    return verdict


def test_canary_verifier_checks_both_phases(tmp_path: Path) -> None:
    task = write_canary_task(
        tmp_path / "task",
        image="img",
        agent_hosts=["api.x.ai"],
        setup_hosts=["x.ai"],
        agent_probe=True,
    )
    blocked = {url: 7 for url in ("https://clinicaltrials.gov", "https://x.ai")}
    ok = {"blocked": blocked, "allowed": {"api.x.ai": 0}}
    assert _run_verifier(task, tmp_path / "ok", {"setup": ok, "agent": ok}) == {
        "probe": {"setup": ok, "agent": ok},
        "reasons": [],
    }
    leaky = {"blocked": {**blocked, "https://x.ai": 0}, "allowed": {"api.x.ai": 0}}
    verdict = _run_verifier(task, tmp_path / "leak", {"setup": ok, "agent": leaky})
    assert verdict["reasons"] == ["agent: blocked URL reachable: https://x.ai"]
    verdict = _run_verifier(task, tmp_path / "missing", {"setup": ok})
    assert verdict["reasons"] == ["agent probe result missing"]
