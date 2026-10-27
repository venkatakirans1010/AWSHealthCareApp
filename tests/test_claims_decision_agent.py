from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.schemas.claim import ClaimStatus
from app.schemas.coding import CodeValidationResult, MedicalCodingResult
from app.schemas.decision import (
    ClaimsDecisionInput,
    DecisionRecommendation,
    DecisionWorkflowStatus,
    SpecialistExecution,
    SpecialistExecutions,
    SpecialistRunStatus,
)
from app.schemas.extraction import DocumentAnalysisResult, NormalizedExtraction, SourceDocument
from app.schemas.fraud import FraudScreeningResult, TriggeredFraudRule
from app.schemas.policy import PolicyEvidence, PolicyValidationResult
from app.services.claims_decision_agent import ClaimsDecisionAgent

CORRELATION_ID = "corr-decision-1"


class FakePersister:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = []

    def persist_recommendation(self, *args):
        if self.error:
            raise self.error
        self.calls.append(args)
        return ClaimStatus.PROCESSING


def normalized(**updates) -> NormalizedExtraction:
    now = datetime.now(timezone.utc)
    values = {
        "claim_id": "CLM10001", "patient_id": "PAT1001", "member_id": "MEM1001",
        "provider_id": "PROV1001", "payer_id": "PAY1001", "diagnosis_code": "J18.9",
        "procedure_code": "99223", "amount": Decimal("45000.00"), "currency": "INR",
        "date_of_service": date(2026, 9, 20), "extraction_status": "COMPLETE",
        "review_required": False, "review_flags": [],
        "source_document": SourceDocument(bucket="test", key="incoming/CLM10001/claim.pdf"),
        "created_at": now, "updated_at": now,
    }
    values.update(updates)
    return NormalizedExtraction.model_validate(values)


def document(**updates) -> DocumentAnalysisResult:
    values = {
        "claim_id": "CLM10001", "correlation_id": CORRELATION_ID,
        "extracted_fields": {}, "document_references": [SourceDocument(bucket="test", key="incoming/CLM10001/claim.pdf")],
        "document_results": [], "extraction_status": "COMPLETE", "missing_fields": [],
        "validation_errors": [], "manual_review_required": False,
    }
    values.update(updates)
    return DocumentAnalysisResult.model_validate(values)


def policy(**updates) -> PolicyValidationResult:
    values = {
        "claim_id": "CLM10001", "correlation_id": CORRELATION_ID,
        "coverage_status": "COVERED", "reason": "Applicable synthetic policy supports coverage review.",
        "evidence": [PolicyEvidence(
            evidence_id="E1", source_reference="s3://policies/inpatient.pdf",
            source_filename="inpatient.pdf", supporting_text="Applicable synthetic coverage text.",
            payer_id="PAY1001", claim_type="INPATIENT", effective_from=date(2026, 1, 1), effective_to=date(2026, 12, 31),
        )],
        "authorization_required": False, "authorization_status": "NOT_REQUIRED",
        "missing_information": [], "evidence_issues": [], "manual_review_required": False, "confidence": 0.9,
    }
    values.update(updates)
    return PolicyValidationResult.model_validate(values)


def coding(status="VALID_REFERENCE_MATCH", **updates) -> MedicalCodingResult:
    values = {
        "claim_id": "CLM10001", "payer_id": "PAY1001", "claim_type": "INPATIENT",
        "correlation_id": CORRELATION_ID,
        "diagnosis_results": [CodeValidationResult(code_system="ICD10", code="J18.9", description="Pneumonia, unspecified organism", validation_status=status)],
        "procedure_results": [CodeValidationResult(code_system="CPT", code="99223", description="Initial hospital inpatient or observation care", validation_status="VALID_REFERENCE_MATCH")],
        "ambiguous": status == "AMBIGUOUS", "manual_review_required": status != "VALID_REFERENCE_MATCH", "issues": [],
    }
    values.update(updates)
    return MedicalCodingResult.model_validate(values)


def fraud(**updates) -> FraudScreeningResult:
    values = {
        "claim_id": "CLM10001", "correlation_id": CORRELATION_ID,
        "fraud_screening_status": "CLEAR", "risk_score": 0, "triggered_rules": [],
        "evidence": [], "missing_information": [], "manual_review_required": False,
    }
    values.update(updates)
    return FraudScreeningResult.model_validate(values)


def workflow(**updates) -> ClaimsDecisionInput:
    values = {
        "normalized_claim": normalized(), "claim_type": "INPATIENT",
        "document_analysis": document(), "policy_validation": policy(),
        "medical_coding": coding(), "fraud_screening": fraud(),
    }
    values.update(updates)
    return ClaimsDecisionInput.model_validate(values)


def decide(input_value=None, persister=None):
    persistence = persister or FakePersister()
    return ClaimsDecisionAgent(persistence).decide(input_value or workflow(), CORRELATION_ID), persistence


def test_complete_claim_is_eligible_for_approval_review() -> None:
    result, persistence = decide()
    assert result.decision_recommendation == DecisionRecommendation.ELIGIBLE_FOR_APPROVAL_REVIEW
    assert result.workflow_status == DecisionWorkflowStatus.COMPLETED
    assert result.manual_review_required is False
    assert result.policy_evidence_references == ["s3://policies/inpatient.pdf"]
    assert result.audit_metadata.persisted is True
    assert set(result.specialist_execution_statuses.values()) == {SpecialistRunStatus.SUCCESS}
    assert len(persistence.calls) == 1
    assert "authorized approval review" in result.decision_reasons[0]


def test_missing_claim_documentation_requests_information() -> None:
    incomplete = document(extraction_status="REVIEW_REQUIRED", missing_fields=["document"], manual_review_required=True)
    result, _ = decide(workflow(document_analysis=incomplete))
    assert result.decision_recommendation == DecisionRecommendation.REQUEST_INFORMATION
    assert "document" in result.missing_information


def test_insufficient_policy_evidence_requests_information() -> None:
    insufficient = policy(
        coverage_status="INSUFFICIENT_INFORMATION", evidence=[], missing_information=["applicable_policy_evidence"],
        manual_review_required=True, confidence=0,
    )
    result, _ = decide(workflow(policy_validation=insufficient))
    assert result.decision_recommendation == DecisionRecommendation.REQUEST_INFORMATION
    assert result.policy_validation_status == "INSUFFICIENT_INFORMATION"


@pytest.mark.parametrize(
    ("status", "expected"),
    [("INVALID_CODE", DecisionRecommendation.REQUEST_INFORMATION), ("AMBIGUOUS", DecisionRecommendation.MANUAL_REVIEW)],
)
def test_invalid_or_ambiguous_coding_routes_safely(status, expected) -> None:
    result, _ = decide(workflow(medical_coding=coding(status)))
    assert result.decision_recommendation == expected


def test_high_risk_fraud_is_an_investigation_signal() -> None:
    screening = fraud(
        fraud_screening_status="REVIEW_REQUIRED", risk_score=75,
        triggered_rules=[TriggeredFraudRule(rule_id="POSSIBLE_EXACT_DUPLICATE", weight=50, explanation="Synthetic duplicate indicator")],
        manual_review_required=True,
    )
    result, _ = decide(workflow(fraud_screening=screening))
    assert result.decision_recommendation == DecisionRecommendation.MANUAL_REVIEW
    assert result.fraud_risk_score == 75
    assert "POSSIBLE_EXACT_DUPLICATE" in result.fraud_risk_indicators


@pytest.mark.parametrize("run_status", ["FAILED", "TIMEOUT"])
def test_specialist_failure_or_timeout_routes_to_manual_review(run_status) -> None:
    executions = SpecialistExecutions(
        policy_validation=SpecialistExecution(status=run_status, error_code="UPSTREAM_UNAVAILABLE")
    )
    result, _ = decide(workflow(policy_validation=None, executions=executions))
    assert result.decision_recommendation == DecisionRecommendation.MANUAL_REVIEW
    assert any(run_status in reason for reason in result.decision_reasons)
    assert result.specialist_execution_statuses["policy_validation"].value == run_status


def test_conflicting_specialist_identity_routes_to_manual_review() -> None:
    conflicting = coding(payer_id="PAY9999")
    result, _ = decide(workflow(medical_coding=conflicting))
    assert result.decision_recommendation == DecisionRecommendation.MANUAL_REVIEW
    assert any("PAYER_ID_MISMATCH" in reason for reason in result.decision_reasons)


def test_unknown_required_authorization_requests_information() -> None:
    unknown = policy(authorization_required=True, authorization_status="UNKNOWN", missing_information=["authorization_status"], manual_review_required=True)
    result, _ = decide(workflow(policy_validation=unknown))
    assert result.decision_recommendation == DecisionRecommendation.REQUEST_INFORMATION
    assert "authorization_status" in result.missing_information


def test_explicit_noncoverage_is_only_eligible_for_denial_review() -> None:
    not_covered = policy(coverage_status="NOT_COVERED", reason="Applicable evidence explicitly excludes this service.")
    result, _ = decide(workflow(policy_validation=not_covered))
    assert result.decision_recommendation == DecisionRecommendation.ELIGIBLE_FOR_DENIAL_REVIEW
    assert result.audit_metadata.persisted_claim_status == "PROCESSING"


def test_persistence_failure_returns_nonpersisted_manual_result() -> None:
    result, _ = decide(persister=FakePersister(RuntimeError("database unavailable")))
    assert result.workflow_status == DecisionWorkflowStatus.PERSISTENCE_FAILED
    assert result.decision_recommendation == DecisionRecommendation.MANUAL_REVIEW
    assert result.audit_metadata.persisted is False
    assert result.manual_review_required
