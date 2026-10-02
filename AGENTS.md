# Agent Guidelines

These notes capture project-specific lessons and internal constraints for AI
agents working on `trialdesignbench`. For general usage instructions, CLI
commands, and artifact structure, refer to the [User Documentation](docs/articles/usage.md).

## Project Mission

TrialDesignBench is a community-driven benchmark to evaluate AI agents in
clinical trial design, focusing on reproducibility and the drafting of new
statistical designs.

## Fixed architecture decisions (do not relitigate)

1. **Harbor is the execution backend, not the benchmark definition.** We own
   the task schema, task materialization, grader, scoring rules, aggregation,
   and provenance. Harbor (v0.23.0) runs first-party agent harnesses in Docker.
2. **Integrate with Harbor through files only**: the task directory format, a
   generated `job.yaml` passed to `harbor run -c`, and the job directory it
   writes. Never `import harbor` in package code. `harbor` is the optional
   extra `trialdesignbench[harbor]`; the core stays light. The package
   requires Python 3.12+.
3. **The grader is a pure function of (submission artifacts, task rubrics,
   judge config).** It runs identically in a Harbor separate-verifier
   container, standalone on any directory with `output.json` and `output.R`,
   and in unit tests with `FakeJudge`.
4. **Fail loudly.** A grading error must never produce a passing or silently
   partial score. Every error is an explicit status in `grade.json` and zeroes
   the reward. Read the "IMPORTANT NOTE FOR AI AGENTS" docstring in
   `src/trialdesignbench/grade.py` before touching grading logic, and ask the
   user when a case is ambiguous.
5. **Rubrics are hidden from the agent.** They live only in each task's
   `tests/` directory, which runs in a separate verifier container.
6. **One shared, pinned Docker image** for agent and verifier environments.
   Pins live in `trialdesignbench.environment.PINS`; the Dockerfile `ARG`
   defaults must match them (a test enforces this).
7. **Closed book is enforced, not requested**: allowlisted egress, harness-level
   web tool disabling, a trajectory scan in the grader, and a network canary.

## Module map

```
src/trialdesignbench/
  schema.py        frozen pydantic models (extra="forbid", schema_version)
  dataset.py       intake JSON -> canonical dataset; load/check/attach-document
  build.py         canonical dataset -> Harbor task directories
  environment/     Dockerfile, install scripts, image pins, build/check commands
  providers.py     model providers (name, key variable, API host) shared by
                   agents and judges; `<provider>/` prefix of every model string
  judge.py         Judge protocol, JudgeBackend, one ApiJudge per provider
                   (AnthropicJudge, OpenaiJudge, XaiJudge with lazily imported
                   official SDKs; OpencodeGoJudge with the stdlib), FakeJudge
  grade.py         deterministic checks + rubric judging + outputs (delicate)
  scoring.py       versioned scoring rules, pure functions
  agents.py        supported agents: providers, credentials, hosts, closed-book settings
  run.py           job.yaml, allowlists, auth validation, harbor invocation
  canary.py        network canary Harbor task
  report.py        job dirs / grade dirs -> ReportSummary + leaderboard
  provenance.py    digests, versions, git SHA, image digest
  templates/       packaged prompt template (versioned default)
  cli/             typer + rich; core modules never import it
```

## Implementation notes

- Network policy has two phases per task: `[agent]` (during `agent.run()`,
  model API hosts only) and the `[environment]` baseline (during agent setup,
  plus install hosts for agents Harbor installs at setup). `tdb build` writes
  both as `allowed_hosts = []` with the markers `# tdb:agent-allowed-hosts`
  and `# tdb:environment-allowed-hosts`; `tdb run` fills them in task copies
  at `<jobs_dir>/<job_name>.tasks/`. Never place files Harbor should keep
  inside a job directory: Harbor deletes subdirectories without
  `result.json` on resume.
- `src/trialdesignbench/agents.py` is the only list of supported agents
  (`AGENTS`) and refused ones (`REFUSED_AGENTS`), with their providers,
  credentials, hosts, pins, and closed-book kwargs/env. Keep
  `docs/articles/agents.md` and the README in sync (tests check this). An
  agent is supported only if its web tools can be disabled, its egress can be
  limited to the model API during `agent.run()`, and Harbor writes an ATIF
  trajectory (the grader errors without one).
- Harbor's adapters for some agents (grok-build, opencode) reinstall the CLI
  at every setup regardless of the image, so preinstalling them does not
  help; they get `setup_hosts` and the pin as Harbor's `version` kwarg.
  Closed-book settings Harbor cannot overwrite (for example
  `/etc/grok/requirements.toml`) belong in the image. Validate new kwargs
  with `harbor agent schema <name>`; Harbor rejects unknown kwargs.
- Separate verifiers do not get `tests/` uploaded, so `tests/Dockerfile` is
  `FROM` the shared image and copies `test.sh` and `rubrics.json` in.
- Harbor reads `reward` from `reward.json` as the headline metric; all values
  must be finite numbers.
- `tdb run` must never pass `--allow-agent-host` or `--allow-environment-host`,
  add MCP servers, or allow an agent version different from the pin.
- Changing scoring rules requires bumping `SCORING_VERSION`. Changing the judge
  prompt changes `judge_prompt_sha256()`, which is recorded in every grade.
- Judges follow one naming rule, documented in the `judge.py` docstring and
  `docs/articles/judge.md`: everything is named after the provider in
  `providers.PROVIDERS`. Judge models are `<provider>/<model>`, the backend
  name is the provider name, the class is `<Provider>Judge` (PascalCase of
  the kebab-case name), and the SDK extra is `judge-<provider>` (`judge`
  installs all). Tests enforce the rule, the extras, and the docs table; do
  not add a judge under another name or accept unprefixed judge models.
- Do not log API keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `XAI_API_KEY`,
  `OPENCODE_API_KEY`) or OAuth tokens; manifests record variable names only.

## Development Environment

- **Dependency Management:** Use `uv`. Local development should prefer
  `uv sync --dev` and `uv run ...`. `[tool.uv] exclude-newer = "7 days"`.
- **Python Pinning:** Pin workflows to the Python from `actions/setup-python`
  using `--python ... --no-python-downloads`.
- **Harbor for local checks:** install it in an isolated environment, for
  example `uv venv tmp/harbor-venv --python 3.12` and
  `uv pip install harbor==0.23.0`, then put `tmp/harbor-venv/bin` on `PATH`.
  Do not add it as a core dependency.
- **Docker/R tests:** tests needing R or Docker carry skip markers
  (`requires_rscript`, `requires_docker` in `tests/conftest.py`).

## Quality Gates

Before finishing a task, ensure the following checks pass:
```bash
uv run isort .
uv run ruff format
uv run ruff check
uv run mypy .
uv run pytest
uv run zensical build
```
*Note: Tools must skip `.venv`, `vendor`, and `deps-src` (configured in `pyproject.toml`).*

## Documentation Structure
- **Shell examples:** Keep multi-argument calls on multiple lines with `\`
  continuations, one positional argument or option/value pair per indented line.
  Keep the command and subcommands together on the first line.
  Check Bash blocks with `shfmt -ln bash` using its default tab indentation.
  Use `text` fences for CLI usage synopses with optional-argument notation.
- **Public Docs:** Managed with Zensical in `docs/`.
- **Vignettes:** Update `docs/articles/` for usage guides.
- **Reference:** Update `docs/reference/` and `zensical.toml` for API changes.
- `docs/index.md` and `docs/changelog.md` are synced from `README.md` and
  `CHANGELOG.md` by `docs/scripts/sync.sh`.
