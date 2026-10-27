from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.policy import ClaimType


class FraudScreeningStatus(StrEnum):
    CLEAR = "CLEAR"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class FraudClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, max_length=64)
    member_id: str | None = None
    provider_id: str | None = None
    payer_id: str = Field(min_length=1, max_length=64)
    claim_type: ClaimType
    service_date: date | None = None
    diagnosis_code: str | None = None
    procedure_code: str | None = None
    billed_amount: Decimal | None = Field(default=None, ge=0)
    conflicting_fields: list[str] = Field(default_factory=list)

    @field_validator("claim_id", "member_id", "provider_id", "payer_id", "diagnosis_code", "procedure_code")
    @classmethod
    def normalize_identifiers(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class HistoricalClaim(BaseModel):
    model_config = ConfigDict(extra="ignore")

    claim_id: str | None = None
    member_id: str | None = None
    provider_id: str | None = None
    payer_id: str | None = None
    claim_type: str | None = None
    service_date: date | None = None
    diagnosis_code: str | None = None
    procedure_code: str | None = None
    amount: Decimal | None = Field(default=None, ge=0)


class FraudEvidence(BaseModel):
    evidence_type: str
    reference: str
    details: str


class TriggeredFraudRule(BaseModel):
    rule_id: str
    weight: int = Field(ge=0, le=100)
    explanation: str
    evidence_references: list[str] = Field(default_factory=list)


class FraudScreeningResult(BaseModel):
    claim_id: str
    correlation_id: str
    fraud_screening_status: FraudScreeningStatus
    risk_score: int = Field(ge=0, le=100)
    triggered_rules: list[TriggeredFraudRule] = Field(default_factory=list)
    evidence: list[FraudEvidence] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    manual_review_required: bool
    disclaimer: str = "Anomaly screening only; this is not a confirmed fraud determination."


class FraudScreeningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conflicting_fields: list[str] = Field(default_factory=list)
