# Judge

The rubric judge is the LLM that grades each answer against the hidden
rubric criteria. It is part of the grader, not of the agent under test: it
runs in the separate verifier container (or wherever `tdb grade` runs), and
its configuration and credentials are independent of the agent's. See
[Grade](grade.md#rubric-judging) for what the judge sees and how its verdicts
are scored.

## Judges

There is one judge per model provider, and one rule names everything about
it: the provider's identifier, which is the same `<provider>/` prefix that
`tdb run --model` uses. For a provider `p`:

- the judge model is `p/<model>` (`--judge-model`, `TDB_JUDGE_MODEL`);
- the backend name is `p` (`tdb grade --judge`, and `judge.name` in
  `grade.json`);
- the class is `p` in PascalCase plus `Judge`, each hyphen-separated segment
  capitalized;
- the provider's official SDK, when the judge uses one, is installed by the
  extra `judge-p`; the `judge` extra installs them all;
- the API key variable and the egress host are the provider's.

The table mirrors `trialdesignbench.judge.API_JUDGES`; a test keeps them,
the extras, and this page in sync.

| Judge | Class | Default model | API | SDK (extra) | Key | Host |
| --- | --- | --- | --- | --- | --- | --- |
| `anthropic` | `AnthropicJudge` | `anthropic/claude-opus-5-5` | Messages API | `anthropic` (`judge-anthropic`) | `ANTHROPIC_API_KEY` | `api.anthropic.com` |
| `openai` | `OpenaiJudge` | `openai/gpt-6-astra` | Responses API | `openai` (`judge-openai`) | `OPENAI_API_KEY` | `api.openai.com` |
| `xai` | `XaiJudge` | `xai/grok-4.7` | gRPC chat API | `xai-sdk` (`judge-xai`) | `XAI_API_KEY` | `api.x.ai` |
| `opencode-go` | `OpencodeGoJudge` | `opencode-go/grok-4.7` | OpenAI-compatible gateway | none (standard library) | `OPENCODE_API_KEY` | `opencode.ai` |

Every provider a [supported agent](agents.md) can call has a judge, so a run
can be graded by a judge from the agent's own provider or from another one.
All judges share the same prompt, response contract (one structured JSON
result per criterion), majority voting, retries with backoff, and judge log
format; they differ only in the API call. A question whose judge call still
fails after the retries marks all its criteria `error`, which zeroes the
trial's reward.

## Configuration

| Setting | Default | Inside Harbor (`tdb run`, `tdb regrade`) | Standalone (`tdb grade`) |
| --- | --- | --- | --- |
| Judge | the provider of the judge model (`anthropic`) | the provider of the built judge model | `--judge anthropic\|openai\|xai\|opencode-go\|fake` (default: the provider of the model) |
| Model | `anthropic/claude-opus-5-5` | fixed per task at build time: `tdb build --judge-model <provider>/<model>` writes `TDB_JUDGE_MODEL` into `[verifier.env]` | `--judge-model <provider>/<model>`, else `TDB_JUDGE_MODEL`, else the judge's default |
| Votes | 1 | 1 | `--judge-votes K` (majority of K calls; ties are `unclear`) |

The judge model is always `<provider>/<model>`. A model id without a
provider, or with a provider that has no judge, is refused by `tdb build` and
`tdb grade`, so a typo never sends the rubrics to the wrong API. The build
writes the provider's key variable and API host into the task (see
[Authentication](#authentication) and [Network](#network)), so nothing else
changes between judges.

The judge model is recorded in `tdb-build.json`, `tdb-run.json`, and every
`grade.json`, together with the judge prompt hash and the SDK version. To
grade with a different model inside Harbor, rebuild the tasks with
`--judge-model` and use `tdb regrade`; no agent is re-run.

### `anthropic`

`AnthropicJudge` uses the Anthropic Messages API with structured JSON output
(`output_config.format`) through the official `anthropic` package. It omits
`temperature` for the `claude-fable`, `claude-mythos`, `claude-opus`, and
`claude-sonnet` families for forward compatibility, including older versions
that still accept it. These models use the API's default sampling behavior;
other models receive `temperature=0`.

### `openai`

`OpenaiJudge` uses the OpenAI Responses API with strict JSON schema output
(`text.format`) through the official `openai` package. Requests set
`store=false`, so OpenAI keeps no copy of the exchange. No sampling
parameters are sent: reasoning models reject them, and the others use the
API defaults. `max_output_tokens` (16000) bounds reasoning and answer tokens
together; a response that stops as `incomplete` is retried.

### `xai`

`XaiJudge` uses the official `xai-sdk`, which speaks gRPC to `api.x.ai` on
port 443, the same host as xAI's REST API, so the verifier allowlist is the
same. The result schema is sent as a JSON schema response format; no sampling
parameters are sent. The SDK's own transport retries are turned off so that
every attempt appears in the judge log; a reply truncated at `max_tokens` is
retried. The log records the gRPC finish reason and the token usage,
including reasoning tokens.

### `opencode-go`

`OpencodeGoJudge` grades over the [OpenCode Go](https://opencode.ai/docs/go)
gateway, an OpenAI-compatible API, with structured JSON output and the
standard library only, so no extra is needed. Judge models are
`opencode-go/<id>` for the ids in the gateway's model list. The gateway
serves each model over exactly one protocol, Chat Completions for most and
the Responses API for the GPT, Grok, and Muse Spark families; the judge
starts with Chat Completions and switches once when the gateway answers
`ModelProtocolUnsupported`. Each question's judge log records the protocol
used. No sampling parameters are sent. Every request carries a stable
`x-opencode-session` header, which the gateway requires, and a
`trialdesignbench/<version>` user agent, because generic HTTP-library user
agents are blocked.

!!! warning "OpenCode Go models that train on request data"

    Some OpenCode Go models, for example `muse-spark-1.3-contributor`,
    train on request data. The gateway refuses them with HTTP 400
    (`This Go model trains on request data`) unless the workspace's privacy
    settings allow such endpoints. The judge sends the hidden rubrics,
    so leave that setting off and use a model the gateway serves without it;
    the default `opencode-go/grok-4.7` and `opencode-go/kimi-k3` are such models.
    A refused model marks every criterion of the question `error`, which zeroes
    the trial, so a wrong choice fails loudly rather than leaking rubrics.

## Authentication

The judge needs the API key of its provider:

| Judge | Key | From |
| --- | --- | --- |
| `anthropic` | `ANTHROPIC_API_KEY` | [Claude Console](https://platform.claude.com/) |
| `openai` | `OPENAI_API_KEY` | [OpenAI Platform](https://platform.openai.com/) |
| `xai` | `XAI_API_KEY` | [xAI Console](https://console.x.ai/) |
| `opencode-go` | `OPENCODE_API_KEY` | [OpenCode Console](https://opencode.ai/console) (Go subscription) |

Agent subscription logins (`CLAUDE_CODE_OAUTH_TOKEN`, Codex's `auth.json`)
cannot be used for the judge.

- **`tdb run` and `tdb regrade`.** Export the key of the built judge model's
  provider in the shell that runs them, for example `OPENAI_API_KEY` for
  tasks built with `--judge-model openai/<model>`. Each task's
  `[verifier.env]` maps the key as `"${...}"`, so Harbor copies the host
  value into the verifier container only. Harbor asks before passing host
  variables into a task; `tdb run --yes` skips that prompt. `tdb run` fails
  before launching when the variable is missing, whatever agent or `--auth`
  mode is used.
- **`tdb grade`.** Export the matching key in the environment and install
  the judge's SDK: `uv add "trialdesignbench[judge-openai]"` for one judge,
  or `uv add "trialdesignbench[judge]"` for all of them (the shared image
  includes them all). The OpenCode Go judge needs no extra.

```bash
export ANTHROPIC_API_KEY=...
uv run tdb grade \
	path/to/submission \
	--rubrics "tmp/dataset/<task_id>/rubrics.json" \
	--out "graded/<task_id>" \
	--trajectory path/to/trajectory.json \
	--judge-model anthropic/claude-opus-5-5 \
	--judge-votes 3
```

When the agent and the judge use the same provider with `--auth api`, for
example `claude-code` with the default judge, they share the key. With
`claude-code --auth subscription`, the key stays exported for the judge and
Harbor keeps it away from the agent (see
[Agents](agents.md#subscriptions-auth-subscription)).

## Network

The verifier container has its own allowlist with only the judge's API host
(the Host column above; plus PyPI hosts with `tdb build --grader-source
pypi`). Only the judge key and `TDB_JUDGE_MODEL` are passed to it, so custom
base URLs (`ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`) do not apply inside
Harbor. Standalone `tdb grade` uses each SDK's defaults, which honor those
variables; the xAI judge always dials `api.x.ai:443` and the OpenCode Go
judge always calls `https://opencode.ai/zen/go/v1`.

## Dry runs without a key

`FakeJudge` gives deterministic verdicts and needs no credentials, for tests
and pipeline checks: `tdb grade ... --judge fake --fake-verdict pass`.

## Adding a judge

Everything lives in `src/trialdesignbench/judge.py` and follows the naming
rule above:

1. Add the provider to `trialdesignbench.providers.PROVIDERS` if it is
   missing: name, title, key variable, and exact API host.
2. Add an `ApiJudge` subclass named `<Provider>Judge`, with
   `backend = JudgeBackend(PROVIDERS[name], default_model, sdk=...)`,
   implementing `_request` (the request recorded in the judge log) and
   `_call_once` (one API call that raises a retryable error for transient
   failures). Import the provider's official SDK lazily, and add the class
   to `API_JUDGES`.
3. Add the extra `judge-<provider>` to `pyproject.toml`, include it in the
   `judge` extra, and add the SDK to the `dev` group so the stub-client
   tests run in CI.
4. Add the row to the table above and a changelog entry; tests check the
   class name, the extras, and the table.
5. Run the live test (`TDB_LIVE_JUDGE_TESTS=1 uv run pytest
   tests/test_judge.py -k live`) and grade a submission end to end with
   `tdb grade --judge-model <provider>/<model>`.
