from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.conftest import FIXTURE_INTAKE, FIXTURE_TASK_ID
from trialdesignbench.dataset import (
    DatasetError,
    attach_document,
    check_dataset,
    import_intake,
    load_dataset,
    read_intake,
    select_submissions,
    task_id_from_trial_id,
)


@pytest.mark.parametrize(
    ("trial_id", "expected"),
    [
        ("10.1200_jco-24-01818", "10-1200-jco-24-01818"),
        ("doi.org/10.1056/nejmoa2505669", "10-1056-nejmoa2505669"),
        ("https://doi.org/10.1056/NEJMoa2505669", "10-1056-nejmoa2505669"),
        ("NCT02578680", "nct02578680"),
    ],
)
def test_task_id_slug(trial_id: str, expected: str) -> None:
    assert task_id_from_trial_id(trial_id) == expected


def test_import_round_trip(dataset_dir: Path) -> None:
    manifest, tasks = load_dataset(dataset_dir)
    assert [e.task_id for e in manifest.tasks] == [FIXTURE_TASK_ID]
    record, rubrics = tasks[0]
    source = json.loads(FIXTURE_INTAKE.read_text())
    prompts = source["comparison"]["prompts"]

    assert record.trial_id == source["trial_id"]
    assert record.source.username == source["username"]
    assert record.source.submitted_at == source["submittedAt"]
    assert record.source.selection == "latest"
    assert record.document is not None
    assert [q.id for q in record.questions] == [p["id"] for p in prompts]

    # Every curated criterion survives with its importance and scoring.
    for q, p in zip(rubrics.questions, prompts):
        got = [(c.criterion, c.importance, c.scoring) for _, c in q.criteria()]
        want = [
            (c["criterion"], c["importance"], c["scoring"])
            for r in p["rubrics"]
            for c in r["criteria"]
        ]
        assert got == want
        assert [r.dimension for r in q.rubrics] == [
            r["dimension"] for r in p["rubrics"]
        ]

    # Skeleton mirrors extract_prompts.py from the intake tooling.
    derivation = next(
        q for q in record.questions if q.question_type == "derivation_required"
    )
    assert derivation.skeleton()["output"] == {
        "dimensions": {"inputs_used": None, "method": None, "calculated_value": None}
    }
    assert check_dataset(dataset_dir) == []


def test_task_json_has_no_rubric_text(dataset_dir: Path) -> None:
    task_json = (dataset_dir / FIXTURE_TASK_ID / "task.json").read_text()
    source = json.loads(FIXTURE_INTAKE.read_text())
    for p in source["comparison"]["prompts"]:
        for r in p["rubrics"]:
            for c in r["criteria"]:
                assert c["criterion"] not in task_json


def test_missing_document_fails_check(tmp_path: Path) -> None:
    out = tmp_path / "ds"
    manifest = import_intake([FIXTURE_INTAKE], out)
    assert not manifest.tasks[0].has_document
    problems = check_dataset(out)
    assert any("no source document" in p for p in problems)

    doc = tmp_path / "sap.md"
    doc.write_text("SAP text\n")
    attach_document(out, FIXTURE_TASK_ID, doc)
    assert check_dataset(out) == []


def test_check_detects_tampering(dataset_dir: Path) -> None:
    rubrics = dataset_dir / FIXTURE_TASK_ID / "rubrics.json"
    rubrics.write_text(rubrics.read_text().replace("Correctly", "Wrongly", 1))
    assert any("digest" in p for p in check_dataset(dataset_dir))


def test_latest_submission_wins_unless_username(tmp_path: Path) -> None:
    older = json.loads(FIXTURE_INTAKE.read_text())
    older["submittedAt"] = "2020-01-01T00:00:00+00:00"
    older["username"] = "someone_else"
    older_path = tmp_path / "older.json"
    older_path.write_text(json.dumps(older))
    subs = [read_intake(FIXTURE_INTAKE), read_intake(older_path)]

    chosen = select_submissions(subs)
    assert chosen[FIXTURE_TASK_ID][0].username == "JinchengZ"
    assert chosen[FIXTURE_TASK_ID][1] == 2

    chosen = select_submissions(subs, username="someone_else")
    assert chosen[FIXTURE_TASK_ID][0].path == older_path
    with pytest.raises(DatasetError, match="nobody"):
        select_submissions(subs, username="nobody")


def test_import_refuses_to_overwrite(dataset_dir: Path) -> None:
    with pytest.raises(DatasetError, match="not empty"):
        import_intake([FIXTURE_INTAKE], dataset_dir)


def test_invalid_intake_is_rejected(tmp_path: Path) -> None:
    bad = json.loads(FIXTURE_INTAKE.read_text())
    bad["comparison"]["prompts"][0]["question_type"] = "free_text"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(DatasetError, match="question_type"):
        import_intake([path], tmp_path / "out")


def test_all_curated_files_import(tmp_path: Path) -> None:
    files = sorted((FIXTURE_INTAKE.parent).glob("*.json"))
    manifest = import_intake(files, tmp_path / "all")
    assert len(manifest.tasks) == len(files)
    shutil.rmtree(tmp_path / "all")
