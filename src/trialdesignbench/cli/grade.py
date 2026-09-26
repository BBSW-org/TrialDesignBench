from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from trialdesignbench.cli._console import console, fail
from trialdesignbench.dataset import DatasetError
from trialdesignbench.grade import DEFAULT_RSCRIPT_TIMEOUT_SEC, grade_directory
from trialdesignbench.judge import AnthropicJudge, FakeJudge, Judge


def _judge(kind: str, model: str | None, votes: int, fake_verdict: str) -> Judge:
    if kind == "anthropic":
        return AnthropicJudge(model, votes=votes)
    if kind == "fake":
        if fake_verdict not in ("pass", "fail", "unclear"):
            fail("--fake-verdict must be pass, fail, or unclear")
        return FakeJudge(default=fake_verdict)  # type: ignore[arg-type]
    fail(f"unknown --judge {kind!r}")


def grade(
    submission_dir: Annotated[
        Path, typer.Argument(help="Directory with output.json and output.R.")
    ],
    rubrics: Annotated[Path, typer.Option("--rubrics", help="Hidden rubrics.json.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")],
    trajectory: Annotated[
        Path | None,
        typer.Option(
            "--trajectory",
            help="ATIF trajectory to scan (default: <submission>/trajectory.json, "
            "then /logs/agent/trajectory.json).",
        ),
    ] = None,
    judge: Annotated[
        str, typer.Option("--judge", help="anthropic or fake.")
    ] = "anthropic",
    judge_model: Annotated[
        str | None,
        typer.Option("--judge-model", help="Judge model (default: $TDB_JUDGE_MODEL)."),
    ] = None,
    judge_votes: Annotated[
        int, typer.Option("--judge-votes", min=1, help="Majority vote over k calls.")
    ] = 1,
    fake_verdict: Annotated[
        str, typer.Option("--fake-verdict", help="Verdict for --judge fake.")
    ] = "pass",
    rscript_timeout: Annotated[
        float, typer.Option("--rscript-timeout", help="Seconds for `Rscript output.R`.")
    ] = DEFAULT_RSCRIPT_TIMEOUT_SEC,
) -> None:
    """Grade one submission. Exit code 0 when graded, 3 when status is error."""
    try:
        run = grade_directory(
            submission_dir,
            rubrics,
            out,
            _judge(judge, judge_model, judge_votes, fake_verdict),
            trajectory_path=trajectory,
            rscript_timeout_sec=rscript_timeout,
        )
    except DatasetError as exc:
        fail(str(exc))
    g = run.grade
    table = Table(title=f"{g.task_id}: {g.status}")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Message", overflow="fold")
    colors = {"pass": "green", "fail": "red", "error": "bold red"}
    for c in g.checks:
        table.add_row(c.name, f"[{colors[c.status]}]{c.status}[/]", escape(c.message))
    console.print(table)
    console.print_json(
        data={
            "score": g.score,
            "rubric_score": g.rubric_score,
            "deterministic_pass_fraction": g.deterministic_pass_fraction,
            "zero_reasons": list(g.zero_reasons),
        }
    )
    if g.status == "error":
        raise typer.Exit(3)
