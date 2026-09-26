"""Versioned scoring rules. Pure functions; no I/O.

Changing any rule here must bump `SCORING_VERSION` so reports never mix
scores computed under different rules.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from trialdesignbench.schema import CriterionResult, QuestionGrade, Verdict

SCORING_VERSION = "1"

DEFAULT_WEIGHTS: Mapping[str, float] = {"High": 3.0, "Medium": 2.0, "Low": 1.0}
SCORED_KIND = "Add"
NON_BLOCKING_CHECKS = frozenset({"numeric_format"})


@dataclass(frozen=True)
class ScoringConfig:
    weights: Mapping[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    version: str = SCORING_VERSION

    def weight(self, importance: str) -> float:
        try:
            return float(self.weights[importance])
        except KeyError as exc:
            raise ValueError(
                f"No weight configured for importance {importance!r}"
            ) from exc


@dataclass(frozen=True)
class QuestionScore:
    score: float | None
    earned: float
    possible: float
    counts: dict[str, int]


def verdict_counts(results: Iterable[CriterionResult]) -> dict[str, int]:
    """Count verdicts by kind."""
    counts = {"pass": 0, "fail": 0, "unclear": 0, "error": 0}
    for r in results:
        counts[r.verdict] += 1
    return counts


def score_question(
    results: Sequence[CriterionResult], config: ScoringConfig | None = None
) -> QuestionScore:
    """Weighted pass fraction over `Add` criteria.

    `unclear` and `error` verdicts earn nothing. Returns `score=None` when the
    question has no `Add` criteria (nothing to score).
    """
    config = config or ScoringConfig()
    scored = [r for r in results if r.scoring == SCORED_KIND]
    possible = sum(config.weight(r.importance) for r in scored)
    earned = sum(config.weight(r.importance) for r in scored if r.verdict == "pass")
    counts = verdict_counts(scored)
    if possible == 0:
        return QuestionScore(score=None, earned=0.0, possible=0.0, counts=counts)
    return QuestionScore(
        score=earned / possible, earned=earned, possible=possible, counts=counts
    )


def mean(values: Iterable[float]) -> float | None:
    """Arithmetic mean, or None for no values."""
    vals = list(values)
    return sum(vals) / len(vals) if vals else None


def task_rubric_score(questions: Sequence[QuestionGrade]) -> float | None:
    """Mean score over scored questions."""
    return mean(q.score for q in questions if q.score is not None)


def dimension_means(
    questions: Sequence[QuestionGrade], config: ScoringConfig | None = None
) -> dict[str, float]:
    """Weighted pass fraction per rubric dimension, pooled across questions.

    Extraction questions have no dimension; they are pooled under `extraction`.
    """
    config = config or ScoringConfig()
    earned: dict[str, float] = {}
    possible: dict[str, float] = {}
    for q in questions:
        if q.status == "unscorable":
            continue
        for r in q.criteria:
            if r.scoring != SCORED_KIND:
                continue
            key = r.dimension or "extraction"
            w = config.weight(r.importance)
            possible[key] = possible.get(key, 0.0) + w
            if r.verdict == "pass":
                earned[key] = earned.get(key, 0.0) + w
    return {k: earned.get(k, 0.0) / v for k, v in sorted(possible.items()) if v > 0}


def group_means(questions: Sequence[QuestionGrade], attr: str) -> dict[str, float]:
    """Mean question score grouped by a QuestionGrade attribute."""
    groups: dict[str, list[float]] = {}
    for q in questions:
        if q.score is None:
            continue
        groups.setdefault(str(getattr(q, attr)), []).append(q.score)
    return {k: sum(v) / len(v) for k, v in sorted(groups.items())}


def majority_verdict(votes: Sequence[Verdict]) -> Verdict:
    """Majority across judge votes. Any `error` vote, or a tie, is not a pass."""
    if not votes:
        return "error"
    if "error" in votes:
        return "error"
    tally: dict[str, int] = {}
    for v in votes:
        tally[v] = tally.get(v, 0) + 1
    best = max(tally.values())
    winners = [v for v, n in tally.items() if n == best]
    if len(winners) != 1:
        return "unclear"
    winner = winners[0]
    assert winner in ("pass", "fail", "unclear")
    return winner  # type: ignore[return-value]


def reward_score(rubric_score: float | None, blocking_failures: Sequence[str]) -> float:
    """Reward is the rubric score unless any gate failed, then 0."""
    if blocking_failures or rubric_score is None:
        return 0.0
    return rubric_score
