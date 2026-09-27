from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tests.conftest import FIXTURE_INTAKE, FIXTURE_TASK_ID
from trialdesignbench.build import (
    AGENT_HOSTS_MARKER,
    BUILD_MANIFEST,
    ENVIRONMENT_HOSTS_MARKER,
    BuildOptions,
    build_tasks,
    default_template,
)
from trialdesignbench.dataset import DatasetError, import_intake

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

IMAGE = "example.org/tdb-env:test"


@pytest.fixture
def tasks_dir(dataset_dir: Path, tmp_path: Path) -> Path:
    out = tmp_path / "tasks"
    build_tasks(dataset_dir, out, options=BuildOptions(image=IMAGE))
    return out


def test_task_toml_fields(tasks_dir: Path) -> None:
    config = tomllib.loads((tasks_dir / FIXTURE_TASK_ID / "task.toml").read_text())
    assert config["schema_version"] == "1.4"
    assert config["task"]["name"] == f"trialdesignbench/{FIXTURE_TASK_ID}"
    assert config["artifacts"] == [
        "/app/output.json",
        "/app/output.R",
        "/logs/agent/trajectory.json",
    ]
    meta = config["metadata"]
    assert meta["trial_id"] == "10.1200_jco-24-01818"
    assert meta["task_type"] == "reproduction"
    assert (
        meta["n_questions"],
        meta["n_extraction_only"],
        meta["n_derivation_required"],
    ) == (8, 5, 3)
    assert "Sample size and power" in meta["design_elements"]
    # Two network phases, both deny-all until `tdb run` fills them: the
    # [agent] allowlist for agent.run(), the [environment] baseline for setup.
    assert config["agent"] == {
        "timeout_sec": 3600.0,
        "user": "agent",
        "network_mode": "allowlist",
        "allowed_hosts": [],
    }
    env = config["environment"]
    assert env["docker_image"] == IMAGE
    assert env["skills_dir"] == "/skills"
    assert env["network_mode"] == "allowlist"
    assert env["allowed_hosts"] == []
    verifier = config["verifier"]
    assert verifier["environment_mode"] == "separate"
    assert verifier["timeout_sec"] == 1800.0
    assert verifier["env"]["ANTHROPIC_API_KEY"] == "${ANTHROPIC_API_KEY}"
    assert verifier["env"]["TDB_JUDGE_MODEL"]
    assert verifier["environment"]["network_mode"] == "allowlist"
    assert verifier["environment"]["allowed_hosts"] == ["api.anthropic.com"]
    assert "network_mode" not in verifier  # no verifier phase override


def test_placeholder_markers_present(tasks_dir: Path) -> None:
    text = (tasks_dir / FIXTURE_TASK_ID / "task.toml").read_text()
    assert text.count(AGENT_HOSTS_MARKER) == 1
    assert text.count(ENVIRONMENT_HOSTS_MARKER) == 1


def test_task_toml_parses_with_harbor(tasks_dir: Path) -> None:
    harbor_task = pytest.importorskip(
        "harbor.models.task.config", reason="harbor not installed"
    )
    config = harbor_task.TaskConfig.model_validate_toml(
        (tasks_dir / FIXTURE_TASK_ID / "task.toml").read_text()
    )
    assert config.agent.explicit_phase_policy() is not None


def test_instruction_contents(tasks_dir: Path, dataset_dir: Path) -> None:
    instruction = (tasks_dir / FIXTURE_TASK_ID / "instruction.md").read_text()
    assert instruction.startswith(default_template().strip()[:60])
    assert "/app/output.json" in instruction and "/app/output.R" in instruction
    assert "offline" in instruction and "install.packages" in instruction
    assert "Section 9.2: The primary endpoint is OS." in instruction
    block = instruction.split("```json\n", 1)[1].split("\n```", 1)[0]
    prompt = json.loads(block)["prompt"]
    assert [p["id"] for p in prompt][:2] == ["P-001", "P-002"]
    assert prompt[0]["output"] == {"extracted_value": None}


def test_instruction_contains_no_rubric_text(tasks_dir: Path) -> None:
    task = tasks_dir / FIXTURE_TASK_ID
    agent_visible = (task / "instruction.md").read_text() + "".join(
        p.read_text() for p in (task / "environment").rglob("*") if p.is_file()
    )
    source = json.loads(FIXTURE_INTAKE.read_text())
    for p in source["comparison"]["prompts"]:
        for r in p["rubrics"]:
            for c in r["criteria"]:
                assert c["criterion"] not in agent_visible
    # Rubrics live only in tests/.
    assert (task / "tests" / "rubrics.json").is_file()
    assert not any((task / "environment").iterdir())


def test_tests_dir(tasks_dir: Path) -> None:
    tests = tasks_dir / FIXTURE_TASK_ID / "tests"
    test_sh = (tests / "test.sh").read_text()
    assert "grade /app --rubrics /tests/rubrics.json --out /logs/verifier" in test_sh
    assert "--trajectory /logs/agent/trajectory.json" in test_sh
    assert 'EXPECTED_VERSION="' in test_sh
    if os.name != "nt":
        assert (tests / "test.sh").stat().st_mode & 0o111
    assert b"\r\n" not in (tests / "test.sh").read_bytes()
    dockerfile = (tests / "Dockerfile").read_text()
    assert dockerfile.startswith(f"FROM {IMAGE}")
    assert "COPY rubrics.json /tests/rubrics.json" in dockerfile


def test_build_manifest(tasks_dir: Path, dataset_dir: Path) -> None:
    info = json.loads((tasks_dir / BUILD_MANIFEST).read_text())
    dataset = json.loads((dataset_dir / "dataset.json").read_text())
    assert info["dataset_digest"] == dataset["digest"]
    assert info["image"] == IMAGE
    assert len(info["prompt_template_sha256"]) == 64
    assert info["task_ids"] == [FIXTURE_TASK_ID]


def test_missing_document_refused(tmp_path: Path) -> None:
    ds = tmp_path / "ds"
    import_intake([FIXTURE_INTAKE], ds)
    with pytest.raises(DatasetError, match="document"):
        build_tasks(ds, tmp_path / "t", options=BuildOptions(image=IMAGE))
    build_tasks(
        ds,
        tmp_path / "t",
        options=BuildOptions(image=IMAGE, allow_missing_document=True),
    )
    meta = tomllib.loads((tmp_path / "t" / FIXTURE_TASK_ID / "task.toml").read_text())
    assert meta["metadata"]["document_missing"] is True


def test_prompt_template_override(dataset_dir: Path, tmp_path: Path) -> None:
    template = tmp_path / "t.txt"
    template.write_text("CUSTOM PREAMBLE")
    build_tasks(
        dataset_dir,
        tmp_path / "t",
        options=BuildOptions(image=IMAGE),
        template_path=template,
    )
    text = (tmp_path / "t" / FIXTURE_TASK_ID / "instruction.md").read_text()
    assert text.startswith("CUSTOM PREAMBLE")


def test_editable_grader_and_pypi(dataset_dir: Path, tmp_path: Path) -> None:
    build_tasks(
        dataset_dir,
        tmp_path / "e",
        options=BuildOptions(image=IMAGE, grader_source="editable"),
    )
    tests = tmp_path / "e" / FIXTURE_TASK_ID / "tests"
    assert (tests / "grader" / "trialdesignbench" / "grade.py").is_file()
    assert "PYTHONPATH=/tests/grader" in (tests / "test.sh").read_text()
    assert "COPY grader/ /tests/grader/" in (tests / "Dockerfile").read_text()

    build_tasks(
        dataset_dir,
        tmp_path / "p",
        options=BuildOptions(image=IMAGE, grader_source="pypi"),
    )
    config = tomllib.loads((tmp_path / "p" / FIXTURE_TASK_ID / "task.toml").read_text())
    assert "pypi.org" in config["verifier"]["environment"]["allowed_hosts"]


def test_dockerfile_mode(dataset_dir: Path, tmp_path: Path) -> None:
    build_tasks(
        dataset_dir,
        tmp_path / "d",
        options=BuildOptions(image=IMAGE, dockerfile=True),
    )
    task = tmp_path / "d" / FIXTURE_TASK_ID
    config = tomllib.loads((task / "task.toml").read_text())
    assert "docker_image" not in config["environment"]
    assert (task / "environment" / "Dockerfile").is_file()
    assert (task / "environment" / "install_skills.sh").is_file()
    assert "COPY rubrics.json" in (task / "tests" / "Dockerfile").read_text()
