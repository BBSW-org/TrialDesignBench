# trialdesignbench <img src="https://github.com/BBSW-org/TrialDesignBench/raw/main/docs/assets/logo.svg" align="right" width="120" />

[![PyPI version](https://img.shields.io/pypi/v/trialdesignbench)](https://pypi.org/project/trialdesignbench/)
![Python versions](https://img.shields.io/pypi/pyversions/trialdesignbench)
[![CI tests](https://github.com/BBSW-org/TrialDesignBench/actions/workflows/ci-tests.yml/badge.svg)](https://github.com/BBSW-org/TrialDesignBench/actions/workflows/ci-tests.yml)
[![Mypy check](https://github.com/BBSW-org/TrialDesignBench/actions/workflows/mypy.yml/badge.svg)](https://github.com/BBSW-org/TrialDesignBench/actions/workflows/mypy.yml)
[![Ruff check](https://github.com/BBSW-org/TrialDesignBench/actions/workflows/ruff-check.yml/badge.svg)](https://github.com/BBSW-org/TrialDesignBench/actions/workflows/ruff-check.yml)
[![Documentation](https://github.com/BBSW-org/TrialDesignBench/actions/workflows/docs.yml/badge.svg)](https://bbsw-org.github.io/TrialDesignBench/)
![License](https://img.shields.io/pypi/l/trialdesignbench)

TrialDesignBench is a community-driven benchmark for evaluating AI agents in 
clinical trial design.

The benchmark currently focuses on two core tasks:

- **Task 1 (Reproduction):** Given a Statistical Analysis Plan (SAP) or study protocol, evaluate how accurately AI agents can reproduce the trial design using R.
- **Task 2 (Design Generation):** Given high-level clinical requirements, evaluate the ability of AI agents to draft new clinical trial designs using R.

# Task 1 (Reproduction)

This baseline implements the workflow for reproducing existing designs:

1. Create a local benchmark workspace.
2. Convert a SAP/protocol PDF to Mathpix Markdown, with optional LaTeX ZIP output.
3. Build the standard TrialDesignBench reproduction prompt.
4. Run the prompt against a locally installed Codex SDK/runtime and save the run artifacts.

# Task 2 (Design Generation)

**Under Development**

## Installation

```bash
uv add trialdesignbench
```

For development:

```bash
git clone https://github.com/BBSW-org/TrialDesignBench.git
cd TrialDesignBench
uv sync
```

The experimental Codex Python SDK is declared as a Git source dependency for
`uv` environments until it is published on PyPI. From a clone of this
repository, `uv sync` installs both `openai-codex` and its pinned local runtime.
For PyPI-only installs before `openai-codex` is published on PyPI, add the SDK
source explicitly in the consuming project:

```bash
uv add "openai-codex @ git+https://github.com/openai/codex.git#subdirectory=sdk/python"
```

## Quick Start

```bash
uv run tdb init tdb-workspace
uv run tdb configure --workspace tdb-workspace
uv run tdb run path/to/sap.pdf --workspace tdb-workspace --case-id tdb-001
```

Use `--no-codex` to exercise only the Mathpix ingestion portion:

```bash
uv run tdb run path/to/sap.pdf --workspace tdb-workspace --no-codex
```

The workspace `.env` file stores `MATHPIX_APP_ID`, `MATHPIX_APP_KEY`,
`CODEX_MODEL`, and optionally `CODEX_BIN`. The default Codex model is
`gpt-5.5`, and the default reasoning effort is `high`. The generated workspace
`.gitignore` excludes credentials and output artifacts by default.

Converted Mathpix artifacts are reused by default on retry. Add `--force` to
submit the PDF again, or `--http-timeout` to increase the per-request Mathpix
HTTP timeout for large uploads.
