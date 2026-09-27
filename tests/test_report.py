from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import FIXTURE_TASK_ID, ok_rscript
from trialdesignbench.grade import grade_directory
from trialdesignbench.judge import FakeJudge
from trialdesignbench.report import (
    ReportError,
    build_report,
    leaderboard,
    render_markdown,
)


def _write_grade(
    make_submission: Callable[..., Path],
    rubrics_path: Path,
    verifier_dir: Path,
    name: str,
    verdict: str = "pass",
) -> None:
    sub = make_submission(name=name)
    grade_directory(
        sub,
        rubrics_path,
        verifier_dir,
        FakeJudge(default=verdict),  # type: ignore[arg-type]
        trajectory_path=sub / "trajectory.json",
        run_rscript=ok_rscript,
    )


def _trial(
    job: Path,
    name: str,
    task: str,
    *,
    agent: str = "claude-code",
    model: str = "anthropic/m",
    kwargs: dict[str, object] | None = None,
    exception: bool = False,
) -> Path:
    trial = job / name
    (trial / "verifier").mkdir(parents=True)
    result = {
        "task_name": f"trialdesignbench/{task}",
        "trial_name": name,
        "agent_info": {"name": agent, "version": "1"},
        "config": {"agent": {"model_name": model, "kwargs": kwargs or {}}},
        "agent_result": {"n_input_tokens": 100, "n_output_tokens": 10, "cost_usd": 0.5},
        "exception_info": {
            "exception_type": "AgentTimeoutError",
            "exception_message": "t",
        }
        if exception
        else None,
    }
    (trial / "result.json").write_text(json.dumps(result))
    return trial


@pytest.fixture
def job(
    tmp_path: Path, make_submission: Callable[..., Path], rubrics_path: Path
) -> Path:
    job = tmp_path / "jobs" / "j1"
    job.mkdir(parents=True)
    tasks = [{"path": f"/x/{FIXTURE_TASK_ID}"}, {"path": "/x/other-task"}]
    (job / "config.json").write_text(json.dumps({"tasks": tasks}))
    t1 = _trial(job, "a__1", FIXTURE_TASK_ID)
    _write_grade(make_submission, rubrics_path, t1 / "verifier", "s1", "pass")
    t2 = _trial(job, "a__2", FIXTURE_TASK_ID)
    _write_grade(make_submission, rubrics_path, t2 / "verifier", "s2", "fail")
    _trial(job, "a__3", FIXTURE_TASK_ID, exception=True)  # errored: no verifier output
    return job


def test_aggregation_with_errors_and_missing(job: Path) -> None:
    report = build_report([job], threshold=0.5)
    (agent,) = report.agents
    assert agent.n_tasks_total == 2
    assert agent.n_tasks_attempted == 1
    assert agent.missing_tasks == ("other-task",)
    assert agent.errored_trials == ("a__3",)
    (task,) = agent.tasks
    assert task.n_attempts == 3
    assert (task.min, task.max) == (0.0, 1.0)
    assert task.mean == pytest.approx(1 / 3)
    assert not task.all_attempts_pass
    # Benchmark mean over all tasks: the missing task counts as 0.
    assert agent.mean_score == pytest.approx((1 / 3) / 2)
    assert agent.pass_rate == 0.0
    assert agent.input_tokens == 300 and agent.cost_usd == pytest.approx(1.5)
    assert agent.by_question_type and agent.by_dimension and agent.by_design_element
    statuses = {t.trial_name: t.status for t in report.trials}
    assert statuses == {"a__1": "graded", "a__2": "graded", "a__3": "error"}


def test_leaderboard_and_markdown(job: Path) -> None:
    report = build_report([job])
    rows = leaderboard(report)
    assert rows[0]["rank"] == 1 and rows[0]["agent"] == "claude-code"
    md = render_markdown(report)
    assert "Missing tasks (scored 0): other-task" in md
    assert "a__3" in md


def test_effort_separates_leaderboard_rows(
    job: Path, make_submission: Callable[..., Path], rubrics_path: Path
) -> None:
    """Trials at different effort levels never merge into one row."""
    t = _trial(job, "b__1", FIXTURE_TASK_ID, kwargs={"reasoning_effort": "max"})
    _write_grade(make_submission, rubrics_path, t / "verifier", "s4", "pass")
    t = _trial(
        job, "c__1", FIXTURE_TASK_ID, agent="opencode", kwargs={"variant": "high"}
    )
    _write_grade(make_submission, rubrics_path, t / "verifier", "s5", "pass")
    report = build_report([job])
    assert [(a.agent, a.model, a.effort) for a in report.agents] == [
        ("claude-code", "anthropic/m", None),  # the fixture's default-effort trials
        ("claude-code", "anthropic/m", "max"),
        ("opencode", "anthropic/m", "high"),
    ]
    by_name = {t.trial_name: t.effort for t in report.trials}
    assert by_name["a__1"] is None and by_name["b__1"] == "max"
    assert by_name["c__1"] == "high"
    rows = leaderboard(report)
    assert [(r["agent"], r["effort"]) for r in rows] == [
        ("claude-code", "max"),
        ("opencode", "high"),
        ("claude-code", "default"),
    ]
    md = render_markdown(report)
    assert "| Effort |" in md
    assert "## claude-code / anthropic/m / max" in md
    assert "## claude-code / anthropic/m / default" in md


def test_standalone_grades_are_comparable(
    tmp_path: Path, make_submission: Callable[..., Path], rubrics_path: Path
) -> None:
    root = tmp_path / "external-lab"
    _write_grade(make_submission, rubrics_path, root / FIXTURE_TASK_ID, "ext", "pass")
    report = build_report([root])
    (agent,) = report.agents
    assert (agent.agent, agent.model) == ("standalone", "external-lab")
    assert agent.effort is None and leaderboard(report)[0]["effort"] == "default"
    assert agent.mean_score == 1.0


def test_zeroed_trial_listed_with_violations(
    tmp_path: Path, make_submission: Callable[..., Path], rubrics_path: Path
) -> None:
    from tests.conftest import trajectory

    root = tmp_path / "ext"
    sub = make_submission(
        traj=trajectory(
            [
                {
                    "tool_call_id": "1",
                    "function_name": "WebSearch",
                    "arguments": {"query": "q"},
                }
            ]
        )
    )
    grade_directory(
        sub,
        rubrics_path,
        root / FIXTURE_TASK_ID,
        FakeJudge(default="pass"),
        trajectory_path=sub / "trajectory.json",
        run_rscript=ok_rscript,
    )
    report = build_report([root])
    (trial,) = report.trials
    assert trial.status == "zeroed" and trial.network_violations == 1
    assert trial.score == 0.0 and trial.rubric_score == 1.0


def test_failed_canary_blocks_report(job: Path) -> None:
    canary = _trial(job, "canary__1", "network-canary")
    (canary / "verifier" / "canary.json").write_text(
        json.dumps({"reasons": ["blocked URL reachable: https://clinicaltrials.gov"]})
    )
    with pytest.raises(ReportError, match="canary"):
        build_report([job])
    report = build_report([job], allow_canary_failure=True)
    assert report.canary_failures
    assert all(t.task_id != "network-canary" for t in report.trials)


def test_passing_canary_is_excluded_from_scores(job: Path) -> None:
    canary = _trial(job, "canary__1", "network-canary")
    (canary / "verifier" / "canary.json").write_text(json.dumps({"reasons": []}))
    report = build_report([job])
    assert "network-canary" not in report.task_ids
