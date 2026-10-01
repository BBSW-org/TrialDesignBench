from __future__ import annotations

import io
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import pytest

from trialdesignbench.judge import (
    JUDGE_BACKENDS,
    AnthropicJudge,
    FakeJudge,
    JudgeArtifacts,
    OpencodeGoJudge,
    build_user_message,
    judge_backend,
    judge_prompt_sha256,
    make_judge,
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
    q = rubrics.questions[0]
    judge = AnthropicJudge(os.environ.get("TDB_JUDGE_MODEL"))
    out = judge.judge(q.question, q.criteria(), _good_answer(rubrics))
    assert [r.verdict for r in out.results] == ["pass"] * len(q.criteria())
    assert all(r.rationale for r in out.results)


@pytest.mark.skipif(
    not (os.environ.get("TDB_LIVE_JUDGE_TESTS") and os.environ.get("OPENCODE_API_KEY")),
    reason="live API test; set TDB_LIVE_JUDGE_TESTS=1 and OPENCODE_API_KEY",
)
@pytest.mark.parametrize(
    ("model", "protocol"),
    [("opencode-go/kimi-k3", "chat"), ("opencode-go/grok-4.7", "responses")],
)
def test_live_opencode_go_judge(rubrics: RubricSet, model: str, protocol: str) -> None:
    q = rubrics.questions[0]
    judge = OpencodeGoJudge(model)
    out = judge.judge(q.question, q.criteria(), _good_answer(rubrics))
    assert [r.verdict for r in out.results] == ["pass"] * len(q.criteria())
    assert all(r.rationale for r in out.results)
    assert judge.protocol == protocol


def _good_answer(rubrics: RubricSet) -> JudgeArtifacts:
    """A correct answer to the first (primary endpoint) question."""
    q = rubrics.questions[0]
    return JudgeArtifacts(
        {
            **q.question.skeleton(),
            "output": {
                "extracted_value": (
                    "Primary endpoint: overall survival (OS), defined as time "
                    "from randomization to death due to any cause, tested in "
                    "the ITT population (Section 9.2)."
                )
            },
        },
        None,
    )


def test_judge_backend_from_model(monkeypatch: pytest.MonkeyPatch) -> None:
    assert list(JUDGE_BACKENDS) == ["anthropic", "opencode-go"]
    anthropic = judge_backend("claude-opus-5-5")
    assert (anthropic.name, anthropic.key_env, anthropic.api_host) == (
        "anthropic",
        "ANTHROPIC_API_KEY",
        "api.anthropic.com",
    )
    go = judge_backend("opencode-go/muse-spark-1.3-contributor")
    assert (go.name, go.key_env, go.api_host) == (
        "opencode-go",
        "OPENCODE_API_KEY",
        "opencode.ai",
    )
    assert go.model_id("opencode-go/muse-spark-1.3-contributor") == (
        "muse-spark-1.3-contributor"
    )
    with pytest.raises(ValueError, match="no known backend"):
        judge_backend("anthropic/claude-opus-5-5")
    # Each judge accepts only its own backend's models.
    with pytest.raises(ValueError, match="not a model of the anthropic judge"):
        AnthropicJudge("opencode-go/x")
    with pytest.raises(ValueError, match="not a model of the opencode-go judge"):
        OpencodeGoJudge("claude-opus-5-5")

    monkeypatch.delenv("TDB_JUDGE_MODEL", raising=False)
    assert isinstance(make_judge(), AnthropicJudge)
    assert make_judge().model == "claude-opus-5-5"
    go_judge = make_judge(backend="opencode-go", votes=3)
    assert isinstance(go_judge, OpencodeGoJudge)
    assert go_judge.model == "opencode-go/muse-spark-1.3-contributor"
    assert go_judge.votes == 3
    monkeypatch.setenv("TDB_JUDGE_MODEL", "opencode-go/kimi-k3")
    assert make_judge().model == "opencode-go/kimi-k3"
    assert make_judge(backend="opencode-go").model == "opencode-go/kimi-k3"
    with pytest.raises(ValueError, match="not a model of the anthropic judge"):
        make_judge(backend="anthropic")
    with pytest.raises(ValueError, match="unknown judge backend"):
        make_judge(backend="openai")


def _results_text(ids: list[str], verdict: str = "pass") -> str:
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


def _chat_payload(text: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "model": "kimi-k3",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


def _responses_payload(text: str, status: str = "completed") -> dict[str, Any]:
    return {
        "id": "resp-test",
        "model": "muse-spark-1.3-contributor",
        "status": status,
        "incomplete_details": None if status == "completed" else {"reason": status},
        "output": [
            {"type": "reasoning", "id": "r", "encrypted_content": "..."},
            {
                "type": "message",
                "id": "m",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
    }


def _gateway_error(error_type: str, message: str) -> dict[str, Any]:
    return {"type": "error", "error": {"type": error_type, "message": message}}


Handler = Callable[[str, dict[str, Any]], tuple[int, dict[str, Any]]]


class FakeGateway:
    """Stands in for `urllib.request.urlopen` in front of the Go gateway."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> io.BytesIO:
        path = request.full_url.removeprefix(OpencodeGoJudge.base_url)
        assert isinstance(request.data, bytes)
        body = json.loads(request.data)
        headers = {k.lower(): v for k, v in request.header_items()}
        self.requests.append((path, body, headers))
        status, payload = self.handler(path, body)
        data = io.BytesIO(json.dumps(payload).encode())
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", None, data)  # type: ignore[arg-type]
        return data

    @property
    def paths(self) -> list[str]:
        return [path for path, _, _ in self.requests]


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], FakeGateway]:
    monkeypatch.setenv("OPENCODE_API_KEY", "sk-test")

    def install(handler: Handler) -> FakeGateway:
        fake = FakeGateway(handler)
        monkeypatch.setattr(urllib.request, "urlopen", fake)
        return fake

    return install


def _go_judge(**kw: Any) -> OpencodeGoJudge:
    kw.setdefault("sleep", lambda seconds: None)
    return OpencodeGoJudge("opencode-go/kimi-k3", **kw)


def test_opencode_go_chat_completions(
    rubrics: RubricSet, gateway: Callable[[Handler], FakeGateway]
) -> None:
    q = rubrics.questions[0]
    ids = [c.criterion_id for _, c in q.criteria()]
    fake = gateway(lambda path, body: (200, _chat_payload(_results_text(ids))))
    judge = _go_judge(session_id="s1")
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert [r.verdict for r in out.results] == ["pass"] * len(ids)
    assert all(r.rationale == "matches" and r.evidence == "quoted" for r in out.results)
    assert all(r.votes == ("pass",) for r in out.results)
    assert all(r.raw_response_ref == f"judge/{q.question.id}.json" for r in out.results)
    info = judge.info()
    assert (info.name, info.model) == ("opencode-go", "opencode-go/kimi-k3")
    assert info.prompt_sha256 == judge_prompt_sha256()
    assert info.sdk_version is None and info.temperature is None

    (path, body, headers) = fake.requests[0]
    assert path == "/chat/completions"
    assert body["model"] == "kimi-k3"  # prefix stripped for the API
    assert body["messages"][0]["role"] == "system"
    assert q.question.question in body["messages"][1]["content"]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert headers["authorization"] == "Bearer sk-test"
    assert headers["x-opencode-session"] == "s1"
    assert headers["user-agent"].startswith("trialdesignbench/")
    # The log holds the protocol-independent request and the wire response.
    assert out.exchanges[0]["request"]["model"] == "kimi-k3"
    assert out.exchanges[1]["response"]["protocol"] == "chat"
    assert out.exchanges[1]["response"]["output_text"] == _results_text(ids)


def test_opencode_go_switches_protocol_once(
    rubrics: RubricSet, gateway: Callable[[Handler], FakeGateway]
) -> None:
    q = rubrics.questions[0]
    ids = [c.criterion_id for _, c in q.criteria()]

    def responses_only(path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if path == "/chat/completions":
            return 400, _gateway_error(
                "ModelProtocolUnsupported", "Model does not support this protocol."
            )
        assert body["instructions"] and body["text"]["format"]["strict"] is True
        return 200, _responses_payload(_results_text(ids))

    fake = gateway(responses_only)
    judge = _go_judge()
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert [r.verdict for r in out.results] == ["pass"] * len(ids)
    assert fake.paths == ["/chat/completions", "/responses"]
    assert "switching to responses" in out.exchanges[1]["error"]
    assert out.exchanges[2]["response"]["protocol"] == "responses"
    # The instance remembers the protocol for the next question.
    judge.judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert fake.paths == ["/chat/completions", "/responses", "/responses"]


def test_opencode_go_neither_protocol_is_fatal(
    rubrics: RubricSet, gateway: Callable[[Handler], FakeGateway]
) -> None:
    q = rubrics.questions[0]
    fake = gateway(
        lambda path, body: (400, _gateway_error("ModelProtocolUnsupported", "no"))
    )
    out = _go_judge().judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert fake.paths == ["/chat/completions", "/responses"]
    assert [r.verdict for r in out.results] == ["error"] * len(q.criteria())
    assert "neither protocol" in out.results[0].rationale


def test_opencode_go_retries_transient_errors(
    rubrics: RubricSet, gateway: Callable[[Handler], FakeGateway]
) -> None:
    q = rubrics.questions[0]
    ids = [c.criterion_id for _, c in q.criteria()]
    replies: list[tuple[int, dict[str, Any]]] = [
        (429, {"error": {"type": "rate_limit", "message": "slow down"}}),
        (200, _chat_payload("", finish_reason="length")),
        (200, _chat_payload(json.dumps({"results": []}))),  # missing criteria
        (200, _chat_payload(_results_text(ids))),
    ]
    fake = gateway(lambda path, body: replies.pop(0))
    out = _go_judge().judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert [r.verdict for r in out.results] == ["pass"] * len(ids)
    assert len(fake.requests) == 4
    errors = [e["error"] for e in out.exchanges[1:4]]
    assert errors[0] == "HTTP 429: slow down"
    assert "truncated" in errors[1] and "missing=" in errors[2]


def test_opencode_go_gives_up_after_max_retries(
    rubrics: RubricSet, gateway: Callable[[Handler], FakeGateway]
) -> None:
    q = rubrics.questions[0]
    fake = gateway(lambda path, body: (503, {"error": {"message": "down"}}))
    judge = _go_judge(max_retries=1)
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert len(fake.requests) == 2  # initial attempt plus one retry
    assert [r.verdict for r in out.results] == ["error"] * len(q.criteria())
    assert all("judge failed: HTTP 503: down" in r.rationale for r in out.results)


def test_opencode_go_client_error_is_not_retried(
    rubrics: RubricSet, gateway: Callable[[Handler], FakeGateway]
) -> None:
    q = rubrics.questions[0]
    message = "Upstream request failed: this model needs a privacy setting."
    fake = gateway(lambda path, body: (400, {"error": {"message": message}}))
    out = _go_judge().judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert len(fake.requests) == 1
    assert [r.verdict for r in out.results] == ["error"] * len(q.criteria())
    assert message in out.results[0].rationale


def test_opencode_go_incomplete_response_is_retryable(
    rubrics: RubricSet, gateway: Callable[[Handler], FakeGateway]
) -> None:
    q = rubrics.questions[0]
    ids = [c.criterion_id for _, c in q.criteria()]
    replies = [
        (200, _responses_payload("", status="incomplete")),
        (200, _responses_payload(_results_text(ids))),
    ]
    gateway(lambda path, body: replies.pop(0))
    judge = _go_judge()
    judge.protocol = "responses"
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert [r.verdict for r in out.results] == ["pass"] * len(ids)
    assert out.exchanges[1]["error"] == "judge response status 'incomplete': incomplete"


def test_opencode_go_missing_key(
    rubrics: RubricSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", None)  # must not be reached
    q = rubrics.questions[0]
    out = _go_judge().judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert [r.verdict for r in out.results] == ["error"] * len(q.criteria())
    assert "OPENCODE_API_KEY" in out.results[0].rationale


def test_anthropic_judge_votes_with_stub_client(rubrics: RubricSet) -> None:
    pytest.importorskip("anthropic")
    q = rubrics.questions[0]
    ids = [c.criterion_id for _, c in q.criteria()]

    class Block:
        type = "text"

        def __init__(self, text: str) -> None:
            self.text = text

        def to_dict(self) -> dict[str, str]:
            return {"type": "text", "text": self.text}

    class Response:
        id = "msg-test"
        model = "claude-opus-5-5"
        usage = None

        def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
            self.content = [Block(text)]
            self.stop_reason = stop_reason

    replies = [
        Response(_results_text(ids, "fail")),
        Response("", stop_reason="max_tokens"),
        Response(_results_text(ids, "pass")),
        Response(_results_text(ids, "pass")),
    ]

    class Messages:
        def create(self, **params: Any) -> Response:
            return replies.pop(0)

    class Client:
        messages = Messages()

    judge = AnthropicJudge(
        "claude-opus-5-5", votes=3, client=Client(), sleep=lambda seconds: None
    )
    out = judge.judge(q.question, q.criteria(), JudgeArtifacts({}, None))
    assert [r.verdict for r in out.results] == ["pass"] * len(ids)
    assert all(r.votes == ("fail", "pass", "pass") for r in out.results)
    assert out.exchanges[2]["error"] == "judge response truncated at max_tokens"
    assert [e["vote"] for e in out.exchanges[1:]] == [0, 1, 1, 2]
