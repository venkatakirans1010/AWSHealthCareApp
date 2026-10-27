from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.coding import MedicalCodingResult
from app.schemas.extraction import DocumentAnalysisResult, NormalizedExtraction
from app.schemas.fraud import FraudScreeningResult
from app.schemas.policy import ClaimType, PolicyValidationResult


class SpecialistRunStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    INCOMPLETE = "INCOMPLETE"


class SpecialistExecution(BaseModel):
    status: SpecialistRunStatus = SpecialistRunStatus.SUCCESS
    error_code: str | None = Field(default=None, max_length=128)


class SpecialistExecutions(BaseModel):
    document_analysis: SpecialistExecution = Field(default_factory=SpecialistExecution)
    policy_validation: SpecialistExecution = Field(default_factory=SpecialistExecution)
    medical_coding: SpecialistExecution = Field(default_factory=SpecialistExecution)
    fraud_screening: SpecialistExecution = Field(default_factory=SpecialistExecution)


class ClaimsDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    normalized_claim: NormalizedExtraction
    claim_type: ClaimType
    document_analysis: DocumentAnalysisResult | None = None
    policy_validation: PolicyValidationResult | None = None
    medical_coding: MedicalCodingResult | None = None
    fraud_screening: FraudScreeningResult | None = None
    executions: SpecialistExecutions = Field(default_factory=SpecialistExecutions)


class ClaimsDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_analysis: DocumentAnalysisResult | None = None
    policy_validation: PolicyValidationResult | None = None
    medical_coding: MedicalCodingResult | None = None
    fraud_screening: FraudScreeningResult | None = None
    executions: SpecialistExecutions = Field(default_factory=SpecialistExecutions)


class DecisionRecommendation(StrEnum):
    REQUEST_INFORMATION = "REQUEST_INFORMATION"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ELIGIBLE_FOR_APPROVAL_REVIEW = "ELIGIBLE_FOR_APPROVAL_REVIEW"
    ELIGIBLE_FOR_DENIAL_REVIEW = "ELIGIBLE_FOR_DENIAL_REVIEW"


class DecisionWorkflowStatus(StrEnum):
    COMPLETED = "COMPLETED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"


class DecisionAuditMetadata(BaseModel):
    persisted: bool
    event_type: str
    persisted_claim_status: str | None = None
    created_at: datetime


class ClaimsDecisionResult(BaseModel):
    claim_id: str
    correlation_id: str
    workflow_status: DecisionWorkflowStatus
    decision_recommendation: DecisionRecommendation
    decision_reasons: list[str] = Field(min_length=1)
    specialist_execution_statuses: dict[str, SpecialistRunStatus]
    document_analysis_status: str
    policy_validation_status: str
    policy_evidence_references: list[str] = Field(default_factory=list)
    medical_coding_status: str
    fraud_screening_status: str
    fraud_risk_score: int | None = Field(default=None, ge=0, le=100)
    fraud_risk_indicators: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    manual_review_required: bool
    claim_summary: str
    audit_metadata: DecisionAuditMetadata
