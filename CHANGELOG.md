# Changelog

## trialdesignbench 1.2.3

### Maintenance

- Bump the pinned Grok Build to 1.0.41 (was 1.0.40) (#102).

## trialdesignbench 1.2.2

### Improvements

- Omit judge sampling parameters for the generic `claude-fable`,
  `claude-mythos`, `claude-opus`, and `claude-sonnet` families so newer
  model versions need no prefix updates. Older models in these families
  also use API defaults instead of `temperature=0` (#93).
- Bump the pinned Codex CLI to 0.158.0 (was 0.157.1) and OpenCode to
  1.18.33 (was 1.18.32) (#96). Bump the pinned Claude Code to 2.1.284
  (was 2.1.283) (#99).

## trialdesignbench 1.2.1

### Documentation

- Format shell examples and update model references (#89).
- Redesign logo and favicon as a generated hex sticker (#90).

## trialdesignbench 1.2.0

### New features

- `tdb run --effort <level>` sets the model's reasoning effort as a
  first-class setting next to `--agent` and `--model` (#84).

    The level is checked against what each agent accepts before launch
    (Claude Code and OpenCode silently ignore levels they do not know),
    mapped to the agent's Harbor kwarg (`reasoning_effort`; `variant`
    for `opencode`), recorded in `tdb-run.json` (`agents[].effort`),
    and read back by `tdb report`, which now groups results by
    agent × model × effort and shows the level in the leaderboard.

    `--effort` is given once for all agents or once per `--agent`
    (`default` keeps the harness default), and the same agent and model
    may run at several levels in one job.

    Without `--effort`, `tdb run` warns that the harness default depends on
    the model and CLI version. The new `trialdesignbench.agents.Effort` record
    documents each agent's kwarg and levels; the Agents article lists them
    with each CLI's behavior.

### Maintenance

- Update the Docker image bundled dependency versions:
  R 4.6.1, uv 0.12.19, Node 24.21.0 (#82).

### Documentation

- Add a new article about closed book runs (#83).

    It documents how Harbor's egress control works and what it cannot stop,
    probe results showing which agent web tools run on the provider's servers
    and which the allowlist already blocks, provider-side controls,
    alternatives considered, what relaxing the prevented or refused policy
    would require, and a reusable probe task. The Environment, Agents, Grade,
    Run, and Usage articles now all link to it.

## trialdesignbench 1.1.0

### New features

- `tdb run` supports Grok Build 1.0.40 (`grok-build`, `xai/` models) and
  OpenCode 1.18.32 (`opencode`, `anthropic/`, `openai/`, and `xai/` models)
  besides Claude Code and Codex CLI, with per-provider API key checks and
  closed-book settings that remove their web search and URL fetch tools.
- New `trialdesignbench.agents` module: the single list of supported agents
  (providers, credentials, hosts, pins, closed-book kwargs and env) and of
  refused agents with the reason. `tdb run` refuses `antigravity-sdk` and
  `antigravity-cli` (web tools cannot be disabled through Harbor),
  `kimi-code` and `muse-code` (no ATIF trajectory), and any other unlisted
  Harbor agent.
- Two-phase network allowlists: `task.toml` now has an `[agent]` allowlist
  (model API hosts, applied during `agent.run()`) and an `[environment]`
  baseline (applied during agent setup), so agents that Harbor installs at
  setup can reach their install hosts only then. Tasks built by 1.0.0 must be
  rebuilt with `tdb build`.
- The network canary follows the two phases. `tdb env check --canary` also
  probes the agent phase with Harbor's `oracle` agent and requires the
  install hosts to be blocked there; its new `--provider` option picks the
  model API for agents with several providers.

### Improvements

- Bump the pinned Claude Code to 2.1.283 (was 2.1.277) and Codex CLI to
  0.157.1 (was 0.155.1), so their latest supported models can run.
- `--model` must be `<provider>/<model>` with a provider the agent supports,
  so the allowlisted API host always matches the model.
- The environment image adds a root-owned `/etc/grok/requirements.toml`
  that pins Grok Build's web search and URL fetch off, pre-seeds OpenCode's
  plugin package, bundles OpenCode's model catalog, and labels every agent
  pin, which `tdb run` checks against the image.
- `tdb-run.json` records the setup allowlist and each agent's setup hosts;
  the host table version is now 2.

### Bug fixes

- `tdb env check` now fails when a tool is missing from the image.
  `check_env.sh` printed `MISSING` but still exited 0.

### Documentation

- Add new Agents article: supported and refused agents, explicit credential
  setup for each agent and auth mode, closed-book settings, network hosts,
  and how to add an agent. The Gemini CLI caveat is replaced by the
  Antigravity one.
- Add new Judge article (model, `ANTHROPIC_API_KEY`, network), separate from
  agent usage.

## trialdesignbench 1.0.0

This release replaces the Mathpix/Codex ingestion pipeline with an
evaluation framework that uses Harbor as its execution backend. It is a
breaking change: the `init`, `configure`, and `convert` commands, workspace
`.env` handling, and the `mathpix`, `pipeline`, `prompt`, `codex`, `config`,
`status`, and `models` modules are removed.

### New features

- Canonical, versioned dataset: `tdb dataset import` turns curated intake JSON
  into per-task `task.json` (agent-visible), `document.md`, and hidden
  `rubrics.json` with digests. `tdb dataset check` fails loudly on missing
  documents or tampering; `tdb dataset attach-document` adds documents later.
- Harbor task materialization with `tdb build`: instruction preamble from the
  packaged prompt template, separate verifier container, hidden rubrics,
  `allowlist` networking, and a `tdb-build.json` provenance manifest.
- Decoupled grader, `tdb grade`: deterministic checks on `output.json`,
  `Rscript output.R`, numeric formatting, and a trajectory scan for network
  use, plus rubric judging with an Anthropic structured-output judge
  (majority voting with `--judge-votes`). Errors are explicit statuses and
  zero the reward. Writes `grade.json`, `reward.json`, `reward-details.json`,
  Rscript logs, and raw judge exchanges.
- Versioned scoring rules (`scoring_version = "1"`): importance weights
  High 3, Medium 2, Low 1; non-`Add` criteria are recorded and excluded.
- `tdb run` generates a Harbor `job.yaml`, validates `api` or `subscription`
  credentials, disables server-side web tools, fills per-agent network
  allowlists, supports agent/model matrices, and records a `tdb-run.json`
  manifest. `tdb regrade` wraps `harbor job regrade`.
- `tdb report` aggregates Harbor jobs and standalone grade directories into a
  leaderboard with per-task attempt statistics, breakdowns, and token and cost
  totals. Missing or errored tasks count as 0.
- Shared pinned Docker image (`tdb env build`, `tdb env check`) with R, a
  dated CRAN snapshot, pinned agent CLIs, the pharma skills, and the grader,
  plus a network canary (`--canary`) that proves the egress policy.

### Maintenance

- Drop the `openai-codex` git dependency and the Linux CI workarounds for it.
- Add optional extras `judge` (Anthropic SDK) and `harbor` (Python 3.12+).
- Set `[tool.uv] exclude-newer = "7 days"` for supply chain hygiene.
- Add a CI workflow that builds the environment image when it changes and
  on a weekly schedule.
- Replace hatchling with `uv_build` as the build backend.
  Ignore the `data/` directory in source and wheel builds.
  Use PEP 639 recommended `license` field in `pyproject.toml` (#73).

## trialdesignbench 0.2.2

### Improvements

- Add Rich-powered status output to `tdb convert` and `tdb run`, showing
  Mathpix upload, polling, download, artifact writing, prompt creation, Codex
  execution, and final artifact locations (#27).
- Stream supported Codex Python SDK turn events to the console during local
  design reproduction, including plan updates, commands, tool calls, file
  changes, final response completion, and token usage (#27).

## trialdesignbench 0.2.1

### Improvements

- Reuse existing non-empty Mathpix Markdown and metadata artifacts by default
  during `convert` and `run`, with `--force` available when a fresh Mathpix
  conversion is required (#14).
- Add `--http-timeout` to `convert` and `run` so large PDF uploads and other
  Mathpix HTTP requests can use a longer per-request timeout (#14).
- Make workspace `.env` configuration authoritative over shell environment
  variables, matching the documented workspace-scoped configuration behavior (#14).
- Write the run summary even when the Codex execution step fails, and
  avoid writing `prompt.md` before the local Codex runtime is importable (#14).

## trialdesignbench 0.2.0

### New features

- Implement a baseline approach for workflow for SAP/protocol PDF ingestion
  using Mathpix and design reproduction using local Codex runs (#10).

## trialdesignbench 0.1.0

### New features

- First version.
