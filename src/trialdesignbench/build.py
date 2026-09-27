"""Materialize canonical dataset tasks as Harbor task directories.

Each task gets:

    <tasks_dir>/<task_id>/
      task.toml          Harbor config (network allowlist placeholder)
      instruction.md     prompt template + question skeleton + source document
      environment/       empty (prebuilt image) or our Dockerfile context
      tests/
        Dockerfile       verifier image: shared image + /tests files
        test.sh          runs `tdb grade`
        rubrics.json     hidden grading spec

Network policy has two phases. `[environment]` is the baseline during agent
setup (and the healthcheck); `[agent]` applies during `agent.run()`. Both
`allowed_hosts` lists are written empty (deny all) with marker comments;
`tdb run` fills them per agent and auth mode: the model API hosts for the
agent phase, plus Harbor's install hosts for the setup baseline when the
agent is not preinstalled in the image.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

from trialdesignbench import environment
from trialdesignbench.dataset import (
    RUBRICS_FILE,
    DatasetError,
    load_dataset,
)
from trialdesignbench.judge import DEFAULT_JUDGE_MODEL
from trialdesignbench.provenance import (
    digest_tree,
    package_version,
    sha256_text,
    utc_now,
)
from trialdesignbench.schema import RubricSet, TaskRecord

TASK_ORG = "trialdesignbench"
HARBOR_TASK_SCHEMA = "1.4"
BUILD_MANIFEST = "tdb-build.json"
AGENT_HOSTS_MARKER = "# tdb:agent-allowed-hosts"
ENVIRONMENT_HOSTS_MARKER = "# tdb:environment-allowed-hosts"
JUDGE_API_HOST = "api.anthropic.com"
PYPI_HOSTS = ("pypi.org", "files.pythonhosted.org")
ARTIFACTS = ("/app/output.json", "/app/output.R", "/logs/agent/trajectory.json")
TDB_BIN = "/opt/tdb/venv/bin/tdb"
TDB_PYTHON = "/opt/tdb/venv/bin/python"

GraderSource = Literal["image", "pypi", "editable"]

SANDBOX_NOTE = """\
## Sandbox

- This sandbox is offline. The only reachable network host is the model API \
used by your own harness. Web access, `curl`/`wget`, `git clone`, \
`pip install`, and `install.packages()` will fail; do not attempt them.
- R {r_version} is installed with these packages preinstalled: {packages}.
- Agent skills (for example `group-sequential-design`) are available in your \
skills directory.
- Work in `/app`. Everything you need is in this instruction."""

OUTPUT_NOTE = """\
## Required output files

Write exactly these two files:

- `/app/output.json`: a JSON object with a single key `output` whose value is \
the question array below, with every `null` replaced by your answer.
- `/app/output.R`: R code implementing every derivation-required calculation \
as reusable functions, printing the source inputs, the method and formula, \
and the final value for each question. It must run with `Rscript output.R` \
from `/app`."""


def default_template() -> str:
    """The packaged prompt template (copy of the intake system prompt)."""
    return (
        files("trialdesignbench.templates")
        .joinpath("system_prompt.txt")
        .read_text(encoding="utf-8")
    )


def render_instruction(record: TaskRecord, document: str | None, template: str) -> str:
    """Agent instruction: template, sandbox note, question block, outputs, document."""
    block = json.dumps(record.skeleton(), indent=2, ensure_ascii=False)
    doc = (
        document
        if document is not None
        else (
            "**The source document for this task is missing.** State in every "
            "answer that the value is not derivable from the input document."
        )
    )
    parts = [
        template.strip(),
        SANDBOX_NOTE.format(
            r_version=environment.PINS.r_version,
            packages=", ".join(environment.R_PACKAGES),
        ),
        "## Evaluation questions (prompt block)\n\n```json\n" + block + "\n```",
        OUTPUT_NOTE,
        "## Input document\n\n" + doc.strip(),
    ]
    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Minimal TOML emitter for the task.toml shape we generate
# ---------------------------------------------------------------------------


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    raise TypeError(f"unsupported TOML value {value!r}")


def _toml_table(
    name: str, items: dict[str, Any], comments: dict[str, str] | None = None
) -> str:
    lines = [f"[{name}]"]
    for key, value in items.items():
        if value is None:
            continue
        line = f"{key} = {_toml_value(value)}"
        if comments and key in comments:
            line += f"  {comments[key]}"
        lines.append(line)
    return "\n".join(lines)


@dataclass(frozen=True)
class BuildOptions:
    image: str
    dockerfile: bool = False
    grader_source: GraderSource = "image"
    template: str | None = None
    agent_timeout_sec: float = 3600.0
    verifier_timeout_sec: float = 1800.0
    cpus: int = 2
    memory_mb: int = 4096
    judge_model: str = DEFAULT_JUDGE_MODEL
    allow_missing_document: bool = False


def render_task_toml(
    record: TaskRecord,
    *,
    options: BuildOptions,
    dataset_version: str,
    dataset_digest: str,
    task_digest: str,
    template_sha256: str,
) -> str:
    """Harbor `task.toml` for one task."""
    extraction = sum(q.question_type == "extraction_only" for q in record.questions)
    metadata: dict[str, Any] = {
        "trial_id": record.trial_id,
        "task_id": record.id,
        "task_type": record.task_type,
        "n_questions": len(record.questions),
        "n_extraction_only": extraction,
        "n_derivation_required": len(record.questions) - extraction,
        "design_elements": record.design_elements,
        "dataset_version": dataset_version,
        "dataset_digest": dataset_digest,
        "task_digest": task_digest,
        "document_missing": record.document is None,
        "publicly_indexed": record.publicly_indexed,
        "source_username": record.source.username,
        "source_submitted_at": record.source.submitted_at,
        "tdb_version": package_version(),
        "prompt_template_sha256": template_sha256,
        "grader_source": options.grader_source,
        "verifier_base_image": None if options.dockerfile else options.image,
    }
    verifier_hosts = [JUDGE_API_HOST]
    if options.grader_source == "pypi":
        verifier_hosts += list(PYPI_HOSTS)
    env: dict[str, Any] = {
        "network_mode": "allowlist",
        "allowed_hosts": [],
        "docker_image": None if options.dockerfile else options.image,
        "skills_dir": "/skills",
        "cpus": options.cpus,
        "memory_mb": options.memory_mb,
        "build_timeout_sec": 3600.0,
    }
    sections = [
        (
            f'schema_version = "{HARBOR_TASK_SCHEMA}"\n'
            f"artifacts = {_toml_value(list(ARTIFACTS))}"
        ),
        _toml_table(
            "task",
            {
                "name": f"{TASK_ORG}/{record.id}",
                "version": dataset_version,
                "description": (
                    f"Reproduce the statistical design of trial {record.trial_id} "
                    "from its protocol/SAP."
                ),
                "keywords": ["clinical-trials", "biostatistics", "r", "reproduction"],
            },
        ),
        _toml_table("metadata", metadata),
        _toml_table(
            "agent",
            {
                "timeout_sec": options.agent_timeout_sec,
                "user": "agent",
                "network_mode": "allowlist",
                "allowed_hosts": [],
            },
            comments={"allowed_hosts": AGENT_HOSTS_MARKER},
        ),
        _toml_table(
            "environment", env, comments={"allowed_hosts": ENVIRONMENT_HOSTS_MARKER}
        ),
        _toml_table(
            "verifier",
            {
                "environment_mode": "separate",
                "timeout_sec": options.verifier_timeout_sec,
            },
        ),
        _toml_table(
            "verifier.env",
            {
                "TDB_JUDGE_MODEL": options.judge_model,
                "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
            },
        ),
        _toml_table(
            "verifier.environment",
            {
                "network_mode": "allowlist",
                "allowed_hosts": verifier_hosts,
                "cpus": options.cpus,
                "memory_mb": options.memory_mb,
                "build_timeout_sec": 3600.0,
            },
        ),
    ]
    return "\n\n".join(sections) + "\n"


def render_test_sh(grader_source: GraderSource, version: str) -> str:
    """Verifier `test.sh` for the chosen grader source."""
    grade_args = (
        "grade /app --rubrics /tests/rubrics.json --out /logs/verifier "
        "--trajectory /logs/agent/trajectory.json"
    )
    head = f"""#!/usr/bin/env bash
# Generated by trialdesignbench {version}. Runs the grader in the separate
# verifier container. A missing reward.json (grader crash, version mismatch)
# makes Harbor record a verifier error instead of a score.
set -uo pipefail
mkdir -p /logs/verifier
EXPECTED_VERSION="{version}"
"""
    if grader_source == "image":
        body = f"""
actual="$({TDB_BIN} --version 2>/dev/null || true)"
if [ "${{actual}}" != "${{EXPECTED_VERSION}}" ]; then
    echo "tdb grader version mismatch: image has '${{actual}}', task expects '${{EXPECTED_VERSION}}'" >&2
    exit 1
fi
{TDB_BIN} {grade_args}
"""
    elif grader_source == "pypi":
        body = f"""
uvx --from "trialdesignbench[judge]==${{EXPECTED_VERSION}}" tdb {grade_args}
"""
    else:
        body = f"""
# Editable grader: package source copied into /tests/grader at build time.
PYTHONPATH=/tests/grader {TDB_PYTHON} -m trialdesignbench.cli.main {grade_args}
"""
    return head + body


def render_tests_dockerfile(options: BuildOptions) -> str:
    """Verifier image definition: shared image plus /tests files."""
    copy = "COPY --chmod=755 test.sh /tests/test.sh\nCOPY rubrics.json /tests/rubrics.json\n"
    if options.grader_source == "editable":
        copy += "COPY grader/ /tests/grader/\n"
    if options.dockerfile:
        base = environment.dockerfile_text().rstrip() + "\n\n# --- Verifier files ---\n"
        return base + copy
    return f"FROM {options.image}\n\n{copy}"


def _package_source_dir() -> Path:
    return Path(str(files("trialdesignbench")))


def build_task(
    record: TaskRecord,
    rubrics: RubricSet,
    task_src: Path,
    out: Path,
    *,
    options: BuildOptions,
    dataset_version: str,
    dataset_digest: str,
    template: str,
) -> None:
    """Write one Harbor task directory."""
    task_dir = out / record.id
    if task_dir.exists():
        shutil.rmtree(task_dir)
    (task_dir / "environment").mkdir(parents=True)
    tests = task_dir / "tests"
    tests.mkdir()
    document = None
    if record.document is not None:
        document = (task_src / record.document.path).read_text(encoding="utf-8")
    elif not options.allow_missing_document:
        raise DatasetError(
            f"{record.id}: no source document; attach one with "
            "`tdb dataset attach-document` or pass --allow-missing-document"
        )
    (task_dir / "instruction.md").write_text(
        render_instruction(record, document, template), encoding="utf-8", newline="\n"
    )
    (task_dir / "task.toml").write_text(
        render_task_toml(
            record,
            options=options,
            dataset_version=dataset_version,
            dataset_digest=dataset_digest,
            task_digest=digest_tree(task_src),
            template_sha256=sha256_text(template),
        ),
        encoding="utf-8",
    )
    shutil.copyfile(task_src / RUBRICS_FILE, tests / RUBRICS_FILE)
    test_sh = tests / "test.sh"
    test_sh.write_text(render_test_sh(options.grader_source, package_version()))
    test_sh.chmod(0o755)
    if options.grader_source == "editable":
        shutil.copytree(
            _package_source_dir(),
            tests / "grader" / "trialdesignbench",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    if options.dockerfile:
        environment.stage_context(task_dir / "environment")
        environment.stage_context(tests)
    (tests / "Dockerfile").write_text(render_tests_dockerfile(options), newline="\n")


def build_tasks(
    dataset_dir: Path,
    out: Path,
    *,
    options: BuildOptions,
    task_ids: Sequence[str] | None = None,
    template_path: Path | None = None,
) -> dict[str, Any]:
    """Build Harbor tasks for a dataset and write `tdb-build.json`."""
    manifest, tasks = load_dataset(dataset_dir, task_ids)
    template = (
        template_path.read_text(encoding="utf-8")
        if template_path
        else default_template()
    )
    missing = [r.id for r, _ in tasks if r.document is None]
    if missing and not options.allow_missing_document:
        raise DatasetError(
            f"tasks without a source document: {missing}; attach documents with "
            "`tdb dataset attach-document` or pass --allow-missing-document"
        )
    out.mkdir(parents=True, exist_ok=True)
    for record, rubrics in tasks:
        build_task(
            record,
            rubrics,
            dataset_dir / record.id,
            out,
            options=options,
            dataset_version=manifest.dataset_version,
            dataset_digest=manifest.digest,
            template=template,
        )
    info: dict[str, Any] = {
        "schema_version": "1",
        "package_version": package_version(),
        "dataset_dir": str(dataset_dir.resolve()),
        "dataset_version": manifest.dataset_version,
        "dataset_digest": manifest.digest,
        "task_ids": [r.id for r, _ in tasks],
        "tasks_missing_document": missing,
        "prompt_template": str(template_path) if template_path else "default",
        "prompt_template_sha256": sha256_text(template),
        "image": None if options.dockerfile else options.image,
        "dockerfile_mode": options.dockerfile,
        "grader_source": options.grader_source,
        "judge_model": options.judge_model,
        "image_pins": environment.PINS.build_args(),
        "built_at": utc_now().isoformat(),
    }
    if options.grader_source == "editable":
        info["grader_source_digest"] = digest_tree(
            _package_source_dir(), exclude=("__pycache__",)
        )
    (out / BUILD_MANIFEST).write_text(json.dumps(info, indent=2) + "\n", newline="\n")
    return info
