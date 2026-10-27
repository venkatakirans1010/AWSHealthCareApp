from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.policy import ClaimType


class CodeSystem(StrEnum):
    ICD10 = "ICD10"
    CPT = "CPT"


class CodeValidationStatus(StrEnum):
    VALID_REFERENCE_MATCH = "VALID_REFERENCE_MATCH"
    INVALID_CODE = "INVALID_CODE"
    INACTIVE_CODE = "INACTIVE_CODE"
    MISSING_CODE = "MISSING_CODE"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING_INPUT = "CONFLICTING_INPUT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class MedicalCodingInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str = Field(min_length=1, max_length=64)
    payer_id: str = Field(min_length=1, max_length=64)
    claim_type: ClaimType
    diagnosis_code: str | None = None
    procedure_code: str | None = None
    diagnosis_description: str | None = Field(default=None, max_length=1000)
    procedure_description: str | None = Field(default=None, max_length=1000)
    clinical_text: str | None = Field(default=None, max_length=20000)
    diagnosis_confidence: float | None = Field(default=None, ge=0, le=1)
    procedure_confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("claim_id", "payer_id", "diagnosis_code", "procedure_code")
    @classmethod
    def normalize_identifiers(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class MedicalCodingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnosis_description: str | None = Field(default=None, max_length=1000)
    procedure_description: str | None = Field(default=None, max_length=1000)
    clinical_text: str | None = Field(default=None, max_length=20000)
    diagnosis_confidence: float | None = Field(default=None, ge=0, le=1)
    procedure_confidence: float | None = Field(default=None, ge=0, le=1)


class CodeValidationResult(BaseModel):
    code_system: CodeSystem
    code: str | None = None
    description: str | None = None
    validation_status: CodeValidationStatus
    confidence: float | None = Field(default=None, ge=0, le=1)
    ambiguous: bool = False
    clinical_correctness_assessed: bool = False
    issues: list[str] = Field(default_factory=list)


class MedicalCodingResult(BaseModel):
    claim_id: str
    payer_id: str
    claim_type: ClaimType
    correlation_id: str
    diagnosis_results: list[CodeValidationResult]
    procedure_results: list[CodeValidationResult]
    ambiguous: bool
    manual_review_required: bool
    issues: list[str] = Field(default_factory=list)
