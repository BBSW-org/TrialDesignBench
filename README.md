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

## Scope

The benchmark currently focuses on two core tasks:

- **Task 1 (Reproduction):** Given a Statistical Analysis Plan (SAP) or
  study protocol, evaluate how accurately AI agents can reproduce the
  trial design using R.
- **Task 2 (design generation):** Given high-level clinical requirements,
  evaluate the ability of AI agents to draft new clinical trial designs using R.

### Task 1 (reproduction)

This baseline implements the workflow for reproducing existing designs:

1. Create a local benchmark workspace.
2. Convert a SAP/protocol PDF to Mathpix Markdown, with optional LaTeX ZIP output.
3. Build the standard TrialDesignBench reproduction prompt.
4. Run the prompt against a locally installed Codex SDK/runtime and save the run artifacts.

### Task 2 (design generation)

Under development.

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

## Quick start

1. Initialize a workspace:
   ```bash
   uv run tdb init tdb-workspace
   ```
2. Configure API credentials (Mathpix):
   ```bash
   uv run tdb configure --workspace tdb-workspace
   ```
3. Run the benchmark on a protocol PDF:
   ```bash
   uv run tdb run path/to/sap.pdf --workspace tdb-workspace --case-id tdb-001
   ```

For a full explanation of CLI commands, artifacts, and configuration options,
see the [usage guide](https://bbsw-org.github.io/TrialDesignBench/articles/usage/)
and [configuration](https://bbsw-org.github.io/TrialDesignBench/articles/configuration/).
