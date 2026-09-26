from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    failing_rscript,
    filled_output,
    missing_rscript,
    ok_rscript,
    requires_rscript,
    trajectory,
)
from trialdesignbench.grade import (
    default_run_rscript,
    grade_directory,
    grade_submission,
    scan_trajectory,
    write_outputs,
)
from trialdesignbench.judge import FakeJudge, JudgeArtifacts, JudgeOutput
from trialdesignbench.schema import Criterion, Question, Rubric, RubricSet, TaskGrade


def check(grade: TaskGrade, name: str) -> Any:
    return next(c for c in grade.checks if c.name == name)


def grade(sub: Path, rubrics: RubricSet, **kw: Any) -> TaskGrade:
    kw.setdefault("run_rscript", ok_rscript)
    kw.setdefault("trajectory_path", sub / "trajectory.json")
    judge = kw.pop("judge", FakeJudge(default="pass"))
    return grade_submission(sub, rubrics, judge, **kw).grade


def test_perfect_submission(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    g = grade(make_submission(), rubrics)
    assert g.status == "ok"
    assert [c.status for c in g.checks] == ["pass"] * 4
    assert g.score == 1.0 and g.rubric_score == 1.0
    assert g.deterministic_pass_fraction == 1.0
    assert g.zero_reasons == ()


def test_partial_rubric_score(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    first = rubrics.questions[0]
    ids = [c.criterion_id for _, c in first.criteria()]
    judge = FakeJudge({ids[0]: "fail"}, default="pass")
    g = grade(make_submission(), rubrics, judge=judge)
    # P-001: High fail, High pass, Medium pass -> 5/8; the other 7 questions 1.0.
    assert g.questions[0].score == pytest.approx(5 / 8)
    assert g.rubric_score == pytest.approx((5 / 8 + 7) / 8)
    assert g.score == g.rubric_score


def _mutate(
    rubrics: RubricSet, fn: Callable[[list[dict[str, Any]]], None]
) -> dict[str, Any]:
    data = filled_output(rubrics)
    fn(data["output"])
    return data


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda out: out.pop(0), "missing ids ['P-001']"),
        (lambda out: out[0].__setitem__("id", "P-999"), "unexpected ids ['P-999']"),
        (
            lambda out: out[0].__setitem__("confidence", "high"),
            "added fields ['confidence']",
        ),
        (lambda out: out[0].pop("design_element"), "removed fields ['design_element']"),
        (
            lambda out: out[0]["output"].__setitem__("extracted_value", None),
            "still null",
        ),
        (
            lambda out: out[3]["output"]["dimensions"].__setitem__("method", "  "),
            "still null",
        ),
        (lambda out: out[0].__setitem__("question", "edited"), "modified"),
        (lambda out: out.append(dict(out[0])), "duplicate ids"),
    ],
)
def test_output_json_schema_violations(
    make_submission: Callable[..., Path],
    rubrics: RubricSet,
    mutation: Callable[[list[dict[str, Any]]], None],
    expected: str,
) -> None:
    g = grade(make_submission(output=_mutate(rubrics, mutation)), rubrics)
    c = check(g, "output_json")
    assert c.status == "fail"
    assert any(expected in p for p in c.details["problems"]), c.details
    assert g.score == 0.0
    assert g.status == "zeroed"
    assert g.rubric_score is not None  # raw rubric score kept regardless


def test_not_derivable_string_counts_as_filled(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    def mutate(out: list[dict[str, Any]]) -> None:
        out[3]["output"]["dimensions"]["calculated_value"] = (
            "Not derivable from the document"
        )

    g = grade(make_submission(output=_mutate(rubrics, mutate)), rubrics)
    assert check(g, "output_json").status == "pass"
    assert check(g, "numeric_format").status == "pass"


@pytest.mark.parametrize(
    "content", ["{not json", "[]", '{"answers": []}', '{"output": {}}']
)
def test_invalid_output_json(
    make_submission: Callable[..., Path], rubrics: RubricSet, content: str
) -> None:
    judge = FakeJudge(default="pass")
    g = grade(make_submission(output=content), rubrics, judge=judge)
    assert check(g, "output_json").status == "fail"
    assert g.score == 0.0
    # No answers means nothing to judge: every criterion fails, no judge calls.
    assert judge.calls == []
    assert g.rubric_score == 0.0


def test_missing_output_json(tmp_path: Path, rubrics: RubricSet) -> None:
    sub = tmp_path / "empty"
    sub.mkdir()
    g = grade(sub, rubrics)
    assert check(g, "output_json").status == "fail"
    assert check(g, "output_r").status == "fail"
    assert check(g, "network").status == "error"
    assert g.status == "error" and g.score == 0.0


def test_rscript_nonzero_exit_zeroes_score(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    g = grade(make_submission(), rubrics, run_rscript=failing_rscript)
    c = check(g, "output_r")
    assert c.status == "fail" and c.details["returncode"] == 1
    assert g.score == 0.0 and g.rubric_score == 1.0
    assert any(r.startswith("output_r: fail") for r in g.zero_reasons)


def test_missing_r_runtime_is_error_not_pass(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    g = grade(make_submission(), rubrics, run_rscript=missing_rscript)
    assert check(g, "output_r").status == "error"
    assert g.status == "error" and g.score == 0.0


def test_rscript_runner_crash_is_error(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    def boom(script: Path, cwd: Path, timeout: float) -> Any:
        raise RuntimeError("sandbox exploded")

    g = grade(make_submission(), rubrics, run_rscript=boom)
    assert check(g, "output_r").status == "error"
    assert g.score == 0.0


def test_missing_output_r(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    g = grade(make_submission(r_code=None), rubrics)
    assert check(g, "output_r").status == "fail"
    assert g.score == 0.0


def test_rscript_runs_in_scratch_copy(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    sub = make_submission()
    seen: dict[str, Path] = {}

    def spy(script: Path, cwd: Path, timeout: float) -> Any:
        seen["cwd"] = cwd
        (cwd / "output.json").write_text("tampered")
        return ok_rscript(script, cwd, timeout)

    grade(sub, rubrics, run_rscript=spy)
    assert seen["cwd"] != sub
    assert json.loads((sub / "output.json").read_text())["output"]


def test_numeric_format_is_report_only(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    def mutate(out: list[dict[str, Any]]) -> None:
        out[3]["output"]["dimensions"]["calculated_value"] = "about 400.5 deaths"

    g = grade(make_submission(output=_mutate(rubrics, mutate)), rubrics)
    c = check(g, "numeric_format")
    assert c.status == "fail" and not c.blocking
    assert g.score == 1.0 and g.status == "ok"
    assert g.deterministic_pass_fraction == 0.75


def test_missing_trajectory_is_error(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    sub = make_submission(traj=False)
    g = grade(sub, rubrics)
    assert check(g, "network").status == "error"
    assert g.status == "error" and g.score == 0.0


@pytest.mark.parametrize(
    ("call", "reason"),
    [
        (
            {"function_name": "WebSearch", "arguments": {"query": "KEYNOTE trial"}},
            "web tool call",
        ),
        (
            {
                "function_name": "web_search_call",
                "arguments": {"action_type": "search"},
            },
            "web tool",
        ),
        ({"function_name": "WebFetch", "arguments": {"url": "x"}}, "web tool call"),
        ({"function_name": "Read", "arguments": {"path": "https://nejm.org/x"}}, "URL"),
        (
            {"function_name": "Bash", "arguments": {"command": "curl -s example"}},
            "curl",
        ),
        ({"function_name": "exec_command", "arguments": {"cmd": "wget x"}}, "wget"),
        (
            {
                "function_name": "Bash",
                "arguments": {"command": "Rscript -e 'install.packages(\"x\")'"},
            },
            "install.packages",
        ),
        (
            {"function_name": "Bash", "arguments": {"command": "pip install requests"}},
            "pip install",
        ),
        (
            {"function_name": "Bash", "arguments": {"command": "git clone repo"}},
            "git clone",
        ),
        (
            {
                "function_name": "Bash",
                "arguments": {"command": "Rscript -e 'download.file(u, f)'"},
            },
            "download.file",
        ),
    ],
)
def test_network_violations(
    make_submission: Callable[..., Path],
    rubrics: RubricSet,
    call: dict[str, Any],
    reason: str,
) -> None:
    call = {"tool_call_id": "x", **call}
    g = grade(make_submission(traj=trajectory([call])), rubrics)
    assert check(g, "network").status == "fail"
    assert g.network_violations and reason in g.network_violations[0].reason
    assert g.network_violations[0].step_index == 1
    assert g.score == 0.0 and g.rubric_score == 1.0


def test_clean_trajectory_has_no_violations() -> None:
    calls = [
        {
            "tool_call_id": "1",
            "function_name": "Bash",
            "arguments": {"command": "Rscript output.R"},
        },
        {
            "tool_call_id": "2",
            "function_name": "Write",
            "arguments": {"file_path": "/app/output.R", "content": "library(gsDesign)"},
        },
        {
            "tool_call_id": "3",
            "function_name": "Bash",
            "arguments": {"command": "grep -n alpha doc"},
        },
    ]
    assert scan_trajectory(trajectory(calls)) == []


def test_malformed_trajectory_is_error(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    g = grade(make_submission(traj={"no_steps": True}), rubrics)
    assert check(g, "network").status == "error"
    assert g.score == 0.0


def test_judge_exception_marks_errors_and_zeroes(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    class Broken(FakeJudge):
        def judge(
            self, question: Question, criteria: Any, artifacts: JudgeArtifacts
        ) -> JudgeOutput:
            if question.id == "P-002":
                raise TimeoutError("judge timed out")
            return super().judge(question, criteria, artifacts)

    g = grade(make_submission(), rubrics, judge=Broken(default="pass"))
    q2 = next(q for q in g.questions if q.question_id == "P-002")
    assert q2.status == "error"
    assert all(r.verdict == "error" for r in q2.criteria)
    assert g.status == "error" and g.score == 0.0
    assert any("judge" in r for r in g.zero_reasons)


def test_judge_returning_wrong_criteria_is_error(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    class Lossy(FakeJudge):
        def judge(
            self, question: Question, criteria: Any, artifacts: JudgeArtifacts
        ) -> JudgeOutput:
            out = super().judge(question, criteria, artifacts)
            return JudgeOutput(results=out.results[:-1])

    g = grade(make_submission(), rubrics, judge=Lossy(default="pass"))
    assert g.status == "error" and g.score == 0.0


def test_judge_sees_only_its_question(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    seen: list[tuple[str, set[str]]] = []

    class Spy(FakeJudge):
        def judge(
            self, question: Question, criteria: Any, artifacts: JudgeArtifacts
        ) -> JudgeOutput:
            seen.append(
                (question.id, {c.criterion_id.split("/")[0] for _, c in criteria})
            )
            assert artifacts.output_entry is not None
            assert artifacts.output_entry["id"] == question.id
            return super().judge(question, criteria, artifacts)

    grade(make_submission(), rubrics, judge=Spy(default="pass"))
    assert all(ids == {qid} for qid, ids in seen)


def test_unsupported_scoring_excluded_with_warning(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    q0 = rubrics.questions[0]
    deduct = Criterion(
        criterion_id="P-001/answer/99",
        criterion="Says PFS is primary",
        importance="High",
        scoring="Deduct",
    )
    rub = Rubric(
        artifact="output.json", dimension="", criteria=(*q0.rubrics[0].criteria, deduct)
    )
    patched = rubrics.model_copy(
        update={
            "questions": (
                q0.model_copy(update={"rubrics": (rub,)}),
                *rubrics.questions[1:],
            )
        }
    )
    judge = FakeJudge(default="pass")
    g = grade(make_submission(), patched, judge=judge)
    assert g.questions[0].score == 1.0
    assert [r.criterion_id for r in g.questions[0].excluded_criteria] == [
        "P-001/answer/99"
    ]
    assert any("Deduct" in w for w in g.warnings)


def test_output_files(
    make_submission: Callable[..., Path], rubrics_path: Path, tmp_path: Path
) -> None:
    sub = make_submission()
    out = tmp_path / "verifier"
    run = grade_directory(
        sub,
        rubrics_path,
        out,
        FakeJudge(default="pass"),
        trajectory_path=sub / "trajectory.json",
        run_rscript=ok_rscript,
    )
    for name in (
        "grade.json",
        "reward.json",
        "reward-details.json",
        "rscript-stdout.txt",
        "rscript-stderr.txt",
    ):
        assert (out / name).is_file(), name
    assert sorted(p.stem for p in (out / "judge").glob("*.json")) == [
        q.question_id for q in run.grade.questions
    ]
    reward = json.loads((out / "reward.json").read_text())
    assert reward == {"reward": 1.0, "score": 1.0, "rubric": 1.0, "deterministic": 1.0}
    assert all(isinstance(v, float) for v in reward.values())
    TaskGrade.model_validate_json((out / "grade.json").read_text())
    details = json.loads((out / "reward-details.json").read_text())
    assert details["deterministic"]["kind"] == "deterministic"
    p1 = details["P-001"]
    assert p1["criteria"][0]["description"].startswith("Correctly identifies OS")
    assert {"name", "value", "weight"} <= set(p1["criteria"][0])
    assert (out / "rscript-stdout.txt").read_text() == "result: 400\n"


def test_write_outputs_records_error_rscript(
    make_submission: Callable[..., Path], rubrics: RubricSet, tmp_path: Path
) -> None:
    sub = make_submission()
    run = grade_submission(
        sub,
        rubrics,
        FakeJudge(),
        run_rscript=missing_rscript,
        trajectory_path=sub / "trajectory.json",
    )
    write_outputs(run, tmp_path / "o")
    assert "Rscript not found" in (tmp_path / "o" / "rscript-stderr.txt").read_text()
    assert json.loads((tmp_path / "o" / "reward.json").read_text())["reward"] == 0.0


@requires_rscript
def test_real_rscript(make_submission: Callable[..., Path], rubrics: RubricSet) -> None:
    good = make_submission(
        r_code='f <- function(x) x * 2\ncat("value:", f(200), "\\n")\n'
    )
    g = grade(good, rubrics, run_rscript=default_run_rscript)
    assert check(g, "output_r").status == "pass"

    bad = make_submission(r_code="stop('boom')\n", name="bad")
    g = grade(bad, rubrics, run_rscript=default_run_rscript)
    assert check(g, "output_r").status == "fail"
    assert g.score == 0.0


@requires_rscript
def test_real_rscript_timeout(
    make_submission: Callable[..., Path], rubrics: RubricSet
) -> None:
    sub = make_submission(r_code="Sys.sleep(30)\n")
    g = grade(sub, rubrics, run_rscript=default_run_rscript, rscript_timeout_sec=1)
    c = check(g, "output_r")
    assert c.status == "fail" and c.details["timed_out"]
