from __future__ import annotations

import json
import os
import urllib.request

import pytest

from trialdesignbench.judge import (
    AnthropicJudge,
    FakeJudge,
    JudgeArtifacts,
    OpencodeGoJudge,
    build_user_message,
    judge_api_host,
    judge_key_env,
    judge_kind_for_model,
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
            "extracted_value": (
                "Primary endpoint: overall survival (OS), defined as time from "
                "randomization to death due to any cause, tested in the ITT "
                "population (Section 9.2)."
            )
        },
    }
    judge = AnthropicJudge(os.environ.get("TDB_JUDGE_MODEL"))
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts(good, None))
    assert [r.verdict for r in out.results] == ["pass"] * len(q.criteria())
    assert all(r.rationale for r in out.results)


def test_judge_kind_for_model() -> None:
    assert judge_kind_for_model("opencode-go/muse-spark-1.3-contributor") == (
        "opencode-go"
    )
    assert judge_kind_for_model("claude-opus-5-5") == "anthropic"
    assert judge_kind_for_model(None) == "anthropic"
    assert judge_key_env("opencode-go") == "OPENCODE_API_KEY"
    assert judge_key_env("anthropic") == "ANTHROPIC_API_KEY"
    assert judge_api_host("opencode-go") == "opencode.ai"
    assert judge_api_host("anthropic") == "api.anthropic.com"


def _go_results_text(ids: list[str], verdict: str = "pass") -> str:
    return json.dumps(
        {
            "results": [
                {
                    "criterion_id": cid,
                    "verdict": verdict,
                    "rationale": "matches",
                    "evidence": "quoted",
                }
                for cid in ids
            ]
        }
    )


def _go_payload(text: str, status: str = "completed") -> dict:
    return {
        "id": "resp-test",
        "model": "muse-spark-1.3-contributor",
        "status": status,
        "output": [
            {"type": "reasoning", "id": "r"},
            {
                "type": "message",
                "id": "m",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
    }


def test_opencode_go_judge_success(rubrics: RubricSet) -> None:
    q = rubrics.questions[0]
    ids = [c.criterion_id for _, c in q.criteria()]
    bodies: list[dict] = []

    def fake_call(body: dict) -> dict:
        bodies.append(body)
        return _go_payload(_go_results_text(ids))

    judge = OpencodeGoJudge(
        "opencode-go/muse-spark-1.3-contributor",
        votes=1,
        sleep=lambda seconds: None,
        call=fake_call,
    )
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert [r.verdict for r in out.results] == ["pass"] * len(ids)
    assert all(r.rationale == "matches" and r.evidence == "quoted" for r in out.results)
    assert all(r.votes == ("pass",) for r in out.results)
    assert all(r.raw_response_ref == f"judge/{q.question.id}.json" for r in out.results)
    info = judge.info()
    assert info.name == "opencode-go"
    assert info.model == "opencode-go/muse-spark-1.3-contributor"
    assert info.prompt_sha256 == judge_prompt_sha256()
    assert info.sdk_version is None and info.temperature is None
    (body,) = bodies
    assert body["model"] == "muse-spark-1.3-contributor"  # prefix stripped for the API
    assert body["text"]["format"]["type"] == "json_schema"
    assert q.question.question in body["input"]


def test_opencode_go_judge_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TDB_JUDGE_MODEL", raising=False)
    assert OpencodeGoJudge().model == "muse-spark-1.3-contributor"
    monkeypatch.setenv("TDB_JUDGE_MODEL", "opencode-go/other-model")
    assert OpencodeGoJudge().model == "opencode-go/other-model"


def test_opencode_go_judge_retries_then_errors(rubrics: RubricSet) -> None:
    q = rubrics.questions[0]
    ids = [c.criterion_id for _, c in q.criteria()]
    calls = []

    def fake_call(body: dict) -> dict:
        calls.append(body)
        return _go_payload(json.dumps({"results": []}))  # missing criteria

    judge = OpencodeGoJudge(
        "opencode-go/muse-spark-1.3-contributor",
        max_retries=1,
        sleep=lambda seconds: None,
        call=fake_call,
    )
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert len(calls) == 2  # initial attempt plus one retry
    assert [r.verdict for r in out.results] == ["error"] * len(ids)
    assert all("judge failed" in r.rationale for r in out.results)


def test_opencode_go_judge_incomplete_is_retryable(rubrics: RubricSet) -> None:
    q = rubrics.questions[0]
    ids = [c.criterion_id for _, c in q.criteria()]
    responses = [
        _go_payload("", status="incomplete"),
        _go_payload(_go_results_text(ids)),
    ]
    judge = OpencodeGoJudge(
        "opencode-go/muse-spark-1.3-contributor",
        sleep=lambda seconds: None,
        call=lambda body: responses.pop(0),
    )
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert [r.verdict for r in out.results] == ["pass"] * len(ids)
    assert out.exchanges[1]["error"] == "judge response status 'incomplete'"


def test_opencode_go_judge_missing_key(
    rubrics: RubricSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    q = rubrics.questions[0]
    judge = OpencodeGoJudge(
        "opencode-go/muse-spark-1.3-contributor", sleep=lambda seconds: None
    )
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert [r.verdict for r in out.results] == ["error"] * len(q.criteria())
    assert "OPENCODE_API_KEY" in out.results[0].rationale


def test_opencode_go_post_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-test")
    seen: dict = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"[]"

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    judge = OpencodeGoJudge("opencode-go/muse-spark-1.3-contributor", session_id="s")
    with pytest.raises(Exception, match="not a JSON object"):
        judge._post({"model": "muse-spark-1.3-contributor"})
    assert seen["url"] == "https://opencode.ai/zen/go/v1/responses"
    headers = {k.lower(): v for k, v in seen["headers"].items()}
    assert headers["authorization"] == "Bearer sk-test"
    assert headers["x-opencode-session"] == "s"
    assert "trialdesignbench/" in headers["user-agent"]
    assert "python-urllib" not in headers["user-agent"]
    assert seen["timeout"] == 120.0
