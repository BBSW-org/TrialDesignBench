import pytest
from pydantic import ValidationError

from trialdesignbench import (
    DocumentKind,
    ExpectedDesign,
    SourceDocument,
    TrialDesignCase,
    TrialPhase,
)


def test_trial_design_case_builds_prompt_with_phase() -> None:
    document = SourceDocument(kind=DocumentKind.PROTOCOL, text="protocol text")
    case = TrialDesignCase(
        case_id="tdb-001",
        title="Example Oncology Trial",
        therapeutic_area="Oncology",
        design_type="Group sequential design",
        phase=TrialPhase.PHASE_3,
        documents=(document,),
        expected_design=ExpectedDesign(
            primary_endpoint="Overall survival",
            statistical_method="Log-rank test",
            target_power=0.9,
            alpha=0.025,
            sample_size=420,
        ),
    )

    assert case.documents == (document,)
    assert case.expected_design.sample_size == 420
    assert (
        case.prompt()
        == "Reproduce the clinical trial design for: Example Oncology Trial. "
        "Therapeutic area: Oncology. Trial phase: phase_3. "
        "Design type: Group sequential design. Produce executable R code and "
        "a short reproduction report."
    )


def test_source_document_requires_content() -> None:
    with pytest.raises(ValidationError, match="at least one of path, url, or text"):
        SourceDocument(kind=DocumentKind.SAP)


def test_trial_design_case_requires_protocol_or_sap() -> None:
    publication = SourceDocument(kind=DocumentKind.PUBLICATION, text="publication")

    with pytest.raises(ValidationError, match="protocol or statistical analysis plan"):
        TrialDesignCase(
            case_id="tdb-002",
            title="Publication Only Trial",
            therapeutic_area="Cardiovascular",
            design_type="Fixed design",
            documents=(publication,),
        )


def test_expected_design_validates_numeric_bounds() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        ExpectedDesign(target_power=1.1)

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        ExpectedDesign(sample_size=0)
