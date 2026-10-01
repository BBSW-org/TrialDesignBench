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
| Judge | `anthropic` (`AnthropicJudge`) | the backend of the built judge model | `--judge anthropic\|opencode-go\|fake` (default: the backend of the model) |
| Model | `claude-opus-5-5` | fixed per task at build time: `tdb build --judge-model ID` writes `TDB_JUDGE_MODEL` into `[verifier.env]` | `--judge-model ID`, else `TDB_JUDGE_MODEL`, else the backend's default |
| Votes | 1 | 1 | `--judge-votes K` (majority of K calls; ties are `unclear`) |

The judge model selects the backend: a plain Anthropic model id, or
`opencode-go/<id>` for the OpenCode Go gateway. Any other prefix is refused
at build time. The build writes the backend's key variable and API host into
the task (see [Authentication](#authentication) and [Network](#network)), so
nothing else changes between backends.

The judge model is recorded in `tdb-build.json`, `tdb-run.json`, and every
`grade.json`, together with the judge prompt hash and the SDK version. To
grade with a different model inside Harbor, rebuild the tasks with
`--judge-model` and use `tdb regrade`; no agent is re-run.

`AnthropicJudge` uses the Anthropic Messages API with structured JSON output
and retries transient errors with backoff. It omits `temperature` for the
`claude-fable`, `claude-mythos`, `claude-opus`, and `claude-sonnet` families
for forward compatibility, including older versions that still accept it.
These models use the API's default sampling behavior; other models receive
`temperature=0`. A question whose judge call still fails marks all its
criteria `error`, which zeroes the trial's reward.

`OpencodeGoJudge` grades with the same prompt, response contract, voting, and
retries over the [OpenCode Go](https://opencode.ai/docs/go) gateway, an
OpenAI-compatible API, with structured JSON output. It uses the standard
library only, so the `judge` extra is not needed. Judge models are
`opencode-go/<id>` for the ids in the gateway's model list. The gateway
serves each model over exactly one protocol, Chat Completions for most and
the Responses API for the GPT, Grok, and Muse Spark families; the judge
starts with Chat Completions and switches once when the gateway answers
`ModelProtocolUnsupported`. Each question's judge log records the protocol
used. No sampling parameters are sent. Every request carries a stable
`x-opencode-session` header, which the gateway requires, and a
`trialdesignbench/<version>` user agent, because generic HTTP-library user
agents are blocked.

## Authentication

Which key the judge needs depends on its backend:

| Backend | Key | From |
| --- | --- | --- |
| `anthropic` | `ANTHROPIC_API_KEY` | [Claude Console](https://platform.claude.com/) |
| `opencode-go` | `OPENCODE_API_KEY` | [OpenCode Console](https://opencode.ai/auth) (Go subscription) |

A Claude subscription token (`CLAUDE_CODE_OAUTH_TOKEN`) cannot be used for
the judge.

- **`tdb run` and `tdb regrade`.** Export the key matching the built judge
  model in the shell that runs them: `ANTHROPIC_API_KEY`, or
  `OPENCODE_API_KEY` for tasks built with `--judge-model
  opencode-go/<model>`. Each task's `[verifier.env]` maps the key as
  `"${...}"`, so Harbor copies the host value into the verifier container
  only. Harbor asks before passing host variables into a task; `tdb run
  --yes` skips that prompt. `tdb run` fails before launching when the
  variable is missing, whatever agent or `--auth` mode is used.
- **`tdb grade`.** Export the matching key in the environment. The Anthropic
  judge needs the `judge` extra: `uv add "trialdesignbench[judge]"` (the
  shared image already includes it); the OpenCode Go judge needs no extra.

```bash
export ANTHROPIC_API_KEY=...
uv run tdb grade \
	path/to/submission \
	--rubrics "tmp/dataset/<task_id>/rubrics.json" \
	--out "graded/<task_id>" \
	--trajectory path/to/trajectory.json \
	--judge-model claude-opus-5-5 \
	--judge-votes 3
```

When the agent is `claude-code` (or `opencode` with an `anthropic/` model)
with `--auth api`, the agent and the judge use the same `ANTHROPIC_API_KEY`.
With `claude-code --auth subscription`, the key stays exported for the judge
and Harbor keeps it away from the agent (see
[Agents](agents.md#subscriptions-auth-subscription)).

## Network

The verifier container has its own allowlist with only the judge API host
(`api.anthropic.com`, or `opencode.ai` for tasks built with an `opencode-go/`
judge model; plus PyPI hosts with `tdb build --grader-source pypi`).
Only the judge key and `TDB_JUDGE_MODEL` are passed to it, so a custom
`ANTHROPIC_BASE_URL` does not apply inside Harbor. Standalone `tdb grade`
uses the Anthropic SDK's defaults, which honor `ANTHROPIC_BASE_URL`; the
OpenCode Go judge always calls `https://opencode.ai/zen/go/v1`.

## Dry runs without a key

`FakeJudge` gives deterministic verdicts and needs no credentials, for tests
and pipeline checks: `tdb grade ... --judge fake --fake-verdict pass`.
