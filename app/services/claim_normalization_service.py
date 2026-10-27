"""Normalize and validate Day 7 OCR output without making claim decisions."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import Depends
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import NORMALIZATION_REQUIRED_FIELDS
from app.database import get_db
from app.models.claim import Claim
from app.repositories.claim_repository import ClaimRepository
from app.schemas.extraction import (
    ExtractionStatus,
    NormalizationResponse,
    NormalizedExtraction,
    RawOCRResult,
    ReviewFlag,
    ReviewSeverity,
    SourceDocument,
)
from app.services.s3_service import download_json, upload_json
from app.services.tesseract_service import extract_claim_fields, extract_document_claim_id

RAW_ARTIFACT_TEMPLATE = "results/{claim_id}/tesseract_raw.json"
NORMALIZED_ARTIFACT_TEMPLATE = "results/{claim_id}/normalized_extraction.json"
DIAGNOSIS_CODE_PATTERN = re.compile(r"^[A-Z][0-9]{2}(?:\.[A-Z0-9]{1,4})?$")
PROCEDURE_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{2,9}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._/-]*$")


class ClaimNotFoundError(LookupError):
    pass


class InvalidRawOCRArtifactError(ValueError):
    pass


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).strip().split())
    return normalized or None


def normalize_identifier(value: Any) -> str | None:
    normalized = normalize_text(value)
    return normalized.upper() if normalized else None


def normalize_diagnosis_code(value: Any) -> str | None:
    normalized = normalize_identifier(value)
    return normalized if normalized and DIAGNOSIS_CODE_PATTERN.fullmatch(normalized) else None


def normalize_procedure_code(value: Any) -> str | None:
    normalized = normalize_identifier(value)
    return normalized if normalized and PROCEDURE_CODE_PATTERN.fullmatch(normalized) else None


def normalize_amount(value: Any) -> tuple[Decimal | None, str | None]:
    """Return an exact amount and explicit currency; never infer a currency."""
    source = normalize_text(value)
    if source is None:
        return None, None

    currency_markers = {
        "USD": ("USD", "$"),
        "INR": ("INR", "₹"),
        "EUR": ("EUR", "€"),
        "GBP": ("GBP", "£"),
    }
    currencies = {
        code
        for code, markers in currency_markers.items()
        if any(re.search(re.escape(marker), source, re.I) for marker in markers)
    }
    if len(currencies) > 1 or source.startswith("(") or source.endswith(")"):
        return None, None
    currency = next(iter(currencies), None)
    numeric = source
    for markers in currency_markers.values():
        for marker in markers:
            numeric = re.sub(re.escape(marker), "", numeric, flags=re.I)
    numeric = numeric.strip()
    if numeric.startswith("-") or not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?", numeric):
        return None, currency
    try:
        amount = Decimal(numeric.replace(",", ""))
    except InvalidOperation:
        return None, currency
    return amount.quantize(Decimal("0.01")), currency


def normalize_date(value: Any) -> str | None:
    source = normalize_text(value)
    if source is None:
        return None
    for date_format in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%b-%Y",
        "%d-%B-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
    ):
        try:
            return datetime.strptime(source, date_format).date().isoformat()
        except ValueError:
            pass
    numeric = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", source)
    if numeric is None:
        return None
    first, second, year = (int(part) for part in numeric.groups())
    if first > 12 >= second:
        day, month = first, second
    elif second > 12 >= first:
        month, day = first, second
    else:
        return None
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def _flag(field: str, code: str, message: str) -> ReviewFlag:
    return ReviewFlag(field=field, code=code, severity=ReviewSeverity.ERROR, message=message)


def _normalized_candidates(
    field: dict[str, Any], normalizer
) -> tuple[Any, bool, bool]:
    candidates = field.get("candidates", [])
    if not candidates:
        return None, False, False
    values = [normalizer(candidate.get("raw_value")) for candidate in candidates]
    valid_values = {value for value in values if value is not None}
    raw_values = {normalize_text(candidate.get("raw_value")) for candidate in candidates}
    conflicting = len(valid_values) > 1 or (len(raw_values) > 1 and len(valid_values) != 1)
    return (next(iter(valid_values)) if len(valid_values) == 1 and not conflicting else None), True, conflicting


def normalize_ocr_result(raw_result: dict[str, Any]) -> NormalizedExtraction:
    try:
        raw = RawOCRResult.model_validate(raw_result)
    except ValidationError as error:
        raise InvalidRawOCRArtifactError(f"Raw OCR JSON does not match the Day 7 schema: {error}") from error

    now = datetime.now(timezone.utc)
    source = SourceDocument(bucket=raw.source_bucket, key=raw.source_key)
    usable_text = any(page.text.strip() for page in raw.pages)
    if raw.extraction_status != "COMPLETED" or not usable_text:
        reason = "OCR output contains no usable text" if not usable_text else f"OCR status is {raw.extraction_status}"
        return NormalizedExtraction(
            claim_id=raw.claim_id,
            extraction_status=ExtractionStatus.FAILED,
            review_required=False,
            review_flags=[_flag("document", "OCR_PROCESSING_FAILED", reason)],
            source_document=source,
            failure_reason=reason,
            created_at=now,
            updated_at=now,
        )

    fields = extract_claim_fields(raw_result)
    flags: list[ReviewFlag] = []
    document_claim_id = extract_document_claim_id(raw_result)
    if document_claim_id is not None and document_claim_id != raw.claim_id.upper():
        flags.append(
            _flag(
                "claim_id",
                "DOCUMENT_CLAIM_ID_MISMATCH",
                f"Document Claim ID {document_claim_id} does not match application Claim ID {raw.claim_id.upper()}.",
            )
        )
    output: dict[str, Any] = {}
    normalizers = {
        "patient_id": normalize_identifier,
        "member_id": normalize_identifier,
        "patient_name": normalize_text,
        "provider_id": normalize_identifier,
        "payer_id": normalize_identifier,
        "policy_id": normalize_identifier,
        "authorization_number": normalize_identifier,
        "authorization_status": normalize_identifier,
        "diagnosis_code": normalize_diagnosis_code,
        "procedure_code": normalize_procedure_code,
        "date_of_service": normalize_date,
    }

    for field_name, normalizer in normalizers.items():
        value, found, conflicting = _normalized_candidates(fields[field_name], normalizer)
        output[field_name] = value
        if conflicting:
            flags.append(_flag(field_name, "CONFLICTING_VALUES", f"Conflicting {field_name} values were extracted."))
        elif found and value is None:
            code = {
                "diagnosis_code": "INVALID_DIAGNOSIS_CODE",
                "procedure_code": "INVALID_PROCEDURE_CODE",
                "date_of_service": "INVALID_OR_AMBIGUOUS_DATE",
            }.get(field_name, "MALFORMED_FIELD")
            flags.append(_flag(field_name, code, f"The extracted {field_name} value is malformed or ambiguous."))

    amount_candidates = fields["amount"].get("candidates", [])
    parsed_amounts = [normalize_amount(item.get("raw_value")) for item in amount_candidates]
    distinct_amounts = set(parsed_amounts)
    raw_amounts = {normalize_text(item.get("raw_value")) for item in amount_candidates}
    amount_found = bool(amount_candidates)
    one_valid_interpretation = (
        len(distinct_amounts) == 1 and next(iter(distinct_amounts), (None, None))[0] is not None
    )
    amount_conflicting = len(distinct_amounts) > 1 or (
        len(raw_amounts) > 1 and not one_valid_interpretation
    )
    if len(distinct_amounts) == 1 and amount_found:
        output["amount"], output["currency"] = parsed_amounts[0]
    else:
        output["amount"], output["currency"] = None, None
    if amount_conflicting:
        flags.append(_flag("amount", "CONFLICTING_VALUES", "Conflicting claim amounts were extracted."))
    elif amount_found and output["amount"] is None:
        flags.append(_flag("amount", "INVALID_AMOUNT", "The extracted amount is malformed, ambiguous, or negative."))

    for field_name in NORMALIZATION_REQUIRED_FIELDS:
        found = amount_found if field_name == "amount" else bool(fields[field_name].get("candidates"))
        if not found:
            flags.append(_flag(field_name, "MISSING_REQUIRED_FIELD", f"Required field {field_name} could not be extracted."))

    for field_name in ("patient_id", "member_id", "provider_id", "payer_id", "policy_id", "authorization_number", "authorization_status"):
        value = output.get(field_name)
        if value is not None and not IDENTIFIER_PATTERN.fullmatch(value):
            output[field_name] = None
            flags.append(_flag(field_name, "INVALID_IDENTIFIER", f"The extracted {field_name} has invalid syntax."))

    status = ExtractionStatus.REVIEW_REQUIRED if flags else ExtractionStatus.COMPLETE
    return NormalizedExtraction(
        claim_id=raw.claim_id,
        **output,
        extraction_status=status,
        review_required=bool(flags),
        review_flags=flags,
        source_document=source,
        created_at=now,
        updated_at=now,
    )


class ClaimNormalizationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = ClaimRepository(session)

    def normalize_claim(self, claim_id: str) -> NormalizationResponse:
        normalized_claim_id = claim_id.strip().upper()
        claim: Claim | None = self._repository.get(normalized_claim_id)
        if claim is None:
            raise ClaimNotFoundError(normalized_claim_id)

        raw_key = RAW_ARTIFACT_TEMPLATE.format(claim_id=normalized_claim_id)
        normalized_key = NORMALIZED_ARTIFACT_TEMPLATE.format(claim_id=normalized_claim_id)
        raw_payload, raw_etag = download_json(raw_key)
        try:
            raw = RawOCRResult.model_validate(raw_payload)
        except ValidationError as error:
            raise InvalidRawOCRArtifactError(f"Raw OCR JSON does not match the Day 7 schema: {error}") from error
        if raw.claim_id.upper() != normalized_claim_id:
            raise InvalidRawOCRArtifactError("Raw OCR claim_id does not match the requested claim")

        pending_values = {
            "extracted_data": {},
            "extraction_status": ExtractionStatus.PENDING.value,
            "review_required": False,
            "review_flags": [],
            "normalized_artifact_key": None,
            "failure_reason": None,
        }
        try:
            self._repository.upsert_extraction(
                claim_id=normalized_claim_id,
                raw_ocr_artifact_key=raw_key,
                raw_ocr_artifact_etag=raw_etag,
                values=pending_values,
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise

        try:
            normalized = normalize_ocr_result(raw_payload)
            serializable = normalized.model_dump(mode="json")
            upload_json(serializable, normalized_key)
        except Exception as error:
            self._session.rollback()
            try:
                self._repository.upsert_extraction(
                    claim_id=normalized_claim_id,
                    raw_ocr_artifact_key=raw_key,
                    raw_ocr_artifact_etag=raw_etag,
                    values={
                        **pending_values,
                        "extraction_status": ExtractionStatus.FAILED.value,
                        "failure_reason": str(error)[:2000],
                    },
                )
                self._session.commit()
            except Exception:
                self._session.rollback()
            raise

        try:
            self._repository.upsert_extraction(
                claim_id=normalized_claim_id,
                raw_ocr_artifact_key=raw_key,
                raw_ocr_artifact_etag=raw_etag,
                values={
                    "extracted_data": serializable,
                    "extraction_status": normalized.extraction_status,
                    "review_required": normalized.review_required,
                    "review_flags": [flag.model_dump(mode="json") for flag in normalized.review_flags],
                    "normalized_artifact_key": normalized_key,
                    "failure_reason": normalized.failure_reason,
                },
            )
            self._repository.add_audit(
                normalized_claim_id,
                "CLAIM_EXTRACTION_NORMALIZED",
                {"status": str(normalized.extraction_status), "artifact_key": normalized_key},
            )
            self._session.commit()
        except Exception as error:
            self._session.rollback()
            setattr(error, "orphaned_s3_object", f"{normalized.source_document.bucket}/{normalized_key}")
            raise

        return NormalizationResponse(
            extraction=normalized,
            raw_ocr_artifact_key=raw_key,
            normalized_artifact_key=normalized_key,
        )

    def get_latest_extraction(self, claim_id: str) -> NormalizationResponse:
        normalized_claim_id = claim_id.strip().upper()
        if self._repository.get(normalized_claim_id) is None:
            raise ClaimNotFoundError(normalized_claim_id)
        extraction = self._repository.get_latest_normalized_extraction(normalized_claim_id)
        if extraction is None:
            raise LookupError("No normalized extraction exists for this claim")
        return NormalizationResponse(
            extraction=NormalizedExtraction.model_validate(extraction.extracted_data),
            raw_ocr_artifact_key=extraction.raw_ocr_artifact_key or "",
            normalized_artifact_key=extraction.normalized_artifact_key or "",
        )


def get_claim_normalization_service(
    session: Session = Depends(get_db),
) -> ClaimNormalizationService:
    return ClaimNormalizationService(session)
