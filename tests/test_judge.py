from __future__ import annotations

import os

import pytest

from trialdesignbench.judge import (
    AnthropicJudge,
    FakeJudge,
    JudgeArtifacts,
    build_user_message,
    judge_prompt_sha256,
    supports_temperature,
)
from trialdesignbench.schema import RubricSet


def test_user_message_scoped_to_one_question(rubrics: RubricSet) -> None:
    q = next(
        q
        for q in rubrics.questions
        if q.question.question_type == "derivation_required"
    )
    other = next(q2 for q2 in rubrics.questions if q2.question.id != q.question.id)
    msg = build_user_message(
        q.question, q.criteria(), JudgeArtifacts({"id": q.question.id}, "cat(1)")
    )
    assert q.question.question in msg
    assert "cat(1)" in msg  # full output.R for derivation questions
    for _, c in q.criteria():
        assert c.criterion in msg
    for _, c in other.criteria():
        assert c.criterion not in msg


def test_extraction_message_omits_r_code(rubrics: RubricSet) -> None:
    q = rubrics.questions[0]
    msg = build_user_message(
        q.question, q.criteria(), JudgeArtifacts({"id": "P-001"}, "SECRET")
    )
    assert "SECRET" not in msg


@pytest.mark.parametrize(
    ("model", "temperature"),
    [
        ("claude-fable-5-1", None),
        ("claude-mythos-5-1", None),
        ("claude-opus-4-6", None),
        ("claude-opus-4-7", None),
        ("claude-opus-4-8", None),
        ("claude-opus-5", None),
        ("claude-opus-5-5", None),
        ("claude-opus-future", None),
        ("claude-sonnet-4-6", None),
        ("claude-sonnet-5", None),
        ("claude-sonnet-5-5", None),
        ("claude-sonnet-future", None),
        ("claude-haiku-4-5", 0.0),
    ],
)
def test_judge_info_matches_request(
    rubrics: RubricSet, model: str, temperature: float | None
) -> None:
    judge = AnthropicJudge(model, votes=3)
    info = judge.info()
    assert info.prompt_sha256 == judge_prompt_sha256()
    assert info.model == model and info.votes == 3
    assert info.temperature == temperature
    assert supports_temperature(model) is (temperature is not None)

    q = rubrics.questions[0]
    request = judge._request(q.question, q.criteria(), JudgeArtifacts({}, None))
    if temperature is None:
        assert "temperature" not in request
    else:
        assert request["temperature"] == temperature


def test_fake_judge_rule(rubrics: RubricSet) -> None:
    q = rubrics.questions[0]
    judge = FakeJudge(rule=lambda c, a: "pass" if c.importance == "High" else "fail")
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert [r.verdict for r in out.results] == [
        "pass" if c.importance == "High" else "fail" for _, c in q.criteria()
    ]


@pytest.mark.skipif(
    not (
        os.environ.get("TDB_LIVE_JUDGE_TESTS") and os.environ.get("ANTHROPIC_API_KEY")
    ),
    reason="live API test; set TDB_LIVE_JUDGE_TESTS=1 and ANTHROPIC_API_KEY",
)
def test_live_anthropic_judge(rubrics: RubricSet) -> None:
    q = rubrics.questions[0]  # primary endpoint question
    good = {
        **q.question.skeleton(),
        "output": {
            "extracted_value": "Primary endpoint: overall survival (OS), defined as time from "
            "randomization to death due to any cause, tested in the ITT population (Section 9.2)."
        },
    }
    judge = AnthropicJudge(os.environ.get("TDB_JUDGE_MODEL"))
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts(good, None))
    assert [r.verdict for r in out.results] == ["pass"] * len(q.criteria())
    assert all(r.rationale for r in out.results)
