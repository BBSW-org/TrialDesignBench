"""Core data structures for describing TrialDesignBench cases."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class DocumentKind(str, Enum):
    """Supported source document categories for a trial design case."""

    PROTOCOL = "protocol"
    SAP = "statistical_analysis_plan"
    PUBLICATION = "publication"
    REGISTRY = "registry"


class TrialPhase(str, Enum):
    """Clinical trial phases commonly represented in the benchmark."""

    PHASE_1 = "phase_1"
    PHASE_2 = "phase_2"
    PHASE_3 = "phase_3"
    PHASE_4 = "phase_4"


class SourceDocument(BaseModel):
    """A source document used by an agent to reproduce a trial design."""

    model_config = ConfigDict(frozen=True)

    kind: DocumentKind
    path: Path | None = None
    url: HttpUrl | None = None
    text: str | None = None

    @model_validator(mode="after")
    def require_source(self) -> SourceDocument:
        """Require at least one way to access the document."""
        if self.path is None and self.url is None and not self.text:
            msg = "at least one of path, url, or text must be provided"
            raise ValueError(msg)
        return self


class ExpectedDesign(BaseModel):
    """Objective design characteristics used for benchmark scoring."""

    model_config = ConfigDict(frozen=True)

    primary_endpoint: str | None = None
    statistical_method: str | None = None
    target_power: float | None = Field(default=None, ge=0.0, le=1.0)
    alpha: float | None = Field(default=None, ge=0.0, le=1.0)
    sample_size: int | None = Field(default=None, ge=1)


class TrialDesignCase(BaseModel):
    """A single benchmark case for trial design reproduction."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    title: str
    therapeutic_area: str
    design_type: str
    phase: TrialPhase | None = None
    documents: tuple[SourceDocument, ...]
    expected_design: ExpectedDesign = Field(default_factory=ExpectedDesign)

    @model_validator(mode="after")
    def require_documents(self) -> TrialDesignCase:
        """Require at least one protocol or statistical analysis plan."""
        kinds = {document.kind for document in self.documents}
        if not ({DocumentKind.PROTOCOL, DocumentKind.SAP} & kinds):
            msg = "case must include a protocol or statistical analysis plan"
            raise ValueError(msg)
        return self

    def prompt(self) -> str:
        """Create a concise agent prompt for the benchmark case."""
        parts = [
            f"Reproduce the clinical trial design for: {self.title}.",
            f"Therapeutic area: {self.therapeutic_area}.",
            f"Design type: {self.design_type}.",
            "Produce executable R code and a short reproduction report.",
        ]
        if self.phase is not None:
            parts.insert(2, f"Trial phase: {self.phase.value}.")
        return " ".join(parts)
