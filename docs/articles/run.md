# Run

`tdb run` writes a Harbor `job.yaml`, validates credentials, and runs
`harbor run -c job.yaml`.

```bash
uv add "trialdesignbench[harbor]"
export ANTHROPIC_API_KEY=... # agent (anthropic/ models) and judge
uv run tdb run \
	--tasks tmp/tasks \
	--agent claude-code \
	--model anthropic/claude-opus-5-5 \
	--effort high \
	--n-attempts 3 \
	--n-concurrent 2 \
	--canary
```

Which agents can run, the credentials each needs, the reasoning effort
levels each accepts, and the closed-book settings `tdb run` applies are
described in [Agents](agents.md). The rubric
judge in the verifier needs the key of the built judge model's provider
(`ANTHROPIC_API_KEY` for the default `anthropic/` model, `OPENAI_API_KEY`
for `openai/`, `XAI_API_KEY` for `xai/`, `OPENCODE_API_KEY` for
`opencode-go/`); see [Judge](judge.md).

## Options

| Option | Meaning |
| --- | --- |
| `--agent`, `--model` | Harbor agent and `provider/model`. Repeat the pair to run a matrix in one job. |
| `--effort` | Reasoning effort level (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, or `default`), once for all agents or once per `--agent`. Each agent accepts a subset, and each model a subset of that; see [Agents](agents.md#reasoning-effort). Unset, the harness default applies and `tdb run` warns. |
| `--agent-version` | Must equal the pinned version (default). |
| `--n-attempts` | Attempts per task (default 1). |
| `--n-concurrent` | Concurrent trials (default 2 for `api`, 1 for `subscription`). |
| `--auth api\|subscription` | Credential mode, see [Agents](agents.md#authentication). |
| `--skill` | Extra skills (local path or git ref), passed to Harbor. The image already ships `/skills`. |
| `--task-ids` | Subset of tasks. |
| `--job-name`, `--jobs-dir` | Job location (default `jobs/<timestamp>`). |
| `--canary` | Run the network canary as the first task. |
| `--yes` | Pass `--yes` to Harbor (skip its prompt before passing the judge key to the verifier). |
| `--dry-run` | Write `job.yaml` and `tdb-run.json`, print the command, and stop. |

`tdb run` fails before launching when an agent is not supported, the model's
provider does not match the agent, an effort level is not one the agent
accepts, a required credential is missing, or the local image was built for
other agent pins or another grader version.

## Files

```text
<jobs_dir>/<job_name>/          Harbor's job directory
  job.yaml                      generated config
  tdb-run.json                  RunManifest (provenance)
  <trial dirs...>               written by Harbor
<jobs_dir>/<job_name>.tasks/    task copies with both allowlists filled in
  harbor-plugin/
    tdb_harbor_agents.py        the agent classes Harbor imports (see below)
```

Task copies live beside the job directory, not inside it: Harbor deletes any
job subdirectory without a `result.json` when a job is resumed.

`job.yaml` uses the field names of Harbor's `JobConfig`. It is written in JSON
syntax, which is valid YAML, so no YAML dependency is needed. Secret values
are never written to `job.yaml` or `tdb-run.json`; only variable names are
recorded.

## How the instruction reaches the agent

Harbor's adapters for all four supported agents hand the rendered
`instruction.md` to the CLI inside a single `docker compose exec` call:
Claude Code as an environment variable, Codex CLI, Grok Build, and OpenCode
as a quoted argument of the `bash -c` command. Linux caps one argument or
environment string at 128 KiB (`MAX_ARG_STRLEN`), on the host and again
inside the container, so an instruction carrying a protocol or SAP of a few
hundred kilobytes kills the trial before the agent starts
(`OSError: [Errno 7] Argument list too long: 'docker'`). Harbor 0.23.0 and
0.24.0 share this limitation.

`tdb run` therefore launches every agent through Harbor's `import_path`
rather than its agent `name`. The classes live in
`trialdesignbench.harbor_agents`, a Harbor plugin: subclasses of Harbor's
adapters that upload the instruction into the container
(`/installed-agent/tdb-instruction.md`, via `docker compose cp`) and make the
CLI read it from there, byte for byte:

| Agent | Harbor's transport | With the plugin |
| --- | --- | --- |
| `claude-code` | env var piped into `claude --print` | the env var becomes a shell variable read from the file, piped the same way |
| `codex` | `codex exec -- '<instruction>'` | `codex exec -- - <FILE` (prompt from stdin) |
| `grok-build` | `grok --single '<instruction>'` | `grok --prompt-file FILE` |
| `opencode` | `opencode run -- '<instruction>'` | `opencode run -- <FILE` (prompt from stdin) |

Everything else is inherited from Harbor's adapter: credentials, settings,
skills, sessions, cleanup, and trajectories, and `name()` still answers
`claude-code`, `codex`, `grok-build`, or `opencode`, which is what
`result.json` and `tdb report` show. `job.yaml` has `import_path =
"tdb_harbor_agents:<Class>"` and no `name` (Harbor would prefer a valid
`name` and skip the plugin); each trial's `result.json` records the same
`config.agent.import_path`.

`tdb run` copies the module beside the task copies
(`<job>.tasks/harbor-plugin/tdb_harbor_agents.py`), runs `harbor` with that
directory first on `PYTHONPATH`, prints the command with that prefix so it
can be replayed by hand, and records the copy's digest in `tdb-run.json`
(`harbor_plugin`) next to each agent's `import_path`. The copy makes the job
runnable whether or not Harbor shares a Python environment with tdb. A
launch command the plugin does not recognize (a future Harbor adapter)
fails the trial with `InstructionTransportError` instead of silently using
the old transport; a command that no longer carries the instruction passes
through unchanged.

!!! note "Grok Build excerpts long requests on its own"

    Grok Build receives the whole instruction but, for requests over about
    100 KB, sends the model an excerpt with a note that the full request is
    saved in its session directory and that the omitted line ranges can be
    read with its `read_file` tool. This is the CLI's own behavior for any
    long prompt, not the transport's; the trajectory records the excerpted
    message. Expect Grok Build models to read the rest of a long protocol
    with tool calls, or not at all.

## Network allowlists

`tdb build` leaves two deny-all allowlists in every `task.toml`; `tdb run`
fills them in the task copies:

- `[agent] allowed_hosts`, applied by Harbor during `agent.run()`: the model
  API hosts of the agents and auth mode.
- `[environment] allowed_hosts`, the baseline during agent setup: the same
  hosts plus the install hosts of agents that Harbor installs at setup
  (grok-build, opencode). For preinstalled agents both lists are equal.

The per-agent hosts are listed in [Agents](agents.md#network) and the policy
itself in [Environment](environment.md#network-policy-closed-book).
[Closed book](closed-book.md) explains how Harbor enforces the allowlist and
why it alone does not make an agent closed book.

## Matrices

Repeating `--agent/--model` puts all agents in one job. Harbor applies one
task definition to every agent, so when agents need different hosts every
trial gets the union (for example `api.anthropic.com` and `api.openai.com`,
plus any setup hosts). `tdb run` warns when this happens; run one job per
agent if you need strict per-agent allowlists.

A matrix may repeat the same agent and model at different `--effort` levels.
Harbor names each trial by task plus a random suffix, every trial's
`result.json` records its own agent kwargs, and `tdb report` keeps the
levels apart.

## Regrade

```bash
uv run tdb regrade \
	"jobs/<job>" \
	--tasks tmp/tasks-v2 \
	--job-name "<job>-regrade"
```

This wraps `harbor job regrade`. The recorded agent outputs and trajectories
are re-scored with the verifier from `--tasks` (for example after a rubric or
grader change). No agent is re-run and no agent credentials are needed; the
judge still needs its provider's key (`ANTHROPIC_API_KEY` by default).
