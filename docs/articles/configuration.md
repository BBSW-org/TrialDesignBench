# Configuration

TrialDesignBench reads configuration from the selected workspace `.env` file.
The CLI creates this file with safe defaults and gitignores it inside the
workspace.

| Variable | Required | Purpose |
| --- | --- | --- |
| `MATHPIX_APP_ID` | Yes | Mathpix API application ID. |
| `MATHPIX_APP_KEY` | Yes | Mathpix API key. |
| `CODEX_MODEL` | No | Local Codex model name. Defaults to `gpt-5.4`. |
| `CODEX_BIN` | No | Path to a specific local `codex` binary. |

The Mathpix credentials are sent as `app_id` and `app_key` headers for PDF
upload, status polling, and result download. PDF processing is asynchronous:
TrialDesignBench uploads the file, polls until completion, then downloads the
`.mmd` result and optional `.tex.zip` conversion.

The Codex SDK integration imports `openai_codex` lazily. This keeps package
installation lightweight while allowing users with a local Codex SDK/runtime to
execute the standard prompt without OpenAI API calls.
