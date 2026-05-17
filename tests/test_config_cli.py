from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from trialdesignbench.cli import app
from trialdesignbench.config import (
    DEFAULT_CODEX_MODEL,
    configure_workspace,
    create_workspace,
    load_config,
)


def test_create_and_load_workspace_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MATHPIX_APP_ID", raising=False)
    monkeypatch.delenv("MATHPIX_APP_KEY", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.delenv("CODEX_BIN", raising=False)
    workspace = create_workspace(tmp_path / "workspace")

    assert (workspace / ".env").exists()
    assert ".env" in (workspace / ".gitignore").read_text(encoding="utf-8")

    env_file = configure_workspace(
        workspace,
        mathpix_app_id="app-id",
        mathpix_app_key="app-key",
        codex_model="gpt-test",
        codex_bin="/usr/local/bin/codex",
    )
    config = load_config(workspace)

    assert env_file == workspace / ".env"
    assert config.mathpix_app_id == "app-id"
    assert config.mathpix_app_key == "app-key"
    assert config.codex_model == "gpt-test"
    assert config.codex_bin == "/usr/local/bin/codex"


def test_cli_init_creates_workspace(tmp_path: Path) -> None:
    runner = CliRunner()
    workspace = tmp_path / "workspace"

    result = runner.invoke(app, ["init", str(workspace)])

    assert result.exit_code == 0
    assert (workspace / ".env").exists()
    assert (workspace / "converted").is_dir()
    assert f"CODEX_MODEL={DEFAULT_CODEX_MODEL}" in (workspace / ".env").read_text(
        encoding="utf-8"
    )
    assert str(workspace.resolve()) in result.stdout
