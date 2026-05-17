# Step 1 Usage

Workflow step 1 starts from an individual SAP or protocol PDF and produces a
local reproduction run directory. The baseline implementation uses Mathpix for
PDF OCR and a local Codex SDK/runtime for the agent execution step.

## Create a workspace

```bash
uv run tdb init tdb-workspace
```

The workspace contains:

- `.env` for Mathpix and Codex configuration.
- `.gitignore` that excludes credentials, converted documents, and run outputs.
- `converted/` for Mathpix Markdown and metadata.
- `runs/` for prompts, Codex responses, and run summaries.

## Configure credentials

```bash
uv run tdb configure --workspace tdb-workspace
```

This writes `MATHPIX_APP_ID`, `MATHPIX_APP_KEY`, `CODEX_MODEL`, and optionally
`CODEX_BIN` to `tdb-workspace/.env`. The default model is `gpt-5.5`; Codex runs
default to high reasoning effort.

## Convert only

```bash
uv run tdb convert path/to/sap.pdf --workspace tdb-workspace
```

Add `--save-tex-zip` to request Mathpix's LaTeX ZIP conversion in addition to
the Mathpix Markdown text.

## Convert and run Codex

```bash
uv run tdb run path/to/sap.pdf --workspace tdb-workspace --case-id tdb-001
```

The command saves:

- `converted/<pdf-stem>.mmd`
- `converted/<pdf-stem>.mathpix.json`
- `runs/<case-id>/prompt.md`
- `runs/<case-id>/codex_response.md`
- `runs/<case-id>/codex_run.json`
- `runs/<case-id>.step1.json`

Use `--no-codex` when you only want to test ingestion while still using the same
output layout.
