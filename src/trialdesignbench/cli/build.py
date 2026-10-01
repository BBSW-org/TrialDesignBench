from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from trialdesignbench import environment
from trialdesignbench.build import BuildOptions, GraderSource, build_tasks
from trialdesignbench.cli._console import console, fail, warn
from trialdesignbench.dataset import DatasetError
from trialdesignbench.judge import DEFAULT_JUDGE_MODEL, judge_backend


def build(
    dataset_dir: Annotated[Path, typer.Argument(help="Canonical dataset directory.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Harbor tasks directory.")],
    image: Annotated[
        str | None,
        typer.Option("--image", help="Shared environment image (default: local tag)."),
    ] = None,
    task_ids: Annotated[
        list[str] | None, typer.Option("--task-ids", help="Only build these tasks.")
    ] = None,
    dockerfile: Annotated[
        bool,
        typer.Option(
            "--dockerfile", help="Copy the Dockerfile so Harbor builds locally."
        ),
    ] = False,
    grader_source: Annotated[
        str,
        typer.Option(
            "--grader-source",
            help=(
                "image (preinstalled), pypi (uvx pinned release), "
                "or editable (copy source)."
            ),
        ),
    ] = "image",
    prompt_template: Annotated[
        Path | None,
        typer.Option(
            "--prompt-template", help="Override the packaged prompt template."
        ),
    ] = None,
    judge_model: Annotated[
        str,
        typer.Option(
            "--judge-model",
            help="Rubric judge model: an Anthropic id or opencode-go/<id>.",
        ),
    ] = DEFAULT_JUDGE_MODEL,
    agent_timeout: Annotated[
        float, typer.Option("--agent-timeout", help="Seconds.")
    ] = 3600.0,
    verifier_timeout: Annotated[
        float, typer.Option("--verifier-timeout", help="Seconds.")
    ] = 1800.0,
    cpus: Annotated[int, typer.Option("--cpus")] = 2,
    memory_mb: Annotated[int, typer.Option("--memory-mb")] = 4096,
    allow_missing_document: Annotated[
        bool,
        typer.Option(
            "--allow-missing-document",
            help="Build tasks without a source document (smoke tests only).",
        ),
    ] = False,
) -> None:
    """Materialize dataset tasks as Harbor task directories."""
    if grader_source not in ("image", "pypi", "editable"):
        fail(f"unknown --grader-source {grader_source!r}")
    source: GraderSource = grader_source  # type: ignore[assignment]
    try:
        judge_backend(judge_model)
    except ValueError as exc:
        fail(str(exc))
    options = BuildOptions(
        image=image or environment.default_image(),
        dockerfile=dockerfile,
        grader_source=source,
        agent_timeout_sec=agent_timeout,
        verifier_timeout_sec=verifier_timeout,
        cpus=cpus,
        memory_mb=memory_mb,
        judge_model=judge_model,
        allow_missing_document=allow_missing_document,
    )
    try:
        info = build_tasks(
            dataset_dir,
            out,
            options=options,
            task_ids=task_ids,
            template_path=prompt_template,
        )
    except DatasetError as exc:
        fail(str(exc))
    if info["tasks_missing_document"]:
        warn(
            "built without source documents (not valid for evaluation): "
            + ", ".join(info["tasks_missing_document"])
        )
    console.print(
        f"[green]built[/green] {len(info['task_ids'])} task(s) in {escape(str(out))} "
        f"(image {info['image'] or 'from Dockerfile'}, grader {info['grader_source']})"
    )
