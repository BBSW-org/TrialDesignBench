from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from trialdesignbench.dataset import RUBRICS_FILE, import_intake, load_rubrics
from trialdesignbench.grade import RscriptResult
from trialdesignbench.schema import RubricSet

REPO = Path(__file__).resolve().parents[1]
FIXTURE_INTAKE = REPO / "data" / "json" / "10.1200_jco-24-01818.json"
FIXTURE_TASK_ID = "10-1200-jco-24-01818"

requires_rscript = pytest.mark.skipif(
    shutil.which("Rscript") is None, reason="needs an R installation (Rscript on PATH)"
)
requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None, reason="needs Docker"
)


@pytest.fixture
def intake_path() -> Path:
    return FIXTURE_INTAKE


@pytest.fixture
def documents_dir(tmp_path: Path) -> Path:
    docs = tmp_path / "documents"
    docs.mkdir()
    (docs / f"{FIXTURE_TASK_ID}.md").write_text(
        "# Statistical Analysis Plan\n\nSection 9.2: The primary endpoint is OS.\n"
    )
    return docs


@pytest.fixture
def dataset_dir(tmp_path: Path, documents_dir: Path) -> Path:
    out = tmp_path / "dataset"
    import_intake([FIXTURE_INTAKE], out, documents_dir=documents_dir)
    return out


@pytest.fixture
def rubrics_path(dataset_dir: Path) -> Path:
    return dataset_dir / FIXTURE_TASK_ID / RUBRICS_FILE


@pytest.fixture
def rubrics(rubrics_path: Path) -> RubricSet:
    return load_rubrics(rubrics_path)


def filled_output(rubrics: RubricSet) -> dict[str, Any]:
    """A complete, well-formed answer for every question."""
    entries = []
    for q in rubrics.questions:
        entry = q.question.skeleton()
        if q.question.question_type == "derivation_required":
            entry["output"]["dimensions"] = {
                "inputs_used": (
                    "HR 0.75, power 82%, one-sided alpha 0.025 (Section 9.2)"
                ),
                "method": "Schoenfeld approximation, implemented in output.R",
                "calculated_value": "400.0000 deaths",
            }
        else:
            entry["output"]["extracted_value"] = (
                "OS in the ITT population (Section 9.2)"
            )
        entries.append(entry)
    return {"output": entries}


def trajectory(tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    calls = tool_calls or [
        {
            "tool_call_id": "t1",
            "function_name": "Bash",
            "arguments": {"command": "Rscript output.R"},
        }
    ]
    return {
        "schema_version": "ATIF-v1.7",
        "session_id": "test",
        "agent": {"name": "claude-code", "version": "0"},
        "steps": [
            {"step_id": 1, "source": "user", "message": "instruction"},
            {"step_id": 2, "source": "agent", "message": "", "tool_calls": calls},
        ],
    }


@pytest.fixture
def make_submission(tmp_path: Path, rubrics: RubricSet) -> Callable[..., Path]:
    def make(
        *,
        output: dict[str, Any] | str | None = None,
        r_code: str | None = 'cat("result:", 400.0, "\\n")\n',
        traj: dict[str, Any] | None | bool = True,
        name: str = "submission",
    ) -> Path:
        sub = tmp_path / name
        sub.mkdir()
        data = filled_output(rubrics) if output is None else output
        (sub / "output.json").write_text(
            data if isinstance(data, str) else json.dumps(data, indent=2)
        )
        if r_code is not None:
            (sub / "output.R").write_text(r_code)
        if traj is True:
            (sub / "trajectory.json").write_text(json.dumps(trajectory()))
        elif isinstance(traj, dict):
            (sub / "trajectory.json").write_text(json.dumps(traj))
        return sub

    return make


def ok_rscript(script: Path, cwd: Path, timeout: float) -> RscriptResult:
    return RscriptResult(0, "result: 400\n", "")


def failing_rscript(script: Path, cwd: Path, timeout: float) -> RscriptResult:
    return RscriptResult(1, "", "Error: object 'x' not found\n")


def missing_rscript(script: Path, cwd: Path, timeout: float) -> RscriptResult:
    return RscriptResult(None, "", "", error="Rscript not found on PATH")
