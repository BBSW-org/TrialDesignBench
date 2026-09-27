"""Grade one submission: deterministic checks, rubric judging, scoring.

IMPORTANT NOTE FOR AI AGENTS
THIS IS A very delicate file.
You need to be extremely conservative and careful about grading logic.
The worst case to avoid here is that grading goes wrong but the result still
indicates a good score. This might for example happen if you skip something
because of some error condition and only show a warning, but it is not
apparent from the output file that something went wrong.
It is always better to clearly mark a failure in the output file than to
silently skip something. Every error must be recorded as an explicit status
(`error`) in grade.json, and any gating error sets the reward score to 0.
Be extremely proactive with the user about clearing up details and
intricacies with how to handle something here. Ask questions and do not be
afraid to ask for clarification.
Do not remove this notice.

The grader is a pure function of (submission artifacts, rubrics, judge). It
runs identically inside a Harbor verifier container, standalone on any
directory with `output.json` and `output.R`, and in tests with `FakeJudge`
and an injected `run_rscript`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trialdesignbench.judge import Judge, JudgeArtifacts, error_results
from trialdesignbench.provenance import package_version, sha256_file, utc_now
from trialdesignbench.schema import (
    CheckResult,
    CriterionResult,
    NetworkViolation,
    QuestionGrade,
    QuestionRubrics,
    RubricSet,
    Submission,
    TaskGrade,
)
from trialdesignbench.scoring import (
    NON_BLOCKING_CHECKS,
    SCORED_KIND,
    ScoringConfig,
    dimension_means,
    group_means,
    reward_score,
    score_question,
    task_rubric_score,
)

OUTPUT_JSON = "output.json"
OUTPUT_R = "output.R"
TRAJECTORY_JSON = "trajectory.json"
DEFAULT_TRAJECTORY_PATH = Path("/logs/agent/trajectory.json")
DEFAULT_RSCRIPT_TIMEOUT_SEC = 600.0

_NOT_DERIVABLE = re.compile(
    r"\bnot\s+(?:derivable|derived|reported|stated|specified|available|"
    r"provided|determinable|calculable)\b|\bcannot\s+be\s+(?:derived|determined|"
    r"calculated)\b|\bnot\s+possible\s+to\s+(?:derive|determine|calculate)\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:\.(\d+))?(?![\w.])")

# Channel 2 (server-side web tools): tool names that fetch from the web.
WEB_TOOL_NAMES = frozenset(
    {
        "websearch",
        "webfetch",
        "web_search_call",
        "google_web_search",
        "web_search",
        "web_fetch",
    }
)
_URL = re.compile(r"https?://[^\s\"'<>)]+", re.IGNORECASE)
# Channel 1 (container egress): shell commands that reach the network.
NETWORK_COMMAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("curl", re.compile(r"(?<![\w.-])curl\b")),
    ("wget", re.compile(r"(?<![\w.-])wget\b")),
    ("download.file", re.compile(r"\bdownload\.file\s*\(")),
    (
        "httr",
        re.compile(
            r"\bhttr2?\s*::|library\(\s*[\"']?httr2?\b|require\(\s*[\"']?httr2?\b"
        ),
    ),
    (
        "requests",
        re.compile(r"\bimport\s+requests\b|\brequests\.(?:get|post|request|Session)\b"),
    ),
    ("urllib", re.compile(r"\burllib(?:\.request|\.urlopen|3)?\b")),
    ("pip install", re.compile(r"\bpip3?\s+install\b|\buv\s+pip\s+install\b")),
    ("install.packages", re.compile(r"\binstall\.packages\s*\(")),
    ("git clone", re.compile(r"\bgit\s+clone\b")),
)
_SHELL_TOOL_HINTS = ("bash", "shell", "exec", "terminal", "command")


# ---------------------------------------------------------------------------
# Rscript execution (injectable)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RscriptResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None
    duration_sec: float = 0.0


RunRscript = Callable[[Path, Path, float], RscriptResult]
"""`run_rscript(script, cwd, timeout_sec) -> RscriptResult`."""


def default_run_rscript(script: Path, cwd: Path, timeout_sec: float) -> RscriptResult:
    exe = shutil.which("Rscript")
    if exe is None:
        return RscriptResult(None, "", "", error="Rscript not found on PATH")
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [exe, script.name],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return RscriptResult(
            None,
            _decode(exc.stdout),
            _decode(exc.stderr),
            timed_out=True,
            duration_sec=time.monotonic() - start,
        )
    except OSError as exc:
        return RscriptResult(None, "", "", error=f"cannot execute Rscript: {exc}")
    return RscriptResult(
        proc.returncode,
        proc.stdout,
        proc.stderr,
        duration_sec=time.monotonic() - start,
    )


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


# ---------------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------------


def _check(name: str, status: str, message: str, **details: Any) -> CheckResult:
    return CheckResult(
        name=name,
        status=status,  # type: ignore[arg-type]
        message=message,
        blocking=name not in NON_BLOCKING_CHECKS,
        details=details,
    )


def _is_unfilled(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _compare(skeleton: Any, got: Any, path: str, problems: list[str]) -> None:
    """Compare an agent entry against its skeleton entry."""
    if isinstance(skeleton, dict):
        if not isinstance(got, dict):
            problems.append(f"{path}: expected an object, got {type(got).__name__}")
            return
        added = sorted(set(got) - set(skeleton))
        removed = sorted(set(skeleton) - set(got))
        if added:
            problems.append(f"{path}: added fields {added}")
        if removed:
            problems.append(f"{path}: removed fields {removed}")
        for key in skeleton:
            if key in got:
                _compare(skeleton[key], got[key], f"{path}.{key}", problems)
    elif skeleton is None:
        if _is_unfilled(got):
            problems.append(f"{path}: still null or empty")
    elif got != skeleton:
        problems.append(f"{path}: modified (expected {skeleton!r})")


def check_output_json(
    path: Path, rubrics: RubricSet
) -> tuple[CheckResult, dict[str, dict[str, Any]]]:
    """Validate output.json against the skeleton. Returns entries by id."""
    name = "output_json"
    if not path.is_file():
        return _check(name, "fail", f"{OUTPUT_JSON} not found"), {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _check(name, "fail", f"{OUTPUT_JSON} is not valid JSON: {exc}"), {}
    if not isinstance(data, dict) or not isinstance(data.get("output"), list):
        return (
            _check(name, "fail", "top level must be an object with an `output` array"),
            {},
        )
    problems: list[str] = []
    extra_top = sorted(set(data) - {"output"})
    if extra_top:
        problems.append(f"top level has extra keys {extra_top}")
    entries: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for idx, entry in enumerate(data["output"]):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            problems.append(f"output[{idx}]: entry without a string `id`")
            continue
        if entry["id"] in entries:
            duplicates.append(entry["id"])
            continue
        entries[entry["id"]] = entry
    skeletons = {q.question.id: q.question.skeleton() for q in rubrics.questions}
    missing = sorted(set(skeletons) - set(entries))
    added = sorted(set(entries) - set(skeletons))
    if duplicates:
        problems.append(f"duplicate ids {sorted(set(duplicates))}")
    if missing:
        problems.append(f"missing ids {missing}")
    if added:
        problems.append(f"unexpected ids {added}")
    for qid, skeleton in skeletons.items():
        if qid in entries:
            _compare(skeleton, entries[qid], qid, problems)
    if problems:
        return (
            _check(
                name,
                "fail",
                f"{len(problems)} problem(s) in {OUTPUT_JSON}",
                problems=problems,
            ),
            entries,
        )
    return _check(name, "pass", f"{OUTPUT_JSON} matches the question skeleton"), entries


def check_output_r(
    submission_dir: Path, run_rscript: RunRscript, timeout_sec: float
) -> tuple[CheckResult, RscriptResult | None]:
    """Run `Rscript output.R` in a scratch copy. Missing R is `error`."""
    name = "output_r"
    script = submission_dir / OUTPUT_R
    if not script.is_file():
        return _check(name, "fail", f"{OUTPUT_R} not found"), None
    if not script.read_text(encoding="utf-8", errors="replace").strip():
        return _check(name, "fail", f"{OUTPUT_R} is empty"), None
    # Run in a scratch copy so the script cannot alter the graded artifacts.
    with tempfile.TemporaryDirectory(prefix="tdb-rscript-") as tmp:
        work = Path(tmp) / "submission"
        shutil.copytree(submission_dir, work, symlinks=True)
        try:
            result = run_rscript(work / OUTPUT_R, work, timeout_sec)
        except Exception as exc:  # noqa: BLE001 - every runner failure must be recorded
            return (
                _check(
                    name, "error", f"Rscript runner failed: {type(exc).__name__}: {exc}"
                ),
                None,
            )
    details = {
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "duration_sec": round(result.duration_sec, 3),
        "timeout_sec": timeout_sec,
    }
    if result.error is not None:
        return _check(name, "error", result.error, **details), result
    if result.timed_out:
        return (
            _check(
                name, "fail", f"Rscript timed out after {timeout_sec:g}s", **details
            ),
            result,
        )
    if result.returncode != 0:
        return (
            _check(
                name, "fail", f"Rscript exited with code {result.returncode}", **details
            ),
            result,
        )
    return _check(
        name, "pass", "Rscript output.R exited with code 0", **details
    ), result


def _numeric_ok(value: Any) -> tuple[bool, str]:
    if isinstance(value, bool) or value is None:
        return False, "no numeric value"
    if isinstance(value, (int, float)):
        text = repr(value)
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False)
    if _NOT_DERIVABLE.search(text):
        return True, "states the value is not derivable"
    decimals = [len(m.group(1) or "") for m in _NUMBER.finditer(text)]
    if not decimals:
        return False, "no number found"
    short = [d for d in decimals if 0 < d < 4]
    if short:
        return False, "number(s) with fewer than 4 decimals"
    if not any(d >= 4 for d in decimals):
        return False, "no number with at least 4 decimals"
    return True, "at least 4 decimals"


def check_numeric_format(
    entries: Mapping[str, Mapping[str, Any]], rubrics: RubricSet
) -> CheckResult:
    """Report-only: calculated values use >= 4 decimals or say not derivable."""
    name = "numeric_format"
    results: dict[str, str] = {}
    failures: list[str] = []
    for q in rubrics.questions:
        if q.question.question_type != "derivation_required":
            continue
        entry = entries.get(q.question.id)
        value = None
        if entry is not None:
            dims = (entry.get("output") or {}).get("dimensions") or {}
            value = dims.get("calculated_value") if isinstance(dims, dict) else None
        ok, why = _numeric_ok(value)
        results[q.question.id] = why
        if not ok:
            failures.append(q.question.id)
    if not results:
        return _check(name, "pass", "no derivation questions")
    if failures:
        return _check(
            name,
            "fail",
            f"calculated_value formatting issues in {failures}",
            per_question=results,
        )
    return _check(name, "pass", "all calculated values formatted", per_question=results)


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def _is_shell_tool(name: str) -> bool:
    lowered = name.lower()
    return any(hint in lowered for hint in _SHELL_TOOL_HINTS)


def scan_trajectory(trajectory: Mapping[str, Any]) -> list[NetworkViolation]:
    """Find web tool use, URLs in tool arguments, and network shell commands."""
    violations: list[NetworkViolation] = []
    steps = trajectory.get("steps")
    if not isinstance(steps, list):
        raise TypeError("trajectory has no `steps` list")
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        for call in step.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            tool = str(call.get("function_name") or call.get("name") or "")
            args = call.get("arguments")
            texts = _strings(args)

            def add(
                reason: str,
                excerpt: str,
                *,
                _tool: str = tool,
                _index: int = index,
                _step_id: int | str | None = step.get("step_id"),
            ) -> None:
                violations.append(
                    NetworkViolation(
                        step_index=_index,
                        step_id=_step_id,
                        tool_name=_tool,
                        reason=reason,
                        excerpt=excerpt[:300],
                    )
                )

            if tool.lower() in WEB_TOOL_NAMES:
                add("web tool call", json.dumps(args, ensure_ascii=False)[:300])
                continue
            urls = [m.group(0) for t in texts for m in _URL.finditer(t)]
            if urls:
                add("URL in tool arguments", ", ".join(sorted(set(urls)))[:300])
            if _is_shell_tool(tool):
                for text in texts:
                    for label, pattern in NETWORK_COMMAND_PATTERNS:
                        match = pattern.search(text)
                        if match:
                            start = max(0, match.start() - 60)
                            add(
                                f"network command: {label}",
                                text[start : match.end() + 120],
                            )
    return violations


def check_trajectory(
    path: Path | None,
) -> tuple[CheckResult, list[NetworkViolation], str | None]:
    """Scan the ATIF trajectory. A missing or unreadable trajectory is `error`."""
    name = "network"
    if path is None or not path.is_file():
        where = str(path) if path is not None else "(none given)"
        return (
            _check(
                name,
                "error",
                f"agent trajectory not found at {where}; "
                "closed-book compliance cannot be verified",
            ),
            [],
            None,
        )
    try:
        trajectory = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(trajectory, dict):
            raise TypeError("trajectory must be a JSON object")
        violations = scan_trajectory(trajectory)
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        return (
            _check(name, "error", f"cannot scan trajectory: {exc}"),
            [],
            sha256_file(path) if path.is_file() else None,
        )
    digest = sha256_file(path)
    n_steps = len(trajectory.get("steps") or [])
    if violations:
        return (
            _check(
                name,
                "fail",
                f"{len(violations)} network violation(s) in trajectory",
                n_steps=n_steps,
            ),
            violations,
            digest,
        )
    return (
        _check(
            name,
            "pass",
            f"no network use found in {n_steps} trajectory steps",
            n_steps=n_steps,
        ),
        [],
        digest,
    )


# ---------------------------------------------------------------------------
# Rubric judging
# ---------------------------------------------------------------------------


def _excluded(q: QuestionRubrics) -> list[CriterionResult]:
    return [
        CriterionResult(
            criterion_id=c.criterion_id,
            question_id=q.question.id,
            dimension=r.dimension,
            importance=c.importance,
            scoring=c.scoring,
            verdict="unclear",
            rationale=f"scoring {c.scoring!r} is not supported; excluded from the "
            "score and not judged",
        )
        for r, c in q.criteria()
        if c.scoring != SCORED_KIND
    ]


def grade_question(
    q: QuestionRubrics,
    entry: Mapping[str, Any] | None,
    output_r: str | None,
    judge: Judge,
    config: ScoringConfig,
) -> tuple[QuestionGrade, list[dict[str, Any]]]:
    """Judge one question's `Add` criteria; judge failures become `error` verdicts."""
    question = q.question
    scored = [(r, c) for r, c in q.criteria() if c.scoring == SCORED_KIND]
    excluded = _excluded(q)
    warnings = (
        [
            (
                f"{question.id}: {len(excluded)} criterion(s) with unsupported scoring "
                f"{sorted({c.scoring for c in excluded})} excluded from the score"
            )
        ]
        if excluded
        else []
    )
    exchanges: list[dict[str, Any]] = []
    if not scored:
        warnings.append(f"{question.id}: no `Add` criteria; question is unscorable")
        grade = QuestionGrade(
            question_id=question.id,
            question_type=question.question_type,
            design_element=question.design_element,
            status="unscorable",
            score=None,
            earned_weight=0.0,
            possible_weight=0.0,
            counts={"pass": 0, "fail": 0, "unclear": 0, "error": 0},
            criteria=(),
            excluded_criteria=tuple(excluded),
            warnings=tuple(warnings),
        )
        return grade, exchanges
    if entry is None:
        results = [
            CriterionResult(
                criterion_id=c.criterion_id,
                question_id=question.id,
                dimension=r.dimension,
                importance=c.importance,
                scoring=c.scoring,
                verdict="fail",
                rationale=f"no answer for {question.id} in {OUTPUT_JSON}",
            )
            for r, c in scored
        ]
    else:
        artifacts = JudgeArtifacts(output_entry=entry, output_r=output_r)
        try:
            output = judge.judge(question, scored, artifacts)
            results = list(output.results)
            exchanges = list(output.exchanges)
            got = [r.criterion_id for r in results]
            want = [c.criterion_id for _, c in scored]
            if sorted(got) != sorted(want):
                raise RuntimeError(
                    f"judge returned criteria {sorted(got)}, expected {sorted(want)}"
                )
        except Exception as exc:  # noqa: BLE001 - every judge failure must be recorded
            results = error_results(
                question, scored, f"judge error: {type(exc).__name__}: {exc}"
            )
            exchanges.append({"error": f"{type(exc).__name__}: {exc}"})
    order = {c.criterion_id: i for i, (_, c) in enumerate(scored)}
    results.sort(key=lambda r: order[r.criterion_id])
    s = score_question(results, config)
    status = "error" if s.counts["error"] else "scored"
    if s.counts["error"]:
        warnings.append(
            f"{question.id}: {s.counts['error']} criterion(s) could not be judged"
        )
    grade = QuestionGrade(
        question_id=question.id,
        question_type=question.question_type,
        design_element=question.design_element,
        status=status,
        score=s.score,
        earned_weight=s.earned,
        possible_weight=s.possible,
        counts=s.counts,
        criteria=tuple(results),
        excluded_criteria=tuple(excluded),
        warnings=tuple(warnings),
    )
    return grade, exchanges


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass
class GradeRun:
    grade: TaskGrade
    rscript: RscriptResult | None
    judge_exchanges: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    rubrics: RubricSet | None = None


def grade_submission(
    submission_dir: Path,
    rubrics: RubricSet,
    judge: Judge,
    *,
    trajectory_path: Path | None = None,
    run_rscript: RunRscript = default_run_rscript,
    rscript_timeout_sec: float = DEFAULT_RSCRIPT_TIMEOUT_SEC,
    scoring: ScoringConfig | None = None,
    rubrics_sha256: str = "",
) -> GradeRun:
    """Grade a submission directory in memory. Pure: writes nothing."""
    config = scoring or ScoringConfig()
    submission_dir = submission_dir.resolve()
    json_check, entries = check_output_json(submission_dir / OUTPUT_JSON, rubrics)
    r_check, rscript = check_output_r(submission_dir, run_rscript, rscript_timeout_sec)
    fmt_check = check_numeric_format(entries, rubrics)
    net_check, violations, traj_digest = check_trajectory(trajectory_path)
    checks = [json_check, r_check, fmt_check, net_check]

    r_path = submission_dir / OUTPUT_R
    output_r = (
        r_path.read_text(encoding="utf-8", errors="replace")
        if r_path.is_file()
        else None
    )
    questions: list[QuestionGrade] = []
    exchanges: dict[str, list[dict[str, Any]]] = {}
    warnings: list[str] = []
    for q in rubrics.questions:
        qgrade, qex = grade_question(
            q, entries.get(q.question.id), output_r, judge, config
        )
        questions.append(qgrade)
        exchanges[q.question.id] = qex
        warnings.extend(qgrade.warnings)

    rubric = task_rubric_score(questions)
    zero_reasons = [
        f"{c.name}: {c.status}: {c.message}"
        for c in checks
        if c.blocking and c.status != "pass"
    ]
    judge_errors = sum(q.counts["error"] for q in questions)
    if judge_errors:
        zero_reasons.append(
            f"judge: error: {judge_errors} criterion(s) could not be judged"
        )
    if rubric is None:
        zero_reasons.append("rubric: error: no scorable questions")
    has_error = (
        any(c.status == "error" for c in checks if c.blocking)
        or judge_errors > 0
        or rubric is None
    )
    status = "error" if has_error else ("zeroed" if zero_reasons else "ok")
    passed = sum(c.status == "pass" for c in checks)

    json_path = submission_dir / OUTPUT_JSON
    grade = TaskGrade(
        scoring_version=config.version,
        package_version=package_version(),
        task_id=rubrics.task_id,
        trial_id=rubrics.trial_id,
        status=status,
        score=reward_score(rubric, zero_reasons),
        rubric_score=rubric,
        deterministic_pass_fraction=passed / len(checks),
        zero_reasons=tuple(zero_reasons),
        checks=tuple(checks),
        network_violations=tuple(violations),
        questions=tuple(questions),
        by_dimension=dimension_means(questions, config),
        by_design_element=group_means(questions, "design_element"),
        by_question_type=group_means(questions, "question_type"),
        warnings=tuple(warnings),
        judge=judge.info(),
        submission=Submission(
            directory=str(submission_dir),
            output_json_sha256=sha256_file(json_path) if json_path.is_file() else None,
            output_r_sha256=sha256_file(r_path) if r_path.is_file() else None,
            trajectory_path=str(trajectory_path) if trajectory_path else None,
            trajectory_sha256=traj_digest,
        ),
        rubrics_sha256=rubrics_sha256,
        graded_at=utc_now(),
    )
    return GradeRun(
        grade=grade, rscript=rscript, judge_exchanges=exchanges, rubrics=rubrics
    )


def reward_json(grade: TaskGrade) -> dict[str, float]:
    """Harbor reads `reward` as the headline metric; all values are numeric."""
    return {
        "reward": grade.score,
        "score": grade.score,
        "rubric": grade.rubric_score if grade.rubric_score is not None else 0.0,
        "deterministic": grade.deterministic_pass_fraction,
    }


def reward_details(
    run: GradeRun, config: ScoringConfig | None = None
) -> dict[str, Any]:
    """Per-criterion tree in the format `harbor view` renders."""
    config = config or ScoringConfig()
    grade = run.grade
    texts: dict[str, str] = {}
    if run.rubrics is not None:
        texts = {
            c.criterion_id: c.criterion
            for q in run.rubrics.questions
            for _, c in q.criteria()
        }
    details: dict[str, Any] = {
        "deterministic": {
            "kind": "deterministic",
            "score": grade.deterministic_pass_fraction,
            "criteria": [
                {
                    "name": c.name,
                    "description": c.message,
                    "value": 1.0 if c.status == "pass" else 0.0,
                    "raw": c.status,
                    "weight": 1.0,
                    **({"error": c.message} if c.status == "error" else {}),
                }
                for c in grade.checks
            ],
            "warnings": list(grade.zero_reasons),
        }
    }
    for q in grade.questions:
        details[q.question_id] = {
            "kind": "llm_judge",
            "score": q.score if q.score is not None else 0.0,
            "criteria": [
                {
                    "name": r.criterion_id,
                    "description": texts.get(r.criterion_id, r.criterion_id),
                    "value": 1.0 if r.verdict == "pass" else 0.0,
                    "raw": r.verdict,
                    "weight": config.weight(r.importance),
                    "reasoning": r.rationale,
                    **({"error": r.rationale} if r.verdict == "error" else {}),
                }
                for r in q.criteria
            ],
            "judge": {"model": grade.judge.model or grade.judge.name},
            "warnings": [f"status: {q.status}", *q.warnings],
        }
    return details


def write_outputs(run: GradeRun, out_dir: Path) -> None:
    """Write grade.json, reward.json, reward-details.json, Rscript logs, judge logs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    grade = run.grade
    (out_dir / "grade.json").write_text(
        grade.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "reward.json").write_text(
        json.dumps(reward_json(grade), indent=2) + "\n"
    )
    (out_dir / "reward-details.json").write_text(
        json.dumps(reward_details(run), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rscript = run.rscript
    (out_dir / "rscript-stdout.txt").write_text(
        rscript.stdout if rscript else "", encoding="utf-8"
    )
    (out_dir / "rscript-stderr.txt").write_text(
        (rscript.stderr if rscript else "")
        + (f"\n[tdb] {rscript.error}\n" if rscript and rscript.error else ""),
        encoding="utf-8",
    )
    judge_dir = out_dir / "judge"
    judge_dir.mkdir(exist_ok=True)
    info = grade.judge.model_dump(mode="json")
    for qid, exchanges in run.judge_exchanges.items():
        (judge_dir / f"{qid}.json").write_text(
            json.dumps(
                {"question_id": qid, "judge": info, "exchanges": exchanges},
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )


def resolve_trajectory(submission_dir: Path, explicit: Path | None) -> Path | None:
    """Explicit path, else `<submission>/trajectory.json`, else Harbor's default."""
    if explicit is not None:
        return explicit
    local = submission_dir / TRAJECTORY_JSON
    if local.is_file():
        return local
    return DEFAULT_TRAJECTORY_PATH


def grade_directory(
    submission_dir: Path,
    rubrics_path: Path,
    out_dir: Path,
    judge: Judge,
    *,
    trajectory_path: Path | None = None,
    run_rscript: RunRscript = default_run_rscript,
    rscript_timeout_sec: float = DEFAULT_RSCRIPT_TIMEOUT_SEC,
    scoring: ScoringConfig | None = None,
) -> GradeRun:
    """Load rubrics, grade, and write all outputs to `out_dir`."""
    from trialdesignbench.dataset import load_rubrics

    rubrics = load_rubrics(rubrics_path)
    run = grade_submission(
        submission_dir,
        rubrics,
        judge,
        trajectory_path=resolve_trajectory(submission_dir, trajectory_path),
        run_rscript=run_rscript,
        rscript_timeout_sec=rscript_timeout_sec,
        scoring=scoring,
        rubrics_sha256=sha256_file(rubrics_path),
    )
    write_outputs(run, out_dir)
    return run


def blocking_failures(checks: Sequence[CheckResult]) -> list[CheckResult]:
    """Checks that gate the reward and did not pass."""
    return [c for c in checks if c.blocking and c.status != "pass"]
