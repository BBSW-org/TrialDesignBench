# Agent Guidelines

These notes capture project-specific lessons for future coding agents working on
`trialdesignbench`.

## Project Scope

`trialdesignbench` is a Python package for a clinical trial design reproduction
benchmark. The current baseline focuses on workflow step 1:

1. Create a local benchmark workspace.
2. Convert SAP/protocol PDFs with Mathpix.
3. Build the standard trial design reproduction prompt.
4. Optionally run the prompt with a local OpenAI Codex SDK/runtime.

Keep changes scoped to this workflow unless the user explicitly asks for later
benchmark stages.

## Environment and Dependencies

- Use `uv` for dependency management and command execution.
- Prefer `uv sync --dev` and `uv run ...` locally.
- The Codex Python SDK is declared through `[tool.uv.sources]` as a Git
  dependency on `openai/codex`, `subdirectory = "sdk/python"`.
- `openai-codex` depends on `openai-codex-cli-bin`. At the time this was
  implemented, the pinned runtime package did not provide a glibc Linux x86_64
  wheel, only musl Linux, macOS, and Windows wheels.
- Because of that runtime wheel gap, do not assume Linux CI can install the
  Codex runtime. Linux workflows intentionally use:

  ```bash
  uv sync --dev --no-install-package openai-codex --no-install-package openai-codex-cli-bin
  uv run --no-sync <tool>
  ```

- `uv run --no-sync` is important after a sync that skipped packages.
  Plain `uv run` performs an implicit sync and will try to install the
  skipped Codex runtime again.
- Pin workflow syncs to the Python from `actions/setup-python` with
  `--python ... --no-python-downloads`; otherwise uv may download
  a different interpreter for the project.

## Codex Integration

- The default model is `gpt-5.5`.
- The default Codex reasoning effort is `high`.
- `LocalCodexRunner` passes high effort in both places supported by the SDK:
  thread config via `model_reasoning_effort`, and turn execution via `effort`.
- The Python SDK surface checked during implementation did not expose
  `toggle_fast_mode` or an equivalent fast-mode setting. Do not add unsupported
  config keys unless the SDK documentation/source has changed and tests cover it.
- Import `openai_codex` lazily so package import, docs, type checks, and
  conversion-only workflows do not require a working local Codex runtime.
- Tests that require the local Codex runtime should be marked
  `@pytest.mark.requires_codex_runtime`. The test hook can skip them when
  `TDB_SKIP_CODEX_RUNTIME_TESTS=1` or when the runtime is unavailable on Linux.

## Mathpix Integration

- Mathpix PDF processing is asynchronous:
  upload PDF, poll status, then download `.mmd` and optional `.tex.zip`.
- Keep external API calls behind injectable transport/protocol boundaries so
  tests can mock them without network access.
- Credentials are loaded from a workspace `.env` file using `python-dotenv`.
  Workspace `.gitignore` should exclude `.env`, `converted/`, and `runs/`.
- Do not log or print Mathpix secrets.

## CLI and Artifacts

- CLI entry points are `tdb` and `trialdesignbench`.
- Main commands are `init`, `configure`, `convert`, and `run`.
- Preserve the artifact layout:
  - `converted/<pdf-stem>.mmd`
  - `converted/<pdf-stem>.mathpix.json`
  - `converted/<pdf-stem>.tex.zip` when requested
  - `runs/<case-id>/prompt.md`
  - `runs/<case-id>/codex_response.md`
  - `runs/<case-id>/codex_run.json`
  - `runs/<case-id>.step1.json`

## Testing and Quality Gates

- Mock Mathpix and Codex in unit tests. Do not require external services for the
  default test suite.
- Keep pytest discovery restricted to `tests/`; vendored Codex SDK tests are not
  part of this package's CI.
- Run the standard checks before finishing:

  ```bash
  uv run isort .
  uv run ruff format
  uv run ruff check
  uv run mypy .
  uv run pytest
  uv run zensical build
  ```

- `isort .` and similar tools must skip `.venv` and `vendor`; these excludes are
  configured in `pyproject.toml`.

## Documentation

- Documentation is built with Zensical.
- Update `docs/articles/` for vignette-style usage explanations.
- Update `docs/reference/` and `zensical.toml` when public modules change.
- Mention the Codex runtime platform caveat wherever setup instructions imply
  local Codex execution.
