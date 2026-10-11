"""Tests for `trialdesignbench.harbor_agents`, the Harbor plugin that moves the
rendered instruction out of exec argv and env into an uploaded file.

They run against the installed Harbor (the dev group pins it) and are skipped
without it. The fake environment records every exec call and upload, like
Harbor's own adapter tests do with `AsyncMock`.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from trialdesignbench import agents, environment

pytest.importorskip("harbor.agents.installed.base", reason="harbor not installed")
harbor_agents = importlib.import_module("trialdesignbench.harbor_agents")

REMOTE = harbor_agents.REMOTE_INSTRUCTION_PATH.as_posix()
MAX_ARG_STRLEN = 131072
"""Linux cap on one argv or environment string (32 pages)."""

API_ENV = {
    "ANTHROPIC_API_KEY": "sk-test",
    "OPENAI_API_KEY": "sk-test",
    "XAI_API_KEY": "sk-test",
    "OPENCODE_API_KEY": "sk-test",
}
MODELS = {
    "claude-code": "anthropic/claude-haiku-5-5",
    "codex": "openai/gpt-6-astra",
    "grok-build": "xai/grok-4.7",
    "opencode": "opencode-go/grok-4.7",
}
LAUNCH_MARKER = {
    "claude-code": "| claude ",
    "codex": "codex exec ",
    "grok-build": "grok --no-auto-update",
    "opencode": "opencode --model=",
}
PROFILES = pytest.mark.parametrize("profile", agents.AGENTS, ids=lambda a: a.name)


def large_instruction(size: int = 300_000) -> str:
    """An instruction past the exec limit with shell-hostile content and a
    trailing newline, like the ones `tdb build` writes."""
    head = (
        "# Instruction\n\nQuotes ' and \" and `backticks`, $DOLLAR ${braces} "
        "backslash \\ percent %s %x, tabs\t, unicode é 中文 — done.\n\n"
    )
    filler = "Protocol section text. " * 40 + "\n"
    body = head
    while len(body.encode()) < size:
        body += filler
    return body + "\n"


@dataclass
class ExecCall:
    command: str
    env: dict[str, str]
    user: str | int | None


@dataclass
class ExecResult:
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeEnvironment:
    """Records what Harbor would run; uploads are read at call time because
    the plugin deletes its temporary file right after uploading."""

    default_user: str = "agent"
    calls: list[ExecCall] = field(default_factory=list)
    uploads: dict[str, bytes] = field(default_factory=dict)

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        self.calls.append(ExecCall(command, dict(env or {}), user))
        return ExecResult()

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        source = Path(source_path)
        assert source.stat().st_mode & 0o777 == 0o644
        self.uploads[target_path] = source.read_bytes()

    def launch(self, marker: str) -> ExecCall:
        hits = [c for c in self.calls if marker in c.command]
        assert len(hits) == 1, [c.command[:80] for c in self.calls]
        return hits[0]


def make_agent(profile: agents.AgentProfile, logs_dir: Path, **extra: Any) -> Any:
    """The plugin class with exactly the kwargs `tdb run` passes."""
    cls = getattr(harbor_agents, profile.harbor_class)
    kwargs = {"version": profile.version, **profile.kwargs, **extra}
    return cls(logs_dir=logs_dir, model_name=MODELS[profile.name], **kwargs)


def run(agent: Any, instruction: str, env: FakeEnvironment) -> None:
    asyncio.run(agent.run(instruction, env, context=None))


@pytest.fixture(autouse=True)
def api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in API_ENV.items():
        monkeypatch.setenv(key, value)


@PROFILES
def test_instruction_leaves_argv_and_env(
    profile: agents.AgentProfile, tmp_path: Path
) -> None:
    instruction = large_instruction()
    assert len(instruction.encode()) > MAX_ARG_STRLEN
    env = FakeEnvironment()
    run(make_agent(profile, tmp_path / "logs"), instruction, env)

    # Uploaded byte for byte where the launch command reads it (Harbor may
    # upload its own settings files as well).
    assert env.uploads[REMOTE] == instruction.encode()
    assert [p for p, data in env.uploads.items() if instruction.encode() in data] == [
        REMOTE
    ]
    quoted = shlex.quote(instruction)
    for call in env.calls:
        assert instruction not in call.command and quoted not in call.command
        assert len(call.command.encode()) < MAX_ARG_STRLEN // 4
        for value in call.env.values():
            assert value != instruction
            assert len(value.encode()) < MAX_ARG_STRLEN // 4
    launch = env.launch(LAUNCH_MARKER[profile.name])
    assert REMOTE in launch.command


def test_claude_code_reads_the_file_into_the_shell_variable(tmp_path: Path) -> None:
    instruction = "Reply with OK.\n"
    env = FakeEnvironment()
    run(make_agent(agents.get_profile("claude-code"), tmp_path), instruction, env)
    launch = env.launch("| claude ")
    # Harbor's own `NAME="${NAME_ENV}"; unset NAME_ENV; printf "%s" "$NAME" |`
    # stays; the env var is replaced by the shell assignment in front of it.
    assert not any(k.startswith("HARBOR_CLAUDE_CODE_INSTRUCTION_") for k in launch.env)
    # Harbor's `_exec` prefixes every command with `set -o pipefail; `.
    assert launch.command.startswith("set -o pipefail; HARBOR_CLAUDE_CODE_INSTRUCTION_")
    assert f"=$(cat {REMOTE}; printf x); " in launch.command
    assert 'printf "%s" "$harbor_claude_code_instruction_' in launch.command
    assert "--print" in launch.command and "--effort" not in launch.command
    assert launch.env["ANTHROPIC_API_KEY"] == "sk-test"  # auth env untouched


def test_codex_reads_stdin(tmp_path: Path) -> None:
    env = FakeEnvironment()
    run(make_agent(agents.get_profile("codex"), tmp_path), "Reply with OK.\n", env)
    launch = env.launch("codex exec ")
    assert f"-- - 2>&1 <{REMOTE} | tee " in launch.command
    assert "/dev/null" not in launch.command
    assert "--model gpt-6-astra" in launch.command
    assert "-c web_search=disabled" in launch.command  # closed-book flag intact


def test_opencode_reads_stdin(tmp_path: Path) -> None:
    env = FakeEnvironment()
    run(make_agent(agents.get_profile("opencode"), tmp_path), "Reply with OK.\n", env)
    launch = env.launch("opencode --model=")
    assert f"--dangerously-skip-permissions -- 2>&1 <{REMOTE} | stdbuf" in (
        launch.command
    )
    assert "/dev/null" not in launch.command


def test_grok_build_reads_the_prompt_file(tmp_path: Path) -> None:
    env = FakeEnvironment()
    run(make_agent(agents.get_profile("grok-build"), tmp_path), "Reply with OK.\n", env)
    launch = env.launch("grok --no-auto-update")
    assert f"--prompt-file {REMOTE} " in launch.command
    assert "--single" not in launch.command
    # grok's stdin stays /dev/null: the prompt comes from the file.
    assert f"--prompt-file {REMOTE} " in launch.command.split("</dev/null &")[0]


@PROFILES
def test_effort_and_closed_book_flags_survive(
    profile: agents.AgentProfile, tmp_path: Path
) -> None:
    """The rewrite touches only the instruction; other flags are upstream's."""
    level = profile.effort.levels[-1]
    env = FakeEnvironment()
    agent = make_agent(profile, tmp_path, **{profile.effort.kwarg: level})
    run(agent, "Reply with OK.\n", env)
    launch = env.launch(LAUNCH_MARKER[profile.name])
    expected = {
        "claude-code": f"--effort {level}",
        "codex": f"model_reasoning_effort={level}",
        "grok-build": f"--reasoning-effort {level}",
        "opencode": f"--variant {level}",
    }[profile.name]
    assert expected in launch.command


def test_prompt_template_is_rendered_once(tmp_path: Path) -> None:
    template = tmp_path / "template.j2"
    template.write_text("PREFIX {{ instruction }} SUFFIX")
    env = FakeEnvironment()
    agent = make_agent(
        agents.get_profile("codex"),
        tmp_path / "logs",
        prompt_template_path=str(template),
    )
    run(agent, "Reply with OK.\n", env)
    assert env.uploads[REMOTE] == b"PREFIX Reply with OK.\n SUFFIX"


def test_commands_without_the_instruction_pass_through(tmp_path: Path) -> None:
    agent = make_agent(agents.get_profile("codex"), tmp_path)
    agent._tdb_instruction = "Reply with OK.\n"
    env = FakeEnvironment()
    command, exec_env = asyncio.run(
        agent._tdb_relocate(env, "mkdir -p /x", {"CODEX_HOME": "/x"})
    )
    assert (command, exec_env) == ("mkdir -p /x", {"CODEX_HOME": "/x"})
    assert not env.uploads


@pytest.mark.parametrize(
    ("name", "command"),
    [
        # Codex without the `</dev/null` the stdin redirect replaces.
        ("codex", "codex exec -- {q} 2>&1 | tee out"),
        # OpenCode with the instruction twice.
        ("opencode", "opencode run -- {q} {q} 2>&1 </dev/null | tee out"),
        # Claude Code never passes it as an argument; no rule exists.
        ("claude-code", "claude --print {q}"),
    ],
)
def test_unknown_launch_shapes_fail_loudly(
    name: str, command: str, tmp_path: Path
) -> None:
    agent = make_agent(agents.get_profile(name), tmp_path)
    agent._tdb_instruction = "Reply with OK.\n"
    quoted = shlex.quote(agent._tdb_instruction)
    with pytest.raises(harbor_agents.InstructionTransportError, match=name):
        asyncio.run(
            agent._tdb_relocate(FakeEnvironment(), command.format(q=quoted), {})
        )


def test_shell_read_file_keeps_every_byte(tmp_path: Path) -> None:
    """The `$(cat FILE; printf x)` idiom used for env var transports, run in
    a real bash with the builtin printf, reproduces the file byte for byte."""
    if subprocess.run(["bash", "-c", "true"], capture_output=True).returncode != 0:
        pytest.skip("needs bash")
    content = large_instruction().encode() + b"\n\n"  # several trailing newlines
    source = tmp_path / "instruction.md"
    source.write_bytes(content)
    script = harbor_agents.shell_read_file("V", str(source)) + 'printf "%s" "$V"'
    result = subprocess.run(["bash", "-c", script], capture_output=True, check=True)
    assert hashlib.sha256(result.stdout).hexdigest() == (
        hashlib.sha256(content).hexdigest()
    )
    assert len(content) > MAX_ARG_STRLEN


def test_plugin_names_follow_the_registry() -> None:
    for profile in agents.AGENTS:
        cls = getattr(harbor_agents, profile.harbor_class)
        assert cls.name() == profile.name
        assert issubclass(cls, harbor_agents.FileInstructionMixin)
    assert environment.PINS  # the plugin itself never imports the package
