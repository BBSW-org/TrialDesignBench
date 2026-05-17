from __future__ import annotations

import importlib.util
import os
import sys

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_codex_runtime: test requires the local OpenAI Codex SDK runtime",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if not _should_skip_codex_runtime_tests():
        return

    skip = pytest.mark.skip(reason="local OpenAI Codex runtime is unavailable")
    for item in items:
        if "requires_codex_runtime" in item.keywords:
            item.add_marker(skip)


def _should_skip_codex_runtime_tests() -> bool:
    if os.environ.get("TDB_SKIP_CODEX_RUNTIME_TESTS"):
        return True
    if sys.platform.startswith("linux"):
        return (
            importlib.util.find_spec("openai_codex") is None
            or importlib.util.find_spec("codex_cli_bin") is None
        )
    return False
