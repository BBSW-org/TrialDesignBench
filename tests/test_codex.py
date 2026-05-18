from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

import pytest

from trialdesignbench import codex
from trialdesignbench.codex import LocalCodexRunner


def test_local_codex_runner_checks_runtime_before_writing_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_load() -> NoReturn:
        raise RuntimeError("SDK unavailable")

    run_directory = tmp_path / "run"
    monkeypatch.setattr(codex, "_load_openai_codex", fail_load)

    with pytest.raises(RuntimeError, match="SDK unavailable"):
        LocalCodexRunner().run(
            prompt="prompt text",
            run_directory=run_directory,
            model="gpt-test",
        )

    assert not (run_directory / "prompt.md").exists()


def test_local_codex_runner_reports_streamed_turn_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[str] = []

    class FakeTurn:
        id = "turn-123"

        def stream(self) -> Any:
            events = [
                SimpleNamespace(
                    method="turn/started",
                    payload=SimpleNamespace(
                        turn=SimpleNamespace(id=self.id, status="running")
                    ),
                ),
                SimpleNamespace(
                    method="item/started",
                    payload=SimpleNamespace(
                        turn_id=self.id,
                        item=SimpleNamespace(
                            type="commandExecution",
                            command="Rscript reproduce_design.R",
                            status="running",
                            exit_code=None,
                        ),
                    ),
                ),
                SimpleNamespace(
                    method="item/completed",
                    payload=SimpleNamespace(
                        turn_id=self.id,
                        item=SimpleNamespace(
                            type="agentMessage",
                            phase="final_answer",
                            text="created reproduction files",
                        ),
                    ),
                ),
                SimpleNamespace(
                    method="turn/completed",
                    payload=SimpleNamespace(
                        turn=SimpleNamespace(
                            id=self.id,
                            status="completed",
                            duration_ms=10,
                            error=None,
                        )
                    ),
                ),
            ]
            return iter(events)

    class FakeThread:
        id = "thread-123"

        def turn(self, prompt: str, **kwargs: Any) -> FakeTurn:
            del prompt, kwargs
            return FakeTurn()

        def run(self, prompt: str, **kwargs: Any) -> NoReturn:
            del prompt, kwargs
            raise AssertionError("streaming path should be used")

    class FakeCodex:
        def __init__(self, config: object | None = None) -> None:
            del config

        def __enter__(self) -> "FakeCodex":
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def thread_start(self, **kwargs: Any) -> FakeThread:
            del kwargs
            return FakeThread()

    fake_sdk = SimpleNamespace(Codex=FakeCodex)
    monkeypatch.setattr(codex, "_load_openai_codex", lambda: fake_sdk)

    artifact = LocalCodexRunner(status_reporter=messages.append).run(
        prompt="prompt text",
        run_directory=tmp_path / "run",
        model="gpt-test",
    )

    assert artifact.final_response == "created reproduction files"
    assert any("Codex: started turn turn-123" in message for message in messages)
    assert any(
        "started command: Rscript reproduce_design.R" in message for message in messages
    )
    assert any("completed final response" in message for message in messages)
