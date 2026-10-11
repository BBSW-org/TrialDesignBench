"""Harbor agent classes `tdb run` launches: Harbor's adapters with a file-based
instruction transport.

This module is a Harbor plugin, not a core module. Harbor imports it through
the `import_path` of each agent in the `job.yaml` that `tdb run` writes:
`tdb run` copies this file beside the task copies as `tdb_harbor_agents.py`
and puts that directory on `PYTHONPATH` for the `harbor` process. It imports
Harbor and the standard library only, never the rest of this package, and no
`tdb` command imports it, so the core modules still never import Harbor.

## Why

Harbor's adapters for Claude Code, Codex CLI, Grok Build, and OpenCode hand
the rendered instruction to the CLI inside one `docker compose exec` call:
Claude Code as an environment variable (`-e NAME=<instruction>`), the other
three as a quoted positional argument inside `bash -c "<command>"`. Both
cross an `execve` boundary on the host, and the argument crosses it again
inside the container, where Linux caps a single argument or environment
string at `MAX_ARG_STRLEN` (128 KiB; 32 KiB on Windows). An instruction with
a protocol or SAP of a few hundred kilobytes therefore kills the trial before
the agent starts: `OSError: [Errno 7] Argument list too long: 'docker'`.
Harbor 0.23.0 and 0.24.0 both behave this way; the upstream fixes proposed in
harbor-framework/harbor#3050 and #3433 are open, and #3433 keeps the
environment variable transport, which still crosses the host boundary.

## What changes

Each class below subclasses Harbor's adapter and changes only the transport.
`run()` records the rendered instruction, and `_exec()`, the choke point for
every command an installed agent runs, relocates it when a command or its
environment carries it:

- The instruction is written to a temporary file and uploaded with
  `environment.upload_file()` to `REMOTE_INSTRUCTION_PATH` (`docker compose
  cp`, which has no such limit).
- An environment variable holding the instruction (Claude Code, and the
  transport of #3433) is dropped and re-created as a shell variable read from
  the file: `NAME=$(cat FILE; printf x); NAME=${NAME%x}` keeps every byte,
  including trailing newlines, and shell variables are not subject to the
  limit as long as only builtins (`printf`) see them.
- A quoted positional argument is replaced by the CLI's own way of reading
  the prompt from a file or stdin: `codex exec -- - <FILE`,
  `opencode run -- <FILE`, `grok --prompt-file FILE`.

What the CLI receives is byte-identical to the instruction Harbor rendered.
Every other upstream behavior (credentials, settings files, skills, sessions,
cleanup, trajectories, `name()`, `version()`) is inherited unchanged, so the
trial's `result.json` still records the agent as `claude-code`, `codex`,
`grok-build`, or `opencode`. A command that does not carry the instruction
(for example once upstream moves to a file transport) passes through
untouched; one that carries it in a shape this module does not know fails
the trial loudly with `InstructionTransportError` instead of launching with
the old transport.
"""

from __future__ import annotations

import shlex
import tempfile
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any

from harbor.agents.installed.claude_code import ClaudeCode as _ClaudeCode
from harbor.agents.installed.codex import Codex as _Codex
from harbor.agents.installed.grok_build import GrokBuild as _GrokBuild
from harbor.agents.installed.opencode import OpenCode as _OpenCode

REMOTE_INSTRUCTION_PATH = PurePosixPath("/installed-agent/tdb-instruction.md")
"""Where the rendered instruction is uploaded in the agent container.

`/installed-agent` is created by Harbor's `BaseInstalledAgent.setup()` (root,
mode 755) before any agent runs; the file is uploaded with mode 644 so the
agent user can read it.
"""


class InstructionTransportError(RuntimeError):
    """A launch command carries the instruction in a shape this module cannot
    relocate. Raised instead of launching with Harbor's original transport."""


def _harbor_version() -> str:
    try:
        return version("harbor")
    except PackageNotFoundError:
        return "unknown"


def shell_read_file(name: str, path: str) -> str:
    """Shell snippet assigning the content of `path` to the variable `name`.

    `$(...)` strips trailing newlines, so a sentinel `x` is appended by the
    builtin `printf` and removed again; the variable then holds the file byte
    for byte.
    """
    return f"{name}=$(cat {shlex.quote(path)}; printf x); {name}=${{{name}%x}}; "


def _count_tokens(command: str, token: str) -> int:
    """Occurrences of `token` in `command` delimited by whitespace or an end."""
    count = 0
    start = 0
    while (index := command.find(token, start)) != -1:
        end = index + len(token)
        before = command[index - 1] if index else " "
        after = command[end] if end < len(command) else " "
        if before.isspace() and after.isspace():
            count += 1
        start = end
    return count


def _replace_once(command: str, old: str, new: str, agent: str) -> str:
    """Replace the single occurrence of `old`; anything else is a shape this
    module does not know."""
    if command.count(old) != 1:
        raise InstructionTransportError(
            f"{agent}: Harbor {_harbor_version()} launches the agent with a "
            f"command this version of trialdesignbench does not know (expected "
            f"exactly one {old[:60]!r}); refusing to pass the instruction on the "
            "command line"
        )
    return command.replace(old, new)


class FileInstructionMixin:
    """Relocate the rendered instruction from exec argv/env to an uploaded file.

    Mix in before a `harbor.agents.installed.base.BaseInstalledAgent` subclass.
    Adapters that pass the instruction as a command argument override
    `_tdb_rewrite_argv` with the CLI's file or stdin option.
    """

    _tdb_instruction: str | None = None
    _tdb_uploaded: bool = False

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        # Upstream's run() is wrapped by `with_prompt_template`, which renders
        # the prompt template; render here too so the exact string Harbor puts
        # into a command or env var is known. Pass the raw instruction on so it
        # is rendered once.
        self._tdb_instruction = self.render_instruction(instruction)  # type: ignore[attr-defined]
        self._tdb_uploaded = False
        try:
            await super().run(instruction, environment, context)  # type: ignore[misc]
        finally:
            self._tdb_instruction = None
            self._tdb_uploaded = False

    async def _exec(
        self,
        environment: Any,
        command: str,
        user: str | int | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout_sec: int | None = None,
        **kwargs: Any,
    ) -> Any:
        if self._tdb_instruction is not None:
            command, env = await self._tdb_relocate(environment, command, env)
        return await super()._exec(  # type: ignore[misc]
            environment,
            command,
            user=user,
            env=env,
            cwd=cwd,
            timeout_sec=timeout_sec,
            **kwargs,
        )

    async def _tdb_relocate(
        self, environment: Any, command: str, env: Mapping[str, str] | None
    ) -> tuple[str, dict[str, str] | None]:
        """Return `command` and `env` without the instruction, uploading it.

        The instruction is recognized as the exact value of an environment
        variable, or as its `shlex.quote` form standing alone as a word of the
        command. Anything else passes through unchanged.
        """
        instruction = self._tdb_instruction
        assert instruction is not None
        env_keys = [k for k, v in (env or {}).items() if v == instruction]
        quoted = shlex.quote(instruction)
        in_argv = _count_tokens(command, quoted) > 0
        if not env_keys and not in_argv:
            return command, dict(env) if env is not None else None
        await self._tdb_upload(environment)
        path = REMOTE_INSTRUCTION_PATH.as_posix()
        new_env = {k: v for k, v in (env or {}).items() if k not in env_keys}
        if env_keys:
            command = "".join(shell_read_file(k, path) for k in env_keys) + command
        if in_argv:
            command = self._tdb_rewrite_argv(command, quoted, path)
        if quoted in command or instruction in command:
            raise InstructionTransportError(
                f"{self._tdb_agent_name()}: the instruction is still on the "
                "command line after rewriting the launch command"
            )
        return command, new_env or None

    def _tdb_rewrite_argv(self, command: str, quoted: str, path: str) -> str:
        raise InstructionTransportError(
            f"{self._tdb_agent_name()}: Harbor {_harbor_version()} passes the "
            "instruction as a command argument, which this version of "
            "trialdesignbench has no rule for"
        )

    async def _tdb_upload(self, environment: Any) -> None:
        if self._tdb_uploaded:
            return
        assert self._tdb_instruction is not None
        with tempfile.TemporaryDirectory(prefix="tdb-instruction-") as tmp:
            local = Path(tmp) / REMOTE_INSTRUCTION_PATH.name
            # Bytes, not text: no newline translation on any host.
            local.write_bytes(self._tdb_instruction.encode("utf-8"))
            # `docker compose cp` keeps the mode; the agent user must read it.
            local.chmod(0o644)
            await environment.upload_file(local, REMOTE_INSTRUCTION_PATH.as_posix())
        self._tdb_uploaded = True

    def _tdb_agent_name(self) -> str:
        try:
            return str(self.name())  # type: ignore[attr-defined]
        except Exception:
            return type(self).__name__


class ClaudeCode(FileInstructionMixin, _ClaudeCode):
    """Claude Code. Harbor passes the instruction in a per-launch environment
    variable and pipes it into `claude --print` with the builtin `printf`; the
    variable becomes a shell variable read from the uploaded file."""


class Codex(FileInstructionMixin, _Codex):
    """Codex CLI. `codex exec ... -- '<instruction>' 2>&1 </dev/null` becomes
    `codex exec ... -- - 2>&1 <FILE`: `-` reads the prompt from stdin."""

    def _tdb_rewrite_argv(self, command: str, quoted: str, path: str) -> str:
        agent = self._tdb_agent_name()
        command = _replace_once(command, f"-- {quoted} ", "-- - ", agent)
        return _replace_once(command, "</dev/null", f"<{path}", agent)


class GrokBuild(FileInstructionMixin, _GrokBuild):
    """Grok Build. `grok --single '<instruction>'` becomes
    `grok --prompt-file FILE`, its single-turn prompt from a file."""

    def _tdb_rewrite_argv(self, command: str, quoted: str, path: str) -> str:
        return _replace_once(
            command,
            f"--single {quoted}",
            f"--prompt-file {shlex.quote(path)}",
            self._tdb_agent_name(),
        )


class OpenCode(FileInstructionMixin, _OpenCode):
    """OpenCode. `opencode run ... -- '<instruction>' 2>&1 </dev/null` becomes
    `opencode run ... -- 2>&1 <FILE`: without a message argument OpenCode
    (0.2.2 and later) reads the prompt from its piped stdin."""

    def _tdb_rewrite_argv(self, command: str, quoted: str, path: str) -> str:
        agent = self._tdb_agent_name()
        command = _replace_once(command, f"-- {quoted} ", "-- ", agent)
        return _replace_once(command, "</dev/null", f"<{path}", agent)


__all__ = [
    "REMOTE_INSTRUCTION_PATH",
    "ClaudeCode",
    "Codex",
    "FileInstructionMixin",
    "GrokBuild",
    "InstructionTransportError",
    "OpenCode",
    "shell_read_file",
]
