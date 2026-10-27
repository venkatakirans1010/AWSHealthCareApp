from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClaimType(StrEnum):
    INPATIENT = "INPATIENT"
    OUTPATIENT = "OUTPATIENT"


class CoverageStatus(StrEnum):
    COVERED = "COVERED"
    NOT_COVERED = "NOT_COVERED"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


class PolicyClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, max_length=64)
    payer_id: str = Field(min_length=1, max_length=64)
    claim_type: ClaimType
    service_date: date
    diagnosis_code: str | None = None
    procedure_code: str | None = None
    authorization_number: str | None = None
    authorization_status: str | None = None
    medical_documentation_available: bool | None = None

    @field_validator("claim_id", "payer_id", "diagnosis_code", "procedure_code", "authorization_number", "authorization_status")
    @classmethod
    def normalize_codes(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class PolicyEvidence(BaseModel):
    evidence_id: str
    source_reference: str
    source_filename: str | None = None
    supporting_text: str
    score: float | None = Field(default=None, ge=0, le=1)
    payer_id: str | None = None
    claim_type: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    policy_version: str | None = None


class PolicyValidationResult(BaseModel):
    claim_id: str
    correlation_id: str
    coverage_status: CoverageStatus
    reason: str
    evidence: list[PolicyEvidence] = Field(default_factory=list)
    authorization_required: bool | None = None
    authorization_status: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    evidence_issues: list[str] = Field(default_factory=list)
    manual_review_required: bool
    confidence: float = Field(ge=0, le=1)


class PolicyValidationRequest(BaseModel):
    medical_documentation_available: bool | None = None


class PolicyEvaluationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coverage_status: CoverageStatus
    reason: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list)
    authorization_required: bool | None = None
    authorization_status: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
