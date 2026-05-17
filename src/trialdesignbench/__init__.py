"""TrialDesignBench workflow tooling."""

from importlib.metadata import PackageNotFoundError, version

from trialdesignbench.codex import CodexRunner, LocalCodexRunner
from trialdesignbench.config import (
    DEFAULT_CODEX_EFFORT,
    DEFAULT_CODEX_MODEL,
    TdbConfig,
    configure_workspace,
    create_workspace,
    load_config,
)
from trialdesignbench.mathpix import MathpixClient, MathpixError
from trialdesignbench.models import CodexRunArtifact, ConversionArtifact, StepOneResult
from trialdesignbench.pipeline import StepOnePipeline
from trialdesignbench.prompt import build_reproduction_prompt

try:
    __version__ = version("trialdesignbench")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "CodexRunArtifact",
    "CodexRunner",
    "ConversionArtifact",
    "DEFAULT_CODEX_EFFORT",
    "DEFAULT_CODEX_MODEL",
    "LocalCodexRunner",
    "MathpixClient",
    "MathpixError",
    "StepOnePipeline",
    "StepOneResult",
    "TdbConfig",
    "__version__",
    "build_reproduction_prompt",
    "configure_workspace",
    "create_workspace",
    "load_config",
]
