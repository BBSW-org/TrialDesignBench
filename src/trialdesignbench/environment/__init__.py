"""Shared, pinned Docker image for agent and verifier environments.

The pins below are the single source of truth: `tdb env build` passes them to
the Dockerfile as build arguments and records them as image labels, and
`tdb run` refuses agent versions that differ from these pins. Which agents
use which pin is recorded in `trialdesignbench.agents`.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path

from trialdesignbench.provenance import package_version

LABEL_PREFIX = "org.trialdesignbench"


@dataclass(frozen=True)
class ImagePins:
    r_version: str = "4.6.1"
    cran_snapshot: str = "2026-09-27"
    ubuntu_codename: str = "noble"
    node_version: str = "24.21.0"
    uv_version: str = "0.12.23"
    # Agent CLIs (Harbor agent versions).
    claude_code_version: str = "2.1.292"
    codex_version: str = "0.161.0"
    grok_build_version: str = "1.0.46"
    opencode_version: str = "1.18.35"
    pharma_skills_repo: str = "https://github.com/RConsortium/pharma-skills.git"
    pharma_skills_commit: str = "4bd5632509a343a674d0762266f1cea9a6d382ad"

    def build_args(self) -> dict[str, str]:
        return {k.upper(): v for k, v in asdict(self).items()}


PINS = ImagePins()

R_PACKAGES = (
    "gsDesign",
    "gsDesign2",
    "rpact",
    "lrstat",
    "graphicalMCP",
    "eventPred",
    "survival",
    "mvtnorm",
    "jsonlite",
    "dplyr",
    "ggplot2",
    "digest",
)
"""Packages the agent may use. Includes everything referenced by the
`group-sequential-design` skill's reference and examples."""

CONTEXT_FILES = (
    "Dockerfile",
    "install_r_packages.R",
    "install_skills.sh",
    "check_env.sh",
    "grok-requirements.toml",
)


OPENCODE_MODELS_PATH = "/opt/tdb/opencode-models.json"
"""OpenCode model catalog downloaded at image build time. The catalog bundled
in the OpenCode binary lags behind new models, and trials cannot fetch it."""


def default_image() -> str:
    return f"trialdesignbench-env:{package_version()}"


def dockerfile_text() -> str:
    return files(__package__).joinpath("Dockerfile").read_text(encoding="utf-8")


def _source_root() -> Path | None:
    """Repository root when running from a source checkout."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src").is_dir():
            return parent
    return None


def stage_context(dest: Path, *, tdb_source: str = "auto") -> dict[str, str]:
    """Write a complete Docker build context to `dest`.

    `tdb_source` controls how the grader package gets into the image:
    `local` builds a wheel from this source checkout, `pypi` installs the
    pinned release, `auto` picks `local` when a checkout is available.
    Returns a description of what was staged.
    """
    dest.mkdir(parents=True, exist_ok=True)
    pkg = files(__package__)
    for name in CONTEXT_FILES:
        (dest / name).write_text(
            pkg.joinpath(name).read_text(encoding="utf-8"), newline="\n"
        )
    (dest / "install_skills.sh").chmod(0o755)
    (dest / "check_env.sh").chmod(0o755)
    (dest / "r-packages.txt").write_text("\n".join(R_PACKAGES) + "\n", newline="\n")
    dist = dest / "dist"
    dist.mkdir(exist_ok=True)
    (dist / ".keep").write_text("", newline="\n")
    root = _source_root()
    mode = tdb_source
    if mode == "auto":
        mode = "local" if root is not None and shutil.which("uv") else "pypi"
    info = {"tdb_source": mode, "tdb_version": package_version()}
    if mode == "local":
        if root is None or shutil.which("uv") is None:
            raise RuntimeError("--tdb-source local needs a source checkout and `uv`")
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(dist)],
            cwd=root,
            check=True,
            capture_output=True,
        )
        # Pin the grader's dependencies to the lock file for reproducibility.
        reqs = subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--extra",
                "judge",
                "--no-emit-project",
                "--no-hashes",
            ],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        (dist / "requirements.txt").write_text(reqs, newline="\n")
        wheels = sorted(p.name for p in dist.glob("*.whl"))
        info["wheel"] = wheels[-1] if wheels else ""
    elif mode != "pypi":
        raise ValueError(f"unknown tdb source {tdb_source!r}")
    return info


def build_command(
    tag: str,
    context: Path,
    *,
    platforms: list[str] | None = None,
    push: bool = False,
    no_cache: bool = False,
) -> list[str]:
    """`docker build` for the native platform, `docker buildx build` otherwise."""
    args = [f"--build-arg={k}={v}" for k, v in PINS.build_args().items()]
    args.append(f"--build-arg=TDB_VERSION={package_version()}")
    if no_cache:
        args.append("--no-cache")
    if platforms:
        cmd = [
            "docker",
            "buildx",
            "build",
            "--platform",
            ",".join(platforms),
            "-t",
            tag,
        ]
        cmd += ["--push"] if push else []
    else:
        cmd = ["docker", "build", "-t", tag]
    return [*cmd, *args, str(context)]


def check_command(tag: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        tag,
        "bash",
        "/opt/tdb/check_env.sh",
    ]


def docker_host_warning() -> str | None:
    """Explain when the Docker host cannot enforce Harbor's allowlist policy."""
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{.OperatingSystem}}|{{.KernelVersion}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "docker is not available"
    if info.returncode != 0:
        return "docker daemon is not reachable"
    os_name = info.stdout.strip()
    if "Docker Desktop" in os_name:
        return (
            f"Docker host is {os_name}. Harbor's `allowlist` network mode needs a "
            "Linux kernel with nft_fib support, which Docker Desktop's LinuxKit "
            "kernel may lack. Use a Linux Docker host, or OrbStack on macOS."
        )
    return None
