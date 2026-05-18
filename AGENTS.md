# Agent Guidelines

These notes capture project-specific lessons and internal constraints for AI 
agents working on `trialdesignbench`. For general usage instructions, CLI 
commands, and artifact structure, refer to the [User Documentation](docs/articles/usage.md).

## Project Mission

TrialDesignBench is a community-driven benchmark to evaluate AI agents in 
clinical trial design, focusing on reproducibility and the drafting of new 
statistical designs.

## Development Environment

- **Dependency Management:** Use `uv`. Local development should prefer 
  `uv sync --dev` and `uv run ...`.
- **Codex SDK:** Declared as a Git dependency in `pyproject.toml`.
- **Linux CI Constraint:** The `openai-codex` runtime may not have compatible 
  wheels for all Linux environments. Workflows should skip Codex installation 
  on Linux if needed:
  ```bash
  uv sync --dev --no-install-package openai-codex --no-install-package openai-codex-cli-bin
  uv run --no-sync <tool>
  ```
- **Python Pinning:** Pin workflows to the Python from `actions/setup-python` 
  using `--python ... --no-python-downloads`.

## Implementation Details

### Codex Integration
- **Model & Effort:** Default to `gpt-5.5` with `high` reasoning effort.
- **SDK Surface:** The SDK does not currently expose `toggle_fast_mode`. 
- **Lazy Imports:** Import `openai_codex` lazily to ensure conversion-only 
  workflows and type checks don't require the local runtime.

### Mathpix Integration
- **Asynchronous Flow:** Upload -> Poll -> Download.
- **Testing:** Keep API calls behind injectable transport boundaries for mocking.
- **Secrets:** Do not log `MATHPIX_APP_ID` or `MATHPIX_APP_KEY`.

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
*Note: Tools must skip `.venv` and `vendor` (configured in `pyproject.toml`).*

## Documentation Structure
- **Public Docs:** Managed with Zensical in `docs/`.
- **Vignettes:** Update `docs/articles/` for usage guides.
- **Reference:** Update `docs/reference/` and `zensical.toml` for API changes.
