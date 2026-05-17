"""Workflow step 1 orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from trialdesignbench.codex import CodexRunner, LocalCodexRunner
from trialdesignbench.config import TdbConfig
from trialdesignbench.mathpix import MathpixClient
from trialdesignbench.models import CodexRunArtifact, ConversionArtifact, StepOneResult
from trialdesignbench.prompt import build_reproduction_prompt


class PdfConverter(Protocol):
    """Interface for converting PDF source documents."""

    def convert_pdf(
        self,
        pdf_path: Path,
        output_dir: Path,
        *,
        save_tex_zip: bool = False,
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = 600.0,
    ) -> ConversionArtifact:
        """Convert `pdf_path` and persist artifacts under `output_dir`."""


@dataclass(frozen=True, slots=True)
class StepOnePipeline:
    """Ingest a SAP/protocol PDF and optionally execute Codex reproduction."""

    config: TdbConfig
    converter: PdfConverter | None = None
    codex_runner: CodexRunner | None = None

    def convert(
        self,
        pdf_path: Path,
        *,
        save_tex_zip: bool = False,
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = 600.0,
    ) -> ConversionArtifact:
        """Convert a SAP/protocol PDF into Mathpix Markdown."""
        converter = self.converter or MathpixClient(
            app_id=self.config.mathpix_app_id,
            app_key=self.config.mathpix_app_key,
        )
        return converter.convert_pdf(
            pdf_path,
            self.config.workspace / "converted",
            save_tex_zip=save_tex_zip,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )

    def run(
        self,
        pdf_path: Path,
        *,
        case_id: str | None = None,
        run_codex: bool = True,
        save_tex_zip: bool = False,
        model: str | None = None,
        codex_bin: str | None = None,
        effort: str = "high",
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = 600.0,
    ) -> StepOneResult:
        """Run workflow step 1 for one SAP/protocol PDF."""
        conversion = self.convert(
            pdf_path,
            save_tex_zip=save_tex_zip,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )
        codex_run: CodexRunArtifact | None = None

        if run_codex:
            prompt = build_reproduction_prompt(
                document_text=conversion.read_text(),
                source_name=conversion.pdf_path.name,
                case_id=case_id,
            )
            run_dir = self._run_directory(conversion, case_id=case_id)
            runner = self.codex_runner or LocalCodexRunner()
            codex_run = runner.run(
                prompt=prompt,
                run_directory=run_dir,
                model=model or self.config.codex_model,
                codex_bin=codex_bin or self.config.codex_bin,
                effort=effort,
            )

        result = StepOneResult(conversion=conversion, codex_run=codex_run)
        result_path = self._result_path(conversion, case_id=case_id)
        result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return result

    def _run_directory(
        self, conversion: ConversionArtifact, *, case_id: str | None
    ) -> Path:
        return self.config.workspace / "runs" / (case_id or conversion.pdf_path.stem)

    def _result_path(
        self, conversion: ConversionArtifact, *, case_id: str | None
    ) -> Path:
        run_root = self.config.workspace / "runs"
        run_root.mkdir(parents=True, exist_ok=True)
        return run_root / f"{case_id or conversion.pdf_path.stem}.step1.json"


def write_result_summary(result: StepOneResult, path: Path) -> None:
    """Write a compact JSON summary for scripts that need a stable artifact."""
    payload = result.model_dump(mode="json")
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
