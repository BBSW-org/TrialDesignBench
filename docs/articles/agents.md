# Agents

`tdb run` launches agents through Harbor's
[pre-integrated agents](https://docs.harborframework.com/core-concepts/agents/pre-integrated-agents). Harbor
can run many more agents than TrialDesignBench accepts: an agent is supported
only when it can run closed book, meaning that its web tools can be turned
off, its network egress can be limited to the model API, and Harbor records
an ATIF trajectory that the grader can scan.

The tables below mirror the single list in the code,
`trialdesignbench.agents.AGENTS` (a test keeps them in sync); `tdb run`
refuses any other agent.

## Supported agents

| Agent | Harness | Pinned version | Installed | `--model` providers | `--auth` |
| --- | --- | --- | --- | --- | --- |
| `claude-code` | [Claude Code](https://code.claude.com/docs) | 2.1.283 | in the image | `anthropic` | `api`, `subscription` |
| `codex` | [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) | 0.157.1 | in the image | `openai` | `api`, `subscription` |
| `grok-build` | [Grok Build](https://docs.x.ai/build/overview) | 1.0.40 | by Harbor at setup | `xai` | `api` |
| `opencode` | [OpenCode](https://opencode.ai/docs/) | 1.18.32 | by Harbor at setup | `anthropic`, `openai`, `xai` | `api` |

- `--model` is always `<provider>/<model>`, for example
  `anthropic/claude-opus-5`, `openai/gpt-5.5`, or `xai/grok-4.7`. The provider
  decides which API key is required and which API host is allowlisted, so a
  model from any other provider is refused.
- The pinned version is passed to Harbor as the agent `version`;
  `--agent-version` may only repeat it. Pins live in
  `trialdesignbench.environment.PINS` and are recorded as image labels.
- *In the image*: the CLI is preinstalled in the shared image, so Harbor skips
  its install step. *By Harbor at setup*: Harbor's adapter reinstalls the CLI
  at every trial setup no matter what the image contains, so a few install
  hosts are reachable during setup only (see [Network](#network)).

Run one agent, or a matrix by repeating the pair:

```bash
uv run tdb run --tasks tmp/tasks --agent opencode --model anthropic/claude-opus-5
uv run tdb run --tasks tmp/tasks \
  --agent claude-code --model anthropic/claude-opus-5 \
  --agent grok-build --model xai/grok-4.7
```

## Authentication

Agent credentials and the [judge](judge.md) credential are separate
concerns. Agents authenticate to their own model API as described here; the
verifier's judge always needs `ANTHROPIC_API_KEY`, whichever agent runs.

Credentials are read from the environment of the shell that runs `tdb run`.
Harbor's adapters pick them up from there, so nothing is passed on the
command line. `tdb run` checks that the required variables or files exist
before launching and fails otherwise. Secret values are never written to
`job.yaml` or `tdb-run.json`; only variable names are recorded.

### API keys (`--auth api`, the default)

Export the key for the provider of each `--model`:

| Provider | Variable | API host | Keys from |
| --- | --- | --- | --- |
| `anthropic` | `ANTHROPIC_API_KEY` | `api.anthropic.com` | [Claude Console](https://platform.claude.com/) |
| `openai` | `OPENAI_API_KEY` | `api.openai.com` | [OpenAI Platform](https://platform.openai.com/api-keys) |
| `xai` | `XAI_API_KEY` | `api.x.ai` | [xAI Console](https://console.x.ai/home) |

```bash
export ANTHROPIC_API_KEY=...   # agent (anthropic/ models) and the judge
export OPENAI_API_KEY=...      # codex, or opencode with openai/ models
export XAI_API_KEY=...         # grok-build, or opencode with xai/ models
uv run tdb run --tasks tmp/tasks --agent codex --model openai/gpt-5.5
```

With `claude-code`, or `opencode` with an `anthropic/` model, the same
`ANTHROPIC_API_KEY` serves the agent and the judge.

### Subscriptions (`--auth subscription`)

Only `claude-code` and `codex` support subscription login through Harbor.
Subscription runs default to one concurrent trial (`--n-concurrent 1`).

| Agent | Plans | Requires | `tdb run` sets |
| --- | --- | --- | --- |
| `claude-code` | Pro, Max, Team, Enterprise | `CLAUDE_CODE_OAUTH_TOKEN` | `CLAUDE_FORCE_OAUTH=1` |
| `codex` | ChatGPT Plus, Pro, Business, Enterprise | `~/.codex/auth.json`, or a file named by `CODEX_AUTH_JSON_PATH` | `CODEX_FORCE_AUTH_JSON=1` |

Claude Code: create a long-lived token with `claude setup-token` and export
it. `CLAUDE_FORCE_OAUTH=1` makes Harbor drop `ANTHROPIC_API_KEY` from the
agent, so the key can stay exported for the judge.

```bash
claude setup-token                     # prints a one-year OAuth token
export CLAUDE_CODE_OAUTH_TOKEN=...
export ANTHROPIC_API_KEY=...           # still required by the judge
uv run tdb run --tasks tmp/tasks --agent claude-code \
  --model anthropic/claude-opus-5 --auth subscription
```

Codex: sign in once with `codex login` (or `codex login --device-auth` on a
headless machine). Harbor uploads that `auth.json` into each trial and does
not set `OPENAI_API_KEY` for the agent.

```bash
codex login                            # writes ~/.codex/auth.json
export ANTHROPIC_API_KEY=...           # required by the judge
uv run tdb run --tasks tmp/tasks --agent codex --model openai/gpt-5.5 \
  --auth subscription
```

## Closed-book settings

`tdb run` adds these settings to every agent it launches. They are
recorded in `tdb-run.json` (`agents[].kwargs`, `env_keys`, and
`network_policy.disabled_tools`).

| Agent | Web tools disabled | How |
| --- | --- | --- |
| `claude-code` | `WebSearch`, `WebFetch` | `disallowed_tools` plus a native settings file with `permissions.deny`; `DISABLE_TELEMETRY`, `DISABLE_ERROR_REPORTING`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` |
| `codex` | `web_search` | `web_search=disabled` |
| `grok-build` | `web_search`, `web_fetch` | `disable_web_search=true` and `[features] web_fetch = false` in the generated config, both also pinned in the image's `/etc/grok/requirements.toml`, which user config cannot override |
| `opencode` | `webfetch`, `websearch` | `permission` denies in `opencode_config` and `OPENCODE_PERMISSION`; auto-update, sharing, LSP, formatters, and snapshots off; model catalog read from the image (`OPENCODE_MODELS_PATH`) instead of fetched |

No MCP servers are ever added. Provider-side web tools never touch the
container network, which is why disabling them in the harness matters; the
grader additionally scans each trajectory for web tool calls (see
[Grade](grade.md)).

## Network

Every task has two allowlists (see [Environment](environment.md#network-policy-closed-book)):

- `[agent]`, during `agent.run()`: only the model API hosts of the agent and
  auth mode.
- `[environment]`, during agent setup and the healthcheck: the same hosts plus
  the install hosts of agents that Harbor installs at setup.

| Agent | `--auth` | Agent phase | Setup adds | Status |
| --- | --- | --- | --- | --- |
| `claude-code` | `api` | `api.anthropic.com` | nothing | verified by canary and a smoke run |
| `claude-code` | `subscription` | `api.anthropic.com` | nothing | unverified: add any OAuth refresh host a smoke run shows |
| `codex` | `api` | `api.openai.com` | nothing | verified by table only |
| `codex` | `subscription` | `chatgpt.com`, `auth.openai.com` | nothing | unverified |
| `grok-build` | `api` | `api.x.ai` | `archive.ubuntu.com`, `security.ubuntu.com`, `ports.ubuntu.com`, `x.ai` | canary and Harbor install-only run pass; needs a smoke run |
| `opencode` | `api` | the provider's API host | `raw.githubusercontent.com`, `github.com`, `nodejs.org`, `registry.npmjs.org` | canary and Harbor install-only run pass; needs a smoke run |

Entries not verified by a smoke run make `tdb run` print a warning. Confirm
them with `tdb env check --canary --agent <name> [--provider <provider>]` and
a smoke run before relying on them. For grok-build and opencode, the canary
(both phases) and a Harbor `--install-only` run (the real install under the
setup allowlist, amd64) pass; a run with a real API key would confirm that
the CLIs need no other host during `agent.run()`. grok-build's setup reaches
the Ubuntu mirrors because Harbor's adapter always runs `apt-get install
ca-certificates`.

## Refused agents

These Harbor agents were evaluated and are refused by `tdb run` with the
reason below. Any other Harbor agent is refused as unsupported.

| Agent | Reason |
| --- | --- |
| `antigravity-sdk` | Harbor's runner enables every Antigravity SDK tool, including the Google-side `search_web` and `read_url_content`, and offers no option to disable them. |
| `antigravity-cli` | Harbor's adapter rewrites the Antigravity CLI settings on every run and offers no option to disable its web search tool. |
| `kimi-code` | Harbor records no ATIF trajectory for it, so the grader cannot verify closed book and every trial would score 0. |
| `muse-code` | No ATIF trajectory (every trial would score 0), no documented way to disable its web tools, and Harbor cannot pin its version. |

## Adding or updating an agent

Everything lives in `src/trialdesignbench/agents.py`:

1. Read the agent's Harbor adapter (`harbor agent schema <name>` lists its
   kwargs). Check how it installs (does it skip when the pinned version is
   present?), which credentials it reads, which web tools it exposes and how
   to disable them, and that it writes an ATIF trajectory.
2. Add a pin to `ImagePins` and a matching `ARG` and label to the Dockerfile
   (a test enforces this), plus any closed-book settings the image must carry.
3. Add an `AgentProfile` to `AGENTS`: providers, pin, `preinstalled` or
   `setup_hosts`, closed-book `kwargs` and `env`, `disabled_tools`, and an
   optional `Subscription`. Add a new provider to `PROVIDERS` if needed.
4. Add the agent to the tables on this page (a test checks the supported and
   refused lists), and make sure the grader's `WEB_TOOL_NAMES` covers its
   disabled tools (also tested).
5. Run `tdb env check --canary --agent <name>` and a smoke run, then set
   `verified=True`.
