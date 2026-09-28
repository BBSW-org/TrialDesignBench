# Judge

The rubric judge is the LLM that grades each answer against the hidden
rubric criteria. It is part of the grader, not of the agent under test: it
runs in the separate verifier container (or wherever `tdb grade` runs), and
its configuration and credentials are independent of the agent's. See
[Grade](grade.md#rubric-judging) for what the judge sees and how its verdicts
are scored.

## Configuration

| Setting | Default | Inside Harbor (`tdb run`, `tdb regrade`) | Standalone (`tdb grade`) |
| --- | --- | --- | --- |
| Judge | `anthropic` (`AnthropicJudge`) | always `anthropic` | `--judge anthropic\|fake` |
| Model | `claude-opus-5` | fixed per task at build time: `tdb build --judge-model ID` writes `TDB_JUDGE_MODEL` into `[verifier.env]` | `--judge-model ID`, else `TDB_JUDGE_MODEL`, else the default |
| Votes | 1 | 1 | `--judge-votes K` (majority of K calls; ties are `unclear`) |

The judge model is recorded in `tdb-build.json`, `tdb-run.json`, and every
`grade.json`, together with the judge prompt hash and the SDK version. To
grade with a different model inside Harbor, rebuild the tasks with
`--judge-model` and use `tdb regrade`; no agent is re-run.

`AnthropicJudge` uses the Anthropic Messages API with structured JSON output,
sends `temperature=0` to models that accept sampling parameters, and retries
transient errors with backoff. A question whose judge call still fails marks
all its criteria `error`, which zeroes the trial's reward.

## Authentication

The judge needs `ANTHROPIC_API_KEY`, an Anthropic API key from the
[Claude Console](https://platform.claude.com/). A Claude subscription token
(`CLAUDE_CODE_OAUTH_TOKEN`) cannot be used for the judge.

- **`tdb run` and `tdb regrade`.** Export `ANTHROPIC_API_KEY` in the shell
  that runs them. Each task's `[verifier.env]` maps
  `ANTHROPIC_API_KEY = "${ANTHROPIC_API_KEY}"`, so Harbor copies the host
  value into the verifier container only. Harbor asks before passing host
  variables into a task; `tdb run --yes` skips that prompt. `tdb run` fails
  before launching when the variable is missing, whatever agent or `--auth`
  mode is used.
- **`tdb grade`.** Export `ANTHROPIC_API_KEY` in the environment. The judge
  needs the `judge` extra: `uv add "trialdesignbench[judge]"` (the shared
  image already includes it).

```bash
export ANTHROPIC_API_KEY=...
uv run tdb grade \
	path/to/submission \
	--rubrics "tmp/dataset/<task_id>/rubrics.json" \
	--out "graded/<task_id>" \
	--trajectory path/to/trajectory.json \
	--judge-model claude-opus-5 \
	--judge-votes 3
```

When the agent is `claude-code` (or `opencode` with an `anthropic/` model)
with `--auth api`, the agent and the judge use the same `ANTHROPIC_API_KEY`.
With `claude-code --auth subscription`, the key stays exported for the judge
and Harbor keeps it away from the agent (see
[Agents](agents.md#subscriptions-auth-subscription)).

## Network

The verifier container has its own allowlist with only the judge API host,
`api.anthropic.com` (plus PyPI hosts with `tdb build --grader-source pypi`).
Only `ANTHROPIC_API_KEY` and `TDB_JUDGE_MODEL` are passed to it, so a custom
`ANTHROPIC_BASE_URL` does not apply inside Harbor. Standalone `tdb grade`
uses the Anthropic SDK's defaults, which honor `ANTHROPIC_BASE_URL`.

## Dry runs without a key

`FakeJudge` gives deterministic verdicts and needs no credentials, for tests
and pipeline checks: `tdb grade ... --judge fake --fake-verdict pass`.
