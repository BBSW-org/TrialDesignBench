from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from trialdesignbench.cli._console import console, fail
from trialdesignbench.dataset import (
    DEFAULT_DATASET_VERSION,
    DatasetError,
    attach_document,
    check_dataset,
    import_intake,
)

app = typer.Typer(
    help="Import and validate the canonical dataset.", no_args_is_help=True
)


@app.command("import")
def import_(
    intake: Annotated[list[Path], typer.Argument(help="Curated intake JSON files.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Dataset directory.")],
    documents: Annotated[
        Path | None,
        typer.Option(
            "--documents",
            help="Directory with `<task_id>.md` / `<trial_id>.md` source documents.",
        ),
    ] = None,
    username: Annotated[
        str | None,
        typer.Option(
            "--username", help="Use this reviewer's submission, not the latest."
        ),
    ] = None,
    dataset_version: Annotated[
        str, typer.Option("--dataset-version", help="Dataset version string.")
    ] = DEFAULT_DATASET_VERSION,
    publicly_indexed: Annotated[
        bool | None,
        typer.Option(
            "--publicly-indexed/--not-publicly-indexed",
            help="Whether the source documents are publicly indexed (default: unknown).",
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Replace an existing dataset.")
    ] = False,
) -> None:
    """Import curated intake JSON into a canonical dataset directory."""
    try:
        manifest = import_intake(
            intake,
            out,
            documents_dir=documents,
            username=username,
            dataset_version=dataset_version,
            publicly_indexed=publicly_indexed,
            force=force,
        )
    except DatasetError as exc:
        fail(str(exc))
    table = Table(title=f"Dataset {escape(str(out))} ({manifest.dataset_version})")
    table.add_column("Task")
    table.add_column("Trial")
    table.add_column("Document")
    for e in manifest.tasks:
        table.add_row(
            e.task_id, e.trial_id, "yes" if e.has_document else "[red]missing[/red]"
        )
    console.print(table)
    console.print(f"digest {manifest.digest}")
    if not all(e.has_document for e in manifest.tasks):
        console.print(
            "[yellow]Some tasks have no source document; `tdb dataset check` will fail "
            "until one is attached.[/yellow]"
        )


@app.command("check")
def check(
    dataset_dir: Annotated[Path, typer.Argument(help="Dataset directory.")],
) -> None:
    """Validate a dataset. Exits non-zero on any problem."""
    problems = check_dataset(dataset_dir)
    if problems:
        for p in problems:
            console.print(f"[red]x[/red] {escape(p)}")
        fail(f"{len(problems)} problem(s) in {dataset_dir}")
    console.print(f"[green]ok[/green] {escape(str(dataset_dir))}")


@app.command("attach-document")
def attach(
    dataset_dir: Annotated[Path, typer.Argument(help="Dataset directory.")],
    task_id: Annotated[str, typer.Argument(help="Task id.")],
    document: Annotated[Path, typer.Argument(help="Protocol/SAP Markdown file.")],
    source: Annotated[
        str | None, typer.Option("--source", help="Provenance note for the document.")
    ] = None,
) -> None:
    """Add or replace a task's source document."""
    try:
        manifest = attach_document(dataset_dir, task_id, document, source=source)
    except (DatasetError, OSError) as exc:
        fail(str(exc))
    console.print(f"[green]attached[/green] {escape(str(document))} to {task_id}")
    console.print(f"digest {manifest.digest}")
