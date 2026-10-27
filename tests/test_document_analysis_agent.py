from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.claim import Claim, ClaimDocument
from app.schemas.extraction import ExtractionStatus, SourceDocument
from app.services.document_analysis_agent import DocumentAnalysisAgent


def ocr(text: str, *, status: str = "COMPLETED", confidence: float | None = 96.0) -> dict:
    return {
        "claim_id": "CLM10001",
        "source_bucket": "claims-test",
        "source_key": "incoming/CLM10001/claim.pdf",
        "extraction_status": status,
        "average_word_confidence": confidence,
        "pages": [{"page_number": 1, "text": text, "words": []}],
    }


VALID_TEXT = """Claim ID: CLM10001
Patient ID: PAT1001
Member ID: MEM1001
Provider ID: PRV1001
Payer ID: PAY1001
Policy ID: POL1001
Diagnosis Code: J18.9
Procedure Code: 99223
Billed Amount: USD 1250.50
Date of Service: 2026-09-20
Authorization Number: AUTH1001
Authorization Status: APPROVED
"""


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        claim = Claim(
            claim_id="CLM10001", patient_id="PAT1001", provider_id="PRV1001",
            payer_id="PAY1001", claim_type="INPATIENT", document_name="claim.pdf", status="RECEIVED",
        )
        db.add(claim)
        db.add(ClaimDocument(
            claim_id="CLM10001", filename="claim.pdf", s3_bucket="claims-test",
            s3_object_key="incoming/CLM10001/claim.pdf", content_type="application/pdf",
        ))
        db.commit()
        yield db
    Base.metadata.drop_all(engine)


def analyze(session: Session, runner) -> object:
    return DocumentAnalysisAgent(session, ocr_runner=runner).analyze(
        "clm10001", "corr-test", [SourceDocument(bucket="claims-test", key="incoming/CLM10001/claim.pdf")]
    )


def test_valid_claim_is_complete_and_preserves_evidence(session: Session) -> None:
    result = analyze(session, lambda *_: ocr(VALID_TEXT))
    assert result.extraction_status == ExtractionStatus.COMPLETE
    assert result.extracted_fields["member_id"] == "MEM1001"
    assert result.extracted_fields["authorization_number"] == "AUTH1001"
    assert result.extracted_fields["amount"] == "1250.50"
    assert result.correlation_id == "corr-test"
    assert result.field_evidence["payer_id"][0].document.key.endswith("claim.pdf")
    assert result.manual_review_required is False


def test_missing_fields_require_review(session: Session) -> None:
    result = analyze(session, lambda *_: ocr("Provider ID: PRV1001\nDiagnosis Code: J18.9\nBilled Amount: 10.00"))
    assert result.extraction_status == ExtractionStatus.REVIEW_REQUIRED
    assert "patient_id" in result.missing_fields
    assert result.manual_review_required


def test_unreadable_document_fails_without_fabricating_fields(session: Session) -> None:
    result = analyze(session, lambda *_: ocr("   ", confidence=None))
    assert result.extraction_status == ExtractionStatus.FAILED
    assert result.extracted_fields == {}
    assert result.document_results[0].status == "UNREADABLE"
    assert result.manual_review_required


def test_conflicting_values_are_null_and_flagged(session: Session) -> None:
    result = analyze(session, lambda *_: ocr(VALID_TEXT + "\nPayer ID: PAY9999"))
    assert result.extracted_fields["payer_id"] is None
    assert any(flag.field == "payer_id" and flag.code == "CONFLICTING_VALUES" for flag in result.validation_errors)


def test_invalid_date_and_amount_are_flagged(session: Session) -> None:
    text = VALID_TEXT.replace("USD 1250.50", "-20.00").replace("2026-09-20", "03/04/2026")
    result = analyze(session, lambda *_: ocr(text))
    codes = {flag.code for flag in result.validation_errors}
    assert result.extracted_fields["amount"] is None
    assert result.extracted_fields["date_of_service"] is None
    assert {"INVALID_AMOUNT", "INVALID_OR_AMBIGUOUS_DATE"} <= codes


def test_extraction_exception_is_contained_as_failed_result(session: Session) -> None:
    def failed(*_args):
        raise OSError("simulated S3 or OCR failure")

    result = analyze(session, failed)
    assert result.extraction_status == ExtractionStatus.FAILED
    assert result.document_results[0].status == "FAILED"
    assert result.document_results[0].error == "OSError"
    assert "simulated" not in result.model_dump_json()
