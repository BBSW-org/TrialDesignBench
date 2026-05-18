from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from trialdesignbench import gemini
from trialdesignbench.gemini import GeminiRunner


def test_gemini_runner_creates_artifacts_and_returns_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Setup mock Google GenAI client
    class MockModels:
        def generate_content(self, model: str, contents: str) -> Any:
            assert model == "gemini-2.5-pro"
            assert "prompt text" in contents
            return SimpleNamespace(text="gemini response text")

    class MockClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "test-key"
            self.models = MockModels()

    # Monkeypatch the genai module loaded inside __init__
    mock_genai = SimpleNamespace(Client=MockClient)
    monkeypatch.setattr(gemini, "genai", mock_genai, raising=False)
    
    # Alternatively, just patch the Client class on the runner instance if it imports it locally, 
    # but the runner imports genai inside __init__. We can mock sys.modules.
    import sys

    sys.modules["google"] = SimpleNamespace(genai=mock_genai)  # type: ignore[assignment]
    sys.modules["google.genai"] = mock_genai  # type: ignore[assignment]

    run_directory = tmp_path / "run"
    messages: list[str] = []

    runner = GeminiRunner(api_key="test-key", status_reporter=messages.append)
    
    artifact = runner.run(
        prompt="prompt text",
        run_directory=run_directory,
        model="gemini-2.5-pro",
    )

    assert artifact.final_response == "gemini response text"
    assert artifact.model == "gemini-2.5-pro"
    
    assert artifact.prompt_path.exists()
    assert artifact.prompt_path.read_text() == "prompt text"
    
    assert artifact.response_path.exists()
    assert artifact.response_path.read_text() == "gemini response text"
    
    assert artifact.metadata_path.exists()
    metadata = json.loads(artifact.metadata_path.read_text())
    assert metadata["model"] == "gemini-2.5-pro"
    assert metadata["provider"] == "gemini"
    assert metadata["final_response_present"] is True

    assert any("Gemini: calling gemini-2.5-pro" in message for message in messages)
