"""Rubric judges.

A judge maps (question, criteria, agent artifacts) to one verdict per
criterion. Judges never see other questions' rubrics. `AnthropicJudge` is the
default; `OpencodeGoJudge` grades over the OpenCode Go gateway; `FakeJudge`
gives deterministic verdicts for tests and dry runs.

The judge model string selects the backend (`judge_backend`): a plain
Anthropic model id, or `<backend>/<model>` for any other backend. `tdb build`
records the model per task, so the verifier receives exactly the API key and
egress its judge needs.

The `anthropic` SDK is imported lazily so the core package and conversion-only
workflows never need it; the OpenCode Go judge uses the standard library.
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, ClassVar, Literal, NoReturn, Protocol

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


@dataclass(frozen=True)
class JudgeBackend:
    """A model API the rubric judge can call.

    `tdb build` writes `key_env` as `"${key_env}"` into `[verifier.env]` and
    `api_host` into the verifier allowlist; `tdb run` checks the key exists.
    """

    name: str
    """Backend name: `JudgeInfo.name`, and a `tdb grade --judge` value."""
    key_env: str
    """Host variable holding the API key."""
    api_host: str
    """Hostname the judge reaches (exact, for the verifier allowlist)."""
    default_model: str
    """Model used when neither `--judge-model` nor `TDB_JUDGE_MODEL` is set."""
    model_prefix: str = ""
    """`<name>/` prefix selecting this backend in a judge model string; empty
    for the backend of unprefixed model ids."""

    def model_id(self, model: str) -> str:
        """The model string without the backend prefix, as the API expects."""
        return model.removeprefix(self.model_prefix)


def judge_backend(model: str) -> JudgeBackend:
    """The backend a judge model string selects.

    `opencode-go/<id>` selects the OpenCode Go gateway; an unprefixed id is an
    Anthropic model. Any other prefix is a `ValueError`, so a typo never
    sends the model to the wrong API.
    """
    prefix = model.partition("/")[0] + "/" if "/" in model else ""
    for backend in JUDGE_BACKENDS.values():
        if backend.model_prefix == prefix:
            return backend
    prefixed = ", ".join(
        f"{b.model_prefix}<model>" for b in JUDGE_BACKENDS.values() if b.model_prefix
    )
    raise ValueError(
        f"judge model {model!r} has no known backend; use an Anthropic model id "
        f"or {prefixed}"
    )


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


class _Retryable(Exception):
    pass


def _parse_results(text: str, ids: Sequence[str]) -> dict[str, dict[str, str]]:
    """Validate a judge's JSON reply: exactly one verdict per criterion id.

    Every defect is `_Retryable`: structured output occasionally returns
    malformed or incomplete JSON that a retry fixes.
    """
    try:
        items = json.loads(text)["results"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise _Retryable(f"judge response is not valid JSON: {exc}") from exc
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


def supports_temperature(model: str) -> bool:
    """Whether the judge should send an explicit temperature for this model."""
    return not model.startswith(_NO_SAMPLING_PREFIXES)


class ApiJudge(ABC):
    """Shared behavior of judges that call a model API.

    All criteria of one question are batched into a single call. With
    `votes > 1`, the call is repeated and verdicts are majority-voted.
    Transient failures (`_Retryable`) are retried with exponential backoff;
    a call that still fails, or any other error, marks every criterion of
    the question `error`. Every attempt is recorded in the question's judge
    log.

    Subclasses set `backend`, build the API request in `_request`, and make
    one call in `_call_once`.
    """

    backend: ClassVar[JudgeBackend]

    def __init__(
        self,
        model: str | None = None,
        *,
        votes: int = 1,
        max_retries: int = 4,
        max_tokens: int = 16000,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if votes < 1:
            raise ValueError("votes must be >= 1")
        self.model = (
            model or os.environ.get(JUDGE_MODEL_ENV) or self.backend.default_model
        )
        if judge_backend(self.model) != self.backend:
            raise ValueError(
                f"{self.model!r} is not a model of the {self.backend.name} judge"
            )
        self.votes = votes
        self.max_retries = max_retries
        self.max_tokens = max_tokens
        self.temperature: float | None = None
        self._sleep = sleep

    def info(self) -> JudgeInfo:
        return JudgeInfo(
            name=self.backend.name,
            model=self.model,
            prompt_sha256=judge_prompt_sha256(),
            sdk_version=self.sdk_version(),
            votes=self.votes,
            temperature=self.temperature,
        )

    def sdk_version(self) -> str | None:
        """Version of the client library, if the judge uses one."""
        return None

    @abstractmethod
    def _request(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        artifacts: JudgeArtifacts,
    ) -> dict[str, Any]:
        """The request for one question, recorded in the judge log."""

    @abstractmethod
    def _call_once(
        self, request: dict[str, Any], ids: Sequence[str]
    ) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
        """Make one call: (verdicts by criterion id, response record).

        Raise `_Retryable` for transient failures (see `_parse_results` for
        the reply) and anything else for failures a retry cannot fix, such
        as bad credentials or a refusal.
        """

    def judge(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        artifacts: JudgeArtifacts,
    ) -> JudgeOutput:
        if not criteria:
            return JudgeOutput(results=[])
        ids = [c.criterion_id for _, c in criteria]
        request = self._request(question, criteria, artifacts)
        exchanges: list[dict[str, Any]] = []
        vote_results: list[dict[str, dict[str, str]]] = []
        for vote in range(self.votes):
            attempt = 0
            while True:
                record: dict[str, Any] = {"vote": vote, "attempt": attempt}
                try:
                    parsed, raw = self._call_once(request, ids)
                except _Retryable as exc:
                    record["error"] = str(exc)
                    exchanges.append(record)
                    if attempt >= self.max_retries:
                        return self._failed(question, criteria, request, exchanges)
                    self._sleep(min(60.0, 2.0**attempt + random.uniform(0, 1)))
                    attempt += 1
                    continue
                # Record all non-retryable failures, including auth and refusal.
                except Exception as exc:  # noqa: BLE001
                    record["error"] = f"{type(exc).__name__}: {exc}"
                    exchanges.append(record)
                    return self._failed(question, criteria, request, exchanges)
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
        return JudgeOutput(
            results=results, exchanges=[{"request": request}, *exchanges]
        )

    def _failed(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        request: dict[str, Any],
        exchanges: list[dict[str, Any]],
    ) -> JudgeOutput:
        last = exchanges[-1].get("error", "unknown error") if exchanges else ""
        results = [
            r.model_copy(update={"raw_response_ref": f"judge/{question.id}.json"})
            for r in error_results(question, criteria, f"judge failed: {last}")
        ]
        return JudgeOutput(
            results=results, exchanges=[{"request": request}, *exchanges]
        )


class AnthropicJudge(ApiJudge):
    """Judge backed by the Anthropic Messages API with structured JSON output.

    Needs the `anthropic` package (`trialdesignbench[judge]`). Sends
    `temperature=0` except for model families that reject sampling
    parameters (`supports_temperature`).
    """

    backend = JudgeBackend(
        name="anthropic",
        key_env="ANTHROPIC_API_KEY",
        api_host="api.anthropic.com",
        default_model=DEFAULT_JUDGE_MODEL,
    )

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
        super().__init__(
            model,
            votes=votes,
            max_retries=max_retries,
            max_tokens=max_tokens,
            sleep=sleep,
        )
        self.temperature = 0.0 if supports_temperature(self.model) else None
        self._client = client

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

    def sdk_version(self) -> str | None:
        try:
            return version("anthropic")
        except PackageNotFoundError:
            return None

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
        self, request: dict[str, Any], ids: Sequence[str]
    ) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
        import anthropic

        try:
            response = self.client.messages.create(**request)
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
        return _parse_results(text, ids), raw


GoProtocol = Literal["chat", "responses"]
"""OpenAI protocols of the OpenCode Go gateway: Chat Completions or Responses."""


class OpencodeGoJudge(ApiJudge):
    """Judge backed by the OpenCode Go gateway, an OpenAI-compatible API.

    Same prompt, response contract, voting, and retries as `AnthropicJudge`,
    with the standard library only. Judge models are `opencode-go/<id>` for
    the ids in the gateway's model list.

    The gateway serves each model over exactly one protocol: Chat
    Completions for most, the Responses API for the GPT, Grok, and Muse
    Spark families. A request in the other protocol fails with
    `ModelProtocolUnsupported`, so the judge starts with Chat Completions
    and switches once per instance. Every request carries a stable
    `x-opencode-session` header, which the gateway requires, and a
    `trialdesignbench/<version>` user agent, because generic HTTP-library
    user agents are blocked.
    """

    backend = JudgeBackend(
        name="opencode-go",
        key_env="OPENCODE_API_KEY",
        api_host="opencode.ai",
        default_model="opencode-go/muse-spark-1.3-contributor",
        model_prefix="opencode-go/",
    )
    base_url = "https://opencode.ai/zen/go/v1"

    def __init__(
        self,
        model: str | None = None,
        *,
        votes: int = 1,
        max_retries: int = 4,
        max_tokens: int = 16000,
        timeout: float = 120.0,
        session_id: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(
            model,
            votes=votes,
            max_retries=max_retries,
            max_tokens=max_tokens,
            sleep=sleep,
        )
        self.timeout = timeout
        self.session_id = session_id or uuid.uuid4().hex
        self.protocol: GoProtocol = "chat"
        self._switched = False

    def _request(
        self,
        question: Question,
        criteria: Sequence[tuple[Rubric, Criterion]],
        artifacts: JudgeArtifacts,
    ) -> dict[str, Any]:
        """Protocol-independent request; `_body` renders it for the wire."""
        ids = [c.criterion_id for _, c in criteria]
        return {
            "model": self.backend.model_id(self.model),
            "system": JUDGE_SYSTEM_PROMPT,
            "user": build_user_message(question, criteria, artifacts),
            "max_tokens": self.max_tokens,
            "schema": _result_schema(ids),
        }

    def _body(self, request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Endpoint path and wire body of `request` in the current protocol."""
        schema = {"name": "grade", "schema": request["schema"], "strict": True}
        if self.protocol == "responses":
            return "/responses", {
                "model": request["model"],
                "instructions": request["system"],
                "input": request["user"],
                "max_output_tokens": request["max_tokens"],
                "text": {"format": {"type": "json_schema", **schema}},
            }
        return "/chat/completions", {
            "model": request["model"],
            "messages": [
                {"role": "system", "content": request["system"]},
                {"role": "user", "content": request["user"]},
            ],
            "max_tokens": request["max_tokens"],
            "response_format": {"type": "json_schema", "json_schema": schema},
        }

    def _call_once(
        self, request: dict[str, Any], ids: Sequence[str]
    ) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
        path, body = self._body(request)
        payload = self._post(path, body)
        raw = {
            "protocol": self.protocol,
            "id": payload.get("id"),
            "model": payload.get("model"),
            "usage": payload.get("usage"),
        }
        text = (
            self._responses_text(payload)
            if self.protocol == "responses"
            else self._chat_text(payload)
        )
        return _parse_results(text, ids), {**raw, "output_text": text}

    @staticmethod
    def _chat_text(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise _Retryable("judge response has no choices")
        choice = choices[0]
        message = choice.get("message") or {}
        if message.get("refusal"):
            raise RuntimeError("judge model refused the request")
        if choice.get("finish_reason") == "length":
            raise _Retryable("judge response truncated at max_tokens")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise _Retryable("judge response message is empty")
        return content

    @staticmethod
    def _responses_text(payload: dict[str, Any]) -> str:
        status = payload.get("status")
        if status != "completed":
            detail = (payload.get("incomplete_details") or {}).get("reason") or (
                payload.get("error") or {}
            ).get("message")
            suffix = f": {detail}" if detail else ""
            raise _Retryable(f"judge response status {status!r}{suffix}")
        output = payload.get("output")
        if not isinstance(output, list):
            raise _Retryable("judge response has no output list")
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            parts = [p for p in item.get("content") or [] if isinstance(p, dict)]
            if any(p.get("type") == "refusal" for p in parts):
                raise RuntimeError("judge model refused the request")
            text = "".join(
                p.get("text", "") for p in parts if p.get("type") == "output_text"
            )
            if text.strip():
                return text
        raise _Retryable("judge response has no message text")

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        key = os.environ.get(self.backend.key_env)
        if not key:
            raise RuntimeError(
                f"The OpenCode Go judge needs {self.backend.key_env} in the "
                "environment."
            )
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": f"trialdesignbench/{package_version()}",
                "x-opencode-session": self.session_id,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            self._raise_http_error(exc)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise _Retryable(f"{type(exc).__name__}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise _Retryable(f"judge response is not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise _Retryable("judge response is not a JSON object")
        return payload

    def _raise_http_error(self, exc: urllib.error.HTTPError) -> NoReturn:
        """Classify a gateway error: protocol switch, retry, or give up."""
        detail = exc.read().decode("utf-8", "replace").strip()
        error_type = None
        try:
            error = json.loads(detail).get("error")
            if isinstance(error, dict):
                error_type = error.get("type")
                detail = str(error.get("message") or detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        summary = f"HTTP {exc.code}: {detail[:300] or exc.reason}"
        if error_type == "ModelProtocolUnsupported":
            if self._switched:
                raise RuntimeError(
                    f"{summary}; the model supports neither protocol"
                ) from exc
            self._switched = True
            self.protocol = "responses" if self.protocol == "chat" else "chat"
            raise _Retryable(f"{summary}; switching to {self.protocol}") from exc
        if exc.code >= 500 or exc.code in (408, 409, 429):
            raise _Retryable(summary) from exc
        raise RuntimeError(summary) from exc


API_JUDGES: tuple[type[ApiJudge], ...] = (AnthropicJudge, OpencodeGoJudge)
"""Judges that call a model API. A new backend is an `ApiJudge` subclass with
its own `JudgeBackend`, added here."""

JUDGE_BACKENDS: Mapping[str, JudgeBackend] = {
    cls.backend.name: cls.backend for cls in API_JUDGES
}
"""Backends by name; `judge_backend` selects one from a judge model string."""


def make_judge(
    model: str | None = None, *, backend: str | None = None, votes: int = 1
) -> ApiJudge:
    """The API judge for a model string.

    `model` defaults to `TDB_JUDGE_MODEL`, then to the backend's default.
    `backend` names the judge explicitly (`tdb grade --judge`); the model
    must then belong to it. Unknown backends and mismatches are a
    `ValueError`.
    """
    model = model or os.environ.get(JUDGE_MODEL_ENV)
    if backend is None:
        backend = judge_backend(model or DEFAULT_JUDGE_MODEL).name
    for cls in API_JUDGES:
        if cls.backend.name == backend:
            return cls(model, votes=votes)
    raise ValueError(
        f"unknown judge backend {backend!r}; backends: {', '.join(JUDGE_BACKENDS)}"
    )
