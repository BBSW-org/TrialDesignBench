"""`tdb` entry point."""

from __future__ import annotations

from typing import Annotated

import typer

from trialdesignbench.cli import build, dataset, env, grade, report, run
from trialdesignbench.provenance import package_version

app = typer.Typer(
    help="TrialDesignBench: evaluate AI agents on clinical trial design.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(package_version())
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version, is_eager=True, help="Print version."
        ),
    ] = False,
) -> None:
    """TrialDesignBench command-line interface."""


app.add_typer(dataset.app, name="dataset")
app.add_typer(env.app, name="env")
app.command("build")(build.build)
app.command("grade")(grade.grade)
app.command("run")(run.run)
app.command("regrade")(run.regrade)
app.command("report")(report.report)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
