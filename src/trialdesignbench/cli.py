"""Command-line interface for TrialDesignBench workflow step 1."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from trialdesignbench.config import DEFAULT_CODEX_MODEL, DEFAULT_WORKSPACE_NAME
from trialdesignbench.config import configure_workspace as write_workspace_config
from trialdesignbench.config import create_workspace, load_config
from trialdesignbench.pipeline import StepOnePipeline

app = typer.Typer(
    help="TrialDesignBench workflow step 1: PDF ingestion and local Codex reproduction.",
    no_args_is_help=True,
)


@app.command()
def init(
    workspace: Annotated[
        Path,
        typer.Argument(help="Workspace directory to create."),
    ] = Path(DEFAULT_WORKSPACE_NAME),
) -> None:
    """Create a local workspace with gitignored secrets and outputs."""
    created = create_workspace(workspace)
    typer.echo(f"Created workspace: {created}")
    typer.echo(f"Edit credentials in: {created / '.env'}")


@app.command()
def configure(
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Workspace directory."),
    ] = Path(DEFAULT_WORKSPACE_NAME),
    mathpix_app_id: Annotated[
        str | None,
        typer.Option("--mathpix-app-id", help="Mathpix app_id."),
    ] = None,
    mathpix_app_key: Annotated[
        str | None,
        typer.Option("--mathpix-app-key", help="Mathpix app_key.", hide_input=True),
    ] = None,
    codex_model: Annotated[
        str,
        typer.Option("--codex-model", help="Local Codex model name."),
    ] = DEFAULT_CODEX_MODEL,
    codex_bin: Annotated[
        str | None,
        typer.Option("--codex-bin", help="Optional path to a local Codex binary."),
    ] = None,
) -> None:
    """Write Mathpix and Codex configuration to the workspace `.env` file."""
    app_id = mathpix_app_id or typer.prompt("Mathpix app_id")
    app_key = mathpix_app_key or typer.prompt("Mathpix app_key", hide_input=True)
    env_file = write_workspace_config(
        workspace,
        mathpix_app_id=app_id,
        mathpix_app_key=app_key,
        codex_model=codex_model,
        codex_bin=codex_bin,
    )
    typer.echo(f"Wrote configuration: {env_file}")


@app.command()
def convert(
    pdf: Annotated[
        Path,
        typer.Argument(help="Input SAP or protocol PDF."),
    ],
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Workspace directory."),
    ] = Path(DEFAULT_WORKSPACE_NAME),
    save_tex_zip: Annotated[
        bool,
        typer.Option("--save-tex-zip", help="Also request and save Mathpix LaTeX ZIP."),
    ] = False,
    poll_interval: Annotated[
        float,
        typer.Option("--poll-interval", help="Seconds between Mathpix status checks."),
    ] = 5.0,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Maximum seconds to wait for Mathpix."),
    ] = 600.0,
) -> None:
    """Convert one SAP/protocol PDF to Mathpix Markdown."""
    pipeline = StepOnePipeline(load_config(workspace))
    artifact = pipeline.convert(
        pdf,
        save_tex_zip=save_tex_zip,
        poll_interval_seconds=poll_interval,
        timeout_seconds=timeout,
    )
    typer.echo(f"Converted text: {artifact.text_path}")
    typer.echo(f"Mathpix metadata: {artifact.metadata_path}")
    if artifact.tex_zip_path:
        typer.echo(f"LaTeX ZIP: {artifact.tex_zip_path}")


@app.command()
def run(
    pdf: Annotated[
        Path,
        typer.Argument(help="Input SAP or protocol PDF."),
    ],
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Workspace directory."),
    ] = Path(DEFAULT_WORKSPACE_NAME),
    case_id: Annotated[
        str | None,
        typer.Option("--case-id", help="Stable case identifier for output paths."),
    ] = None,
    no_codex: Annotated[
        bool,
        typer.Option("--no-codex", help="Only convert the PDF; do not run Codex."),
    ] = False,
    save_tex_zip: Annotated[
        bool,
        typer.Option("--save-tex-zip", help="Also request and save Mathpix LaTeX ZIP."),
    ] = False,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override CODEX_MODEL from the workspace `.env`."),
    ] = None,
    codex_bin: Annotated[
        str | None,
        typer.Option(
            "--codex-bin", help="Override CODEX_BIN from the workspace `.env`."
        ),
    ] = None,
    effort: Annotated[
        str,
        typer.Option("--effort", help="Codex reasoning effort."),
    ] = "high",
    poll_interval: Annotated[
        float,
        typer.Option("--poll-interval", help="Seconds between Mathpix status checks."),
    ] = 5.0,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Maximum seconds to wait for Mathpix."),
    ] = 600.0,
) -> None:
    """Convert a PDF and run the standard reproduction prompt with local Codex."""
    pipeline = StepOnePipeline(load_config(workspace))
    result = pipeline.run(
        pdf,
        case_id=case_id,
        run_codex=not no_codex,
        save_tex_zip=save_tex_zip,
        model=model,
        codex_bin=codex_bin,
        effort=effort,
        poll_interval_seconds=poll_interval,
        timeout_seconds=timeout,
    )
    typer.echo(f"Converted text: {result.conversion.text_path}")
    if result.codex_run:
        typer.echo(f"Codex run directory: {result.codex_run.run_directory}")
        typer.echo(f"Codex response: {result.codex_run.response_path}")


def main() -> None:
    """CLI entry point."""
    app()
