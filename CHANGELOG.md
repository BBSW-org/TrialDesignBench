# Changelog

## trialdesignbench 0.2.1

### Improvements

- Reuse existing non-empty Mathpix Markdown and metadata artifacts by default
  during `convert` and `run`, with `--force` available when a fresh Mathpix
  conversion is required (#14).
- Add `--http-timeout` to `convert` and `run` so large PDF uploads and other
  Mathpix HTTP requests can use a longer per-request timeout (#14).
- Make workspace `.env` configuration authoritative over shell environment
  variables, matching the documented workspace-scoped configuration behavior (#14).
- Write the run summary even when the Codex execution step fails, and
  avoid writing `prompt.md` before the local Codex runtime is importable (#14).

## trialdesignbench 0.2.0

### New features

- Implement a baseline approach for workflow for SAP/protocol PDF ingestion
  using Mathpix and design reproduction using local Codex runs (#10).

## trialdesignbench 0.1.0

### New features

- First version.
