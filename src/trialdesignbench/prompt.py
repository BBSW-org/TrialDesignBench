"""Standard prompt construction for trial design reproduction."""

from __future__ import annotations

from pathlib import Path


def build_reproduction_prompt(
    *,
    document_text: str,
    source_name: str,
    case_id: str | None = None,
) -> str:
    """Build the standard workflow step 1 prompt for a converted SAP/protocol."""
    case_label = case_id or Path(source_name).stem
    return f"""You are reproducing a clinical trial design for TrialDesignBench.

Source document: {source_name}
Case identifier: {case_label}

Use only the protocol or Statistical Analysis Plan text below as the source of truth.
Your task is to reconstruct the trial design's operational characteristics in a
local harness with R, Python, and common statistical packages available.

Produce these files in the current working directory:

1. `reproduce_design.R`: executable R code that calculates the design quantities
   stated or implied by the source document, including sample size, power, type I
   error, allocation ratio, interim analysis rules, and other design parameters
   when present.
2. `reproduction_report.md`: a concise report that states the extracted design,
   assumptions, formulas or package functions used, and how the code output
   matches the source document.
3. `run_notes.md`: brief notes about ambiguities, unavailable inputs, and any
   manual verification a trial statistician should perform.

Keep the implementation reproducible and deterministic. Prefer explicit
statistical formulas or well-established R packages over hidden heuristics.

Converted source document:

```text
{document_text}
```
"""
