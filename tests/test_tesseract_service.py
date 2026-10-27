import json
from pathlib import Path

import pytest

from app.services import tesseract_service


def sample_ocr_result() -> dict:
    text = """Patient ID: PAT1001
Patient Name: Ada Lovelace
Provider ID: PRV2001
Payer ID: PAY3001
Policy ID: POL4001
Diagnosis Code: E11.9
Procedure Code: 99213
Claimed Amount: $1,234.50
Date of Service: 2026-10-20
"""
    words = []
    for index, token in enumerate(text.replace("\n", " ").split()):
        words.append({"text": token.strip(":"), "confidence": 97.0, "left": index * 10, "top": 10, "width": 8, "height": 10})
    return {
        "claim_id": "CLM10001",
        "source_bucket": "test-bucket",
        "source_key": "incoming/CLM10001/claim.pdf",
        "ocr_engine": "Tesseract",
        "language": "eng",
        "page_count": 1,
        "average_word_confidence": 97.0,
        "pages": [{"page_number": 1, "text": text, "words": words, "ocr_status": "READABLE"}],
        "extraction_status": "COMPLETED",
    }


def test_extracts_and_normalizes_candidates_with_field_evidence() -> None:
    fields = tesseract_service.extract_claim_fields(sample_ocr_result())
    assert fields["patient_id"]["value"] == "PAT1001"
    assert fields["patient_name"]["value"] == "Ada Lovelace"
    assert fields["amount"]["value"] == "1234.50"
    assert fields["date_of_service"]["value"] == "2026-10-20"
    assert fields["patient_id"]["candidates"][0]["evidence"]["confidence"] == 97.0

    review = tesseract_service.validate_claim_fields(fields)
    assert all(item["validation_outcome"] == "AUTOMATED_VALIDATION_CANDIDATE" for item in review.values())
    assert not any(item["review_required"] for item in review.values())


def test_extracts_supported_claim_form_aliases_and_inr_amount() -> None:
    result = sample_ocr_result()
    result["pages"][0]["text"] = result["pages"][0]["text"].replace(
        "Diagnosis Code: E11.9", "ICD-10: J18.9"
    ).replace(
        "Procedure Code: 99213", "Procedure/CPT: 99223"
    ).replace(
        "Claimed Amount: $1,234.50", "Billed Amount: INR 250,000"
    )

    fields = tesseract_service.extract_claim_fields(result)

    assert fields["diagnosis_code"]["value"] == "J18.9"
    assert fields["procedure_code"]["value"] == "99223"
    assert fields["amount"]["value"] == "250000.00"


def test_extracts_document_claim_id() -> None:
    result = sample_ocr_result()
    result["pages"][0]["text"] = "Claim ID: CLM10008\n" + result["pages"][0]["text"]
    assert tesseract_service.extract_document_claim_id(result) == "CLM10008"


def test_ambiguous_date_missing_field_and_conflict_require_review() -> None:
    result = sample_ocr_result()
    result["pages"][0]["text"] = result["pages"][0]["text"].replace(
        "Date of Service: 2026-10-20", "Date of Service: 10/11/2026"
    ).replace("Payer ID: PAY3001\n", "") + "Provider ID: PRV9999\n"
    fields = tesseract_service.extract_claim_fields(result)
    review = tesseract_service.validate_claim_fields(fields)
    assert review["payer_id"]["review_required"]
    assert review["date_of_service"]["value"] is None
    assert "ambiguous" in review["date_of_service"]["review_reasons"][0]
    assert fields["provider_id"]["conflicting"]


def test_analyze_writes_raw_json_and_optionally_uploads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    raw = sample_ocr_result()
    uploaded: list[tuple[dict, str]] = []
    monkeypatch.setattr(tesseract_service, "download_bytes", lambda _key: b"pdf")
    monkeypatch.setattr(tesseract_service, "_ocr_pdf", lambda *_args: raw)
    monkeypatch.setattr(tesseract_service, "upload_json", lambda payload, key: uploaded.append((payload, key)) or key)

    result = tesseract_service.analyze_claim_document(
        "clm10001", "incoming/CLM10001/claim.pdf", output_dir=tmp_path
    )
    saved = json.loads(Path(result["local_output_path"]).read_text(encoding="utf-8"))
    assert saved == raw
    assert result["s3_result_key"] == "results/CLM10001/tesseract_raw.json"
    assert uploaded == [(raw, "results/CLM10001/tesseract_raw.json")]


@pytest.mark.parametrize(
    ("claim_id", "key"),
    [("10001", "incoming/claim.pdf"), ("CLM10001", ""), ("CLM10001", "/claim.pdf"), ("CLM10001", "claim.txt")],
)
def test_analyze_rejects_invalid_inputs(claim_id: str, key: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        tesseract_service.analyze_claim_document(claim_id, key, output_dir=tmp_path)
