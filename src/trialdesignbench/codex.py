"""Local Codex SDK integration."""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from trialdesignbench.config import DEFAULT_CODEX_EFFORT
from trialdesignbench.models import CodexRunArtifact


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

    def run(
        self,
        *,
        prompt: str,
        run_directory: Path,
        model: str,
        codex_bin: str | None = None,
        effort: str = DEFAULT_CODEX_EFFORT,
    ) -> CodexRunArtifact:
        openai_codex = _load_openai_codex()
        run_dir = run_directory.expanduser().resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = run_dir / "prompt.md"
        response_path = run_dir / "codex_response.md"
        metadata_path = run_dir / "codex_run.json"
        prompt_path.write_text(prompt, encoding="utf-8")

        config = None
        if codex_bin:
            config = openai_codex.AppServerConfig(
                codex_bin=codex_bin,
                cwd=str(run_dir),
            )

        with openai_codex.Codex(config=config) as codex:
            thread = codex.thread_start(
                cwd=str(run_dir),
                model=model,
                config={"model_reasoning_effort": effort},
            )
            result = thread.run(
                prompt,
                cwd=str(run_dir),
                model=model,
                effort=effort,
            )

        final_response = _final_response(result)
        response_path.write_text(final_response or "", encoding="utf-8")
        metadata = {
            "model": model,
            "effort": effort,
            "codex_bin": codex_bin,
            "final_response_present": final_response is not None,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return CodexRunArtifact(
            prompt_path=prompt_path,
            response_path=response_path,
            metadata_path=metadata_path,
            run_directory=run_dir,
            model=model,
            final_response=final_response,
        )


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
