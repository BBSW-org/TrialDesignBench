from __future__ import annotations

import pytest

from trialdesignbench.schema import CriterionResult, QuestionGrade, Verdict
from trialdesignbench.scoring import (
    ScoringConfig,
    dimension_means,
    majority_verdict,
    reward_score,
    score_question,
    task_rubric_score,
)


def cr(
    verdict: Verdict,
    importance: str = "High",
    scoring: str = "Add",
    dimension: str = "",
) -> CriterionResult:
    return CriterionResult(
        criterion_id=f"P-1/x/{id(object())}",
        question_id="P-1",
        dimension=dimension,
        importance=importance,  # type: ignore[arg-type]
        scoring=scoring,
        verdict=verdict,
        rationale="",
    )


def test_weighted_question_score() -> None:
    # High(3) pass, Medium(2) fail, Low(1) pass -> 4 / 6
    results = [cr("pass", "High"), cr("fail", "Medium"), cr("pass", "Low")]
    s = score_question(results)
    assert s.score == pytest.approx(4 / 6)
    assert (s.earned, s.possible) == (4.0, 6.0)


def test_unclear_and_error_count_zero_and_are_counted() -> None:
    results = [cr("pass", "Low"), cr("unclear", "Low"), cr("error", "Low")]
    s = score_question(results)
    assert s.score == pytest.approx(1 / 3)
    assert s.counts == {"pass": 1, "fail": 0, "unclear": 1, "error": 1}


def test_non_add_criteria_are_excluded() -> None:
    results = [cr("pass", "Medium"), cr("pass", "High", scoring="Deduct")]
    assert score_question(results).score == 1.0
    assert score_question([cr("pass", scoring="Deduct")]).score is None


def test_custom_weights() -> None:
    config = ScoringConfig(weights={"High": 10, "Medium": 1, "Low": 1})
    s = score_question([cr("pass", "High"), cr("fail", "Medium")], config)
    assert s.score == pytest.approx(10 / 11)


def qgrade(score: float | None, criteria: list[CriterionResult]) -> QuestionGrade:
    return QuestionGrade(
        question_id="P-1",
        question_type="derivation_required",
        design_element="Sample size and power",
        status="scored" if score is not None else "unscorable",
        score=score,
        earned_weight=0,
        possible_weight=0,
        counts={},
        criteria=tuple(criteria),
    )


def test_task_score_and_dimensions() -> None:
    q1 = qgrade(1.0, [cr("pass", dimension="Inputs used")])
    q2 = qgrade(
        0.0, [cr("fail", dimension="Method"), cr("fail", dimension="Inputs used")]
    )
    q3 = qgrade(None, [])
    assert task_rubric_score([q1, q2, q3]) == 0.5
    assert dimension_means([q1, q2]) == {"Inputs used": 0.5, "Method": 0.0}


def test_majority_vote() -> None:
    assert majority_verdict(["pass", "pass", "fail"]) == "pass"
    assert majority_verdict(["pass", "fail"]) == "unclear"
    assert majority_verdict(["pass", "error", "pass"]) == "error"
    assert majority_verdict([]) == "error"


def test_reward_gate() -> None:
    assert reward_score(0.7, []) == 0.7
    assert reward_score(0.7, ["output_r: fail"]) == 0.0
    assert reward_score(None, []) == 0.0
