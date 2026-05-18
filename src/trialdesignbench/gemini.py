"""Google GenAI Gemini SDK integration."""

from __future__ import annotations

import json
from pathlib import Path

from trialdesignbench.config import DEFAULT_CODEX_EFFORT
from trialdesignbench.models import CodexRunArtifact
from trialdesignbench.status import StatusReporter


class GeminiRunner:
    """Run the prompt against Google's Gemini models via the GenAI SDK."""

    def __init__(
        self, api_key: str, status_reporter: StatusReporter | None = None
    ) -> None:
        try:
            from google import genai
        except ImportError as exc:
            msg = (
                "The Google GenAI SDK is not installed. "
                "Run `uv add google-genai` to install it."
            )
            raise RuntimeError(msg) from exc

        self.client = genai.Client(api_key=api_key)
        self.status_reporter = status_reporter

    def run(
        self,
        *,
        prompt: str,
        run_directory: Path,
        model: str,
        codex_bin: str | None = None,
        effort: str = DEFAULT_CODEX_EFFORT,
    ) -> CodexRunArtifact:
        """Run Gemini and return persisted artifacts."""
        run_dir = run_directory.expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = run_dir / "prompt.md"
        response_path = run_dir / "codex_response.md"
        metadata_path = run_dir / "codex_run.json"

        self._report(f"Gemini: writing prompt to {prompt_path}")
        prompt_path.write_text(prompt, encoding="utf-8")

        self._report(f"Gemini: calling {model}")

        response = self.client.models.generate_content(
            model=model,
            contents=prompt,
        )

        final_response = response.text

        self._report(f"Gemini: writing final response to {response_path}")
        response_path.write_text(final_response or "", encoding="utf-8")

        metadata = {
            "model": model,
            "provider": "gemini",
            "final_response_present": final_response is not None,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self._report(f"Gemini: wrote run metadata to {metadata_path}")

        return CodexRunArtifact(
            prompt_path=prompt_path,
            response_path=response_path,
            metadata_path=metadata_path,
            run_directory=run_dir,
            model=model,
            final_response=final_response,
        )

    def _report(self, message: str) -> None:
        if self.status_reporter is not None:
            self.status_reporter(message)
