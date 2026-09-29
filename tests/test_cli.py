from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from tests.conftest import FIXTURE_INTAKE, FIXTURE_TASK_ID, requires_rscript
from trialdesignbench.cli.main import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and result.stdout.strip() == "1.3.1"


def test_dataset_import_and_build(tmp_path: Path, documents_dir: Path) -> None:
    ds, tasks = tmp_path / "ds", tmp_path / "tasks"
    r = runner.invoke(
        app,
        [
            "dataset",
            "import",
            str(FIXTURE_INTAKE),
            "--out",
            str(ds),
            "--documents",
            str(documents_dir),
        ],
    )
    assert r.exit_code == 0, r.output
    assert runner.invoke(app, ["dataset", "check", str(ds)]).exit_code == 0
    r = runner.invoke(app, ["build", str(ds), "--out", str(tasks), "--image", "x/y:z"])
    assert r.exit_code == 0, r.output
    assert (tasks / FIXTURE_TASK_ID / "task.toml").is_file()


def test_dataset_check_fails_without_document(tmp_path: Path) -> None:
    ds = tmp_path / "ds"
    assert (
        runner.invoke(
            app, ["dataset", "import", str(FIXTURE_INTAKE), "-o", str(ds)]
        ).exit_code
        == 0
    )
    r = runner.invoke(app, ["dataset", "check", str(ds)])
    assert r.exit_code == 1
    assert "no source document" in r.output


@requires_rscript
def test_grade_with_fake_judge(
    make_submission: Callable[..., Path], rubrics_path: Path, tmp_path: Path
) -> None:
    sub = make_submission()
    out = tmp_path / "out"
    r = runner.invoke(
        app,
        [
            "grade",
            str(sub),
            "--rubrics",
            str(rubrics_path),
            "--out",
            str(out),
            "--judge",
            "fake",
        ],
    )
    assert r.exit_code == 0, r.output
    assert json.loads((out / "reward.json").read_text())["reward"] == 1.0


def test_grade_error_exit_code(
    make_submission: Callable[..., Path], rubrics_path: Path, tmp_path: Path
) -> None:
    sub = make_submission(traj=False)
    r = runner.invoke(
        app,
        [
            "grade",
            str(sub),
            "--rubrics",
            str(rubrics_path),
            "--out",
            str(tmp_path / "o"),
            "--judge",
            "fake",
            "--trajectory",
            str(tmp_path / "missing.json"),
        ],
    )
    assert r.exit_code == 3
    assert json.loads((tmp_path / "o" / "reward.json").read_text())["reward"] == 0.0


def test_run_dry_run(tmp_path: Path, dataset_dir: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    tasks = tmp_path / "tasks"
    assert (
        runner.invoke(app, ["build", str(dataset_dir), "-o", str(tasks)]).exit_code == 0
    )
    r = runner.invoke(
        app,
        [
            "run",
            "--tasks",
            str(tasks),
            "--agent",
            "claude-code",
            "--model",
            "anthropic/claude-opus-5-5",
            "--dry-run",
            "--jobs-dir",
            str(tmp_path / "jobs"),
            "--job-name",
            "dry",
        ],
    )
    assert r.exit_code == 0, r.output
    assert (tmp_path / "jobs" / "dry" / "job.yaml").is_file()
    assert "harbor run -c" in r.output

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    r = runner.invoke(
        app,
        [
            "run",
            "--tasks",
            str(tasks),
            "--agent",
            "claude-code",
            "--model",
            "anthropic/x",
            "--dry-run",
            "--jobs-dir",
            str(tmp_path / "jobs"),
            "--job-name",
            "dry2",
        ],
    )
    assert r.exit_code == 1
    assert "ANTHROPIC_API_KEY" in r.output


def test_run_effort_option(tmp_path: Path, dataset_dir: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    tasks = tmp_path / "tasks"
    assert (
        runner.invoke(app, ["build", str(dataset_dir), "-o", str(tasks)]).exit_code == 0
    )
    common = ["run", "--tasks", str(tasks), "--dry-run", "--jobs-dir"]
    common += [str(tmp_path / "jobs")]
    matrix = ["--agent", "claude-code", "--model", "anthropic/claude-opus-5-5"]
    matrix += ["--agent", "codex", "--model", "openai/gpt-6-astra"]
    r = runner.invoke(
        app,
        [*common, "--job-name", "e1", *matrix, "--effort", "max", "--effort", "xhigh"],
    )
    assert r.exit_code == 0, r.output
    job = json.loads(
        "\n".join(
            line
            for line in (tmp_path / "jobs" / "e1" / "job.yaml").read_text().splitlines()
            if not line.startswith("#")
        )
    )
    assert [a["kwargs"]["reasoning_effort"] for a in job["agents"]] == ["max", "xhigh"]
    assert "reasoning effort" not in r.output

    r = runner.invoke(app, [*common, "--job-name", "e2", *matrix])
    assert r.exit_code == 0, r.output
    assert "reasoning effort for claude-code not set" in r.output

    r = runner.invoke(app, [*common, "--job-name", "e3", *matrix, "--effort", "none"])
    assert r.exit_code == 1
    assert "claude-code does not accept --effort 'none'" in r.output
