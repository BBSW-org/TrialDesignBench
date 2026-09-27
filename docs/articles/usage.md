# Overview

`trialdesignbench` is a thin evaluation framework. It owns the task schema,
task materialization, the grader, scoring rules, aggregation, and provenance.
[Harbor](https://github.com/harbor-framework/harbor) is the execution backend:
it runs first-party agent harnesses (Claude Code, Codex CLI, Grok Build,
OpenCode; see [Agents](agents.md)) inside Docker with concurrency, retries,
trajectories, and token accounting.

The two tools meet only through files:

- the Harbor task directory format (`task.toml`, `instruction.md`,
  `environment/`, `tests/`),
- a generated `job.yaml` passed to `harbor run -c`,
- the job directory Harbor writes (`result.json`, `verifier/reward.json`,
  `verifier/reward-details.json`, `agent/trajectory.json`).

`trialdesignbench` never imports Harbor, so the core package stays light and
runs on Python 3.10+. Harbor is an optional extra that needs Python 3.12+.

## Pipeline

```text
curated intake JSON ──tdb dataset import──▶ canonical dataset
                                             │ tdb build
                                             ▼
                                     Harbor task directories
                                             │ tdb run  (harbor run -c job.yaml)
                                             ▼
                                     Harbor job directory ──tdb report──▶ report.json
                                             ▲
                            tdb regrade (harbor job regrade)
```

Grading is decoupled from inference. `tdb grade` is a pure function of the
submission artifacts, the hidden rubrics, and the judge configuration, so it
runs identically inside Harbor's verifier container, standalone on any
directory with `output.json` and `output.R`, and in tests.

## Quick start

```bash
# 1. Canonical dataset from curated submissions (+ protocol/SAP Markdown)
uv run tdb dataset import data/json/*.json --out tmp/dataset --documents path/to/docs
uv run tdb dataset check tmp/dataset

# 2. Shared environment image (R, pinned CRAN snapshot, agent CLIs, skills)
uv run tdb env build
uv run tdb env check --canary

# 3. Harbor tasks
uv run tdb build tmp/dataset --out tmp/tasks

# 4. Run agents (needs `trialdesignbench[harbor]` on Python 3.12+)
export ANTHROPIC_API_KEY=...   # the agent's model API key and the judge's key
uv run tdb run --tasks tmp/tasks --agent claude-code --model anthropic/claude-opus-5 \
  --effort high --n-attempts 3 --canary

# 5. Aggregate
uv run tdb report jobs/<job-name> --format md
```

Each step has its own article:

- [Dataset](dataset.md): intake import, the canonical format, documents.
- [Environment](environment.md): the shared image and the network policy.
- [Build](build.md): Harbor task materialization.
- [Agents](agents.md): supported agents, credentials, reasoning effort,
  closed-book settings.
- [Closed book](closed-book.md): how egress control works, which agent web
  tools run provider-side, and why some agents are refused.
- [Run](run.md): `job.yaml`, network allowlists, matrices, regrading.
- [Grade](grade.md): deterministic checks, rubric judging, scoring.
- [Judge](judge.md): judge model, credentials, and network access.
- [Report](report.md): aggregation and leaderboards.
- [Reproducibility](reproducibility.md): what is pinned and recorded.
