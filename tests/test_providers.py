from __future__ import annotations

import re

import pytest

from trialdesignbench import agents
from trialdesignbench.judge import JUDGE_BACKENDS
from trialdesignbench.providers import PROVIDERS, split_model


def test_registry_is_consistent() -> None:
    for name, provider in PROVIDERS.items():
        assert provider.name == name
        assert re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", name), name
        assert provider.title
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*_API_KEY", provider.key_env), name
        # Exact hostnames only, so the allowlists stay portable.
        assert "*" not in provider.host and "/" not in provider.host
    # Agents and judges share the registry.
    assert agents.PROVIDERS is PROVIDERS
    assert set(JUDGE_BACKENDS) <= set(PROVIDERS)
    assert {p for a in agents.AGENTS for p in a.providers} == set(PROVIDERS)


def test_split_model() -> None:
    assert split_model("anthropic/claude-opus-5-5") == ("anthropic", "claude-opus-5-5")
    assert split_model("opencode-go/org/model") == ("opencode-go", "org/model")
    for bad in ("claude-opus-5-5", "openai/", "/gpt", ""):
        with pytest.raises(ValueError, match="must be <provider>/<model>"):
            split_model(bad)
