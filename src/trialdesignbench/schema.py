"""Versioned data models shared by every TrialDesignBench stage.

All models are frozen and reject unknown fields, so a file written by one
version of the package is either read back exactly or rejected loudly.
Each model carries a `schema_version` so on-disk artifacts are
self-describing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1"

TaskType = Literal["reproduction"]
"""Kind of benchmark task. Only reproduction exists today; design generation
(Task 2) will extend this literal."""

QuestionType = Literal["extraction_only", "derivation_required"]
Importance = Literal["High", "Medium", "Low"]
Verdict = Literal["pass", "fail", "unclear", "error"]
CheckStatus = Literal["pass", "fail", "error"]
QuestionStatus = Literal["scored", "unscorable", "error"]
GradeStatus = Literal["ok", "zeroed", "error"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Dataset records
# ---------------------------------------------------------------------------


class SubmissionSource(_Model):
    """Which curated intake submission a task was imported from."""

    schema_version: str = SCHEMA_VERSION
    submission_id: str
    username: str
    submitted_at: str
    version: str | None = None
    intake_file: str
    intake_sha256: str
    selection: Literal["latest", "username"]
    candidates: int = Field(
        description="Number of intake submissions available for this trial."
    )


class DocumentRef(_Model):
    """Reference to the agent-visible source document (protocol or SAP)."""

    schema_version: str = SCHEMA_VERSION
    path: str = "document.md"
    sha256: str
    source: str | None = Field(
        default=None, description="Where the document text came from."
    )


class Question(_Model):
    """One evaluation question. The agent sees `skeleton()` only."""

    schema_version: str = SCHEMA_VERSION
    id: str
    design_element: str
    design_element_other: str = ""
    question: str
    question_type: QuestionType

    def skeleton(self) -> dict[str, Any]:
        """Agent-visible prompt entry with every answer field nulled.

        Mirrors `extract_prompts.py` from the intake tooling so the agent
        contract matches what reviewers curated.
        """
        output: dict[str, Any]
        if self.question_type == "derivation_required":
            output = {
                "dimensions": {
                    "inputs_used": None,
                    "method": None,
                    "calculated_value": None,
                }
            }
        else:
            output = {"extracted_value": None}
        return {
            "id": self.id,
            "design_element": self.design_element,
            "question": self.question,
            "question_type": self.question_type,
            "output": output,
        }

    @property
    def design_element_label(self) -> str:
        if self.design_element_other:
            return f"{self.design_element} ({self.design_element_other})"
        return self.design_element


class TaskRecord(_Model):
    """Agent-visible task definition (`<task_id>/task.json`)."""

    schema_version: str = SCHEMA_VERSION
    id: str
    trial_id: str
    task_type: TaskType = "reproduction"
    source: SubmissionSource
    document: DocumentRef | None
    publicly_indexed: bool | None = Field(
        default=None,
        description=(
            "Whether the source document is publicly indexed (journal "
            "supplement, registry). None means unknown. Used to split results "
            "by training-data exposure."
        ),
    )
    questions: tuple[Question, ...]

    def skeleton(self) -> dict[str, Any]:
        return {"prompt": [q.skeleton() for q in self.questions]}

    @property
    def design_elements(self) -> list[str]:
        return sorted({q.design_element for q in self.questions})


class Criterion(_Model):
    schema_version: str = SCHEMA_VERSION
    criterion_id: str
    criterion: str
    importance: Importance
    scoring: str = Field(
        description="`Add` is scored; any other value is recorded but excluded."
    )


class Rubric(_Model):
    schema_version: str = SCHEMA_VERSION
    artifact: str
    dimension: str = Field(
        description="Empty for extraction questions; `Inputs used`, `Method`, "
        "or `Calculated value` for derivation questions."
    )
    criteria: tuple[Criterion, ...]


class QuestionRubrics(_Model):
    schema_version: str = SCHEMA_VERSION
    question: Question
    rubrics: tuple[Rubric, ...]

    def criteria(self) -> list[tuple[Rubric, Criterion]]:
        return [(r, c) for r in self.rubrics for c in r.criteria]


class RubricSet(_Model):
    """Hidden grading spec (`rubrics.json`). Never shown to the agent."""

    schema_version: str = SCHEMA_VERSION
    task_id: str
    trial_id: str
    questions: tuple[QuestionRubrics, ...]


class DatasetTaskEntry(_Model):
    schema_version: str = SCHEMA_VERSION
    task_id: str
    trial_id: str
    digest: str
    has_document: bool


class DatasetManifest(_Model):
    """Canonical dataset index (`dataset.json`)."""

    schema_version: str = SCHEMA_VERSION
    dataset_version: str
    package_version: str
    created_at: datetime
    digest: str = Field(description="sha256 over the sorted task digests.")
    tasks: tuple[DatasetTaskEntry, ...]


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


class Submission(_Model):
    """Artifacts found in a submission directory."""

    schema_version: str = SCHEMA_VERSION
    directory: str
    output_json_sha256: str | None
    output_r_sha256: str | None
    trajectory_path: str | None
    trajectory_sha256: str | None


class CheckResult(_Model):
    """One deterministic check. `error` means the check could not run."""

    schema_version: str = SCHEMA_VERSION
    name: str
    status: CheckStatus
    message: str
    blocking: bool = Field(
        description="Whether a non-pass status zeroes the task reward."
    )
    details: dict[str, Any] = Field(default_factory=dict)


class NetworkViolation(_Model):
    schema_version: str = SCHEMA_VERSION
    step_index: int
    step_id: int | str | None = None
    tool_name: str
    reason: str
    excerpt: str


class CriterionResult(_Model):
    schema_version: str = SCHEMA_VERSION
    criterion_id: str
    question_id: str
    dimension: str
    importance: Importance
    scoring: str
    verdict: Verdict
    rationale: str
    evidence: str = ""
    votes: tuple[Verdict, ...] = ()
    raw_response_ref: str | None = None


class QuestionGrade(_Model):
    schema_version: str = SCHEMA_VERSION
    question_id: str
    question_type: QuestionType
    design_element: str
    status: QuestionStatus
    score: float | None
    earned_weight: float
    possible_weight: float
    counts: dict[str, int]
    criteria: tuple[CriterionResult, ...]
    excluded_criteria: tuple[CriterionResult, ...] = ()
    warnings: tuple[str, ...] = ()


class JudgeInfo(_Model):
    schema_version: str = SCHEMA_VERSION
    name: str
    model: str | None
    prompt_sha256: str
    sdk_version: str | None
    votes: int
    temperature: float | None


class TaskGrade(_Model):
    """Full grading result (`grade.json`)."""

    schema_version: str = SCHEMA_VERSION
    scoring_version: str
    package_version: str
    task_id: str
    trial_id: str
    status: GradeStatus
    score: float = Field(description="Reward score written to reward.json.")
    rubric_score: float | None = Field(
        description="Mean question score regardless of gating failures."
    )
    deterministic_pass_fraction: float
    zero_reasons: tuple[str, ...]
    checks: tuple[CheckResult, ...]
    network_violations: tuple[NetworkViolation, ...]
    questions: tuple[QuestionGrade, ...]
    by_dimension: dict[str, float]
    by_design_element: dict[str, float]
    by_question_type: dict[str, float]
    warnings: tuple[str, ...]
    judge: JudgeInfo
    submission: Submission
    rubrics_sha256: str
    graded_at: datetime


# ---------------------------------------------------------------------------
# Runs and reports
# ---------------------------------------------------------------------------


class AgentSpec(_Model):
    schema_version: str = SCHEMA_VERSION
    agent: str
    model: str
    agent_version: str | None
    kwargs: dict[str, Any] = Field(default_factory=dict)
    env_keys: tuple[str, ...] = Field(
        default=(), description="Names (never values) of agent env vars set."
    )
    allowed_hosts: tuple[str, ...]


class NetworkPolicy(_Model):
    schema_version: str = SCHEMA_VERSION
    host_table_version: str
    agent_network_mode: str
    agent_allowed_hosts: tuple[str, ...]
    verifier_network_mode: str
    verifier_allowed_hosts: tuple[str, ...]
    disabled_tools: dict[str, tuple[str, ...]]
    mcp_servers: tuple[str, ...] = ()
    canary: bool


class RunManifest(_Model):
    """Provenance for one `tdb run` invocation (`tdb-run.json`)."""

    schema_version: str = SCHEMA_VERSION
    package_version: str
    harbor_version: str | None
    job_name: str
    job_dir: str
    dataset_digest: str | None
    tasks_digest: str
    image_ref: str | None
    image_digest: str | None
    agents: tuple[AgentSpec, ...]
    auth_mode: Literal["api", "subscription"]
    skills: tuple[str, ...]
    judge_model: str | None
    n_attempts: int
    n_concurrent: int
    network_policy: NetworkPolicy
    repo_git_sha: str | None
    command: tuple[str, ...]
    started_at: datetime
    finished_at: datetime | None = None
    exit_code: int | None = None


class TrialSummary(_Model):
    schema_version: str = SCHEMA_VERSION
    trial_name: str
    task_id: str
    agent: str
    model: str
    status: Literal["graded", "zeroed", "error", "missing"]
    score: float
    rubric_score: float | None
    network_violations: int
    zero_reasons: tuple[str, ...]
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


class TaskAggregate(_Model):
    schema_version: str = SCHEMA_VERSION
    task_id: str
    n_attempts: int
    mean: float
    min: float
    max: float
    all_attempts_pass: bool


class AgentAggregate(_Model):
    schema_version: str = SCHEMA_VERSION
    agent: str
    model: str
    mean_score: float
    pass_rate: float
    n_tasks_total: int
    n_tasks_attempted: int
    missing_tasks: tuple[str, ...]
    errored_trials: tuple[str, ...]
    tasks: tuple[TaskAggregate, ...]
    by_question_type: dict[str, float]
    by_design_element: dict[str, float]
    by_dimension: dict[str, float]
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


class ReportSummary(_Model):
    """Aggregated benchmark results (`report.json`)."""

    schema_version: str = SCHEMA_VERSION
    package_version: str
    scoring_version: str
    threshold: float
    sources: tuple[str, ...]
    task_ids: tuple[str, ...]
    agents: tuple[AgentAggregate, ...]
    trials: tuple[TrialSummary, ...]
    canary_failures: tuple[str, ...] = ()
    generated_at: datetime
