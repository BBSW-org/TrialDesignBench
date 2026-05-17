from __future__ import annotations

import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

from trialdesignbench.config import DEFAULT_CODEX_EFFORT, TdbConfig
from trialdesignbench.models import CodexRunArtifact, ConversionArtifact
from trialdesignbench.pipeline import StepOnePipeline


class FakeConverter:
    def __init__(self) -> None:
        self.calls = 0

    def convert_pdf(
        self,
        pdf_path: Path,
        output_dir: Path,
        *,
        save_tex_zip: bool = False,
        poll_interval_seconds: float = 5.0,
        timeout_seconds: float = 600.0,
    ) -> ConversionArtifact:
        del save_tex_zip, poll_interval_seconds, timeout_seconds
        self.calls += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        text_path = output_dir / "sap.mmd"
        text_path.write_text(
            "The target sample size is 120 participants.", encoding="utf-8"
        )
        metadata_path = output_dir / "sap.mathpix.json"
        metadata_path.write_text("{}", encoding="utf-8")
        return ConversionArtifact(
            pdf_path=pdf_path.resolve(),
            pdf_id="pdf-123",
            text_path=text_path,
            metadata_path=metadata_path,
            status={"status": "completed"},
        )


class FakeCodexRunner:
    def __init__(self) -> None:
        self.prompt = ""
        self.effort = ""

    def run(
        self,
        *,
        prompt: str,
        run_directory: Path,
        model: str,
        codex_bin: str | None = None,
        effort: str = DEFAULT_CODEX_EFFORT,
    ) -> CodexRunArtifact:
        del codex_bin
        self.prompt = prompt
        self.effort = effort
        run_directory.mkdir(parents=True, exist_ok=True)
        prompt_path = run_directory / "prompt.md"
        response_path = run_directory / "codex_response.md"
        metadata_path = run_directory / "codex_run.json"
        prompt_path.write_text(prompt, encoding="utf-8")
        response_path.write_text("created reproduction files", encoding="utf-8")
        metadata_path.write_text("{}", encoding="utf-8")
        return CodexRunArtifact(
            prompt_path=prompt_path,
            response_path=response_path,
            metadata_path=metadata_path,
            run_directory=run_directory,
            model=model,
            final_response="created reproduction files",
        )


class FailingCodexRunner:
    def run(
        self,
        *,
        prompt: str,
        run_directory: Path,
        model: str,
        codex_bin: str | None = None,
        effort: str = DEFAULT_CODEX_EFFORT,
    ) -> CodexRunArtifact:
        del prompt, run_directory, model, codex_bin, effort
        raise RuntimeError("Codex failed")


def test_step_one_pipeline_writes_conversion_and_codex_artifacts(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "sap.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    config = TdbConfig(
        workspace=tmp_path / "workspace",
        env_file=tmp_path / "workspace" / ".env",
        mathpix_app_id="app-id",
        mathpix_app_key="app-key",
        codex_model="gpt-test",
    )
    codex_runner = FakeCodexRunner()
    pipeline = StepOnePipeline(
        config=config,
        converter=FakeConverter(),
        codex_runner=codex_runner,
    )

    result = pipeline.run(pdf_path, case_id="case-001")

    assert result.conversion.text_path.exists()
    assert result.codex_run is not None
    assert result.codex_run.model == "gpt-test"
    assert codex_runner.effort == DEFAULT_CODEX_EFFORT
    assert "The target sample size is 120 participants." in codex_runner.prompt
    assert "reproduce_design.R" in codex_runner.prompt
    assert (config.workspace / "runs" / "case-001.step1.json").exists()


def test_pipeline_reuses_existing_conversion_artifacts(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sap.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    workspace = tmp_path / "workspace"
    converted = workspace / "converted"
    converted.mkdir(parents=True)
    text_path = converted / "sap.mmd"
    text_path.write_text("Existing Mathpix Markdown", encoding="utf-8")
    metadata_path = converted / "sap.mathpix.json"
    metadata_path.write_text(
        json.dumps(
            {
                "pdf_path": str(pdf_path.resolve()),
                "pdf_id": "existing-pdf-id",
                "status": {"status": "completed"},
                "text_path": str(text_path),
                "tex_zip_path": None,
            }
        ),
        encoding="utf-8",
    )
    converter = FakeConverter()
    pipeline = StepOnePipeline(
        config=TdbConfig(
            workspace=workspace,
            env_file=workspace / ".env",
            mathpix_app_id="app-id",
            mathpix_app_key="app-key",
        ),
        converter=converter,
    )

    artifact = pipeline.convert(pdf_path)

    assert artifact.pdf_id == "existing-pdf-id"
    assert artifact.text_path == text_path
    assert converter.calls == 0


def test_pipeline_force_bypasses_existing_conversion_artifacts(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sap.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    workspace = tmp_path / "workspace"
    converted = workspace / "converted"
    converted.mkdir(parents=True)
    (converted / "sap.mmd").write_text("Existing Mathpix Markdown", encoding="utf-8")
    (converted / "sap.mathpix.json").write_text(
        json.dumps(
            {
                "pdf_path": str(pdf_path.resolve()),
                "pdf_id": "existing-pdf-id",
                "status": {"status": "completed"},
            }
        ),
        encoding="utf-8",
    )
    converter = FakeConverter()
    pipeline = StepOnePipeline(
        config=TdbConfig(
            workspace=workspace,
            env_file=workspace / ".env",
            mathpix_app_id="app-id",
            mathpix_app_key="app-key",
        ),
        converter=converter,
    )

    artifact = pipeline.convert(pdf_path, force=True)

    assert artifact.pdf_id == "pdf-123"
    assert converter.calls == 1


def test_pipeline_writes_result_summary_when_codex_fails(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sap.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    workspace = tmp_path / "workspace"
    pipeline = StepOnePipeline(
        config=TdbConfig(
            workspace=workspace,
            env_file=workspace / ".env",
            mathpix_app_id="app-id",
            mathpix_app_key="app-key",
        ),
        converter=FakeConverter(),
        codex_runner=FailingCodexRunner(),
    )

    with pytest.raises(RuntimeError, match="Codex failed"):
        pipeline.run(pdf_path, case_id="case-001")

    result_path = workspace / "runs" / "case-001.step1.json"
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["conversion"]["pdf_id"] == "pdf-123"
    assert payload["codex_run"] is None
