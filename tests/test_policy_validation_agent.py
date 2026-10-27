from __future__ import annotations

from copy import deepcopy

import pytest

from app.schemas.policy import CoverageStatus, PolicyClaimInput, PolicyEvaluationDraft
from app.services.policy_validation_agent import PolicyValidationAgent


def claim(**overrides) -> PolicyClaimInput:
    values = {
        "claim_id": "CLM10001",
        "payer_id": "PAY1001",
        "claim_type": "INPATIENT",
        "service_date": "2026-09-20",
        "diagnosis_code": "J18.9",
        "procedure_code": "99223",
        "authorization_status": None,
    }
    values.update(overrides)
    return PolicyClaimInput.model_validate(values)


def evidence(**metadata_overrides) -> dict:
    metadata = {
        "payer_id": "PAY1001",
        "claim_type": "INPATIENT",
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
        "policy_version": "2026.1",
    }
    metadata.update(metadata_overrides)
    return {
        "evidence_id": "E1",
        "source_reference": "s3://policy-bucket/PAY1001/inpatient-policy.pdf",
        "source_filename": "inpatient-policy.pdf",
        "supporting_text": "Inpatient pneumonia treatment is covered when medical necessity requirements are met.",
        "score": 0.91,
        "metadata": metadata,
    }


class FakeKnowledgeBase:
    def __init__(self, retrieved, evaluated=None, retrieve_error: Exception | None = None):
        self.retrieved = retrieved
        self.evaluated = evaluated
        self.retrieve_error = retrieve_error
        self.query = None
        self.evaluation_calls = 0

    def retrieve(self, query):
        self.query = query
        if self.retrieve_error:
            raise self.retrieve_error
        return deepcopy(self.retrieved)

    def evaluate(self, _claim, _evidence):
        self.evaluation_calls += 1
        if isinstance(self.evaluated, Exception):
            raise self.evaluated
        return self.evaluated


def draft(status="COVERED", **overrides) -> PolicyEvaluationDraft:
    values = {
        "coverage_status": status,
        "reason": "Applicable policy evidence supports the stated policy coverage category.",
        "evidence_ids": ["E1"],
        "authorization_required": False,
        "authorization_status": "NOT_REQUIRED",
        "missing_information": [],
        "confidence": 0.9,
    }
    values.update(overrides)
    return PolicyEvaluationDraft.model_validate(values)


def run(kb: FakeKnowledgeBase, input_claim: PolicyClaimInput | None = None):
    return PolicyValidationAgent(kb).validate(input_claim or claim(), "corr-policy-1")


def test_applicable_coverage_evidence_with_citation() -> None:
    kb = FakeKnowledgeBase([evidence()], draft())
    result = run(kb)
    assert result.coverage_status == CoverageStatus.COVERED
    assert result.evidence[0].source_filename == "inpatient-policy.pdf"
    assert result.evidence[0].supporting_text.startswith("Inpatient pneumonia")
    assert result.manual_review_required is False
    assert result.confidence == 0.9
    assert all(term in kb.query for term in ("PAY1001", "INPATIENT", "2026-09-20", "J18.9", "99223"))


def test_explicit_exclusion_is_not_covered_but_not_a_claim_denial() -> None:
    excluded = evidence()
    excluded["supporting_text"] = "The listed procedure is explicitly excluded from this inpatient benefit."
    result = run(FakeKnowledgeBase([excluded], draft("NOT_COVERED", reason="The applicable policy explicitly excludes the procedure.")))
    assert result.coverage_status == CoverageStatus.NOT_COVERED
    assert result.reason == "The applicable policy explicitly excludes the procedure."


def test_missing_medical_documentation_is_unresolved() -> None:
    result = run(FakeKnowledgeBase([evidence()], draft(
        "INSUFFICIENT_INFORMATION",
        reason="Medical-necessity documentation is required but was not supplied.",
        missing_information=["medical_documentation"],
        confidence=0.65,
    )))
    assert result.coverage_status == CoverageStatus.INSUFFICIENT_INFORMATION
    assert "medical_documentation" in result.missing_information
    assert result.manual_review_required


def test_wrong_payer_evidence_is_ignored() -> None:
    kb = FakeKnowledgeBase([evidence(payer_id="PAY9999")], draft())
    result = run(kb)
    assert result.coverage_status == CoverageStatus.INSUFFICIENT_INFORMATION
    assert result.evidence == []
    assert any("wrong or missing payer" in issue for issue in result.evidence_issues)
    assert kb.evaluation_calls == 0


def test_expired_policy_is_ignored() -> None:
    result = run(FakeKnowledgeBase([evidence(effective_to="2025-12-31")], draft()))
    assert result.coverage_status == CoverageStatus.INSUFFICIENT_INFORMATION
    assert any("not effective" in issue for issue in result.evidence_issues)


def test_empty_retrieval_is_not_treated_as_noncoverage() -> None:
    result = run(FakeKnowledgeBase([], draft("NOT_COVERED")))
    assert result.coverage_status == CoverageStatus.INSUFFICIENT_INFORMATION
    assert "EMPTY_POLICY_RETRIEVAL" in result.evidence_issues


def test_conflicting_policy_versions_route_to_review() -> None:
    second = evidence(policy_version="2026.2")
    second["evidence_id"] = "E2"
    kb = FakeKnowledgeBase([evidence(), second], draft())
    result = run(kb)
    assert result.coverage_status == CoverageStatus.INSUFFICIENT_INFORMATION
    assert "CONFLICTING_POLICY_VERSIONS" in result.evidence_issues
    assert kb.evaluation_calls == 0


def test_unknown_authorization_status_requires_review() -> None:
    result = run(FakeKnowledgeBase([evidence()], draft(
        authorization_required=True,
        authorization_status="UNKNOWN",
        missing_information=[],
    )))
    assert result.coverage_status == CoverageStatus.COVERED
    assert result.authorization_required is True
    assert result.authorization_status == "UNKNOWN"
    assert "authorization_status" in result.missing_information
    assert result.manual_review_required


def test_malformed_model_output_routes_to_review() -> None:
    result = run(FakeKnowledgeBase([evidence()], "not a validated model"))
    assert result.coverage_status == CoverageStatus.INSUFFICIENT_INFORMATION
    assert "MALFORMED_POLICY_MODEL_OUTPUT" in result.evidence_issues
    assert result.confidence == 0


def test_aws_retrieval_error_is_contained_without_sensitive_message() -> None:
    result = run(FakeKnowledgeBase([], retrieve_error=OSError("secret endpoint detail")))
    assert result.coverage_status == CoverageStatus.INSUFFICIENT_INFORMATION
    assert "POLICY_RETRIEVAL_FAILED" in result.evidence_issues
    assert "secret" not in result.model_dump_json()
