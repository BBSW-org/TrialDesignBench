"""Agents `tdb run` can launch closed book, and why others are refused.

This module is the one place to edit when adding, changing, or removing an
agent. Each `AgentProfile` in `AGENTS` records everything `tdb run` needs:

- the model providers the agent may call (the `provider/` prefix of
  `--model`), which fix the API key variable and the API host for
  `--auth api`;
- optional subscription login for `--auth subscription`;
- the pinned CLI version (an `ImagePins` field) and whether the CLI is
  preinstalled in the image or installed by Harbor during agent setup. In
  the latter case `setup_hosts` are reachable during setup only;
- the Harbor kwargs and env vars that disable web tools and nonessential
  traffic, and the disabled tool names recorded for provenance.

Harbor agents that are not in `AGENTS` are refused; `REFUSED_AGENTS` explains
why for the notable ones. To add an agent, check its Harbor adapter (for
example `harbor agent schema <name>`) for how it installs, authenticates, and
exposes web tools, pin its version in `ImagePins`, add a profile here, and
confirm the hosts with `tdb env check --canary --agent <name>` and a smoke
run before marking them verified.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from trialdesignbench import environment

AuthMode = Literal["api", "subscription"]

HOST_TABLE_VERSION = "2"


class AgentError(ValueError):
    """Raised when an agent, model, or credential cannot be used closed book."""


@dataclass(frozen=True)
class Provider:
    """A model API used with an API key."""

    key_env: str
    host: str


# Exact hostnames only (no wildcards) so the policy stays portable.
PROVIDERS: Mapping[str, Provider] = {
    "anthropic": Provider("ANTHROPIC_API_KEY", "api.anthropic.com"),
    "openai": Provider("OPENAI_API_KEY", "api.openai.com"),
    "xai": Provider("XAI_API_KEY", "api.x.ai"),
}


@dataclass(frozen=True)
class Subscription:
    """Subscription (OAuth) login supported by an agent's Harbor adapter."""

    hosts: tuple[str, ...]
    env: Mapping[str, str]
    """Agent env flags that make Harbor use the login instead of an API key."""
    token_env: str | None = None
    """Host variable holding a login token."""
    file_env: str | None = None
    """Host variable naming a credential file."""
    default_file: str | None = None
    """Credential file used when `file_env` is unset (`~` is the home dir)."""
    how: str = ""
    verified: bool = False
    note: str = ""


@dataclass(frozen=True)
class AgentProfile:
    """How to run one Harbor agent closed book."""

    name: str
    title: str
    providers: tuple[str, ...]
    version_pin: str
    """`ImagePins` field holding the pinned CLI version."""
    preinstalled: bool
    """True when the image ships the CLI and Harbor skips its install."""
    setup_hosts: tuple[str, ...] = ()
    """Hosts Harbor's install step needs; reachable during agent setup only."""
    kwargs: Mapping[str, Any] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    disabled_tools: tuple[str, ...] = ()
    subscription: Subscription | None = None
    verified: bool = False
    """True when the API and setup hosts were confirmed by the canary and a
    smoke run."""
    note: str = ""

    @property
    def version(self) -> str:
        return str(getattr(environment.PINS, self.version_pin))

    @property
    def image_label(self) -> str:
        """Image label recording the pinned version (see the Dockerfile)."""
        return f"{environment.LABEL_PREFIX}.{self.name}-version"


_CLAUDE_CODE_DENIED = ["WebSearch", "WebFetch"]
_OPENCODE_DENIED = {"webfetch": "deny", "websearch": "deny"}
# Hosts for Harbor's nvm + npm install of Node-based CLIs (nvm clones
# github.com when git is present).
_NVM_NPM_HOSTS = (
    "raw.githubusercontent.com",
    "github.com",
    "nodejs.org",
    "registry.npmjs.org",
)
# Ubuntu apt mirrors of the image (ports.ubuntu.com serves arm64).
_APT_HOSTS = ("archive.ubuntu.com", "security.ubuntu.com", "ports.ubuntu.com")

AGENTS: tuple[AgentProfile, ...] = (
    AgentProfile(
        name="claude-code",
        title="Claude Code",
        providers=("anthropic",),
        version_pin="claude_code_version",
        preinstalled=True,
        kwargs={
            "disallowed_tools": ",".join(_CLAUDE_CODE_DENIED),
            "config": {"permissions": {"deny": _CLAUDE_CODE_DENIED}},
        },
        env={
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
        disabled_tools=tuple(_CLAUDE_CODE_DENIED),
        subscription=Subscription(
            hosts=("api.anthropic.com",),
            env={"CLAUDE_FORCE_OAUTH": "1"},
            token_env="CLAUDE_CODE_OAUTH_TOKEN",
            how="create a token with `claude setup-token` (Pro, Max, Team, or "
            "Enterprise plan)",
            note="add any OAuth token refresh host observed in a smoke run",
        ),
        verified=True,
    ),
    AgentProfile(
        name="codex",
        title="Codex CLI",
        providers=("openai",),
        version_pin="codex_version",
        preinstalled=True,
        kwargs={"web_search": "disabled"},
        disabled_tools=("web_search",),
        subscription=Subscription(
            hosts=("chatgpt.com", "auth.openai.com"),
            env={"CODEX_FORCE_AUTH_JSON": "1"},
            file_env="CODEX_AUTH_JSON_PATH",
            default_file="~/.codex/auth.json",
            how="sign in with `codex login` (ChatGPT Plus, Pro, Business, or "
            "Enterprise plan)",
            note="auth.openai.com is the expected token refresh host; confirm "
            "by smoke test",
        ),
        verified=True,
    ),
    AgentProfile(
        name="grok-build",
        title="Grok Build",
        providers=("xai",),
        version_pin="grok_build_version",
        preinstalled=False,
        # Harbor's install runs `apt-get install ca-certificates` and x.ai's
        # installer on every setup.
        setup_hosts=(*_APT_HOSTS, "x.ai"),
        # The image also pins these in /etc/grok/requirements.toml.
        kwargs={
            "disable_web_search": True,
            "grok_config": {"features": {"web_fetch": False}},
        },
        disabled_tools=("web_search", "web_fetch"),
        note="setup hosts and both phases pass the canary and a Harbor "
        "install-only run; confirm the agent phase with a smoke run",
    ),
    AgentProfile(
        name="opencode",
        title="OpenCode",
        providers=("anthropic", "openai", "xai"),
        version_pin="opencode_version",
        preinstalled=False,
        # Harbor's install runs nvm and `npm i -g opencode-ai` on every setup.
        setup_hosts=_NVM_NPM_HOSTS,
        kwargs={
            "opencode_config": {
                "autoupdate": False,
                "share": "disabled",
                "lsp": False,
                "formatter": False,
                "snapshot": False,
                "permission": dict(_OPENCODE_DENIED),
            },
        },
        env={
            "OPENCODE_PERMISSION": json.dumps(_OPENCODE_DENIED),
            "OPENCODE_DISABLE_MODELS_FETCH": "1",
            "OPENCODE_MODELS_PATH": environment.OPENCODE_MODELS_PATH,
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        },
        disabled_tools=tuple(_OPENCODE_DENIED),
        note="setup hosts and both phases pass the canary and a Harbor "
        "install-only run; confirm the agent phase with a smoke run",
    ),
)

REFUSED_AGENTS: Mapping[str, str] = {
    "antigravity-sdk": (
        "Harbor's runner enables every Antigravity SDK tool, including "
        "search_web and read_url_content, and offers no option to disable them"
    ),
    "antigravity-cli": (
        "Harbor's adapter rewrites the Antigravity CLI settings on every run "
        "and offers no option to disable its web search tool"
    ),
    "kimi-code": (
        "Harbor records no ATIF trajectory for it, so the grader cannot "
        "verify closed book and every trial would score 0"
    ),
    "muse-code": (
        "Harbor records no ATIF trajectory for it (every trial would score "
        "0), it has no documented way to disable its web tools, and Harbor "
        "cannot pin its version"
    ),
}

_BY_NAME: Mapping[str, AgentProfile] = {a.name: a for a in AGENTS}


def supported_names() -> tuple[str, ...]:
    return tuple(_BY_NAME)


def get_profile(name: str) -> AgentProfile:
    """The profile for a Harbor agent name, or `AgentError` with the reason."""
    if name in _BY_NAME:
        return _BY_NAME[name]
    supported = ", ".join(_BY_NAME)
    if name in REFUSED_AGENTS:
        raise AgentError(
            f"{name} is not supported: {REFUSED_AGENTS[name]}. "
            f"Supported agents: {supported}"
        )
    raise AgentError(f"agent {name!r} is not supported. Supported agents: {supported}")


def model_provider(profile: AgentProfile, model: str) -> str:
    """The provider prefix of `model`, which must be one the agent may call."""
    prefix, sep, rest = model.partition("/")
    if not sep or not prefix or not rest:
        raise AgentError(
            f"--model for {profile.name} must be <provider>/<model> with "
            f"provider one of {', '.join(profile.providers)}; got {model!r}"
        )
    if prefix not in profile.providers:
        raise AgentError(
            f"{profile.name} cannot use {prefix!r} models closed book; "
            f"providers: {', '.join(profile.providers)}"
        )
    return prefix


def api_hosts(profile: AgentProfile, auth: AuthMode, provider: str) -> tuple[str, ...]:
    """Model API hosts the agent reaches during `agent.run()`."""
    if auth == "api":
        return (PROVIDERS[provider].host,)
    if profile.subscription is None:
        raise AgentError(f"{profile.name} supports only --auth api")
    return profile.subscription.hosts


def hosts_verified(profile: AgentProfile, auth: AuthMode) -> tuple[bool, str]:
    """Whether the host list for this agent and auth mode was smoke tested."""
    if auth == "subscription" and profile.subscription is not None:
        return profile.subscription.verified, profile.subscription.note
    note = profile.note or (
        f"confirm with `tdb env check --canary --agent {profile.name}` and a smoke run"
    )
    return profile.verified, note


def resolve_auth(
    profile: AgentProfile,
    auth: AuthMode,
    provider: str,
    env: Mapping[str, str],
) -> dict[str, str]:
    """Check the host credentials and return the agent env flags for them.

    Harbor's adapters read API keys and tokens from the host environment
    themselves; only the names are checked here, and values are never
    copied into `job.yaml`.
    """
    if auth == "api":
        key = PROVIDERS[provider].key_env
        if not env.get(key):
            raise AgentError(
                f"--auth api for {profile.name} with {provider} models requires "
                f"{key} in the environment"
            )
        return {}
    sub = profile.subscription
    if sub is None:
        raise AgentError(
            f"{profile.name} supports only --auth api through Harbor; "
            f"export {PROVIDERS[provider].key_env}"
        )
    if sub.token_env and not env.get(sub.token_env):
        raise AgentError(
            f"--auth subscription for {profile.name} requires {sub.token_env} "
            f"({sub.how})"
        )
    if sub.default_file or sub.file_env:
        explicit = env.get(sub.file_env) if sub.file_env else None
        path = Path(explicit or os.path.expanduser(sub.default_file or ""))
        if not path.is_file():
            alt = f" or set {sub.file_env}" if sub.file_env else ""
            raise AgentError(
                f"--auth subscription for {profile.name} requires a credential "
                f"file at {path} ({sub.how}){alt}"
            )
    return dict(sub.env)
