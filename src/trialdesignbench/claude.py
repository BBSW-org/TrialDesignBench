"""Anthropic Claude SDK integration."""

from __future__ import annotations

import json
from pathlib import Path

from trialdesignbench.config import DEFAULT_CODEX_EFFORT
from trialdesignbench.models import CodexRunArtifact
from trialdesignbench.status import StatusReporter


class ClaudeRunner:
    """Run the prompt against Anthropic Claude models."""

    def __init__(
        self, api_key: str, status_reporter: StatusReporter | None = None
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:
            msg = (
                "The Anthropic SDK is not installed. "
                "Run `uv add anthropic` to install it."
            )
            raise RuntimeError(msg) from exc

        self.client = anthropic.Anthropic(api_key=api_key)
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
        """Run Claude and return persisted artifacts."""
        run_dir = run_directory.expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = run_dir / "prompt.md"
        response_path = run_dir / "codex_response.md"
        metadata_path = run_dir / "codex_run.json"

        self._report(f"Claude: writing prompt to {prompt_path}")
        prompt_path.write_text(prompt, encoding="utf-8")

        self._report(f"Claude: calling {model}")

        message = self.client.messages.create(
            model=model,
            max_tokens=16384,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract text from the response content blocks.
        final_response = "\n".join(
            block.text for block in message.content if hasattr(block, "text")
        )

        self._report(f"Claude: writing final response to {response_path}")
        response_path.write_text(final_response or "", encoding="utf-8")

        metadata = {
            "model": model,
            "provider": "anthropic",
            "final_response_present": bool(final_response),
            "usage": {
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            },
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        self._report(f"Claude: wrote run metadata to {metadata_path}")

        return CodexRunArtifact(
            prompt_path=prompt_path,
            response_path=response_path,
            metadata_path=metadata_path,
            run_directory=run_dir,
            model=model,
            final_response=final_response or None,
        )

    def _report(self, message: str) -> None:
        if self.status_reporter is not None:
            self.status_reporter(message)
