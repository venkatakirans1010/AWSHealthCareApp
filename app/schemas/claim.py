from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ClaimStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    REQUEST_INFORMATION = "REQUEST_INFORMATION"
    FRAUD_REVIEW = "FRAUD_REVIEW"
    FAILED = "FAILED"


class ClaimCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    patient_id: str = Field(min_length=1, max_length=64)
    provider_id: str = Field(min_length=1, max_length=64)
    payer_id: str = Field(min_length=1, max_length=64)
    claim_type: str = Field(min_length=1, max_length=64)
    document_name: str = Field(min_length=1, max_length=255)

    @field_validator("patient_id", "provider_id", "payer_id", "claim_type")
    @classmethod
    def normalize_identifiers(cls, value: str) -> str:
        return value.upper()

    @field_validator("document_name")
    @classmethod
    def validate_document_name(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("document_name must be a filename without a path")
        return value


class ClaimResponse(ClaimCreateRequest):
    model_config = ConfigDict(from_attributes=True)
    claim_id: str
    status: ClaimStatus
    created_at: datetime
    updated_at: datetime
    s3_bucket: str | None = None
    s3_object_key: str | None = None


class ClaimListResponse(BaseModel):
    claims: list[ClaimResponse]
    total: int = Field(ge=0)


class ClaimUploadResponse(BaseModel):
    claim_id: str
    filename: str
    s3_bucket: str
    s3_object_key: str
    status: str = "UPLOADED"
