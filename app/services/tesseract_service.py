"""Local Tesseract OCR and teaching-oriented claim field validation.

The label patterns below are intentionally simple and must be adapted when the
synthetic claim layout changes. They identify candidates; they do not make a
claim approval or denial decision.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from statistics import fmean
from typing import Any, Callable

import pymupdf as fitz
import pytesseract
from PIL import Image
from pytesseract import Output

from app.config import (
    OCR_AUTO_VALIDATION_CONFIDENCE,
    OCR_RENDER_DPI,
    OCR_VERIFICATION_CONFIDENCE,
    S3_BUCKET_NAME,
    TESSERACT_CMD,
    TESSERACT_LANGUAGE,
)
from app.services.s3_service import download_bytes, upload_json

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "ocr"
CLAIM_ID_PATTERN = re.compile(r"^CLM[0-9]+$")
DOCUMENT_CLAIM_ID_PATTERN = re.compile(
    r"^\s*Claim\s+ID\s*[:#-]?\s*(?P<value>CLM[0-9]+)\s*$", re.I | re.M
)

FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "patient_id": re.compile(r"^\s*Patient\s+ID\s*[:#-]?\s*(?P<value>\S.*?)\s*$", re.I | re.M),
    "member_id": re.compile(r"^\s*Member\s+ID\s*[:#-]?\s*(?P<value>\S.*?)\s*$", re.I | re.M),
    "patient_name": re.compile(r"^\s*Patient\s+Name\s*[:#-]?\s*(?P<value>\S.*?)\s*$", re.I | re.M),
    "provider_id": re.compile(r"^\s*Provider\s+ID\s*[:#-]?\s*(?P<value>\S.*?)\s*$", re.I | re.M),
    "payer_id": re.compile(r"^\s*Payer\s+ID\s*[:#-]?\s*(?P<value>\S.*?)\s*$", re.I | re.M),
    "policy_id": re.compile(r"^\s*Policy\s+ID\s*[:#-]?\s*(?P<value>\S.*?)\s*$", re.I | re.M),
    "authorization_number": re.compile(r"^\s*(?:Authorization|Auth)(?:\s+(?:ID|Number|No\.?))\s*[:#-]?\s*(?P<value>\S.*?)\s*$", re.I | re.M),
    "authorization_status": re.compile(r"^\s*(?:Authorization|Auth)\s+Status\s*[:#-]?\s*(?P<value>\S.*?)\s*$", re.I | re.M),
    "diagnosis_code": re.compile(
        r"^\s*(?:Diagnosis\s+Code|ICD(?:-?10)?)\s*[:#-]?\s*(?P<value>\S.*?)\s*$", re.I | re.M
    ),
    "procedure_code": re.compile(
        r"^\s*(?:Procedure\s+Code|Procedure\s*/\s*CPT|CPT)\s*[:#-]?\s*(?P<value>\S.*?)\s*$",
        re.I | re.M,
    ),
    "amount": re.compile(
        r"^\s*(?:Billed\s+Amount|Claimed\s+Amount|Amount)\s*[:#-]?\s*(?P<value>\S.*?)\s*$",
        re.I | re.M,
    ),
    "date_of_service": re.compile(r"^\s*Date\s+of\s+Service\s*[:#-]?\s*(?P<value>\S.*?)\s*$", re.I | re.M),
}
REQUIRED_FIELDS = (
    "patient_id",
    "patient_name",
    "provider_id",
    "payer_id",
    "policy_id",
    "diagnosis_code",
    "procedure_code",
    "amount",
    "date_of_service",
)
IDENTIFIER_FIELDS = {
    "patient_id",
    "member_id",
    "provider_id",
    "payer_id",
    "policy_id",
    "authorization_number",
    "authorization_status",
    "diagnosis_code",
    "procedure_code",
}


def _normalize_identifier(value: str) -> str | None:
    token = value.strip().split()[0] if value.strip() else ""
    return token.upper() if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", token) else None


def _normalize_name(value: str) -> str | None:
    normalized = " ".join(value.split())
    return normalized if normalized and not any(character.isdigit() for character in normalized) else None


def _normalize_amount(value: str) -> str | None:
    cleaned = value.strip().replace(",", "")
    cleaned = re.sub(r"^(?:(?:USD|INR|EUR|GBP)\s*)?[\$₹€£]?", "", cleaned, flags=re.I).strip()
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", cleaned):
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    return format(amount.quantize(Decimal("0.01")), "f")


def extract_document_claim_id(ocr_result: dict[str, Any]) -> str | None:
    """Extract one unambiguous Claim ID printed inside the document."""
    full_text = "\n".join(page.get("text", "") for page in ocr_result.get("pages", []))
    values = {match.group("value").upper() for match in DOCUMENT_CLAIM_ID_PATTERN.finditer(full_text)}
    return next(iter(values)) if len(values) == 1 else None


def _normalize_date(value: str) -> str | None:
    source = " ".join(value.strip().split())
    for date_format in ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d-%B-%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(source, date_format).date().isoformat()
        except ValueError:
            pass

    numeric = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", source)
    if numeric is None:
        return None
    first, second, year = (int(part) for part in numeric.groups())
    if first > 12 and second <= 12:
        day, month = first, second
    elif second > 12 and first <= 12:
        month, day = first, second
    else:
        return None
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


NORMALIZERS: dict[str, Callable[[str], str | None]] = {
    **{field: _normalize_identifier for field in IDENTIFIER_FIELDS},
    "patient_name": _normalize_name,
    "amount": _normalize_amount,
    "date_of_service": _normalize_date,
}


def _field_evidence(ocr_result: dict[str, Any], raw_value: str, matched_text: str) -> dict[str, Any] | None:
    value_tokens = {re.sub(r"[^A-Za-z0-9.]", "", token).casefold() for token in raw_value.split()}
    value_tokens.discard("")
    for page in ocr_result.get("pages", []):
        if matched_text.casefold() not in page.get("text", "").casefold():
            continue
        confidences = [
            word["confidence"]
            for word in page.get("words", [])
            if word.get("confidence") is not None
            and re.sub(r"[^A-Za-z0-9.]", "", word.get("text", "")).casefold() in value_tokens
        ]
        return {
            "page_number": page["page_number"],
            "text": matched_text,
            "confidence": round(fmean(confidences), 2) if confidences else None,
        }
    return None


def extract_claim_fields(ocr_result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return regex-derived candidates without mixing them into raw OCR output."""
    full_text = "\n".join(page.get("text", "") for page in ocr_result.get("pages", []))
    extracted: dict[str, dict[str, Any]] = {}
    for field_name, pattern in FIELD_PATTERNS.items():
        matches = list(pattern.finditer(full_text))
        candidates = []
        for match in matches:
            raw_value = match.group("value").strip()
            candidates.append(
                {
                    "raw_value": raw_value,
                    "normalized_value": NORMALIZERS[field_name](raw_value),
                    "evidence": _field_evidence(ocr_result, raw_value, match.group(0).strip()),
                }
            )
        distinct_values = {
            candidate["normalized_value"] or candidate["raw_value"]
            for candidate in candidates
        }
        extracted[field_name] = {
            "value": candidates[0]["normalized_value"] if len(distinct_values) == 1 and candidates else None,
            "found": bool(candidates),
            "conflicting": len(distinct_values) > 1,
            "candidates": candidates,
        }
    return extracted


def validate_claim_fields(candidate_fields: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Apply field rules and teaching thresholds; this is not adjudication."""
    review: dict[str, dict[str, Any]] = {}
    for field_name in REQUIRED_FIELDS:
        field = candidate_fields.get(field_name, {"found": False, "candidates": []})
        candidates = field.get("candidates", [])
        evidence = candidates[0].get("evidence") if candidates else None
        confidence = evidence.get("confidence") if evidence else None
        reasons: list[str] = []

        if not field.get("found"):
            reasons.append("Required field was not found")
        elif field.get("conflicting"):
            reasons.append("Conflicting candidate values were found")
        elif field.get("value") is None:
            if field_name == "date_of_service":
                reasons.append("Date is invalid or its numeric format is ambiguous")
            elif field_name == "amount":
                reasons.append("Amount is malformed")
            else:
                reasons.append("Candidate failed field-format validation")

        rule_failure = bool(reasons)
        if confidence is None:
            reasons.append("Reliable field-specific OCR confidence is unavailable")
            outcome = "VALIDATION_FAILED" if rule_failure else "RULE_VALIDATION_REQUIRED"
        elif rule_failure:
            outcome = "VALIDATION_FAILED"
        elif confidence >= OCR_AUTO_VALIDATION_CONFIDENCE:
            outcome = "AUTOMATED_VALIDATION_CANDIDATE"
        elif confidence >= OCR_VERIFICATION_CONFIDENCE:
            reasons.append("Field confidence is in the verification range")
            outcome = "VERIFICATION_RECOMMENDED"
        else:
            reasons.append("Field confidence is in the manual-review range")
            outcome = "MANUAL_REVIEW_RECOMMENDED"

        review[field_name] = {
            "value": field.get("value"),
            "found": bool(field.get("found")),
            "evidence": evidence,
            "confidence": confidence,
            "validation_outcome": outcome,
            "review_required": bool(reasons),
            "review_reasons": reasons,
        }
    return review


def _configure_tesseract() -> None:
    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD


def _ocr_pdf(pdf_bytes: bytes, claim_id: str, source_key: str) -> dict[str, Any]:
    _configure_tesseract()
    pages: list[dict[str, Any]] = []
    valid_confidences: list[float] = []
    zoom = OCR_RENDER_DPI / 72
    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        if document.page_count == 0:
            raise ValueError("The source PDF has no pages")
        for page_index, pdf_page in enumerate(document):
            pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            with Image.open(BytesIO(pixmap.tobytes("png"))) as rendered:
                with rendered.convert("RGB") as image:
                    text = pytesseract.image_to_string(image, lang=TESSERACT_LANGUAGE)
                    data = pytesseract.image_to_data(image, lang=TESSERACT_LANGUAGE, output_type=Output.DICT)

            words: list[dict[str, Any]] = []
            for index, word_text in enumerate(data.get("text", [])):
                if not word_text.strip():
                    continue
                try:
                    confidence = float(data["conf"][index])
                except (KeyError, TypeError, ValueError, IndexError):
                    confidence = None
                if confidence is not None and confidence < 0:
                    confidence = None
                if confidence is not None:
                    valid_confidences.append(confidence)
                words.append(
                    {
                        "text": word_text,
                        "confidence": round(confidence, 2) if confidence is not None else None,
                        "page_number": page_index + 1,
                        "left": int(data["left"][index]),
                        "top": int(data["top"][index]),
                        "width": int(data["width"][index]),
                        "height": int(data["height"][index]),
                    }
                )
            pages.append(
                {
                    "page_number": page_index + 1,
                    "text": text,
                    "words": words,
                    "ocr_status": "READABLE" if text.strip() else "EMPTY_OR_UNREADABLE",
                }
            )

    return {
        "claim_id": claim_id,
        "source_bucket": S3_BUCKET_NAME,
        "source_key": source_key,
        "ocr_engine": "Tesseract",
        "language": TESSERACT_LANGUAGE,
        "page_count": len(pages),
        "average_word_confidence": round(fmean(valid_confidences), 2) if valid_confidences else None,
        "pages": pages,
        "extraction_status": "COMPLETED",
    }


def analyze_claim_document(
    claim_id: str,
    s3_object_key: str,
    upload_to_s3: bool = True,
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    """Download a claim PDF, OCR it, persist raw JSON, and return review metadata."""
    normalized_claim_id = claim_id.strip().upper()
    normalized_key = s3_object_key.strip()
    if not CLAIM_ID_PATTERN.fullmatch(normalized_claim_id):
        raise ValueError("claim_id must match CLM followed by digits")
    if not normalized_key or normalized_key.startswith("/") or any(character in normalized_key for character in "\r\n\0"):
        raise ValueError("s3_object_key must be a non-empty relative object key")
    if not normalized_key.lower().endswith(".pdf"):
        raise ValueError("s3_object_key must identify a PDF")

    raw_result = _ocr_pdf(download_bytes(normalized_key), normalized_claim_id, normalized_key)
    output_dir.mkdir(parents=True, exist_ok=True)
    local_path = output_dir / f"{normalized_claim_id}_tesseract_raw.json"
    local_path.write_text(json.dumps(raw_result, ensure_ascii=False, indent=2), encoding="utf-8")

    result_key = f"results/{normalized_claim_id}/tesseract_raw.json"
    if upload_to_s3:
        upload_json(raw_result, result_key)
    else:
        result_key = None

    candidate_fields = extract_claim_fields(raw_result)
    field_review = validate_claim_fields(candidate_fields)
    document_claim_id = extract_document_claim_id(raw_result)
    claim_id_matches_document = (
        document_claim_id == normalized_claim_id if document_claim_id is not None else None
    )
    return {
        "claim_id": normalized_claim_id,
        "extraction_status": raw_result["extraction_status"],
        "page_count": raw_result["page_count"],
        "average_word_confidence": raw_result["average_word_confidence"],
        "local_output_path": str(local_path.resolve()),
        "s3_result_key": result_key,
        "candidate_fields": candidate_fields,
        "field_review": field_review,
        "document_claim_id": document_claim_id,
        "claim_id_matches_document": claim_id_matches_document,
        "review_required": claim_id_matches_document is False
        or any(item["review_required"] for item in field_review.values()),
        "raw_ocr_result": raw_result,
    }
