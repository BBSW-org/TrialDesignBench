"""Local Codex SDK integration."""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from trialdesignbench.config import DEFAULT_CODEX_EFFORT
from trialdesignbench.models import CodexRunArtifact
from trialdesignbench.status import StatusReporter


class CodexRunner(Protocol):
    """Interface used by the step 1 pipeline to run an agent prompt."""

    def run(
        self,
        *,
        prompt: str,
        run_directory: Path,
        model: str,
        codex_bin: str | None = None,
        effort: str = DEFAULT_CODEX_EFFORT,
    ) -> CodexRunArtifact:
        """Run Codex and return persisted artifacts."""


@dataclass(frozen=True, slots=True)
class LocalCodexRunner:
    """Run the prompt against a locally installed OpenAI Codex SDK/runtime."""

    status_reporter: StatusReporter | None = None

    def run(
        self,
        *,
        prompt: str,
        run_directory: Path,
        model: str,
        codex_bin: str | None = None,
        effort: str = DEFAULT_CODEX_EFFORT,
    ) -> CodexRunArtifact:
        self._report("Codex: loading Python SDK")
        openai_codex = _load_openai_codex()
        run_dir = run_directory.expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = run_dir / "prompt.md"
        response_path = run_dir / "codex_response.md"
        metadata_path = run_dir / "codex_run.json"
        self._report(f"Codex: writing prompt to {prompt_path}")
        prompt_path.write_text(prompt, encoding="utf-8")

        config = None
        if codex_bin:
            self._report(f"Codex: using runtime binary {codex_bin}")
            config = openai_codex.AppServerConfig(
                codex_bin=codex_bin,
                cwd=str(run_dir),
            )

        self._report("Codex: starting local runtime")
        with openai_codex.Codex(config=config) as codex:
            thread = codex.thread_start(
                cwd=str(run_dir),
                model=model,
                config={"model_reasoning_effort": effort},
            )
            self._report(f"Codex: started thread {thread.id}")
            result = self._run_turn(
                thread,
                prompt,
                cwd=str(run_dir),
                model=model,
                effort=effort,
            )

        final_response = _final_response(result)
        self._report(f"Codex: writing final response to {response_path}")
        response_path.write_text(final_response or "", encoding="utf-8")
        metadata = {
            "model": model,
            "effort": effort,
            "codex_bin": codex_bin,
            "final_response_present": final_response is not None,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self._report(f"Codex: wrote run metadata to {metadata_path}")
        return CodexRunArtifact(
            prompt_path=prompt_path,
            response_path=response_path,
            metadata_path=metadata_path,
            run_directory=run_dir,
            model=model,
            final_response=final_response,
        )

    def _run_turn(self, thread: Any, prompt: str, **kwargs: Any) -> Any:
        if self.status_reporter is None:
            return thread.run(prompt, **kwargs)

        turn = thread.turn(prompt, **kwargs)
        self._report(f"Codex: started turn {turn.id}")
        stream = turn.stream()
        try:
            return _collect_streamed_turn_result(
                stream,
                turn_id=turn.id,
                status_reporter=self.status_reporter,
            )
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()

    def _report(self, message: str) -> None:
        if self.status_reporter is not None:
            self.status_reporter(message)


@dataclass(frozen=True, slots=True)
class _StreamedTurnResult:
    final_response: str | None


def _load_openai_codex() -> Any:
    try:
        return importlib.import_module("openai_codex")
    except ImportError as exc:
        msg = (
            "The OpenAI Codex Python SDK is not installed in this environment. "
            "Run `uv sync` from a TrialDesignBench checkout, or add the SDK with "
            '`uv add "openai-codex @ '
            'git+https://github.com/openai/codex.git#subdirectory=sdk/python"`.'
        )
        raise RuntimeError(msg) from exc


def _final_response(result: Any) -> str | None:
    value = getattr(result, "final_response", None)
    if value is None or isinstance(value, str):
        return value
    return str(value)


def _collect_streamed_turn_result(
    stream: Iterator[Any],
    *,
    turn_id: str,
    status_reporter: StatusReporter,
) -> _StreamedTurnResult:
    completed_turn: Any | None = None
    items: list[Any] = []
    usage: Any | None = None

    for event in stream:
        message = _describe_codex_event(event, turn_id=turn_id)
        if message is not None:
            status_reporter(f"Codex: {message}")

        payload = getattr(event, "payload", None)
        if not _payload_matches_turn(payload, turn_id):
            continue

        method = getattr(event, "method", "")
        if method == "item/completed":
            items.append(getattr(payload, "item", None))
            continue
        if method == "thread/tokenUsage/updated":
            usage = getattr(payload, "token_usage", None)
            continue
        if method == "turn/completed":
            completed_turn = getattr(payload, "turn", None)

    if completed_turn is None:
        raise RuntimeError("Codex turn completed event was not received")

    _raise_for_failed_turn(completed_turn)
    if usage is not None:
        total = getattr(usage, "total", None)
        total_tokens = getattr(total, "total_tokens", None)
        if total_tokens is not None:
            status_reporter(f"Codex: token usage total {total_tokens}")

    return _StreamedTurnResult(final_response=_final_response_from_items(items))


def _describe_codex_event(event: Any, *, turn_id: str) -> str | None:
    payload = getattr(event, "payload", None)
    if not _payload_matches_turn(payload, turn_id):
        return None

    method = getattr(event, "method", "")
    if method == "turn/started":
        return "turn started"
    if method == "turn/completed":
        turn = getattr(payload, "turn", None)
        status = _enum_value(getattr(turn, "status", None)) or "unknown"
        duration_ms = getattr(turn, "duration_ms", None)
        if duration_ms is not None:
            return f"turn completed with status {status} in {duration_ms} ms"
        return f"turn completed with status {status}"
    if method == "turn/plan/updated":
        return _describe_plan_update(payload)
    if method == "item/started":
        return _describe_item_event("started", getattr(payload, "item", None))
    if method == "item/completed":
        return _describe_item_event("completed", getattr(payload, "item", None))
    if method == "thread/tokenUsage/updated":
        return _describe_token_usage(payload)
    return None


def _describe_plan_update(payload: Any) -> str:
    steps = getattr(payload, "plan", None)
    if not isinstance(steps, list) or not steps:
        return "updated plan"

    active = next(
        (
            getattr(step, "step", "")
            for step in steps
            if _enum_value(getattr(step, "status", None)) == "inProgress"
        ),
        "",
    )
    if active:
        return f"working on plan step: {_truncate(active)}"
    completed = sum(
        1 for step in steps if _enum_value(getattr(step, "status", None)) == "completed"
    )
    return f"updated plan ({completed}/{len(steps)} completed)"


def _describe_item_event(action: str, item: Any) -> str:
    thread_item = _unwrap_root(item)
    item_type = getattr(thread_item, "type", None)

    if item_type == "plan":
        text = _truncate(str(getattr(thread_item, "text", "")))
        return f"{action} plan: {text}" if text else f"{action} plan"
    if item_type == "reasoning":
        return f"{action} reasoning step"
    if item_type == "agentMessage":
        phase = _enum_value(getattr(thread_item, "phase", None))
        if phase == "final_answer":
            return f"{action} final response"
        return f"{action} assistant message"
    if item_type == "commandExecution":
        command = _truncate(str(getattr(thread_item, "command", "")), limit=120)
        status = _enum_value(getattr(thread_item, "status", None))
        exit_code = getattr(thread_item, "exit_code", None)
        suffix = f" ({status})" if status else ""
        if exit_code is not None:
            suffix = f"{suffix}, exit {exit_code}"
        return f"{action} command: {command}{suffix}"
    if item_type == "fileChange":
        status = _enum_value(getattr(thread_item, "status", None))
        return f"{action} file changes ({status or 'unknown status'})"
    if item_type == "mcpToolCall":
        server = getattr(thread_item, "server", "")
        tool = getattr(thread_item, "tool", "")
        status = _enum_value(getattr(thread_item, "status", None))
        name = ".".join(part for part in (server, tool) if part)
        return f"{action} tool call {name or 'MCP tool'} ({status or 'unknown status'})"
    if item_type == "dynamicToolCall":
        name = getattr(thread_item, "name", None) or "dynamic tool"
        status = _enum_value(getattr(thread_item, "status", None))
        return f"{action} {name} ({status or 'unknown status'})"
    if item_type == "webSearch":
        return f"{action} web search"
    if item_type == "collabAgentToolCall":
        tool = _enum_value(getattr(thread_item, "tool", None)) or "agent task"
        status = _enum_value(getattr(thread_item, "status", None))
        return f"{action} {tool} ({status or 'unknown status'})"

    return f"{action} {item_type or type(thread_item).__name__}"


def _describe_token_usage(payload: Any) -> str | None:
    usage = getattr(payload, "token_usage", None)
    total = getattr(usage, "total", None)
    total_tokens = getattr(total, "total_tokens", None)
    if total_tokens is None:
        return None
    return f"token usage total {total_tokens}"


def _payload_matches_turn(payload: Any, turn_id: str) -> bool:
    direct_turn_id = getattr(payload, "turn_id", None)
    if direct_turn_id is not None:
        return direct_turn_id == turn_id
    turn = getattr(payload, "turn", None)
    nested_turn_id = getattr(turn, "id", None)
    if nested_turn_id is not None:
        return nested_turn_id == turn_id
    return False


def _raise_for_failed_turn(turn: Any) -> None:
    status = _enum_value(getattr(turn, "status", None))
    if status != "failed":
        return
    error = getattr(turn, "error", None)
    message = getattr(error, "message", None)
    if message:
        raise RuntimeError(str(message))
    raise RuntimeError("Codex turn failed")


def _final_response_from_items(items: list[Any]) -> str | None:
    last_unknown_phase_response: str | None = None
    for item in reversed(items):
        thread_item = _unwrap_root(item)
        if getattr(thread_item, "type", None) != "agentMessage":
            continue
        text = getattr(thread_item, "text", None)
        if not isinstance(text, str):
            continue
        phase = _enum_value(getattr(thread_item, "phase", None))
        if phase == "final_answer":
            return text
        if phase is None and last_unknown_phase_response is None:
            last_unknown_phase_response = text
    return last_unknown_phase_response


def _unwrap_root(value: Any) -> Any:
    return getattr(value, "root", value)


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) else str(raw)


def _truncate(value: str, *, limit: int = 80) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."
