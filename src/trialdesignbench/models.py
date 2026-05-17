"""Artifact models produced by the TrialDesignBench step 1 pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConversionArtifact(BaseModel):
    """Files and Mathpix metadata created from a source PDF."""

    model_config = ConfigDict(frozen=True)

    pdf_path: Path
    pdf_id: str
    text_path: Path
    metadata_path: Path
    tex_zip_path: Path | None = None
    status: dict[str, Any] = Field(default_factory=dict)

    def read_text(self) -> str:
        """Read the converted Mathpix Markdown text from disk."""
        return self.text_path.read_text(encoding="utf-8")


class CodexRunArtifact(BaseModel):
    """Files created around a local Codex run."""

    model_config = ConfigDict(frozen=True)

    prompt_path: Path
    response_path: Path
    metadata_path: Path
    run_directory: Path
    model: str
    final_response: str | None = None


class StepOneResult(BaseModel):
    """Combined result for workflow step 1."""

    model_config = ConfigDict(frozen=True)

    conversion: ConversionArtifact
    codex_run: CodexRunArtifact | None = None
