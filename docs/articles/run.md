# Run

`tdb run` writes a Harbor `job.yaml`, validates credentials, and runs
`harbor run -c job.yaml`.

```bash
uv add "trialdesignbench[harbor]"   # Python 3.12+
uv run tdb run --tasks tmp/tasks \
  --agent claude-code --model anthropic/claude-opus-5 \
  --n-attempts 3 --n-concurrent 2 --canary
```

## Options

| Option | Meaning |
| --- | --- |
| `--agent`, `--model` | Harbor agent and model. Repeat the pair to run a matrix in one job. |
| `--agent-version` | Must equal the version shipped in the image (default). |
| `--n-attempts` | Attempts per task (default 1). |
| `--n-concurrent` | Concurrent trials (default 2 for `api`, 1 for `subscription`). |
| `--auth api\|subscription` | Credential mode, see below. |
| `--skill` | Extra skills (local path or git ref), passed to Harbor. The image already ships `/skills`. |
| `--task-ids` | Subset of tasks. |
| `--job-name`, `--jobs-dir` | Job location (default `jobs/<timestamp>`). |
| `--canary` | Run the network canary as the first task. |
| `--yes` | Pass `--yes` to Harbor (skip its host env var approval prompt). |
| `--dry-run` | Write `job.yaml` and `tdb-run.json`, print the command, and stop. |

## Files

```text
<jobs_dir>/<job_name>/          Harbor's job directory
  job.yaml                      generated config
  tdb-run.json                  RunManifest (provenance)
  <trial dirs...>               written by Harbor
<jobs_dir>/<job_name>.tasks/    task copies with the agent allowlist filled in
```

Task copies live beside the job directory, not inside it: Harbor deletes any
job subdirectory without a `result.json` when a job is resumed.

`job.yaml` uses the field names of Harbor's `JobConfig`. It is written in JSON
syntax, which is valid YAML, so no YAML dependency is needed.

## Auth modes

| Mode | Agent | Requires | Sets |
| --- | --- | --- | --- |
| `api` | claude-code | `ANTHROPIC_API_KEY` | |
| `api` | codex | `OPENAI_API_KEY` | |
| `subscription` | claude-code | `CLAUDE_CODE_OAUTH_TOKEN` | `CLAUDE_FORCE_OAUTH=1` |
| `subscription` | codex | `~/.codex/auth.json` or `CODEX_AUTH_JSON_PATH` | `CODEX_FORCE_AUTH_JSON=1` |

The verifier's judge always needs `ANTHROPIC_API_KEY`. `tdb run` fails before
launching when a required credential is missing. Secret values are never
written to `job.yaml` or `tdb-run.json`; only variable names are recorded.

## Harness settings

- Claude Code: `disallowed_tools=WebSearch,WebFetch`, a settings file with the
  same `permissions.deny`, and `DISABLE_TELEMETRY=1`,
  `DISABLE_ERROR_REPORTING=1`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`.
- Codex: `web_search=disabled`.
- `gemini-cli` is refused (see [Environment](environment.md)).

## Matrices

Repeating `--agent/--model` puts all agents in one job. Harbor applies one
task definition to every agent, so when agents need different API hosts every
trial gets the union (for example `api.anthropic.com` and `api.openai.com`).
`tdb run` warns when this happens; run one job per agent if you need strict
per-agent allowlists.

## Regrade

```bash
uv run tdb regrade jobs/<job> --tasks tmp/tasks-v2 --job-name <job>-regrade
```

This wraps `harbor job regrade`. The recorded agent outputs and trajectories
are re-scored with the verifier from `--tasks` (for example after a rubric or
grader change). No agent is re-run and no agent credentials are needed.
