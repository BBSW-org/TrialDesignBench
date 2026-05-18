# trialdesignbench <img src="assets/logo.svg" align="right" width="120" />

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

For detailed installation and development setup, see the [Installation Guide](https://bbsw-org.github.io/TrialDesignBench/articles/configuration.html).

## Quick Start

1. **Initialize** a workspace:
   ```bash
   uv run tdb init tdb-workspace
   ```
2. **Configure** credentials (Mathpix and Codex):
   ```bash
   uv run tdb configure --workspace tdb-workspace
   ```
3. **Run** the benchmark on a protocol PDF:
   ```bash
   uv run tdb run path/to/sap.pdf --workspace tdb-workspace --case-id tdb-001
   ```

For a full explanation of CLI commands, artifacts, and configuration options, refer to the [Usage Guide](https://bbsw-org.github.io/TrialDesignBench/articles/usage.html) and [Configuration Reference](https://bbsw-org.github.io/TrialDesignBench/articles/configuration.html).
