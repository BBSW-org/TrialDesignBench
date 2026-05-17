from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter

from trialdesignbench.config import TdbConfig
from trialdesignbench.models import CodexRunArtifact, ConversionArtifact
from trialdesignbench.pipeline import StepOnePipeline


class FakeConverter:
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

    def run(
        self,
        *,
        prompt: str,
        run_directory: Path,
        model: str,
        codex_bin: str | None = None,
        effort: str = "high",
    ) -> CodexRunArtifact:
        del codex_bin, effort
        self.prompt = prompt
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
    assert "The target sample size is 120 participants." in codex_runner.prompt
    assert "reproduce_design.R" in codex_runner.prompt
    assert (config.workspace / "runs" / "case-001.step1.json").exists()
