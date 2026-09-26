from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import requires_docker
from trialdesignbench import environment
from trialdesignbench.provenance import docker_image_labels, package_version


def _arg_defaults() -> dict[str, list[str]]:
    defaults: dict[str, list[str]] = {}
    for m in re.finditer(
        r"^ARG (\w+)=(\S+)$", environment.dockerfile_text(), re.MULTILINE
    ):
        defaults.setdefault(m.group(1), []).append(m.group(2))
    return defaults


def test_dockerfile_defaults_match_pins() -> None:
    defaults = _arg_defaults()
    for key, value in environment.PINS.build_args().items():
        assert defaults.get(key), f"Dockerfile lacks ARG {key}"
        assert set(defaults[key]) == {value}, key
    assert defaults["TDB_VERSION"] == [package_version()]


def test_stage_context_pypi(tmp_path: Path) -> None:
    info = environment.stage_context(tmp_path, tdb_source="pypi")
    assert info["tdb_source"] == "pypi"
    for name in (*environment.CONTEXT_FILES, "r-packages.txt"):
        assert (tmp_path / name).is_file()
    assert (tmp_path / "r-packages.txt").read_text().split() == list(
        environment.R_PACKAGES
    )


def test_build_command_passes_pins(tmp_path: Path) -> None:
    cmd = environment.build_command("t:1", tmp_path)
    assert cmd[:4] == ["docker", "build", "-t", "t:1"]
    assert (
        f"--build-arg=CLAUDE_CODE_VERSION={environment.PINS.claude_code_version}" in cmd
    )
    multi = environment.build_command(
        "t:1", tmp_path, platforms=["linux/amd64", "linux/arm64"]
    )
    assert multi[:4] == ["docker", "buildx", "build", "--platform"]


@requires_docker
def test_built_image_matches_pins() -> None:
    image = environment.default_image()
    labels = docker_image_labels(image)
    if labels is None:
        pytest.skip(f"image {image} not built locally (run `tdb env build`)")
    prefix = environment.LABEL_PREFIX
    assert (
        labels[f"{prefix}.claude-code-version"] == environment.PINS.claude_code_version
    )
    assert labels[f"{prefix}.codex-version"] == environment.PINS.codex_version
    assert (
        labels[f"{prefix}.pharma-skills-commit"]
        == environment.PINS.pharma_skills_commit
    )
    result = subprocess.run(
        environment.check_command(image), capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
