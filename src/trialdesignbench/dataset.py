"""Import curated intake JSON into the canonical, versioned dataset format.

Layout written by `import_intake`:

    <dataset_dir>/
      dataset.json          DatasetManifest
      <task_id>/
        task.json           TaskRecord (agent-visible)
        document.md         protocol/SAP text, when available
        rubrics.json        RubricSet (hidden from the agent)
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from trialdesignbench.provenance import (
    digest_tree,
    package_version,
    sha256_file,
    sha256_text,
    utc_now,
)
from trialdesignbench.schema import (
    Criterion,
    DatasetManifest,
    DatasetTaskEntry,
    DocumentRef,
    Question,
    QuestionRubrics,
    Rubric,
    RubricSet,
    SubmissionSource,
    TaskRecord,
)

DATASET_FILE = "dataset.json"
TASK_FILE = "task.json"
RUBRICS_FILE = "rubrics.json"
DOCUMENT_FILE = "document.md"
DEFAULT_DATASET_VERSION = "1.0.0"

_QUESTION_TYPES = ("extraction_only", "derivation_required")
_IMPORTANCE = ("High", "Medium", "Low")


class DatasetError(ValueError):
    """Raised when intake data or a dataset directory is invalid."""


def task_id_from_trial_id(trial_id: str) -> str:
    """Filesystem- and registry-safe slug for a trial identifier.

    `10.1200_jco-24-01818` -> `10-1200-jco-24-01818`. A leading URL scheme
    and `doi.org/` are dropped so DOI spellings map to the same slug.
    """
    text = trial_id.strip().lower()
    text = re.sub(r"^[a-z]+://", "", text)
    text = re.sub(r"^(dx\.)?doi\.org/", "", text)
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not slug:
        raise DatasetError(f"Cannot derive a task id from trial id {trial_id!r}")
    return slug


@dataclass(frozen=True)
class IntakeSubmission:
    path: Path
    sha256: str
    data: dict[str, Any]

    @property
    def trial_id(self) -> str:
        return str(self.data["trial_id"])

    @property
    def task_id(self) -> str:
        return task_id_from_trial_id(self.trial_id)

    @property
    def username(self) -> str:
        return str(self.data.get("username") or "")

    @property
    def submitted_at(self) -> str:
        return str(self.data.get("submittedAt") or "")


def read_intake(path: Path) -> IntakeSubmission:
    """Read and minimally validate one curated intake JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"{path}: cannot read intake JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DatasetError(f"{path}: intake JSON must be an object")
    for key in ("trial_id", "submittedAt", "comparison"):
        if key not in data:
            raise DatasetError(f"{path}: missing required field {key!r}")
    prompts = (data.get("comparison") or {}).get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise DatasetError(f"{path}: comparison.prompts must be a non-empty list")
    return IntakeSubmission(path=path, sha256=sha256_file(path), data=data)


def select_submissions(
    submissions: Sequence[IntakeSubmission], username: str | None = None
) -> dict[str, tuple[IntakeSubmission, int]]:
    """Pick one submission per task: latest `submittedAt`, or the given user's."""
    by_task: dict[str, list[IntakeSubmission]] = {}
    for sub in submissions:
        by_task.setdefault(sub.task_id, []).append(sub)
    chosen: dict[str, tuple[IntakeSubmission, int]] = {}
    for task_id, subs in sorted(by_task.items()):
        pool = subs
        if username is not None:
            pool = [s for s in subs if s.username == username]
            if not pool:
                users = sorted({s.username for s in subs})
                raise DatasetError(
                    f"No submission by {username!r} for task {task_id}; "
                    f"available users: {users}"
                )
        pool = sorted(pool, key=lambda s: (s.submitted_at, s.path.name))
        chosen[task_id] = (pool[-1], len(subs))
    return chosen


def _dimension_slug(dimension: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", dimension.lower()).strip("-") or "answer"


def convert_prompts(
    sub: IntakeSubmission,
) -> tuple[tuple[Question, ...], tuple[QuestionRubrics, ...]]:
    """Split intake prompts into agent-visible questions and hidden rubrics."""
    prompts = sub.data["comparison"]["prompts"]
    questions: list[Question] = []
    graded: list[QuestionRubrics] = []
    seen: set[str] = set()
    for idx, p in enumerate(prompts):
        where = f"{sub.path}: prompts[{idx}]"
        qid = p.get("id")
        if not qid or not isinstance(qid, str):
            raise DatasetError(f"{where}: missing question id")
        if qid in seen:
            raise DatasetError(f"{where}: duplicate question id {qid!r}")
        seen.add(qid)
        qtype = p.get("question_type")
        if qtype not in _QUESTION_TYPES:
            raise DatasetError(f"{where}: unsupported question_type {qtype!r}")
        if not p.get("question"):
            raise DatasetError(f"{where}: empty question text")
        question = Question(
            id=qid,
            design_element=str(p.get("design_element") or ""),
            design_element_other=str(p.get("design_element_other") or ""),
            question=str(p["question"]),
            question_type=qtype,
        )
        rubrics: list[Rubric] = []
        counters: dict[str, int] = {}
        for r in p.get("rubrics") or []:
            dimension = str(r.get("dimension") or "")
            slug = _dimension_slug(dimension)
            criteria: list[Criterion] = []
            for c in r.get("criteria") or []:
                importance = c.get("importance")
                if importance not in _IMPORTANCE:
                    raise DatasetError(
                        f"{where}: unsupported importance {importance!r}"
                    )
                text = str(c.get("criterion") or "").strip()
                if not text:
                    raise DatasetError(f"{where}: empty criterion text")
                counters[slug] = counters.get(slug, 0) + 1
                criteria.append(
                    Criterion(
                        criterion_id=f"{qid}/{slug}/{counters[slug]}",
                        criterion=text,
                        importance=importance,
                        scoring=str(c.get("scoring") or ""),
                    )
                )
            rubrics.append(
                Rubric(
                    artifact=str(r.get("artifact") or "output.json"),
                    dimension=dimension,
                    criteria=tuple(criteria),
                )
            )
        questions.append(question)
        graded.append(QuestionRubrics(question=question, rubrics=tuple(rubrics)))
    return tuple(questions), tuple(graded)


def find_document(documents_dir: Path | None, sub: IntakeSubmission) -> Path | None:
    """Locate `<task_id>.md`, `<trial_id>.md`, or `<intake stem>.md`."""
    if documents_dir is None:
        return None
    names = [
        f"{sub.task_id}.md",
        f"{sub.trial_id.replace('/', '_')}.md",
        f"{sub.path.stem}.md",
    ]
    for name in names:
        candidate = documents_dir / name
        if candidate.is_file():
            return candidate
    return None


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(
        json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def write_model(path: Path, model: Any) -> None:
    path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")


def import_intake(
    intake_files: Sequence[Path],
    out: Path,
    *,
    documents_dir: Path | None = None,
    username: str | None = None,
    dataset_version: str = DEFAULT_DATASET_VERSION,
    publicly_indexed: bool | None = None,
    force: bool = False,
) -> DatasetManifest:
    """Import intake files into a canonical dataset directory."""
    if not intake_files:
        raise DatasetError("No intake files given")
    if out.exists() and any(out.iterdir()):
        if not force:
            raise DatasetError(f"{out} exists and is not empty (use --force)")
        shutil.rmtree(out)
    submissions = [read_intake(p) for p in intake_files]
    chosen = select_submissions(submissions, username=username)
    out.mkdir(parents=True, exist_ok=True)
    for task_id, (sub, n_candidates) in chosen.items():
        task_dir = out / task_id
        task_dir.mkdir()
        questions, graded = convert_prompts(sub)
        doc_ref: DocumentRef | None = None
        doc_path = find_document(documents_dir, sub)
        if doc_path is not None:
            shutil.copyfile(doc_path, task_dir / DOCUMENT_FILE)
            doc_ref = DocumentRef(
                sha256=sha256_file(task_dir / DOCUMENT_FILE),
                source=doc_path.name,
            )
        record = TaskRecord(
            id=task_id,
            trial_id=sub.trial_id,
            source=SubmissionSource(
                submission_id=str(sub.data.get("submissionId") or ""),
                username=sub.username,
                submitted_at=sub.submitted_at,
                version=sub.data.get("version"),
                intake_file=sub.path.name,
                intake_sha256=sub.sha256,
                selection="username" if username else "latest",
                candidates=n_candidates,
            ),
            document=doc_ref,
            publicly_indexed=publicly_indexed,
            questions=questions,
        )
        write_model(task_dir / TASK_FILE, record)
        write_model(
            task_dir / RUBRICS_FILE,
            RubricSet(task_id=task_id, trial_id=sub.trial_id, questions=graded),
        )
    return write_manifest(out, dataset_version=dataset_version)


def write_manifest(out: Path, *, dataset_version: str) -> DatasetManifest:
    """Recompute task digests and write `dataset.json`."""
    entries = []
    for task_dir in sorted(p for p in out.iterdir() if p.is_dir()):
        record = load_task(task_dir)
        entries.append(
            DatasetTaskEntry(
                task_id=record.id,
                trial_id=record.trial_id,
                digest=digest_tree(task_dir),
                has_document=record.document is not None,
            )
        )
    manifest = DatasetManifest(
        dataset_version=dataset_version,
        package_version=package_version(),
        created_at=utc_now(),
        digest=sha256_text("\n".join(f"{e.task_id}\0{e.digest}" for e in entries)),
        tasks=tuple(entries),
    )
    write_model(out / DATASET_FILE, manifest)
    return manifest


def _load(path: Path, model: Any) -> Any:
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DatasetError(f"{path}: {exc}") from exc
    except ValidationError as exc:
        raise DatasetError(f"{path}: invalid {model.__name__}: {exc}") from exc


def load_task(task_dir: Path) -> TaskRecord:
    """Load a `task.json`."""
    record: TaskRecord = _load(task_dir / TASK_FILE, TaskRecord)
    return record


def load_rubrics(path: Path) -> RubricSet:
    """Load a `rubrics.json`."""
    rubrics: RubricSet = _load(path, RubricSet)
    return rubrics


def load_manifest(dataset_dir: Path) -> DatasetManifest:
    """Load a `dataset.json`."""
    manifest: DatasetManifest = _load(dataset_dir / DATASET_FILE, DatasetManifest)
    return manifest


def load_dataset(
    dataset_dir: Path, task_ids: Iterable[str] | None = None
) -> tuple[DatasetManifest, list[tuple[TaskRecord, RubricSet]]]:
    """Load the manifest and (task, rubrics) pairs, optionally a subset."""
    manifest = load_manifest(dataset_dir)
    known = [e.task_id for e in manifest.tasks]
    wanted = list(task_ids) if task_ids else known
    unknown = sorted(set(wanted) - set(known))
    if unknown:
        raise DatasetError(f"Unknown task ids: {unknown}")
    tasks = []
    for task_id in wanted:
        task_dir = dataset_dir / task_id
        tasks.append((load_task(task_dir), load_rubrics(task_dir / RUBRICS_FILE)))
    return manifest, tasks


def check_dataset(dataset_dir: Path) -> list[str]:
    """Return every problem found. An empty list means the dataset is usable."""
    problems: list[str] = []
    try:
        manifest = load_manifest(dataset_dir)
    except DatasetError as exc:
        return [str(exc)]
    for entry in manifest.tasks:
        task_dir = dataset_dir / entry.task_id
        try:
            record = load_task(task_dir)
            rubrics = load_rubrics(task_dir / RUBRICS_FILE)
        except DatasetError as exc:
            problems.append(str(exc))
            continue
        prefix = f"{entry.task_id}:"
        if digest_tree(task_dir) != entry.digest:
            problems.append(f"{prefix} content digest does not match dataset.json")
        if record.document is None:
            problems.append(
                f"{prefix} no source document (document: null); add one with "
                "`tdb dataset attach-document`"
            )
        else:
            doc = task_dir / record.document.path
            if not doc.is_file():
                problems.append(f"{prefix} {record.document.path} is missing")
            elif sha256_file(doc) != record.document.sha256:
                problems.append(f"{prefix} {record.document.path} sha256 mismatch")
            elif not doc.read_text(encoding="utf-8").strip():
                problems.append(f"{prefix} {record.document.path} is empty")
        visible = [q.id for q in record.questions]
        hidden = [q.question.id for q in rubrics.questions]
        if visible != hidden:
            problems.append(f"{prefix} question ids differ between task and rubrics")
        for q in rubrics.questions:
            if not any(c.scoring == "Add" for _, c in q.criteria()):
                problems.append(f"{prefix} {q.question.id} has no `Add` criteria")
    return problems


def attach_document(
    dataset_dir: Path, task_id: str, document: Path, *, source: str | None = None
) -> DatasetManifest:
    """Add or replace a task's source document and refresh digests."""
    manifest = load_manifest(dataset_dir)
    task_dir = dataset_dir / task_id
    record = load_task(task_dir)
    text = document.read_text(encoding="utf-8")
    if not text.strip():
        raise DatasetError(f"{document} is empty")
    (task_dir / DOCUMENT_FILE).write_text(text, encoding="utf-8")
    updated = record.model_copy(
        update={
            "document": DocumentRef(
                sha256=sha256_file(task_dir / DOCUMENT_FILE),
                source=source or document.name,
            )
        }
    )
    write_model(task_dir / TASK_FILE, updated)
    return write_manifest(dataset_dir, dataset_version=manifest.dataset_version)
