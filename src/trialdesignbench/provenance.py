"""Digests, versions, and identifiers recorded alongside every artifact."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


def package_version() -> str:
    try:
        return version("trialdesignbench")
    except PackageNotFoundError:
        return "0.0.0"


def utc_now() -> datetime:
    return datetime.now(UTC)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json(obj: Any) -> str:
    """Digest of canonical JSON (sorted keys, no whitespace)."""
    return sha256_text(json.dumps(obj, sort_keys=True, separators=(",", ":")))


def digest_tree(root: Path, exclude: Iterable[str] = ()) -> str:
    """Content digest of a directory: relative paths plus file hashes.

    Independent of timestamps and permissions, so it is stable across copies.
    """
    excluded = set(exclude)
    entries = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel in excluded or any(part in excluded for part in Path(rel).parts):
            continue
        entries.append(f"{rel}\0{sha256_file(path)}")
    return sha256_text("\n".join(entries))


def git_sha(path: Path | None = None) -> str | None:
    """HEAD commit of the repository containing `path`, with a dirty marker."""
    if shutil.which("git") is None:
        return None
    cwd = str(path or Path.cwd())
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    return f"{sha}-dirty" if dirty else sha


def command_output(args: list[str], timeout: float = 30.0) -> str | None:
    """First line of a command's stdout, or None if it cannot run."""
    if shutil.which(args[0]) is None:
        return None
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip() or result.stderr.strip()
    return out.splitlines()[0] if out else None


def harbor_version() -> str | None:
    return command_output(["harbor", "--version"])


def docker_image_inspect(image: str) -> dict[str, Any] | None:
    """`docker image inspect` output for a local image, or None."""
    if shutil.which("docker") is None:
        return None
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    return data[0] if data else None


def docker_image_digest(image: str) -> str | None:
    """Repo digest if the image was pulled, else the local image ID."""
    info = docker_image_inspect(image)
    if info is None:
        return None
    digests = info.get("RepoDigests") or []
    if digests:
        return str(digests[0])
    image_id = info.get("Id")
    return str(image_id) if image_id else None


def docker_image_labels(image: str) -> dict[str, str] | None:
    info = docker_image_inspect(image)
    if info is None:
        return None
    return dict((info.get("Config") or {}).get("Labels") or {})
