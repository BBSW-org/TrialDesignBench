"""Workflow step 1 orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from trialdesignbench.codex import CodexRunner, LocalCodexRunner
from trialdesignbench.config import DEFAULT_CODEX_EFFORT, TdbConfig
from trialdesignbench.mathpix import DEFAULT_HTTP_TIMEOUT_SECONDS, MathpixClient
from trialdesignbench.models import CodexRunArtifact, ConversionArtifact, StepOneResult
from trialdesignbench.prompt import build_reproduction_prompt
from trialdesignbench.status import StatusReporter


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
    status_reporter: StatusReporter | None = None

    def convert(
        self,
        pdf_path: Path,
        *,
        save_tex_zip: bool = False,
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = 600.0,
        http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        force: bool = False,
    ) -> ConversionArtifact:
        """Convert a SAP/protocol PDF into Mathpix Markdown."""
        source_pdf = pdf_path.expanduser().resolve()
        self._report(f"Checking source PDF: {source_pdf}")
        if not source_pdf.exists():
            raise FileNotFoundError(source_pdf)
        if source_pdf.suffix.lower() != ".pdf":
            msg = f"Expected a PDF file, got: {source_pdf}"
            raise ValueError(msg)

        output_dir = (self.config.workspace / "converted").expanduser().resolve()
        if not force:
            existing = _load_existing_conversion(
                source_pdf,
                output_dir,
                require_tex_zip=save_tex_zip,
            )
            if existing is not None:
                self._report(
                    f"Reusing existing Mathpix conversion: {existing.text_path}"
                )
                return existing

        self._report("Starting Mathpix PDF conversion")
        converter = self.converter or MathpixClient(
            app_id=self.config.mathpix_app_id,
            app_key=self.config.mathpix_app_key,
            http_timeout_seconds=http_timeout_seconds,
            status_reporter=self.status_reporter,
        )
        artifact = converter.convert_pdf(
            source_pdf,
            output_dir,
            save_tex_zip=save_tex_zip,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )
        self._report("Mathpix conversion completed")
        return artifact

    def run(
        self,
        pdf_path: Path,
        *,
        case_id: str | None = None,
        run_codex: bool = True,
        save_tex_zip: bool = False,
        model: str | None = None,
        codex_bin: str | None = None,
        effort: str = DEFAULT_CODEX_EFFORT,
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = 600.0,
        http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        force: bool = False,
    ) -> StepOneResult:
        """Run workflow step 1 for one SAP/protocol PDF."""
        conversion = self.convert(
            pdf_path,
            save_tex_zip=save_tex_zip,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
            http_timeout_seconds=http_timeout_seconds,
            force=force,
        )
        codex_run: CodexRunArtifact | None = None
        result_path = self._result_path(conversion, case_id=case_id)

        if run_codex:
            self._report("Building the trial design reproduction prompt")
            prompt = build_reproduction_prompt(
                document_text=conversion.read_text(),
                source_name=conversion.pdf_path.name,
                case_id=case_id,
            )
            run_dir = self._run_directory(conversion, case_id=case_id)
            active_model = model or self.config.codex_model
            self._report(
                f"Starting reproduction with model {active_model} and effort {effort}"
            )

            if active_model.startswith("claude"):
                from trialdesignbench.claude import ClaudeRunner

                if not self.config.anthropic_api_key:
                    raise ValueError(
                        "Anthropic API key is required to use a Claude model. "
                        "Run `tdb configure` or edit .env"
                    )
                runner = self.codex_runner or ClaudeRunner(
                    api_key=self.config.anthropic_api_key,
                    status_reporter=self.status_reporter,
                )
            else:
                runner = self.codex_runner or LocalCodexRunner(
                    status_reporter=self.status_reporter
                )

            try:
                codex_run = runner.run(
                    prompt=prompt,
                    run_directory=run_dir,
                    model=active_model,
                    codex_bin=codex_bin or self.config.codex_bin,
                    effort=effort,
                )
            except Exception:
                result = StepOneResult(conversion=conversion, codex_run=None)
                self._report(
                    f"Codex failed; writing partial workflow summary to {result_path}"
                )
                result_path.write_text(
                    result.model_dump_json(indent=2), encoding="utf-8"
                )
                raise
            self._report(f"Codex reproduction completed: {codex_run.response_path}")
        else:
            self._report("Skipping Codex reproduction because --no-codex was set")

        result = StepOneResult(conversion=conversion, codex_run=codex_run)
        self._report(f"Writing workflow summary to {result_path}")
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

    def _report(self, message: str) -> None:
        if self.status_reporter is not None:
            self.status_reporter(message)


def _load_existing_conversion(
    pdf_path: Path, output_dir: Path, *, require_tex_zip: bool
) -> ConversionArtifact | None:
    stem = pdf_path.stem
    text_path = output_dir / f"{stem}.mmd"
    metadata_path = output_dir / f"{stem}.mathpix.json"
    if not _non_empty_file(text_path) or not _non_empty_file(metadata_path):
        return None

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(metadata, dict):
        return None

    metadata_pdf_path = metadata.get("pdf_path")
    if metadata_pdf_path is not None:
        try:
            if Path(str(metadata_pdf_path)).expanduser().resolve() != pdf_path:
                return None
        except OSError:
            return None

    pdf_id = metadata.get("pdf_id")
    if not isinstance(pdf_id, str) or not pdf_id:
        return None

    status = metadata.get("status")
    if not isinstance(status, dict):
        status = {}

    tex_zip_path = _existing_tex_zip_path(metadata, output_dir, stem)
    if require_tex_zip and tex_zip_path is None:
        return None

    return ConversionArtifact(
        pdf_path=pdf_path,
        pdf_id=pdf_id,
        text_path=text_path,
        tex_zip_path=tex_zip_path,
        metadata_path=metadata_path,
        status=status,
    )


def _existing_tex_zip_path(
    metadata: dict[object, object], output_dir: Path, stem: str
) -> Path | None:
    raw_path = metadata.get("tex_zip_path")
    candidates: list[Path] = []
    if isinstance(raw_path, str) and raw_path:
        candidates.append(Path(raw_path))
    candidates.append(output_dir / f"{stem}.tex.zip")
    for candidate in candidates:
        path = candidate.expanduser()
        if _non_empty_file(path):
            return path
    return None


def _non_empty_file(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0
