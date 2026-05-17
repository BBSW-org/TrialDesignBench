from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from trialdesignbench import codex
from trialdesignbench.codex import LocalCodexRunner


def test_local_codex_runner_checks_runtime_before_writing_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_load() -> NoReturn:
        raise RuntimeError("SDK unavailable")

    run_directory = tmp_path / "run"
    monkeypatch.setattr(codex, "_load_openai_codex", fail_load)

    with pytest.raises(RuntimeError, match="SDK unavailable"):
        LocalCodexRunner().run(
            prompt="prompt text",
            run_directory=run_directory,
            model="gpt-test",
        )

    assert not (run_directory / "prompt.md").exists()
