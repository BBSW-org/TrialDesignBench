from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from trialdesignbench import environment
from trialdesignbench.canary import write_canary_task
from trialdesignbench.cli._console import console, fail, warn
from trialdesignbench.run import AuthMode, RunError, allowed_hosts

app = typer.Typer(
    help="Build and check the shared environment image.", no_args_is_help=True
)


@app.command("build")
def build(
    tag: Annotated[str | None, typer.Option("--tag", help="Image tag.")] = None,
    platform: Annotated[
        list[str] | None,
        typer.Option(
            "--platform", help="e.g. linux/amd64 (repeat for multi-arch buildx)."
        ),
    ] = None,
    push: Annotated[
        bool, typer.Option("--push", help="Push (multi-arch buildx only).")
    ] = False,
    tdb_source: Annotated[
        str, typer.Option("--tdb-source", help="auto, local (build wheel), or pypi.")
    ] = "auto",
    no_cache: Annotated[bool, typer.Option("--no-cache")] = False,
    context: Annotated[
        Path | None,
        typer.Option(
            "--context", help="Keep the staged build context in this directory."
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Stage and print only.")
    ] = False,
) -> None:
    """Build the shared agent/verifier image with the pinned versions."""
    image = tag or environment.default_image()
    tmp = None
    if context is None:
        tmp = tempfile.TemporaryDirectory(prefix="tdb-env-")
        context = Path(tmp.name)
    try:
        info = environment.stage_context(context, tdb_source=tdb_source)
        cmd = environment.build_command(
            image, context, platforms=platform, push=push, no_cache=no_cache
        )
        console.print(f"staged context: {escape(json.dumps(info))}")
        console.print(f"command: {escape(shlex.join(cmd))}")
        if dry_run:
            return
        code = subprocess.run(cmd, check=False).returncode
        if code != 0:
            fail(f"docker build failed with code {code}", code)
    finally:
        if tmp is not None:
            tmp.cleanup()
    console.print(f"[green]built[/green] {escape(image)}")


@app.command("check")
def check(
    tag: Annotated[str | None, typer.Option("--tag", help="Image tag.")] = None,
    canary: Annotated[
        bool,
        typer.Option("--canary", help="Also run the network canary through Harbor."),
    ] = False,
    agent: Annotated[
        str, typer.Option("--agent", help="Agent whose API hosts the canary allows.")
    ] = "claude-code",
    auth: Annotated[str, typer.Option("--auth", help="api or subscription.")] = "api",
    jobs_dir: Annotated[Path, typer.Option("--jobs-dir")] = Path("jobs"),
) -> None:
    """Check the image's R packages and tools, and optionally the network canary."""
    image = tag or environment.default_image()
    host_warning = environment.docker_host_warning()
    if host_warning:
        warn(host_warning)
    code = subprocess.run(environment.check_command(image), check=False).returncode
    if code != 0:
        fail(f"environment check failed for {image} (exit {code})", code)
    console.print(f"[green]ok[/green] {escape(image)} tools and R packages")
    if not canary:
        return
    if auth not in ("api", "subscription"):
        fail("--auth must be api or subscription")
    mode: AuthMode = auth  # type: ignore[assignment]
    try:
        hosts = allowed_hosts(agent, mode).hosts
    except RunError as exc:
        fail(str(exc))
    if shutil.which("harbor") is None:
        fail("`harbor` not found; install `trialdesignbench[harbor]` (Python 3.12+)")
    with tempfile.TemporaryDirectory(prefix="tdb-canary-") as tmp:
        task = write_canary_task(Path(tmp), image=image, allowed_hosts=hosts)
        job_name = f"canary-{agent}-{auth}"
        cmd = [
            "harbor",
            "run",
            "-p",
            str(task),
            "-a",
            "oracle",
            "-o",
            str(jobs_dir.resolve()),
            "--job-name",
            job_name,
            "--n-concurrent",
            "1",
            "--yes",
        ]
        console.print(f"command: {escape(shlex.join(cmd))}")
        subprocess.run(cmd, check=False)
        job_dir = jobs_dir.resolve() / job_name
        verdicts = sorted(job_dir.glob("*/verifier/canary.json"))
        if not verdicts:
            fail(f"canary produced no verdict in {job_dir}")
        data = json.loads(verdicts[-1].read_text())
        console.print_json(data=data)
        if data.get("reasons"):
            fail("network canary FAILED: " + "; ".join(data["reasons"]))
    console.print(
        f"[green]ok[/green] network canary for {agent} ({auth}): {', '.join(hosts)}"
    )
