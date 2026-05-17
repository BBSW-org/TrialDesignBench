"""Workspace and environment configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv, set_key

DEFAULT_WORKSPACE_NAME = "tdb-workspace"
ENV_FILENAME = ".env"
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_EFFORT = "high"


@dataclass(frozen=True, slots=True)
class TdbConfig:
    """Runtime configuration loaded from a workspace `.env` file."""

    workspace: Path
    env_file: Path
    mathpix_app_id: str
    mathpix_app_key: str
    codex_model: str = DEFAULT_CODEX_MODEL
    codex_bin: str | None = None


def create_workspace(path: Path) -> Path:
    """Create a local TrialDesignBench workspace."""
    workspace = path.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    for child in ("input", "converted", "runs"):
        (workspace / child).mkdir(exist_ok=True)

    gitignore = workspace / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "\n".join(
                [
                    ".env",
                    "converted/",
                    "runs/",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    env_file = workspace / ENV_FILENAME
    if not env_file.exists():
        env_file.write_text(
            "\n".join(
                [
                    "MATHPIX_APP_ID=",
                    "MATHPIX_APP_KEY=",
                    f"CODEX_MODEL={DEFAULT_CODEX_MODEL}",
                    "CODEX_BIN=",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    return workspace


def configure_workspace(
    workspace: Path,
    *,
    mathpix_app_id: str,
    mathpix_app_key: str,
    codex_model: str = DEFAULT_CODEX_MODEL,
    codex_bin: str | None = None,
) -> Path:
    """Write user configuration values into the workspace `.env` file."""
    resolved = create_workspace(workspace)
    env_file = resolved / ENV_FILENAME
    set_key(str(env_file), "MATHPIX_APP_ID", mathpix_app_id)
    set_key(str(env_file), "MATHPIX_APP_KEY", mathpix_app_key)
    set_key(str(env_file), "CODEX_MODEL", codex_model)
    if codex_bin:
        set_key(str(env_file), "CODEX_BIN", codex_bin)
    return env_file


def load_config(workspace: Path) -> TdbConfig:
    """Load configuration from a workspace `.env` file."""
    resolved = workspace.expanduser().resolve()
    env_file = resolved / ENV_FILENAME
    if not env_file.exists():
        msg = f"Configuration file not found: {env_file}. Run `tdb init {resolved}` first."
        raise FileNotFoundError(msg)

    values = dotenv_values(env_file)
    load_dotenv(env_file, override=False)

    app_id = os.environ.get("MATHPIX_APP_ID") or values.get("MATHPIX_APP_ID") or ""
    app_key = os.environ.get("MATHPIX_APP_KEY") or values.get("MATHPIX_APP_KEY") or ""
    if not app_id or not app_key:
        msg = (
            "Missing Mathpix credentials. Set MATHPIX_APP_ID and MATHPIX_APP_KEY "
            f"in {env_file} or run `tdb configure`."
        )
        raise ValueError(msg)

    codex_model = (
        os.environ.get("CODEX_MODEL")
        or values.get("CODEX_MODEL")
        or DEFAULT_CODEX_MODEL
    )
    codex_bin = os.environ.get("CODEX_BIN") or values.get("CODEX_BIN")
    return TdbConfig(
        workspace=resolved,
        env_file=env_file,
        mathpix_app_id=app_id,
        mathpix_app_key=app_key,
        codex_model=codex_model,
        codex_bin=codex_bin,
    )
