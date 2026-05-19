"""Standard prompt construction for trial design reproduction."""

from __future__ import annotations

from pathlib import Path


# def build_reproduction_prompt(
#     *,
#     document_text: str,
#     source_name: str,
#     case_id: str | None = None,
# ) -> str:
#     """Build the standard workflow step 1 prompt for a converted SAP/protocol."""
#     case_label = case_id or Path(source_name).stem
#     return f"""You are reproducing a clinical trial design for TrialDesignBench.

# Source document: {source_name}
# Case identifier: {case_label}

# Use only the protocol or Statistical Analysis Plan text below as the source of truth.
# Your task is to reconstruct the trial design's operational characteristics in a
# local harness with R, Python, and common statistical packages available.

# Produce these files in the current working directory:

# 1. `reproduce_design.R`: executable R code that calculates the design quantities
#    stated or implied by the source document, including sample size, power, type I
#    error, allocation ratio, interim analysis rules, and other design parameters
#    when present.
# 2. `reproduction_report.md`: a concise report that states the extracted design,
#    assumptions, formulas or package functions used, and how the code output
#    matches the source document.
# 3. `run_notes.md`: brief notes about ambiguities, unavailable inputs, and any
#    manual verification a trial statistician should perform.

# Keep the implementation reproducible and deterministic. Prefer explicit
# statistical formulas or well-established R packages over hidden heuristics.

# Converted source document:

# ```text
# {document_text}
# ```
# """


def build_reproduction_prompt(
    *,
    document_text: str,
    source_name: str,
    case_id: str | None = None,
) -> str:
    """Build the standard workflow prompt for a converted SAP/protocol."""
    case_label = case_id or Path(source_name).stem
    return f"""
# KEYNOTE-189 — Benchmark 1: Reproduction Task

## Instructions

You are an experienced oncology trial statistician. You will be provided with
the protocol from a Phase 3 registrational trial.

Your task is to extract and reproduce the statistical design of this trial
covering the following three design elements:

1. **Alpha allocation** — how type I error is allocated and controlled
   across endpoints and analyses
2. **Target event numbers** — the inputs and calculations underlying the
   target number of events for each primary endpoint
3. **Interim analysis plan** — the planned analyses, their timing, and the
   group sequential boundaries at each analysis

> **Closed-book constraint.** Use **only** the trial information found in
> the input protocol provided below. Do **not** rely on prior knowledge
> of this trial (e.g. published papers, press releases, registry entries,
> later protocol amendments, or your training data). If a value is not
> present or not derivable from the input protocol, say so explicitly
> rather than supplying it from outside knowledge. Every value you
> report must be traceable to a specific section/page of the input
> protocol, or to a calculation whose inputs are themselves traceable.

---

## Output Format

### Step 1: Summary
A concise summary of the overall statistical design.

### Step 2: Comparison Table

Produce a single table with one row per **parameter**, grouped by design
element (Alpha allocation, Target events, Interim analysis). A design
element will typically have multiple parameter rows (e.g. Alpha allocation
includes one-sided α, PFS alpha, OS alpha, rollover rule). The table has
the following columns:

- **Design element:** Alpha allocation / Target events / Interim analysis.
  Repeat the value on every row belonging to that element.
- **Parameter:** the specific quantity or rule the row covers
  (e.g. `One-sided α`, `PFS alpha`, `Target PFS events`, `IA1 information
  fraction`, `IA1 Z-boundary`).
- **Extracted input:** the input(s) feeding this row, parsed from the
  document, with source location (e.g. `[Sec 8.2, p. 45]`). For an
  input-type row this is simply the parameter's extracted value. For a
  derived-value row this is the list of upstream inputs the calculation
  depends on — restate them here even if they also appear as their own
  rows, so the dependency is explicit on every derived row. Identifying
  *which* inputs are needed is part of the task.
- **Calculated value:** the value that follows from the inputs
  (e.g. target event count, interim Z-boundary, nominal alpha at each
  look). Use `—` if the row is direct extraction with no derived value.
- **Calculation method:** a brief explanation of how the calculated
  value was derived (formula, spending function, rollover rule, etc.).
  Use `—` for direct-extraction rows.

> **Note on grading.** A statistical reviewer supplies the ground-truth
> values for the **Extracted input**, **Calculated value**, and
> **Calculation method** columns separately, and grades your table
> against those references. Do **not** attempt to provide or guess
> ground-truth values yourself — report only what you derive from the
> input protocol.

**Boundary scope:** report each interim/final boundary at the endpoint's
**initial allocated alpha only** — i.e. the alpha originally allocated
to that endpoint under the multiplicity framework. Do **not** include
the rollover-scenario boundaries computed at the full overall alpha;
those are out of scope for this benchmark.

Render as (fill in every `…` cell; use `—` where a column does not
apply per the rules above):

| Design element | Parameter | Extracted input | Calculated value | Calculation method |
| --- | --- | --- | --- | --- |
| Alpha allocation | Overall one-sided alpha | … | — | — |
| Alpha allocation | Multiplicity framework | … | — | — |
| Alpha allocation | Alpha allocated to PFS | … | — | — |
| Alpha allocation | Alpha allocated to OS | … | — | — |
| Alpha allocation | PFS rollover rule | … | — | — |
| Alpha allocation | OS rollover rule | … | — | — |
| Alpha allocation | ORR testing condition | … | — | — |
| Target events | PFS assumed HR | … | — | — |
| Target events | OS assumed HR | … | — | — |
| Target events | Randomization ratio | … | — | — |
| Target events | Target power for PFS | … | — | — |
| Target events | Target power for OS | … | — | — |
| Target events | Total PFS target events | … | … | … |
| Target events | Total OS target events | … | … | … |
| Interim analysis | PFS tested at IA1 (Y/N) | … | — | — |
| Interim analysis | PFS tested at IA2 (Y/N) | … | — | — |
| Interim analysis | PFS OBF spending function | … | — | — |
| Interim analysis | PFS information fraction at IA1 | … | — | — |
| Interim analysis | PFS events at IA1 | … | … | … |
| Interim analysis | PFS IA1 Z statistic (at initial allocated α) | … | … | … |
| Interim analysis | PFS IA1 p-value (at initial allocated α) | … | … | … |
| Interim analysis | PFS IA1 HR at bound (at initial allocated α) | … | … | … |
| Interim analysis | PFS IA1 cumulative alpha spent (at initial allocated α) | … | … | … |
| Interim analysis | PFS IA1 cumulative power at assumed HR (at initial allocated α) | … | … | … |
| Interim analysis | PFS IA2 Z statistic (at initial allocated α) | … | … | … |
| Interim analysis | PFS IA2 p-value (at initial allocated α) | … | … | … |
| Interim analysis | PFS IA2 HR at bound (at initial allocated α) | … | … | … |
| Interim analysis | PFS IA2 cumulative alpha spent (at initial allocated α) | … | … | … |
| Interim analysis | PFS IA2 cumulative power at assumed HR (at initial allocated α) | … | … | … |
| Interim analysis | OS tested at IA1 (Y/N) | … | — | — |
| Interim analysis | OS tested at IA2 (Y/N) | … | — | — |
| Interim analysis | OS tested at FA (Y/N) | … | — | — |
| Interim analysis | OS OBF spending function | … | — | — |
| Interim analysis | OS information fraction at IA1 | … | — | — |
| Interim analysis | OS information fraction at IA2 | … | — | — |
| Interim analysis | OS events at IA1 | … | … | … |
| Interim analysis | OS events at IA2 | … | … | … |
| Interim analysis | OS IA1 Z statistic (at initial allocated α) | … | … | … |
| Interim analysis | OS IA1 p-value (at initial allocated α) | … | … | … |
| Interim analysis | OS IA1 HR at bound (at initial allocated α) | … | … | … |
| Interim analysis | OS IA1 cumulative alpha spent (at initial allocated α) | … | … | … |
| Interim analysis | OS IA1 cumulative power at assumed HR (at initial allocated α) | … | … | … |
| Interim analysis | OS IA2 Z statistic (at initial allocated α) | … | … | … |
| Interim analysis | OS IA2 p-value (at initial allocated α) | … | … | … |
| Interim analysis | OS IA2 HR at bound (at initial allocated α) | … | … | … |
| Interim analysis | OS IA2 cumulative alpha spent (at initial allocated α) | … | … | … |
| Interim analysis | OS IA2 cumulative power at assumed HR (at initial allocated α) | … | … | … |
| Interim analysis | OS FA Z statistic (at initial allocated α) | … | … | … |
| Interim analysis | OS FA p-value (at initial allocated α) | … | … | … |
| Interim analysis | OS FA HR at bound (at initial allocated α) | … | … | … |
| Interim analysis | OS FA cumulative alpha spent (at initial allocated α) | … | … | … |
| Interim analysis | OS FA cumulative power at assumed HR (at initial allocated α) | … | … | … |
| Interim analysis | Data cutoff used for ORR hypothesis test | … | — | — |

## Input Document
Converted source document of KN189/corpus/KN189_protocol.pdf:

```text
{document_text}
```
"""
