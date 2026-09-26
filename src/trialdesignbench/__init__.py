"""TrialDesignBench: a thin evaluation framework for AI agents in clinical
trial design.

Harbor runs agents; this package owns the task schema, task materialization,
grading, scoring, aggregation, and provenance.
"""

from trialdesignbench.provenance import package_version

__version__ = package_version()

__all__ = ["__version__"]
