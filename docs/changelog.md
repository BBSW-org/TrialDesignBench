# Changelog

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
