from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from trialdesignbench.cli._console import console, fail
from trialdesignbench.report import (
    DEFAULT_THRESHOLD,
    ReportError,
    build_report,
    leaderboard,
    render_markdown,
)


def report(
    sources: Annotated[
        list[Path],
        typer.Argument(help="Harbor job dirs and/or dirs of <task_id>/grade.json."),
    ],
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Write report JSON here.")
    ] = None,
    fmt: Annotated[str, typer.Option("--format", help="table, json, or md.")] = "table",
    threshold: Annotated[
        float, typer.Option("--threshold", help="Pass threshold for all-attempts pass.")
    ] = DEFAULT_THRESHOLD,
    task_ids: Annotated[
        list[str] | None,
        typer.Option("--task-ids", help="Expected tasks (missing ones count as 0)."),
    ] = None,
) -> None:
    """Aggregate graded trials into a report and leaderboard."""
    if fmt not in ("table", "json", "md"):
        fail("--format must be table, json, or md")
    try:
        summary = build_report(sources, threshold=threshold, task_ids=task_ids)
    except ReportError as exc:
        fail(str(exc))
    if out is not None:
        payload = summary.model_dump(mode="json")
        payload["leaderboard"] = leaderboard(summary)
        out.write_text(json.dumps(payload, indent=2) + "\n")
    if fmt == "json":
        payload = summary.model_dump(mode="json")
        payload["leaderboard"] = leaderboard(summary)
        typer.echo(json.dumps(payload, indent=2))
        return
    if fmt == "md":
        typer.echo(render_markdown(summary))
        return
    table = Table(title=f"TrialDesignBench ({len(summary.task_ids)} tasks)")
    for col in (
        "Rank",
        "Agent",
        "Model",
        "Effort",
        "Mean",
        "Pass rate",
        "Attempted",
        "Errored",
    ):
        table.add_column(col)
    for r in leaderboard(summary):
        table.add_row(
            str(r["rank"]),
            r["agent"],
            r["model"],
            r["effort"],
            f"{r['mean_score']:.4f}",
            f"{r['pass_rate']:.4f}",
            f"{r['tasks_attempted']}/{r['tasks_total']}",
            str(r["errored_trials"]),
        )
    console.print(table)
    flagged = [
        t for t in summary.trials if t.status != "graded" or t.network_violations
    ]
    for t in flagged:
        reason = "; ".join(t.zero_reasons) or (t.error or "")
        console.print(
            f"[yellow]{t.trial_name}[/yellow] {t.status} "
            f"violations={t.network_violations} {reason}",
            markup=True,
            highlight=False,
        )
