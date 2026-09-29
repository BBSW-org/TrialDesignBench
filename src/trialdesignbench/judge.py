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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol

from trialdesignbench.provenance import sha256_text
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
