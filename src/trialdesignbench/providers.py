"""Model providers: the APIs that agents and rubric judges call.

A provider is identified by one short kebab-case name, which is the
`<provider>/` prefix of every model string in TrialDesignBench: `tdb run
--model`, `tdb build --judge-model`, and `TDB_JUDGE_MODEL`. The name fixes
the API key variable and the exact API host, so the credential check and the
egress allowlist follow from the model string alone. `trialdesignbench.agents`
records which providers each agent may call; `trialdesignbench.judge` names
its judges after them (see the naming rule there).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    """A model API used with an API key."""

    name: str
    """Identifier: the `<provider>/` prefix of model strings (kebab-case)."""
    title: str
    """Display name."""
    key_env: str
    """Host variable holding the API key."""
    host: str
    """Hostname the API is reached at (exact, no wildcards, for allowlists)."""
    harbor_credential: bool = True
    """True when Harbor's model connection for the provider passes `key_env`
    to the agent itself. False when Harbor has no mapping for it: `tdb run`
    then adds `key_env = "${key_env}"` to `agents[].env`, which Harbor
    resolves from the host environment at launch, so the value is still
    never written to `job.yaml`."""


# Exact hostnames only (no wildcards) so the policy stays portable.
PROVIDERS: Mapping[str, Provider] = {
    p.name: p
    for p in (
        Provider("anthropic", "Anthropic", "ANTHROPIC_API_KEY", "api.anthropic.com"),
        Provider("openai", "OpenAI", "OPENAI_API_KEY", "api.openai.com"),
        Provider("xai", "xAI", "XAI_API_KEY", "api.x.ai"),
        # OpenCode Go, a subscription gateway with its own model ids; Harbor
        # only knows the `opencode` (Zen) provider under the same key.
        Provider(
            "opencode-go",
            "OpenCode Go",
            "OPENCODE_API_KEY",
            "opencode.ai",
            harbor_credential=False,
        ),
    )
}


def split_model(model: str) -> tuple[str, str]:
    """`(provider, model_id)` of a `<provider>/<model>` string.

    Purely syntactic: the first `/` separates the provider, so model ids may
    contain slashes. `ValueError` when either part is empty. Callers check
    the provider against the set they accept.
    """
    prefix, sep, rest = model.partition("/")
    if not sep or not prefix or not rest:
        raise ValueError(f"model must be <provider>/<model>; got {model!r}")
    return prefix, rest
