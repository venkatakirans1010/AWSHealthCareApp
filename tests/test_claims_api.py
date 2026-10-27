from decimal import Decimal

from fastapi.testclient import TestClient
import pytest
from botocore.exceptions import ClientError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.claim import AuditEvent, Claim, ClaimDecision, ClaimDocument, ClaimExtraction
from app.schemas.extraction import NormalizationResponse, NormalizedExtraction, SourceDocument
from app.services.claim_normalization_service import ClaimNotFoundError, InvalidRawOCRArtifactError


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(session_factory) -> TestClient:
    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def claim_payload() -> dict[str, str]:
    return {"patient_id": "PAT10001", "provider_id": "PRV10001", "payer_id": "PAY10001", "claim_type": "OUTPATIENT", "document_name": "claim_10001.pdf"}


def test_create_and_read_persisted_claim(client: TestClient, session_factory, claim_payload: dict[str, str]) -> None:
    response = client.post("/claims", json=claim_payload)
    assert response.status_code == 201
    created = response.json()
    assert created["claim_id"] == "CLM10001"
    assert created["status"] == "RECEIVED"
    assert created["created_at"] and created["updated_at"]
    assert client.get("/claims/CLM10001").json() == created
    with session_factory() as session:
        assert session.get(Claim, "CLM10001") is not None
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "CLAIM_CREATED")) == 1


def test_list_filter_and_unique_sequence(client: TestClient, claim_payload: dict[str, str]) -> None:
    first = client.post("/claims", json=claim_payload).json()
    second = client.post("/claims", json={**claim_payload, "document_name": "claim_10002.pdf"}).json()
    assert [first["claim_id"], second["claim_id"]] == ["CLM10001", "CLM10002"]
    response = client.get("/claims", params={"status": "RECEIVED", "limit": 20, "offset": 0})
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert len(response.json()["claims"]) == 2


def test_unknown_claim_returns_404(client: TestClient) -> None:
    response = client.get("/claims/UNKNOWN")
    assert response.status_code == 404


def test_invalid_request_returns_422(client: TestClient) -> None:
    assert client.post("/claims", json={"patient_id": ""}).status_code == 422


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    response = client.post("/claims/upload", files={"file": ("claim.txt", b"text", "text/plain")})
    assert response.status_code == 400


def test_upload_stores_document_and_audits(client: TestClient, session_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routes.claims.upload_claim", lambda _file, claim_id, filename: f"incoming/{claim_id}/{filename}")
    monkeypatch.setattr("app.routes.claims.S3_BUCKET_NAME", "private-test-bucket")
    response = client.post(
        "/claims/upload",
        data={"patient_id": "PAT10001", "provider_id": "PRV10001", "payer_id": "PAY10001", "claim_type": "OUTPATIENT"},
        files={"file": ("claim.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 201
    assert response.json()["s3_object_key"] == "incoming/CLM10001/claim.pdf"
    assert client.get("/claims/CLM10001").json()["s3_object_key"] == "incoming/CLM10001/claim.pdf"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ClaimDocument)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 2


def test_s3_failure_rolls_back_claim(client: TestClient, session_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_upload(*_args):
        raise OSError("simulated S3 failure")

    monkeypatch.setattr("app.routes.claims.upload_claim", fail_upload)
    monkeypatch.setattr("app.routes.claims.S3_BUCKET_NAME", "private-test-bucket")
    response = client.post("/claims/upload", files={"file": ("claim.pdf", b"%PDF-1.4", "application/pdf")})
    assert response.status_code == 500
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Claim)) == 0


def test_database_error_is_not_false_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.claim_service import ClaimService

    monkeypatch.setattr(ClaimService, "create_claim", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database down")))
    response = client.post("/claims", json={"patient_id": "PAT1", "provider_id": "PRV1", "payer_id": "PAY1", "claim_type": "OUTPATIENT", "document_name": "claim.pdf"})
    assert response.status_code == 503


def test_normalize_endpoint_success(client: TestClient, monkeypatch: pytest.MonkeyPatch, claim_payload: dict[str, str]) -> None:
    from datetime import datetime, timezone
    from decimal import Decimal
    from app.services.claim_normalization_service import ClaimNormalizationService

    client.post("/claims", json=claim_payload)
    now = datetime.now(timezone.utc)
    response_value = NormalizationResponse(
        extraction=NormalizedExtraction(
            claim_id="CLM10001",
            patient_id="PAT10001",
            provider_id="PRV10001",
            diagnosis_code="J18.9",
            amount=Decimal("125.00"),
            extraction_status="COMPLETE",
            review_required=False,
            source_document=SourceDocument(bucket="test", key="incoming/CLM10001/claim.pdf"),
            created_at=now,
            updated_at=now,
        ),
        raw_ocr_artifact_key="results/CLM10001/tesseract_raw.json",
        normalized_artifact_key="results/CLM10001/normalized_extraction.json",
    )
    monkeypatch.setattr(ClaimNormalizationService, "normalize_claim", lambda *_args: response_value)
    response = client.post("/claims/CLM10001/normalize")
    assert response.status_code == 200
    assert response.json()["extraction"]["amount"] == "125.00"
    assert response.json()["extraction"]["extraction_status"] == "COMPLETE"


def test_normalize_endpoint_unknown_claim_returns_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.claim_normalization_service import ClaimNormalizationService

    monkeypatch.setattr(
        ClaimNormalizationService,
        "normalize_claim",
        lambda *_args: (_ for _ in ()).throw(ClaimNotFoundError("CLM99999")),
    )
    response = client.post("/claims/CLM99999/normalize")
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (InvalidRawOCRArtifactError("invalid"), 422),
        (ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject"), 404),
        (OSError("S3 unavailable"), 502),
        (RuntimeError("database unavailable"), 503),
    ],
)
def test_normalize_endpoint_maps_boundary_errors(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: int,
) -> None:
    from app.services.claim_normalization_service import ClaimNormalizationService

    def fail(*_args):
        raise error

    monkeypatch.setattr(ClaimNormalizationService, "normalize_claim", fail)
    assert client.post("/claims/CLM10001/normalize").status_code == expected_status


def test_ocr_endpoint_requires_uploaded_document(client: TestClient, claim_payload: dict[str, str]) -> None:
    client.post("/claims", json=claim_payload)
    response = client.post("/claims/CLM10001/ocr")
    assert response.status_code == 400
    assert response.json()["detail"] == "Claim does not have an uploaded PDF"


def test_ocr_endpoint_returns_real_contract(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.claim_ocr_service import ClaimOCRService

    monkeypatch.setattr(
        ClaimOCRService,
        "run_ocr",
        lambda *_args: {
            "claim_id": "CLM10001",
            "extraction_status": "COMPLETED",
            "page_count": 1,
            "average_word_confidence": 96.25,
            "local_output_path": "output/ocr/CLM10001_tesseract_raw.json",
            "s3_result_key": "results/CLM10001/tesseract_raw.json",
            "candidate_fields": {
                "patient_id": {
                    "value": "PAT1001",
                    "found": True,
                    "conflicting": False,
                    "candidates": [],
                }
            },
            "field_review": {
                "patient_id": {
                    "value": "PAT1001",
                    "found": True,
                    "evidence": None,
                    "confidence": 96.25,
                    "validation_outcome": "AUTOMATED_VALIDATION_CANDIDATE",
                    "review_required": False,
                    "review_reasons": [],
                }
            },
            "review_required": False,
        },
    )
    response = client.post("/claims/CLM10001/ocr")
    assert response.status_code == 200
    assert response.json()["candidate_fields"]["patient_id"]["value"] == "PAT1001"


def test_latest_extraction_endpoint_returns_404_when_empty(
    client: TestClient, claim_payload: dict[str, str]
) -> None:
    client.post("/claims", json=claim_payload)
    assert client.get("/claims/CLM10001/extraction").status_code == 404


def test_policy_validation_endpoint_uses_normalized_claim_and_preserves_claim_type(
    client: TestClient, session_factory, monkeypatch: pytest.MonkeyPatch, claim_payload: dict[str, str]
) -> None:
    from datetime import date, datetime, timezone
    from app.services.policy_validation_agent import PolicyValidationAgent

    created = client.post("/claims", json={**claim_payload, "payer_id": "PAY1001", "claim_type": "INPATIENT"}).json()
    now = datetime.now(timezone.utc)
    normalized = NormalizedExtraction(
        claim_id=created["claim_id"], patient_id="PAT10001", payer_id="PAY1001",
        provider_id="PRV10001", diagnosis_code="J18.9", procedure_code="99223",
        date_of_service=date(2026, 9, 20), amount=Decimal("125.00"),
        extraction_status="COMPLETE", review_required=False,
        source_document=SourceDocument(bucket="test", key="incoming/CLM10001/claim.pdf"),
        created_at=now, updated_at=now,
    )
    with session_factory() as session:
        session.add(ClaimExtraction(
            claim_id=created["claim_id"], extracted_data=normalized.model_dump(mode="json"),
            extraction_status="COMPLETE", review_required=False, review_flags=[],
            raw_ocr_artifact_key="results/CLM10001/raw.json", raw_ocr_artifact_etag="etag",
            normalized_artifact_key="results/CLM10001/normalized.json",
        ))
        session.commit()

    captured = {}
    def fake_validate(_self, policy_claim, correlation_id):
        captured["claim"] = policy_claim
        return {
            "claim_id": policy_claim.claim_id, "correlation_id": correlation_id,
            "coverage_status": "COVERED", "reason": "Applicable test policy evidence.",
            "evidence": [], "authorization_required": False,
            "authorization_status": "NOT_REQUIRED", "missing_information": [],
            "evidence_issues": [], "manual_review_required": False, "confidence": 0.9,
        }

    monkeypatch.setattr(PolicyValidationAgent, "validate", fake_validate)
    response = client.post(
        f"/claims/{created['claim_id']}/policy-validation",
        json={"medical_documentation_available": True},
        headers={"X-Correlation-ID": "corr-policy-api"},
    )
    assert response.status_code == 200
    assert response.json()["correlation_id"] == "corr-policy-api"
    assert captured["claim"].payer_id == "PAY1001"
    assert captured["claim"].claim_type.value == "INPATIENT"


def test_medical_coding_endpoint_uses_latest_normalized_codes(
    client: TestClient, session_factory, monkeypatch: pytest.MonkeyPatch, claim_payload: dict[str, str]
) -> None:
    from datetime import datetime, timezone
    from app.services.medical_coding_agent import MedicalCodingAgent

    created = client.post("/claims", json={**claim_payload, "payer_id": "PAY1001", "claim_type": "OUTPATIENT"}).json()
    now = datetime.now(timezone.utc)
    normalized = NormalizedExtraction(
        claim_id=created["claim_id"], payer_id="PAY1001", diagnosis_code="I10", procedure_code="99213",
        extraction_status="COMPLETE", review_required=False,
        source_document=SourceDocument(bucket="test", key="incoming/CLM10001/claim.pdf"),
        created_at=now, updated_at=now,
    )
    with session_factory() as session:
        session.add(ClaimExtraction(
            claim_id=created["claim_id"], extracted_data=normalized.model_dump(mode="json"),
            extraction_status="COMPLETE", review_required=False, review_flags=[],
            raw_ocr_artifact_key="results/CLM10001/raw-coding.json", raw_ocr_artifact_etag="etag-coding",
            normalized_artifact_key="results/CLM10001/normalized-coding.json",
        ))
        session.commit()

    captured = {}
    def fake_validate(_self, coding_claim, correlation_id):
        captured["claim"] = coding_claim
        return {
            "claim_id": coding_claim.claim_id, "payer_id": coding_claim.payer_id,
            "claim_type": coding_claim.claim_type, "correlation_id": correlation_id,
            "diagnosis_results": [{"code_system": "ICD10", "code": "I10", "description": "Essential (primary) hypertension", "validation_status": "VALID_REFERENCE_MATCH", "confidence": None}],
            "procedure_results": [{"code_system": "CPT", "code": "99213", "description": "Established patient office or outpatient visit", "validation_status": "VALID_REFERENCE_MATCH", "confidence": None}],
            "ambiguous": False, "manual_review_required": False, "issues": [],
        }

    monkeypatch.setattr(MedicalCodingAgent, "validate", fake_validate)
    response = client.post(
        f"/claims/{created['claim_id']}/medical-coding",
        json={}, headers={"X-Correlation-ID": "corr-coding-api"},
    )
    assert response.status_code == 200
    assert response.json()["correlation_id"] == "corr-coding-api"
    assert captured["claim"].diagnosis_code == "I10"
    assert captured["claim"].procedure_code == "99213"
    assert captured["claim"].claim_type.value == "OUTPATIENT"


def test_fraud_screening_endpoint_uses_normalized_claim_without_persistence_changes(
    client: TestClient, session_factory, monkeypatch: pytest.MonkeyPatch, claim_payload: dict[str, str]
) -> None:
    from datetime import date, datetime, timezone
    from app.services.fraud_detection_agent import FraudDetectionAgent

    created = client.post("/claims", json={**claim_payload, "provider_id": "PROV1001", "payer_id": "PAY1001", "claim_type": "INPATIENT"}).json()
    now = datetime.now(timezone.utc)
    normalized = NormalizedExtraction(
        claim_id=created["claim_id"], member_id="MEM1001", provider_id="PROV1001",
        payer_id="PAY1001", diagnosis_code="J18.9", procedure_code="99223",
        date_of_service=date(2026, 9, 20), amount=Decimal("45000.00"),
        extraction_status="COMPLETE", review_required=False,
        source_document=SourceDocument(bucket="test", key="incoming/CLM10001/claim.pdf"),
        created_at=now, updated_at=now,
    )
    with session_factory() as session:
        session.add(ClaimExtraction(
            claim_id=created["claim_id"], extracted_data=normalized.model_dump(mode="json"),
            extraction_status="COMPLETE", review_required=False, review_flags=[],
            raw_ocr_artifact_key="results/CLM10001/raw-fraud.json", raw_ocr_artifact_etag="etag-fraud",
            normalized_artifact_key="results/CLM10001/normalized-fraud.json",
        ))
        session.commit()

    captured = {}
    def fake_screen(_self, fraud_claim, correlation_id):
        captured["claim"] = fraud_claim
        return {
            "claim_id": fraud_claim.claim_id, "correlation_id": correlation_id,
            "fraud_screening_status": "CLEAR", "risk_score": 0,
            "triggered_rules": [], "evidence": [], "missing_information": [],
            "manual_review_required": False,
        }

    monkeypatch.setattr(FraudDetectionAgent, "screen", fake_screen)
    response = client.post(
        f"/claims/{created['claim_id']}/fraud-screening",
        json={}, headers={"X-Correlation-ID": "corr-fraud-api"},
    )
    assert response.status_code == 200
    assert response.json()["correlation_id"] == "corr-fraud-api"
    assert captured["claim"].member_id == "MEM1001"
    assert captured["claim"].provider_id == "PROV1001"
    assert captured["claim"].billed_amount == Decimal("45000.00")


def test_decision_recommendation_endpoint_persists_recommendation_not_final_decision(
    client: TestClient, session_factory, claim_payload: dict[str, str]
) -> None:
    from tests.test_claims_decision_agent import CORRELATION_ID, coding, document, fraud, normalized, policy

    created = client.post("/claims", json={**claim_payload, "payer_id": "PAY1001", "claim_type": "INPATIENT"}).json()
    extraction = normalized()
    with session_factory() as session:
        session.add(ClaimExtraction(
            claim_id=created["claim_id"], extracted_data=extraction.model_dump(mode="json"),
            extraction_status="COMPLETE", review_required=False, review_flags=[],
            raw_ocr_artifact_key="results/CLM10001/raw-decision.json", raw_ocr_artifact_etag="etag-decision",
            normalized_artifact_key="results/CLM10001/normalized-decision.json",
        ))
        session.commit()

    response = client.post(
        f"/claims/{created['claim_id']}/decision-recommendation",
        json={
            "document_analysis": document().model_dump(mode="json"),
            "policy_validation": policy().model_dump(mode="json"),
            "medical_coding": coding().model_dump(mode="json"),
            "fraud_screening": fraud().model_dump(mode="json"),
        },
        headers={"X-Correlation-ID": CORRELATION_ID},
    )
    assert response.status_code == 200
    assert response.json()["decision_recommendation"] == "ELIGIBLE_FOR_APPROVAL_REVIEW"
    assert response.json()["audit_metadata"]["persisted"] is True
    with session_factory() as session:
        assert session.get(Claim, created["claim_id"]).status == "PROCESSING"
        decision = session.scalar(select(ClaimDecision).where(ClaimDecision.claim_id == created["claim_id"]))
        assert decision is not None
        assert decision.decision == "ELIGIBLE_FOR_APPROVAL_REVIEW"
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.event_type == "CLAIM_DECISION_RECOMMENDED")) == 1
