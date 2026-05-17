# Configuration

TrialDesignBench reads configuration from the selected workspace `.env` file.
The CLI creates this file with safe defaults and gitignores it inside the
workspace.

| Variable | Required | Purpose |
| --- | --- | --- |
| `MATHPIX_APP_ID` | Yes | Mathpix API application ID. |
| `MATHPIX_APP_KEY` | Yes | Mathpix API key. |
| `CODEX_MODEL` | No | Local Codex model name. Defaults to `gpt-5.5`. |
| `CODEX_BIN` | No | Path to a specific local `codex` binary. |

The Mathpix credentials are sent as `app_id` and `app_key` headers for PDF
upload, status polling, and result download. PDF processing is asynchronous:
TrialDesignBench uploads the file, polls until completion, then downloads the
`.mmd` result and optional `.tex.zip` conversion.

The Codex SDK integration uses the GitHub-hosted `openai-codex` Python package
declared in `pyproject.toml` for `uv` environments. The default reproduction run
uses `model_reasoning_effort="high"` when starting the Codex thread and
`effort="high"` for the turn run.

The Git source declaration makes `uv sync` sufficient from a TrialDesignBench
checkout. PyPI package metadata cannot rely on `uv` source tables, so PyPI-only
consumers may need to add the same SDK Git source explicitly until
`openai-codex` is available from a package index.

The current Python SDK schema does not expose `toggle_fast_mode` or an
equivalent fast-mode setting, so TrialDesignBench does not send that option.
