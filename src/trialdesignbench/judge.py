"""Rubric judges.

A judge maps (question, criteria, agent artifacts) to one verdict per
criterion. Judges never see other questions' rubrics. `AnthropicJudge` is the
default; `FakeJudge` gives deterministic verdicts for tests and dry runs.

The `anthropic` SDK is imported lazily so the core package and conversion-only
workflows never need it.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal, Protocol

from trialdesignbench.provenance import package_version, sha256_text
from trialdesignbench.schema import (
    Criterion,
    CriterionResult,
    JudgeInfo,
    Question,
    Rubric,
    Verdict,
)
from trialdesignbench.scoring import majority_verdict

DEFAULT_JUDGE_MODEL = "claude-opus-5-5"
JUDGE_MODEL_ENV = "TDB_JUDGE_MODEL"

OPENCODE_GO_KEY_ENV = "OPENCODE_API_KEY"
"""Host variable holding the OpenCode Go subscription key (agent and judge)."""

OPENCODE_GO_BASE_URL_ENV = "OPENCODE_GO_BASE_URL"
"""Optional gateway override, honored by standalone `tdb grade` only."""

OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
"""Default gateway. Inside Harbor only `opencode.ai` is allowlisted."""

OPENCODE_GO_API_HOST = "opencode.ai"
OPENCODE_GO_MODEL_PREFIX = "opencode-go/"
DEFAULT_OPENCODE_GO_JUDGE_MODEL = "muse-spark-1.3-contributor"

JudgeKind = Literal["anthropic", "opencode-go"]
"""Judge backend. The `opencode-go/` model prefix selects the Go gateway."""


def judge_kind_for_model(model: str | None) -> JudgeKind:
    """Which judge backend grades this judge-model string."""
    if (model or "").startswith(OPENCODE_GO_MODEL_PREFIX):
        return "opencode-go"
    return "anthropic"


def judge_key_env(kind: JudgeKind) -> str:
    """Host variable holding the judge credential for this backend."""
    if kind == "opencode-go":
        return OPENCODE_GO_KEY_ENV
    return "ANTHROPIC_API_KEY"


def judge_api_host(kind: JudgeKind) -> str:
    """API host the judge reaches (the verifier allowlist entry)."""
    if kind == "opencode-go":
        return OPENCODE_GO_API_HOST
    return "api.anthropic.com"


# Omit sampling parameters for these model families for forward compatibility,
# including older versions that still accept them.
_NO_SAMPLING_PREFIXES = (
    "claude-fable",
    "claude-mythos",
    "claude-opus",
    "claude-sonnet",
)

JUDGE_SYSTEM_PROMPT = """\
You are an expert clinical trial statistician grading one answer written by an \
AI agent. The agent was given the protocol or statistical analysis plan (SAP) \
of a Phase 3 trial and asked to reproduce its statistical design. It answered \
under a closed-book rule: only the provided document may be used.

You receive the question, the agent's answer for that question (a JSON entry \
from its output.json), for derivation questions the agent's full output.R, and \
a list of grading criteria written by a reviewer who knows the correct answer.

Judge each criterion independently:
- "pass": the answer clearly and correctly satisfies the criterion. Numeric \
values match the criterion within reasonable rounding unless the criterion \
demands more precision.
- "fail": the answer does not satisfy the criterion, contradicts it, omits the \
required content, or states the value is not derivable when the criterion \
expects a value.
- "unclear": the answer is ambiguous and a careful expert could not decide.

Closed-book rule: every value should be traceable to the provided document \
(a section or page reference, or a calculation from traceable inputs). An \
answer that is correct but cites nothing is judged on the criterion alone. An \
answer that relies on or cites a source outside the document (a publication, \
press release, registry entry, amendment, or prior knowledge of the trial) \
fails every criterion it supports with that source.

For criteria about the method, judge the explanation in the answer together \
with the R code. For criteria about calculated values, judge the reported \
value in the answer; use the R code only to understand how it was obtained.

For each criterion give a one or two sentence rationale and quote the exact \
excerpt of the answer or R code that supports your verdict as evidence (empty \
string if nothing relevant exists). Return exactly one result per criterion \
id, in the order given."""

_RESULT_SCHEMA_VERSION = "1"


def _result_schema(criterion_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion_id": {"type": "string", "enum": list(criterion_ids)},
                        "verdict": {
                            "type": "string",
                            "enum": ["pass", "fail", "unclear"],
                        },
                        "rationale": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["criterion_id", "verdict", "rationale", "evidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    }


def judge_prompt_sha256() -> str:
    """Hash identifying the judge prompt and response contract."""
    return sha256_text(
        JUDGE_SYSTEM_PROMPT
        + "\n--schema--\n"
        + json.dumps(_result_schema(["<id>"]), sort_keys=True)
        + f"\n--v{_RESULT_SCHEMA_VERSION}"
    )


@dataclass(frozen=True)
class JudgeArtifacts:
    """What the judge may see for one question."""

    output_entry: Mapping[str, Any] | None
    output_r: str | None


@dataclass
class JudgeOutput:
    results: list[CriterionResult]
    exchanges: list[dict[str, Any]] = field(default_factory=list)


class Judge(Protocol):
    def info(self) -> JudgeInfo: ...

    def judge(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        artifacts: JudgeArtifacts,
    ) -> JudgeOutput: ...


def build_user_message(
    question: Question,
    criteria: Sequence[tuple[Rubric, Criterion]],
    artifacts: JudgeArtifacts,
) -> str:
    """Judge input for one question. Never includes other questions."""
    parts = [
        "## Question",
        json.dumps(
            {
                "id": question.id,
                "question_type": question.question_type,
                "design_element": question.design_element_label,
                "question": question.question,
            },
            indent=2,
            ensure_ascii=False,
        ),
        "## Agent answer (output.json entry)",
        json.dumps(artifacts.output_entry, indent=2, ensure_ascii=False),
    ]
    if question.question_type == "derivation_required":
        parts += [
            "## Agent output.R (full file)",
            "```r\n" + (artifacts.output_r or "# (output.R missing)") + "\n```",
        ]
    parts += [
        "## Criteria",
        json.dumps(
            [
                {
                    "criterion_id": c.criterion_id,
                    "dimension": r.dimension or "answer",
                    "importance": c.importance,
                    "criterion": c.criterion,
                }
                for r, c in criteria
            ],
            indent=2,
            ensure_ascii=False,
        ),
    ]
    return "\n\n".join(parts)


def _result(
    question: Question,
    rubric: Rubric,
    criterion: Criterion,
    verdict: Verdict,
    rationale: str,
    *,
    evidence: str = "",
    votes: Sequence[Verdict] = (),
    ref: str | None = None,
) -> CriterionResult:
    return CriterionResult(
        criterion_id=criterion.criterion_id,
        question_id=question.id,
        dimension=rubric.dimension,
        importance=criterion.importance,
        scoring=criterion.scoring,
        verdict=verdict,
        rationale=rationale,
        evidence=evidence,
        votes=tuple(votes),
        raw_response_ref=ref,
    )


def error_results(
    question: Question,
    criteria: Sequence[tuple[Rubric, Criterion]],
    message: str,
) -> list[CriterionResult]:
    """Mark every criterion of a question as `error`."""
    return [_result(question, r, c, "error", message) for r, c in criteria]


class FakeJudge:
    """Deterministic judge for tests.

    `verdicts` maps criterion ids to verdicts; `rule` computes a verdict from
    (criterion, artifacts) for anything not in the mapping; otherwise
    `default` is used.
    """

    def __init__(
        self,
        verdicts: Mapping[str, Verdict] | None = None,
        *,
        rule: Callable[[Criterion, JudgeArtifacts], Verdict] | None = None,
        default: Verdict = "fail",
    ) -> None:
        self.verdicts = dict(verdicts or {})
        self.rule = rule
        self.default = default
        self.calls: list[str] = []

    def info(self) -> JudgeInfo:
        return JudgeInfo(
            name="fake",
            model=None,
            prompt_sha256=judge_prompt_sha256(),
            sdk_version=None,
            votes=1,
            temperature=None,
        )

    def judge(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        artifacts: JudgeArtifacts,
    ) -> JudgeOutput:
        self.calls.append(question.id)
        results = []
        for rubric, criterion in criteria:
            if criterion.criterion_id in self.verdicts:
                verdict = self.verdicts[criterion.criterion_id]
            elif self.rule is not None:
                verdict = self.rule(criterion, artifacts)
            else:
                verdict = self.default
            results.append(
                _result(
                    question,
                    rubric,
                    criterion,
                    verdict,
                    "fake judge",
                    votes=(verdict,),
                )
            )
        exchange = {"judge": "fake", "question_id": question.id}
        return JudgeOutput(results=results, exchanges=[exchange])


def _checked_results(items: Any, ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """Validate one verdict per criterion id, in any order. Shared by judges."""
    if not isinstance(items, list):
        raise _Retryable("judge results are not a list")
    parsed: dict[str, dict[str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise _Retryable("judge result is not an object")
        cid = item.get("criterion_id")
        if not isinstance(cid, str):
            raise _Retryable(f"judge returned non-string criterion id {cid!r}")
        if cid in parsed:
            raise _Retryable(f"judge returned criterion {cid!r} twice")
        if item.get("verdict") not in ("pass", "fail", "unclear"):
            raise _Retryable(f"judge returned invalid verdict for {cid!r}")
        parsed[cid] = item
    if set(parsed) != set(ids):
        missing = sorted(set(ids) - set(parsed))
        extra = sorted(set(parsed) - set(ids))
        raise _Retryable(
            f"judge results do not match criteria (missing={missing}, extra={extra})"
        )
    return parsed


class _Retryable(Exception):
    pass


def _sdk_version() -> str | None:
    try:
        return version("anthropic")
    except PackageNotFoundError:
        return None


def supports_temperature(model: str) -> bool:
    """Whether the judge should send an explicit temperature for this model."""
    return not model.startswith(_NO_SAMPLING_PREFIXES)


class AnthropicJudge:
    """Judge backed by the Anthropic Messages API with structured JSON output.

    All criteria of one question are batched into a single call. With
    `votes > 1`, the call is repeated and verdicts are majority-voted. Any
    call that still fails after retries marks every criterion of that
    question `error`.
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        votes: int = 1,
        max_retries: int = 4,
        max_tokens: int = 16000,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if votes < 1:
            raise ValueError("votes must be >= 1")
        self.model = model or os.environ.get(JUDGE_MODEL_ENV) or DEFAULT_JUDGE_MODEL
        self.votes = votes
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.temperature = 0.0 if supports_temperature(self.model) else None
        self._client = client
        self._sleep = sleep

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError(
                    "The Anthropic judge needs the `anthropic` package: "
                    "install `trialdesignbench[judge]`."
                ) from exc
            # Retries are handled here so each attempt is recorded.
            self._client = anthropic.Anthropic(max_retries=0)
        return self._client

    def info(self) -> JudgeInfo:
        return JudgeInfo(
            name="anthropic",
            model=self.model,
            prompt_sha256=judge_prompt_sha256(),
            sdk_version=_sdk_version(),
            votes=self.votes,
            temperature=self.temperature,
        )

    def _request(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        artifacts: JudgeArtifacts,
    ) -> dict[str, Any]:
        ids = [c.criterion_id for _, c in criteria]
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": JUDGE_SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": build_user_message(question, criteria, artifacts),
                }
            ],
            "output_config": {
                "format": {"type": "json_schema", "schema": _result_schema(ids)}
            },
        }
        if self.temperature is not None:
            params["temperature"] = self.temperature
        return params

    def _call_once(
        self, params: dict[str, Any], ids: Sequence[str]
    ) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
        import anthropic

        try:
            response = self.client.messages.create(**params)
        except (
            anthropic.RateLimitError,
            anthropic.APIConnectionError,
            anthropic.InternalServerError,
        ) as exc:
            raise _Retryable(f"{type(exc).__name__}: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code in (408, 409, 429, 529):
                raise _Retryable(f"{type(exc).__name__}: {exc}") from exc
            raise
        raw = {
            "id": getattr(response, "id", None),
            "request_id": getattr(response, "_request_id", None),
            "model": getattr(response, "model", None),
            "stop_reason": response.stop_reason,
            "content": [
                block.to_dict() if hasattr(block, "to_dict") else str(block)
                for block in response.content
            ],
            "usage": response.usage.to_dict()
            if hasattr(response.usage, "to_dict")
            else None,
        }
        if response.stop_reason == "refusal":
            raise RuntimeError("judge model refused the request")
        if response.stop_reason == "max_tokens":
            raise _Retryable("judge response truncated at max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise _Retryable("judge response has no text block")
        try:
            data = json.loads(text)
            items = data["results"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise _Retryable(f"judge response is not valid JSON: {exc}") from exc
        parsed: dict[str, dict[str, str]] = {}
        for item in items:
            cid = item.get("criterion_id")
            if cid in parsed:
                raise _Retryable(f"judge returned criterion {cid!r} twice")
            if item.get("verdict") not in ("pass", "fail", "unclear"):
                raise _Retryable(f"judge returned invalid verdict for {cid!r}")
            parsed[cid] = item
        if set(parsed) != set(ids):
            missing = sorted(set(ids) - set(parsed))
            extra = sorted(set(parsed) - set(ids))
            raise _Retryable(
                "judge results do not match criteria "
                f"(missing={missing}, extra={extra})"
            )
        return parsed, raw

    def judge(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        artifacts: JudgeArtifacts,
    ) -> JudgeOutput:
        if not criteria:
            return JudgeOutput(results=[])
        ids = [c.criterion_id for _, c in criteria]
        params = self._request(question, criteria, artifacts)
        exchanges: list[dict[str, Any]] = []
        vote_results: list[dict[str, dict[str, str]]] = []
        for vote in range(self.votes):
            attempt = 0
            while True:
                record: dict[str, Any] = {"vote": vote, "attempt": attempt}
                try:
                    parsed, raw = self._call_once(params, ids)
                except _Retryable as exc:
                    record["error"] = str(exc)
                    exchanges.append(record)
                    if attempt >= self.max_retries:
                        return self._failed(question, criteria, params, exchanges)
                    self._sleep(min(60.0, 2.0**attempt + random.uniform(0, 1)))
                    attempt += 1
                    continue
                # Record all non-retryable failures, including auth and refusal.
                except Exception as exc:  # noqa: BLE001
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    exchanges.append(record)
                    return self._failed(question, criteria, params, exchanges)
                record["response"] = raw
                exchanges.append(record)
                vote_results.append(parsed)
                break
        results = []
        for rubric, criterion in criteria:
            cid = criterion.criterion_id
            votes: list[Verdict] = [v[cid]["verdict"] for v in vote_results]  # type: ignore[misc]
            final = majority_verdict(votes)
            chosen = next(
                (v[cid] for v in vote_results if v[cid]["verdict"] == final),
                vote_results[0][cid],
            )
            results.append(
                _result(
                    question,
                    rubric,
                    criterion,
                    final,
                    chosen.get("rationale", ""),
                    evidence=chosen.get("evidence", ""),
                    votes=votes,
                    ref=f"judge/{question.id}.json",
                )
            )
        return JudgeOutput(results=results, exchanges=[{"request": params}, *exchanges])

    def _failed(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        params: dict[str, Any],
        exchanges: list[dict[str, Any]],
    ) -> JudgeOutput:
        last = exchanges[-1].get("error", "unknown error") if exchanges else ""
        results = [
            r.model_copy(update={"raw_response_ref": f"judge/{question.id}.json"})
            for r in error_results(question, criteria, f"judge failed: {last}")
        ]
        return JudgeOutput(results=results, exchanges=[{"request": params}, *exchanges])


class OpencodeGoJudge:
    """Judge backed by the OpenCode Go gateway's Responses API.

    Same prompt, response contract, voting, and retry semantics as
    `AnthropicJudge`; only the transport differs. Standard library only, so
    no `judge` extra is needed.

    The gateway requires a stable `x-opencode-session` header per
    conversation and rejects generic HTTP-library user agents, so each
    instance sends one generated session id and identifies as
    `trialdesignbench/<version>`. Any call that still fails after retries
    marks every criterion of that question `error`.
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        votes: int = 1,
        max_retries: int = 4,
        max_tokens: int = 16000,
        timeout: float = 120.0,
        session_id: str | None = None,
        call: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if votes < 1:
            raise ValueError("votes must be >= 1")
        self.model = (
            model or os.environ.get(JUDGE_MODEL_ENV) or DEFAULT_OPENCODE_GO_JUDGE_MODEL
        )
        self.votes = votes
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.session_id = session_id or uuid.uuid4().hex
        self._call = call
        self._sleep = sleep

    @property
    def base_url(self) -> str:
        return os.environ.get(OPENCODE_GO_BASE_URL_ENV) or OPENCODE_GO_BASE_URL

    @property
    def user_agent(self) -> str:
        return f"trialdesignbench/{package_version()}"

    def info(self) -> JudgeInfo:
        return JudgeInfo(
            name="opencode-go",
            model=self.model,
            prompt_sha256=judge_prompt_sha256(),
            sdk_version=None,
            votes=self.votes,
            temperature=None,
        )

    def _request(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        artifacts: JudgeArtifacts,
    ) -> dict[str, Any]:
        ids = [c.criterion_id for _, c in criteria]
        return {
            "model": self.model.removeprefix(OPENCODE_GO_MODEL_PREFIX),
            "input": (
                f"{JUDGE_SYSTEM_PROMPT}\n\n"
                f"{build_user_message(question, criteria, artifacts)}"
            ),
            "max_output_tokens": self.max_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "grade",
                    "schema": _result_schema(ids),
                    "strict": True,
                }
            },
        }

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        key = os.environ.get(OPENCODE_GO_KEY_ENV)
        if not key:
            raise RuntimeError(
                f"The OpenCode Go judge needs {OPENCODE_GO_KEY_ENV} in the environment."
            )
        request = urllib.request.Request(
            f"{self.base_url}/responses",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
                "x-opencode-session": self.session_id,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 or exc.code in (408, 409, 429):
                raise _Retryable(f"HTTPError {exc.code}: {exc.reason}") from exc
            raise
        except urllib.error.URLError as exc:
            raise _Retryable(f"URLError: {exc.reason}") from exc
        if not isinstance(payload, dict):
            raise _Retryable("judge response is not a JSON object")
        return payload

    def _call_once(
        self, body: dict[str, Any], ids: Sequence[str]
    ) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
        payload = self._call(body) if self._call is not None else self._post(body)
        raw = {
            "status": payload.get("status"),
            "model": payload.get("model"),
            "usage": payload.get("usage"),
        }
        if payload.get("status") != "completed":
            raise _Retryable(f"judge response status {payload.get('status')!r}")
        output = payload.get("output")
        if not isinstance(output, list):
            raise _Retryable("judge response has no output list")
        message = next(
            (
                item
                for item in output
                if isinstance(item, dict) and item.get("type") == "message"
            ),
            None,
        )
        if message is None:
            raise _Retryable("judge response has no message item")
        content = message.get("content")
        if not isinstance(content, list):
            raise _Retryable("judge response message has no content list")
        text = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
        if not text.strip():
            raise _Retryable("judge response message is empty")
        try:
            data = json.loads(text)
            items = data["results"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise _Retryable(f"judge response is not valid JSON: {exc}") from exc
        return _checked_results(items, ids), {**raw, "output_text": text}

    def judge(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        artifacts: JudgeArtifacts,
    ) -> JudgeOutput:
        if not criteria:
            return JudgeOutput(results=[])
        ids = [c.criterion_id for _, c in criteria]
        body = self._request(question, criteria, artifacts)
        exchanges: list[dict[str, Any]] = []
        vote_results: list[dict[str, dict[str, str]]] = []
        for vote in range(self.votes):
            attempt = 0
            while True:
                record: dict[str, Any] = {"vote": vote, "attempt": attempt}
                try:
                    parsed, raw = self._call_once(body, ids)
                except _Retryable as exc:
                    record["error"] = str(exc)
                    exchanges.append(record)
                    if attempt >= self.max_retries:
                        return self._failed(question, criteria, body, exchanges)
                    self._sleep(min(60.0, 2.0**attempt + random.uniform(0, 1)))
                    attempt += 1
                    continue
                # Record all non-retryable failures, including auth and refusal.
                except Exception as exc:  # noqa: BLE001
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    exchanges.append(record)
                    return self._failed(question, criteria, body, exchanges)
                record["response"] = raw
                exchanges.append(record)
                vote_results.append(parsed)
                break
        results = []
        for rubric, criterion in criteria:
            cid = criterion.criterion_id
            votes: list[Verdict] = [v[cid]["verdict"] for v in vote_results]  # type: ignore[misc]
            final = majority_verdict(votes)
            chosen = next(
                (v[cid] for v in vote_results if v[cid]["verdict"] == final),
                vote_results[0][cid],
            )
            results.append(
                _result(
                    question,
                    rubric,
                    criterion,
                    final,
                    chosen.get("rationale", ""),
                    evidence=chosen.get("evidence", ""),
                    votes=votes,
                    ref=f"judge/{question.id}.json",
                )
            )
        return JudgeOutput(results=results, exchanges=[{"request": body}, *exchanges])

    def _failed(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        body: dict[str, Any],
        exchanges: list[dict[str, Any]],
    ) -> JudgeOutput:
        last = exchanges[-1].get("error", "unknown error") if exchanges else ""
        results = [
            r.model_copy(update={"raw_response_ref": f"judge/{question.id}.json"})
            for r in error_results(question, criteria, f"judge failed: {last}")
        ]
        return JudgeOutput(results=results, exchanges=[{"request": body}, *exchanges])
