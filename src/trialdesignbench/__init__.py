"""TrialDesignBench package.

TrialDesignBench is intended to provide benchmark tooling for evaluating AI
agents that reproduce clinical trial design characteristics from protocols and
statistical analysis plans.
"""

from importlib.metadata import PackageNotFoundError, version

from trialdesignbench.cases import (
    DocumentKind,
    ExpectedDesign,
    SourceDocument,
    TrialDesignCase,
    TrialPhase,
)

try:
    __version__ = version("trialdesignbench")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "DocumentKind",
    "ExpectedDesign",
    "SourceDocument",
    "TrialDesignCase",
    "TrialPhase",
    "__version__",
]
