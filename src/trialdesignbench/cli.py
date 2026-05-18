"""Command-line interface for TrialDesignBench workflow step 1."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from trialdesignbench.config import (
    DEFAULT_CODEX_EFFORT,
    DEFAULT_CODEX_MODEL,
    DEFAULT_WORKSPACE_NAME,
)
from trialdesignbench.config import configure_workspace as write_workspace_config
from trialdesignbench.config import create_workspace, load_config
from trialdesignbench.mathpix import DEFAULT_HTTP_TIMEOUT_SECONDS
from trialdesignbench.models import ConversionArtifact, StepOneResult
from trialdesignbench.pipeline import StepOnePipeline
from trialdesignbench.status import StatusReporter

app = typer.Typer(
    help="TrialDesignBench workflow step 1: PDF ingestion and local Codex reproduction.",
    no_args_is_help=True,
)
console = Console(soft_wrap=True)


@app.command()
def init(
    workspace: Annotated[
        Path,
        typer.Argument(help="Workspace directory to create."),
    ] = Path(DEFAULT_WORKSPACE_NAME),
) -> None:
    """Create a local workspace with gitignored secrets and outputs."""
    created = create_workspace(workspace)
    console.print(f"[bold green]Created workspace[/bold green] {escape(str(created))}")
    console.print(f"Edit credentials in: [cyan]{escape(str(created / '.env'))}[/cyan]")


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
    console.print(
        f"[bold green]Wrote configuration[/bold green] {escape(str(env_file))}"
    )


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
    http_timeout: Annotated[
        float,
        typer.Option(
            "--http-timeout",
            help="Per-request Mathpix HTTP timeout in seconds.",
        ),
    ] = DEFAULT_HTTP_TIMEOUT_SECONDS,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Re-run Mathpix conversion even when converted artifacts exist.",
        ),
    ] = False,
) -> None:
    """Convert one SAP/protocol PDF to Mathpix Markdown."""
    reporter = _rich_reporter()
    pipeline = StepOnePipeline(load_config(workspace), status_reporter=reporter)
    console.print(
        Panel.fit(
            f"[bold]Converting PDF with Mathpix[/bold]\n{escape(str(pdf))}",
            border_style="cyan",
        )
    )
    with console.status("[bold cyan]Running Mathpix conversion...[/bold cyan]"):
        artifact = pipeline.convert(
            pdf,
            save_tex_zip=save_tex_zip,
            poll_interval_seconds=poll_interval,
            timeout_seconds=timeout,
            http_timeout_seconds=http_timeout,
            force=force,
        )
    _print_conversion_summary(artifact)


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
    ] = DEFAULT_CODEX_EFFORT,
    poll_interval: Annotated[
        float,
        typer.Option("--poll-interval", help="Seconds between Mathpix status checks."),
    ] = 5.0,
    timeout: Annotated[
        float,
        typer.Option("--timeout", help="Maximum seconds to wait for Mathpix."),
    ] = 600.0,
    http_timeout: Annotated[
        float,
        typer.Option(
            "--http-timeout",
            help="Per-request Mathpix HTTP timeout in seconds.",
        ),
    ] = DEFAULT_HTTP_TIMEOUT_SECONDS,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Re-run Mathpix conversion even when converted artifacts exist.",
        ),
    ] = False,
) -> None:
    """Convert a PDF and run the standard reproduction prompt with local Codex."""
    reporter = _rich_reporter()
    pipeline = StepOnePipeline(load_config(workspace), status_reporter=reporter)
    target = "Mathpix conversion only" if no_codex else "Mathpix + Codex reproduction"
    console.print(
        Panel.fit(
            f"[bold]{escape(target)}[/bold]\n{escape(str(pdf))}",
            border_style="cyan",
        )
    )
    with console.status("[bold cyan]Running workflow step 1...[/bold cyan]"):
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
            http_timeout_seconds=http_timeout,
            force=force,
        )
    _print_run_summary(result)


def main() -> None:
    """CLI entry point."""
    app()


def _rich_reporter() -> StatusReporter:
    def report(message: str) -> None:
        console.print(f"[bold cyan]tdb[/bold cyan] {escape(message)}")

    return report


def _print_conversion_summary(artifact: ConversionArtifact) -> None:
    table = Table(
        title="Conversion Artifacts",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
        title_style="bold green",
    )
    table.add_column("Artifact", style="bold", no_wrap=True)
    table.add_column("Path", style="cyan", overflow="fold")
    table.add_row("Converted text", escape(str(artifact.text_path)))
    table.add_row("Mathpix metadata", escape(str(artifact.metadata_path)))
    if artifact.tex_zip_path:
        table.add_row("LaTeX ZIP", escape(str(artifact.tex_zip_path)))
    console.print(table)


def _print_run_summary(result: StepOneResult) -> None:
    _print_conversion_summary(result.conversion)
    codex_run = result.codex_run
    if codex_run is None:
        return

    table = Table(
        title="Codex Artifacts",
        box=box.SIMPLE_HEAVY,
        show_lines=False,
        title_style="bold green",
    )
    table.add_column("Artifact", style="bold", no_wrap=True)
    table.add_column("Path", style="cyan", overflow="fold")
    table.add_row("Run directory", escape(str(codex_run.run_directory)))
    table.add_row("Prompt", escape(str(codex_run.prompt_path)))
    table.add_row("Response", escape(str(codex_run.response_path)))
    table.add_row("Metadata", escape(str(codex_run.metadata_path)))
    console.print(table)
