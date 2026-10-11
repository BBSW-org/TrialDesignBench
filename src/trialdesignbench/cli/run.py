from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from trialdesignbench.agents import AGENTS, DEFAULT_EFFORT, EFFORT_LEVELS, AuthMode
from trialdesignbench.cli._console import console, fail, warn
from trialdesignbench.run import (
    RunError,
    execute,
    parse_agent_pairs,
    plan_run,
    regrade_command,
)

_AGENT_NAMES = ", ".join(a.name for a in AGENTS)
_SUBSCRIPTION_AGENTS = ", ".join(a.name for a in AGENTS if a.subscription)
_EFFORT_LEVELS = ", ".join((*EFFORT_LEVELS, DEFAULT_EFFORT))


def run(
    tasks: Annotated[
        Path, typer.Option("--tasks", help="Harbor tasks dir from `tdb build`.")
    ],
    agent: Annotated[
        list[str],
        typer.Option(
            "--agent", help=f"Harbor agent: {_AGENT_NAMES} (repeat for a matrix)."
        ),
    ],
    model: Annotated[
        list[str],
        typer.Option(
            "--model", help="provider/model, e.g. anthropic/<id> (one per --agent)."
        ),
    ],
    effort: Annotated[
        list[str] | None,
        typer.Option(
            "--effort",
            help=f"Reasoning effort ({_EFFORT_LEVELS}), once for all agents or "
            "once per --agent. Each agent accepts a subset; `default` keeps the "
            "harness default.",
        ),
    ] = None,
    agent_version: Annotated[
        list[str] | None,
        typer.Option("--agent-version", help="Must match the pinned agent version."),
    ] = None,
    n_attempts: Annotated[int, typer.Option("--n-attempts", min=1)] = 1,
    n_concurrent: Annotated[
        int | None,
        typer.Option(
            "--n-concurrent", min=1, help="Default 2 (api) or 1 (subscription)."
        ),
    ] = None,
    auth: Annotated[
        str,
        typer.Option(
            "--auth",
            help=f"api (provider API key) or subscription ({_SUBSCRIPTION_AGENTS}).",
        ),
    ] = "api",
    skill: Annotated[
        list[str] | None,
        typer.Option(
            "--skill", help="Extra skill: local path or git ref (repeatable)."
        ),
    ] = None,
    task_ids: Annotated[list[str] | None, typer.Option("--task-ids")] = None,
    job_name: Annotated[str | None, typer.Option("--job-name")] = None,
    jobs_dir: Annotated[Path, typer.Option("--jobs-dir")] = Path("jobs"),
    canary: Annotated[
        bool, typer.Option("--canary", help="Run the network canary as the first task.")
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Pass --yes to harbor (skip env var approval)."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Write job.yaml and print the command only."),
    ] = False,
) -> None:
    """Run agents on built tasks through Harbor."""
    if auth not in ("api", "subscription"):
        fail("--auth must be api or subscription")
    mode: AuthMode = auth  # type: ignore[assignment]
    try:
        requests = parse_agent_pairs(agent, model, agent_version or [], effort or [])
        plan = plan_run(
            tasks,
            requests,
            auth=mode,
            n_attempts=n_attempts,
            n_concurrent=n_concurrent,
            skills=skill or [],
            job_name=job_name,
            jobs_dir=jobs_dir,
            task_ids=task_ids,
            canary=canary,
            yes=yes,
            dry_run=dry_run,
        )
    except RunError as exc:
        fail(str(exc))
    for w in plan.warnings:
        warn(w)
    console.print(f"job config: {escape(str(plan.job_yaml))}")
    prefix = "".join(f"{k}={shlex.quote(v)} " for k, v in plan.env.items())
    console.print(f"command: {escape(prefix + shlex.join(plan.command))}")
    if dry_run:
        return
    try:
        code = execute(plan)
    except RunError as exc:
        fail(str(exc))
    if code != 0:
        fail(f"harbor exited with code {code}", code)
    console.print(
        f"[green]done[/green] report with: tdb report {escape(str(plan.job_dir))}"
    )


def regrade(
    job_dir: Annotated[Path, typer.Argument(help="Harbor job directory to re-score.")],
    tasks: Annotated[
        Path, typer.Option("--tasks", help="Tasks providing the new verifier.")
    ],
    job_name: Annotated[str | None, typer.Option("--job-name")] = None,
    jobs_dir: Annotated[Path | None, typer.Option("--jobs-dir")] = None,
    n_concurrent: Annotated[int | None, typer.Option("--n-concurrent", min=1)] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Re-score recorded trials with a new grader or rubrics (no agent re-run)."""
    command = regrade_command(
        job_dir, tasks, job_name=job_name, jobs_dir=jobs_dir, n_concurrent=n_concurrent
    )
    console.print(f"command: {escape(shlex.join(command))}")
    if dry_run:
        return
    if shutil.which("harbor") is None:
        fail("`harbor` not found; install `trialdesignbench[harbor]` (Python 3.12+)")
    code = subprocess.run(command, check=False).returncode
    if code != 0:
        fail(f"harbor exited with code {code}", code)
