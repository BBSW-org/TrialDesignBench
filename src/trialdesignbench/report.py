"""Aggregate graded trials into a benchmark report and leaderboard.

Accepts Harbor job directories (trial dirs with `result.json` and
`verifier/grade.json`) and plain directories of standalone
`<task_id>/grade.json` files, so external submissions are comparable.

Unattempted or errored tasks count as 0 and are listed explicitly.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from trialdesignbench.build import TASK_ORG
from trialdesignbench.canary import CANARY_TASK_ID, canary_result
from trialdesignbench.provenance import package_version, utc_now
from trialdesignbench.schema import (
    AgentAggregate,
    ReportSummary,
    TaskAggregate,
    TaskGrade,
    TrialSummary,
)
from trialdesignbench.scoring import SCORING_VERSION

DEFAULT_THRESHOLD = 0.8


class ReportError(RuntimeError):
    pass


@dataclass
class _Trial:
    summary: TrialSummary
    grade: TaskGrade | None


def _task_id(task_name: str) -> str:
    prefix = f"{TASK_ORG}/"
    return (
        task_name[len(prefix) :]
        if task_name.startswith(prefix)
        else task_name.rsplit("/", 1)[-1]
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_grade(path: Path) -> tuple[TaskGrade | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        return TaskGrade.model_validate_json(path.read_text(encoding="utf-8")), None
    except ValidationError as exc:
        return None, f"invalid grade.json: {exc.errors()[0]['msg']}"


def _usage(
    trial_dir: Path, result: dict[str, Any]
) -> tuple[int | None, int | None, float | None]:
    traj = _read_json(trial_dir / "agent" / "trajectory.json")
    if isinstance(traj, dict) and isinstance(traj.get("final_metrics"), dict):
        fm = traj["final_metrics"]
        return (
            fm.get("total_prompt_tokens"),
            fm.get("total_completion_tokens"),
            fm.get("total_cost_usd"),
        )
    ar = result.get("agent_result") or {}
    return ar.get("n_input_tokens"), ar.get("n_output_tokens"), ar.get("cost_usd")


def _summary_from_grade(
    trial_name: str,
    task_id: str,
    agent: str,
    model: str,
    grade: TaskGrade | None,
    grade_error: str | None,
    reward: dict[str, Any] | None,
    exception: str | None,
    usage: tuple[int | None, int | None, float | None] = (None, None, None),
) -> TrialSummary:
    tokens_in, tokens_out, cost = usage
    common = {
        "trial_name": trial_name,
        "task_id": task_id,
        "agent": agent,
        "model": model,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cost_usd": cost,
    }
    if grade is not None:
        status = {"ok": "graded", "zeroed": "zeroed", "error": "error"}[grade.status]
        return TrialSummary(
            **common,
            status=status,
            score=grade.score,
            rubric_score=grade.rubric_score,
            network_violations=len(grade.network_violations),
            zero_reasons=grade.zero_reasons,
            error=exception,
        )
    error = grade_error or exception
    if reward is not None and error is None:
        # Fallback: reward.json without grade.json (older grader). No detail.
        score = float(reward.get("reward", reward.get("score", 0.0)))
        return TrialSummary(
            **common,
            status="graded",
            score=score,
            rubric_score=reward.get("rubric"),
            network_violations=0,
            zero_reasons=(),
            error="grade.json missing; scored from reward.json",
        )
    return TrialSummary(
        **common,
        status="error",
        score=0.0,
        rubric_score=None,
        network_violations=0,
        zero_reasons=(),
        error=error or "no verifier output",
    )


def load_job(job_dir: Path) -> tuple[list[_Trial], set[str], list[str]]:
    """Trials, expected task ids, and canary failures for one Harbor job."""
    trials: list[_Trial] = []
    canary_failures: list[str] = []
    config = _read_json(job_dir / "config.json") or {}
    expected = {
        Path(t["path"]).name
        for t in config.get("tasks", [])
        if isinstance(t, dict) and t.get("path")
    }
    expected.discard(CANARY_TASK_ID)
    for trial_dir in sorted(p for p in job_dir.iterdir() if p.is_dir()):
        result = _read_json(trial_dir / "result.json")
        if not isinstance(result, dict):
            continue
        task_id = _task_id(str(result.get("task_name", trial_dir.name)))
        agent = (result.get("agent_info") or {}).get("name") or "unknown"
        model = ((result.get("config") or {}).get("agent") or {}).get("model_name") or (
            ((result.get("agent_info") or {}).get("model_info") or {}).get("name")
            or "unknown"
        )
        exc = result.get("exception_info")
        exception = (
            f"{exc['exception_type']}: {exc['exception_message']}" if exc else None
        )
        if task_id == CANARY_TASK_ID:
            passed, reasons = canary_result(trial_dir)
            if exception:
                passed, reasons = False, [exception, *reasons]
            if not passed:
                canary_failures.append(
                    f"{job_dir.name}/{trial_dir.name}: {'; '.join(reasons)}"
                )
            continue
        grade, grade_error = _load_grade(trial_dir / "verifier" / "grade.json")
        reward = _read_json(trial_dir / "verifier" / "reward.json")
        summary = _summary_from_grade(
            trial_dir.name,
            task_id,
            agent,
            model,
            grade,
            grade_error,
            reward if isinstance(reward, dict) else None,
            exception,
            _usage(trial_dir, result),
        )
        trials.append(_Trial(summary, grade))
    return trials, expected, canary_failures


def load_standalone(root: Path) -> tuple[list[_Trial], set[str]]:
    """Trials from a directory of `<task_id>/grade.json` files."""
    trials = []
    expected = set()
    for task_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        expected.add(task_dir.name)
        grade, grade_error = _load_grade(task_dir / "grade.json")
        reward = _read_json(task_dir / "reward.json")
        summary = _summary_from_grade(
            f"{root.name}/{task_dir.name}",
            grade.task_id if grade else task_dir.name,
            "standalone",
            root.name,
            grade,
            grade_error,
            reward if isinstance(reward, dict) else None,
            None,
        )
        trials.append(_Trial(summary, grade))
    return trials, expected


def is_job_dir(path: Path) -> bool:
    """Whether `path` looks like a Harbor job directory."""
    return (path / "config.json").is_file() or any(
        (p / "result.json").is_file() for p in path.iterdir() if p.is_dir()
    )


def _mean_dicts(dicts: Iterable[dict[str, float]]) -> dict[str, float]:
    acc: dict[str, list[float]] = {}
    for d in dicts:
        for k, v in d.items():
            acc.setdefault(k, []).append(v)
    return {k: sum(v) / len(v) for k, v in sorted(acc.items())}


def aggregate(
    trials: Sequence[_Trial], task_ids: Sequence[str], threshold: float
) -> list[AgentAggregate]:
    """Per agent x model aggregates; unattempted tasks count as 0."""
    groups: dict[tuple[str, str], list[_Trial]] = {}
    for t in trials:
        groups.setdefault((t.summary.agent, t.summary.model), []).append(t)
    out = []
    for (agent, model), group in sorted(groups.items()):
        by_task: dict[str, list[_Trial]] = {}
        for t in group:
            by_task.setdefault(t.summary.task_id, []).append(t)
        tasks = []
        for task_id in task_ids:
            attempts = by_task.get(task_id, [])
            if not attempts:
                continue
            scores = [a.summary.score for a in attempts]
            tasks.append(
                TaskAggregate(
                    task_id=task_id,
                    n_attempts=len(scores),
                    mean=sum(scores) / len(scores),
                    min=min(scores),
                    max=max(scores),
                    all_attempts_pass=all(s >= threshold for s in scores),
                )
            )
        n_total = len(task_ids)
        grades = [t.grade for t in group if t.grade is not None]
        costs = [t.summary.cost_usd for t in group]
        out.append(
            AgentAggregate(
                agent=agent,
                model=model,
                # Over the full benchmark: an unattempted task counts as 0.
                mean_score=sum(t.mean for t in tasks) / n_total if n_total else 0.0,
                pass_rate=sum(t.all_attempts_pass for t in tasks) / n_total
                if n_total
                else 0.0,
                n_tasks_total=n_total,
                n_tasks_attempted=len(tasks),
                missing_tasks=tuple(sorted(set(task_ids) - set(by_task))),
                errored_trials=tuple(
                    t.summary.trial_name for t in group if t.summary.status == "error"
                ),
                tasks=tuple(tasks),
                by_question_type=_mean_dicts(g.by_question_type for g in grades),
                by_design_element=_mean_dicts(g.by_design_element for g in grades),
                by_dimension=_mean_dicts(g.by_dimension for g in grades),
                input_tokens=sum(t.summary.input_tokens or 0 for t in group),
                output_tokens=sum(t.summary.output_tokens or 0 for t in group),
                cost_usd=None
                if any(c is None for c in costs)
                else sum(c or 0.0 for c in costs),
            )
        )
    return out


def build_report(
    sources: Sequence[Path],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    task_ids: Sequence[str] | None = None,
    allow_canary_failure: bool = False,
) -> ReportSummary:
    """Build a ReportSummary; refuses when a network canary failed."""
    trials: list[_Trial] = []
    expected: set[str] = set()
    canary_failures: list[str] = []
    for src in sources:
        if not src.is_dir():
            raise ReportError(f"{src} is not a directory")
        if is_job_dir(src):
            t, e, c = load_job(src)
            canary_failures += c
        else:
            t, e = load_standalone(src)
        trials += t
        expected |= e
    if canary_failures and not allow_canary_failure:
        raise ReportError(
            "network canary failed; refusing to report results:\n  "
            + "\n  ".join(canary_failures)
        )
    ids = (
        sorted(task_ids)
        if task_ids
        else sorted(expected | {t.summary.task_id for t in trials})
    )
    return ReportSummary(
        package_version=package_version(),
        scoring_version=SCORING_VERSION,
        threshold=threshold,
        sources=tuple(str(s) for s in sources),
        task_ids=tuple(ids),
        agents=tuple(aggregate(trials, ids, threshold)),
        trials=tuple(t.summary for t in trials),
        canary_failures=tuple(canary_failures),
        generated_at=utc_now(),
    )


def leaderboard(report: ReportSummary) -> list[dict[str, Any]]:
    """Ranked rows by mean score, then pass rate."""
    rows = [
        {
            "rank": 0,
            "agent": a.agent,
            "model": a.model,
            "mean_score": round(a.mean_score, 4),
            "pass_rate": round(a.pass_rate, 4),
            "tasks_attempted": a.n_tasks_attempted,
            "tasks_total": a.n_tasks_total,
            "errored_trials": len(a.errored_trials),
            "cost_usd": a.cost_usd,
        }
        for a in sorted(report.agents, key=lambda a: (-a.mean_score, -a.pass_rate))
    ]
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def render_markdown(report: ReportSummary) -> str:
    """Markdown report with leaderboard, per-task tables, and flagged trials."""
    lines = [
        "# TrialDesignBench report",
        "",
        f"Scoring version {report.scoring_version}; pass threshold "
        f"{report.threshold:g}; {len(report.task_ids)} task(s).",
        "",
        "| Rank | Agent | Model | Mean score | Pass rate | Attempted | Errored trials |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in leaderboard(report):
        lines.append(
            f"| {r['rank']} | {r['agent']} | {r['model']} | {r['mean_score']:.4f} | "
            f"{r['pass_rate']:.4f} | {r['tasks_attempted']}/{r['tasks_total']} | "
            f"{r['errored_trials']} |"
        )
    for a in report.agents:
        lines += ["", f"## {a.agent} / {a.model}", ""]
        lines += [
            "| Task | Attempts | Mean | Min | Max | All pass |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for ta in a.tasks:
            lines.append(
                f"| {ta.task_id} | {ta.n_attempts} | {ta.mean:.4f} | {ta.min:.4f} | "
                f"{ta.max:.4f} | {'yes' if ta.all_attempts_pass else 'no'} |"
            )
        if a.missing_tasks:
            lines += ["", f"Missing tasks (scored 0): {', '.join(a.missing_tasks)}"]
        if a.errored_trials:
            lines += ["", f"Errored trials (scored 0): {', '.join(a.errored_trials)}"]
        for title, data in (
            ("Question type", a.by_question_type),
            ("Design element", a.by_design_element),
            ("Rubric dimension", a.by_dimension),
        ):
            if data:
                lines += [
                    "",
                    f"{title} (rubric score): "
                    + ", ".join(f"{k} {v:.4f}" for k, v in data.items()),
                ]
    flagged = [t for t in report.trials if t.network_violations or t.status != "graded"]
    if flagged:
        lines += [
            "",
            "## Trials needing attention",
            "",
            "| Trial | Status | Violations | Reason |",
            "| --- | --- | --- | --- |",
        ]
        for t in flagged:
            reason = "; ".join(t.zero_reasons) or (t.error or "")
            lines.append(
                f"| {t.trial_name} | {t.status} | {t.network_violations} | {reason} |"
            )
    return "\n".join(lines) + "\n"
