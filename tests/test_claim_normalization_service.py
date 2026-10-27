from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.claim import Claim, ClaimExtraction
from app.schemas.extraction import ExtractionStatus
from app.services import claim_normalization_service as normalization


def raw_ocr(text: str | None = None) -> dict:
    content = text if text is not None else """Patient ID: PAT0001
Patient Name: Example Patient
Provider ID: PROV0002
Payer ID: PAY0003
Policy ID: POL0004
Diagnosis Code: J18.9
Procedure Code: 99223
Claimed Amount: INR 45,000.00
Date of Service: 2026-09-20
"""
    return {
        "claim_id": "CLM10001",
        "source_bucket": "configured-bucket",
        "source_key": "incoming/CLM10001/claim_10001.pdf",
        "ocr_engine": "Tesseract",
        "language": "eng",
        "page_count": 1,
        "average_word_confidence": 96.0,
        "pages": [{"page_number": 1, "text": content, "words": []}],
        "extraction_status": "COMPLETED",
    }


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database_session:
        database_session.add(
            Claim(
                claim_id="CLM10001",
                patient_id="PAT0001",
                provider_id="PROV0002",
                payer_id="PAY0003",
                claim_type="OUTPATIENT",
                document_name="claim.pdf",
                status="RECEIVED",
            )
        )
        database_session.commit()
        yield database_session
    Base.metadata.drop_all(engine)


def test_normalization_helpers_preserve_semantics() -> None:
    assert normalization.normalize_text("  Ada   M.  Lovelace  ") == "Ada M. Lovelace"
    assert normalization.normalize_identifier("  pat-0007 ") == "PAT-0007"
    assert normalization.normalize_diagnosis_code(" j18.9 ") == "J18.9"
    assert normalization.normalize_procedure_code(" 00123 ") == "00123"
    assert normalization.normalize_amount("₹ 1,234.5") == (Decimal("1234.50"), "INR")
    assert normalization.normalize_amount("00123") == (Decimal("123.00"), None)
    assert normalization.normalize_amount("1,23,4.00") == (None, None)
    assert normalization.normalize_amount("-10.00") == (None, None)
    assert normalization.normalize_date("2026-09-20") == "2026-09-20"
    assert normalization.normalize_date("03/04/2026") is None


def test_document_claim_id_mismatch_requires_review() -> None:
    payload = raw_ocr()
    payload["pages"][0]["text"] = "Claim ID: CLM99999\n" + payload["pages"][0]["text"]

    result = normalization.normalize_ocr_result(payload)

    assert result.review_required
    assert any(flag.code == "DOCUMENT_CLAIM_ID_MISMATCH" for flag in result.review_flags)


def test_valid_raw_ocr_becomes_complete() -> None:
    result = normalization.normalize_ocr_result(raw_ocr())
    assert result.extraction_status == ExtractionStatus.COMPLETE.value
    assert result.amount == Decimal("45000.00")
    assert result.currency == "INR"
    assert result.patient_id == "PAT0001"
    assert result.review_required is False
    assert result.review_flags == []


def test_missing_invalid_and_conflicting_values_require_review() -> None:
    text = """Provider ID: PROV0002
Diagnosis Code: NOT-A-CODE
Procedure Code: ?
Claimed Amount: -50.00
Claimed Amount: USD 75.00
Date of Service: 03/04/2026
"""
    result = normalization.normalize_ocr_result(raw_ocr(text))
    codes = {flag.code for flag in result.review_flags}
    assert result.extraction_status == ExtractionStatus.REVIEW_REQUIRED.value
    assert result.patient_id is None
    assert result.amount is None
    assert "MISSING_REQUIRED_FIELD" in codes
    assert "INVALID_DIAGNOSIS_CODE" in codes
    assert "INVALID_PROCEDURE_CODE" in codes
    assert "CONFLICTING_VALUES" in codes
    assert "INVALID_OR_AMBIGUOUS_DATE" in codes


def test_no_usable_ocr_text_is_failed() -> None:
    result = normalization.normalize_ocr_result(raw_ocr("   "))
    assert result.extraction_status == ExtractionStatus.FAILED.value
    assert result.failure_reason == "OCR output contains no usable text"
    assert result.review_required is False


def test_workflow_is_idempotent_and_persists_normalized_data(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploads: list[tuple[dict, str]] = []
    monkeypatch.setattr(normalization, "download_json", lambda _key: (raw_ocr(), "etag-1"))
    monkeypatch.setattr(normalization, "upload_json", lambda payload, key: uploads.append((payload, key)) or key)
    service = normalization.ClaimNormalizationService(session)

    first = service.normalize_claim("clm10001")
    second = service.normalize_claim("CLM10001")

    rows = list(session.scalars(select(ClaimExtraction)))
    assert len(rows) == 1
    assert rows[0].extraction_status == "COMPLETE"
    assert rows[0].normalized_artifact_key == "results/CLM10001/normalized_extraction.json"
    assert first.extraction.amount == second.extraction.amount == Decimal("45000.00")
    assert len(uploads) == 2
    latest = service.get_latest_extraction("CLM10001")
    assert latest.normalized_artifact_key == "results/CLM10001/normalized_extraction.json"
    assert latest.extraction.extraction_status == "COMPLETE"


def test_s3_upload_failure_is_persisted_as_failed(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(normalization, "download_json", lambda _key: (raw_ocr(), "etag-2"))
    monkeypatch.setattr(normalization, "upload_json", lambda *_args: (_ for _ in ()).throw(OSError("upload failed")))
    with pytest.raises(OSError, match="upload failed"):
        normalization.ClaimNormalizationService(session).normalize_claim("CLM10001")
    extraction = session.scalar(select(ClaimExtraction).where(ClaimExtraction.raw_ocr_artifact_etag == "etag-2"))
    assert extraction is not None
    assert extraction.extraction_status == "FAILED"
    assert "upload failed" in (extraction.failure_reason or "")


def test_database_failure_after_upload_reports_partial_failure(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(normalization, "download_json", lambda _key: (raw_ocr(), "etag-3"))
    monkeypatch.setattr(normalization, "upload_json", lambda _payload, key: key)
    real_commit = session.commit
    commit_count = 0

    def failing_commit() -> None:
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise RuntimeError("database unavailable")
        real_commit()

    monkeypatch.setattr(session, "commit", failing_commit)
    with pytest.raises(RuntimeError) as error:
        normalization.ClaimNormalizationService(session).normalize_claim("CLM10001")
    assert getattr(error.value, "orphaned_s3_object").endswith(
        "/results/CLM10001/normalized_extraction.json"
    )
