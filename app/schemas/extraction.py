from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExtractionStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ReviewSeverity(StrEnum):
    WARNING = "WARNING"
    ERROR = "ERROR"


class ReviewFlag(BaseModel):
    field: str
    code: str
    severity: ReviewSeverity
    message: str


class SourceDocument(BaseModel):
    bucket: str
    key: str


class DocumentAnalysisRequest(BaseModel):
    """Optional subset of document references already attached to a claim."""

    model_config = ConfigDict(extra="forbid")
    documents: list[SourceDocument] | None = None


class FieldEvidence(BaseModel):
    document: SourceDocument
    page_number: int
    text: str
    confidence: float | None = None


class DocumentExtractionResult(BaseModel):
    document: SourceDocument
    status: str
    page_count: int = Field(default=0, ge=0)
    average_word_confidence: float | None = None
    error: str | None = None


class DocumentAnalysisResult(BaseModel):
    claim_id: str
    correlation_id: str
    extracted_fields: dict[str, Any | None]
    document_references: list[SourceDocument]
    document_results: list[DocumentExtractionResult]
    field_evidence: dict[str, list[FieldEvidence]] = Field(default_factory=dict)
    extraction_status: ExtractionStatus
    missing_fields: list[str] = Field(default_factory=list)
    validation_errors: list[ReviewFlag] = Field(default_factory=list)
    average_word_confidence: float | None = None
    manual_review_required: bool


class NormalizedExtraction(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    claim_id: str
    patient_id: str | None = None
    member_id: str | None = None
    patient_name: str | None = None
    provider_id: str | None = None
    payer_id: str | None = None
    policy_id: str | None = None
    authorization_number: str | None = None
    authorization_status: str | None = None
    diagnosis_code: str | None = None
    procedure_code: str | None = None
    amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    date_of_service: date | None = None
    extraction_status: ExtractionStatus
    review_required: bool
    review_flags: list[ReviewFlag] = Field(default_factory=list)
    source_document: SourceDocument
    failure_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class RawOCRPage(BaseModel):
    model_config = ConfigDict(extra="allow")

    page_number: int = Field(ge=1)
    text: str
    words: list[dict[str, Any]] = Field(default_factory=list)


class RawOCRResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    claim_id: str
    source_bucket: str
    source_key: str
    extraction_status: str
    pages: list[RawOCRPage]


class NormalizationResponse(BaseModel):
    extraction: NormalizedExtraction
    raw_ocr_artifact_key: str
    normalized_artifact_key: str


class OCREvidence(BaseModel):
    page_number: int
    text: str
    confidence: float | None = None


class OCRCandidate(BaseModel):
    raw_value: str
    normalized_value: str | None = None
    evidence: OCREvidence | None = None


class OCRCandidateField(BaseModel):
    value: str | None = None
    found: bool
    conflicting: bool
    candidates: list[OCRCandidate] = Field(default_factory=list)


class OCRFieldReview(BaseModel):
    value: str | None = None
    found: bool
    evidence: OCREvidence | None = None
    confidence: float | None = None
    validation_outcome: str
    review_required: bool
    review_reasons: list[str] = Field(default_factory=list)


class OCRResponse(BaseModel):
    claim_id: str
    extraction_status: str
    page_count: int = Field(ge=1)
    average_word_confidence: float | None = None
    local_output_path: str
    s3_result_key: str | None = None
    candidate_fields: dict[str, OCRCandidateField]
    field_review: dict[str, OCRFieldReview]
    document_claim_id: str | None = None
    claim_id_matches_document: bool | None = None
    review_required: bool
