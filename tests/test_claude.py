from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trialdesignbench import claude
from trialdesignbench.claude import ClaudeRunner


def test_claude_runner_creates_artifacts_and_returns_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify ClaudeRunner calls the Anthropic SDK and writes all artifacts."""

    class MockMessages:
        def create(self, model: str, max_tokens: int, messages: list[Any]) -> Any:
            assert model == "claude-sonnet-4-20250514"
            assert max_tokens == 16384
            assert messages[0]["role"] == "user"
            assert "prompt text" in messages[0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text="claude response text")],
                usage=SimpleNamespace(input_tokens=100, output_tokens=200),
            )

    class MockAnthropic:
        def __init__(self, api_key: str) -> None:
            assert api_key == "test-key"
            self.messages = MockMessages()

    mock_anthropic = SimpleNamespace(Anthropic=MockAnthropic)
    monkeypatch.setattr(claude, "anthropic", mock_anthropic, raising=False)

    import sys

    sys.modules["anthropic"] = mock_anthropic  # type: ignore[assignment]

    run_directory = tmp_path / "run"
    messages: list[str] = []

    runner = ClaudeRunner(api_key="test-key", status_reporter=messages.append)

    artifact = runner.run(
        prompt="prompt text",
        run_directory=run_directory,
        model="claude-sonnet-4-20250514",
    )

    assert artifact.final_response == "claude response text"
    assert artifact.model == "claude-sonnet-4-20250514"

    assert artifact.prompt_path.exists()
    assert artifact.prompt_path.read_text() == "prompt text"

    assert artifact.response_path.exists()
    assert artifact.response_path.read_text() == "claude response text"

    assert artifact.metadata_path.exists()
    metadata = json.loads(artifact.metadata_path.read_text())
    assert metadata["model"] == "claude-sonnet-4-20250514"
    assert metadata["provider"] == "anthropic"
    assert metadata["final_response_present"] is True
    assert metadata["usage"]["input_tokens"] == 100
    assert metadata["usage"]["output_tokens"] == 200

    assert any(
        "Claude: calling claude-sonnet-4-20250514" in message for message in messages
    )
