import logging
from pathlib import PurePath
from typing import Annotated
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from pytesseract import TesseractError, TesseractNotFoundError
from sqlalchemy.orm import Session

from app.config import S3_BUCKET_NAME
from app.database import get_db
from app.repositories.claim_repository import ClaimRepository
from app.schemas.claim import ClaimCreateRequest, ClaimListResponse, ClaimResponse, ClaimStatus, ClaimUploadResponse
from app.schemas.coding import MedicalCodingInput, MedicalCodingRequest, MedicalCodingResult
from app.schemas.decision import ClaimsDecisionInput, ClaimsDecisionRequest, ClaimsDecisionResult
from app.schemas.fraud import FraudClaimInput, FraudScreeningRequest, FraudScreeningResult
from app.schemas.extraction import DocumentAnalysisRequest, DocumentAnalysisResult, NormalizationResponse, NormalizedExtraction, OCRResponse
from app.schemas.policy import PolicyClaimInput, PolicyValidationRequest, PolicyValidationResult
from app.services.document_analysis_agent import DocumentAnalysisAgent, InvalidDocumentReferenceError
from app.services.claims_decision_agent import ClaimsDecisionAgent
from app.services.claims_decision_service import ClaimsDecisionService
from app.services.medical_coding_agent import MedicalCodingAgent
from app.services.fraud_detection_agent import FraudDetectionAgent
from app.services.claim_ocr_service import (
    ClaimDocumentNotFoundError,
    ClaimOCRService,
    get_claim_ocr_service,
)
from app.services.claim_normalization_service import (
    ClaimNormalizationService,
    ClaimNotFoundError,
    InvalidRawOCRArtifactError,
    get_claim_normalization_service,
)
from app.services.claim_service import ClaimService, get_claim_service
from app.services.s3_service import S3ConfigurationError, upload_claim
from app.services.policy_validation_agent import PolicyValidationAgent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/claims", tags=["Claims"])


@router.post(
    "/{claim_id}/decision-recommendation",
    response_model=ClaimsDecisionResult,
    summary="Coordinate specialist outputs into a persisted decision recommendation",
)
def recommend_claim_decision(
    claim_id: str,
    request: ClaimsDecisionRequest,
    session: Annotated[Session, Depends(get_db)],
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> ClaimsDecisionResult:
    normalized_claim_id = claim_id.strip().upper()
    repository = ClaimRepository(session)
    claim = repository.get(normalized_claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    extraction = repository.get_latest_normalized_extraction(normalized_claim_id)
    if extraction is None:
        raise HTTPException(status_code=409, detail="A normalized extraction is required")
    normalized = NormalizedExtraction.model_validate(extraction.extracted_data)
    outputs = [request.document_analysis, request.policy_validation, request.medical_coding, request.fraud_screening]
    cleaned = "".join(character for character in (correlation_id or "").strip() if character.isprintable())
    workflow_correlation_id = cleaned[:128] or next((item.correlation_id for item in outputs if item is not None), str(uuid4()))
    workflow_input = ClaimsDecisionInput(
        normalized_claim=normalized, claim_type=claim.claim_type,
        document_analysis=request.document_analysis,
        policy_validation=request.policy_validation,
        medical_coding=request.medical_coding,
        fraud_screening=request.fraud_screening,
        executions=request.executions,
    )
    return ClaimsDecisionAgent(ClaimsDecisionService(session)).decide(workflow_input, workflow_correlation_id)


@router.post(
    "/{claim_id}/fraud-screening",
    response_model=FraudScreeningResult,
    summary="Screen a normalized claim for explainable anomaly indicators",
)
def screen_claim_for_fraud_indicators(
    claim_id: str,
    request: FraudScreeningRequest,
    session: Annotated[Session, Depends(get_db)],
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> FraudScreeningResult:
    normalized_claim_id = claim_id.strip().upper()
    repository = ClaimRepository(session)
    claim = repository.get(normalized_claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    extraction = repository.get_latest_normalized_extraction(normalized_claim_id)
    if extraction is None:
        raise HTTPException(status_code=409, detail="A normalized extraction is required")
    normalized = NormalizedExtraction.model_validate(extraction.extracted_data)
    pipeline_conflicts = [flag.field for flag in normalized.review_flags if flag.code == "CONFLICTING_VALUES"]
    cleaned = "".join(character for character in (correlation_id or "").strip() if character.isprintable())
    fraud_claim = FraudClaimInput(
        claim_id=normalized_claim_id, member_id=normalized.member_id,
        provider_id=normalized.provider_id or claim.provider_id, payer_id=normalized.payer_id or claim.payer_id,
        claim_type=claim.claim_type, service_date=normalized.date_of_service,
        diagnosis_code=normalized.diagnosis_code, procedure_code=normalized.procedure_code,
        billed_amount=normalized.amount,
        conflicting_fields=sorted(set(pipeline_conflicts + request.conflicting_fields)),
    )
    return FraudDetectionAgent().screen(fraud_claim, cleaned[:128] or str(uuid4()))


@router.post(
    "/{claim_id}/medical-coding",
    response_model=MedicalCodingResult,
    summary="Validate normalized claim codes against the coding reference service",
)
def validate_claim_codes(
    claim_id: str,
    request: MedicalCodingRequest,
    session: Annotated[Session, Depends(get_db)],
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> MedicalCodingResult:
    normalized_claim_id = claim_id.strip().upper()
    repository = ClaimRepository(session)
    claim = repository.get(normalized_claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    extraction = repository.get_latest_normalized_extraction(normalized_claim_id)
    if extraction is None:
        raise HTTPException(status_code=409, detail="A normalized extraction is required")
    normalized = NormalizedExtraction.model_validate(extraction.extracted_data)
    if normalized.payer_id is None:
        raise HTTPException(status_code=422, detail="Normalized extraction is missing: payer_id")
    cleaned = "".join(character for character in (correlation_id or "").strip() if character.isprintable())
    coding_claim = MedicalCodingInput(
        claim_id=normalized_claim_id, payer_id=normalized.payer_id, claim_type=claim.claim_type,
        diagnosis_code=normalized.diagnosis_code, procedure_code=normalized.procedure_code,
        **request.model_dump(),
    )
    return MedicalCodingAgent().validate(coding_claim, cleaned[:128] or str(uuid4()))


@router.post(
    "/{claim_id}/policy-validation",
    response_model=PolicyValidationResult,
    summary="Validate a normalized claim against Bedrock Knowledge Base policy evidence",
)
def validate_claim_policy(
    claim_id: str,
    request: PolicyValidationRequest,
    session: Annotated[Session, Depends(get_db)],
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> PolicyValidationResult:
    normalized_claim_id = claim_id.strip().upper()
    repository = ClaimRepository(session)
    claim = repository.get(normalized_claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    extraction = repository.get_latest_normalized_extraction(normalized_claim_id)
    if extraction is None:
        raise HTTPException(status_code=409, detail="A normalized extraction is required")
    normalized = NormalizedExtraction.model_validate(extraction.extracted_data)
    missing = [name for name in ("payer_id", "date_of_service") if getattr(normalized, name) is None]
    if missing:
        raise HTTPException(status_code=422, detail=f"Normalized extraction is missing: {', '.join(missing)}")
    cleaned = "".join(character for character in (correlation_id or "").strip() if character.isprintable())
    policy_claim = PolicyClaimInput(
        claim_id=normalized_claim_id,
        payer_id=normalized.payer_id,
        claim_type=claim.claim_type,
        service_date=normalized.date_of_service,
        diagnosis_code=normalized.diagnosis_code,
        procedure_code=normalized.procedure_code,
        authorization_number=normalized.authorization_number,
        authorization_status=normalized.authorization_status,
        medical_documentation_available=request.medical_documentation_available,
    )
    return PolicyValidationAgent().validate(policy_claim, cleaned[:128] or str(uuid4()))


@router.post(
    "/{claim_id}/analyze",
    response_model=DocumentAnalysisResult,
    summary="Analyze uploaded claim documents with the LangGraph workflow",
)
def analyze_claim_documents(
    claim_id: str,
    request: DocumentAnalysisRequest,
    session: Annotated[Session, Depends(get_db)],
    correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
) -> DocumentAnalysisResult:
    cleaned_correlation_id = "".join(
        character for character in (correlation_id or "").strip() if character.isprintable()
    )
    safe_correlation_id = cleaned_correlation_id[:128] or str(uuid4())
    try:
        return DocumentAnalysisAgent(session).analyze(claim_id, safe_correlation_id, request.documents)
    except ClaimNotFoundError:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found") from None
    except InvalidDocumentReferenceError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


@router.post("", response_model=ClaimResponse, status_code=status.HTTP_201_CREATED, summary="Create a new healthcare claim", description="Persists a claim in PostgreSQL in the RECEIVED lifecycle state.")
def create_claim(request: ClaimCreateRequest, service: Annotated[ClaimService, Depends(get_claim_service)]) -> ClaimResponse:
    try:
        return service.create_claim(request)
    except Exception:
        logger.exception("Database create failed")
        raise HTTPException(status_code=503, detail="Claim database is unavailable") from None


@router.post("/upload", response_model=ClaimUploadResponse, status_code=status.HTTP_201_CREATED, summary="Upload a claim PDF", description="Uploads a private PDF to S3 and persists its claim and document metadata in PostgreSQL.")
def upload_claim_document(
    service: Annotated[ClaimService, Depends(get_claim_service)],
    file: Annotated[UploadFile, File(description="PDF claim document")],
    patient_id: Annotated[str, Form(min_length=1)] = "UNKNOWN",
    provider_id: Annotated[str, Form(min_length=1)] = "UNKNOWN",
    payer_id: Annotated[str, Form(min_length=1)] = "UNKNOWN",
    claim_type: Annotated[str, Form(min_length=1)] = "OUTPATIENT",
) -> ClaimUploadResponse:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    filename = PurePath((file.filename or "claim.pdf").replace("\\", "/")).name
    if filename in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="A valid PDF filename is required")
    request = ClaimCreateRequest(patient_id=patient_id, provider_id=provider_id, payer_id=payer_id, claim_type=claim_type, document_name=filename)
    try:
        claim = service.create_claim_with_upload(request, file.file, S3_BUCKET_NAME, upload_claim)
    except (BotoCoreError, ClientError, OSError, S3ConfigurationError):
        logger.exception("S3 upload failed; database transaction rolled back")
        raise HTTPException(status_code=500, detail="Failed to upload claim document to S3") from None
    except Exception as error:
        orphan = getattr(error, "orphaned_s3_object", None)
        logger.exception("Database persistence failed after upload; orphaned object: %s", orphan or "none")
        raise HTTPException(status_code=503, detail="Claim database is unavailable; upload was not recorded") from None
    finally:
        file.file.close()
    return ClaimUploadResponse(claim_id=claim.claim_id, filename=filename, s3_bucket=S3_BUCKET_NAME, s3_object_key=claim.s3_object_key or "")


@router.get("", response_model=ClaimListResponse, summary="List healthcare claims", description="Lists claims with optional status filtering and offset pagination.")
def list_claims(
    service: Annotated[ClaimService, Depends(get_claim_service)],
    status_filter: Annotated[ClaimStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ClaimListResponse:
    return service.list_claims(status=status_filter, limit=limit, offset=offset)


@router.get("/{claim_id}", response_model=ClaimResponse, summary="Get claim by ID", description="Returns the persisted healthcare claim.")
def get_claim(claim_id: str, service: Annotated[ClaimService, Depends(get_claim_service)]) -> ClaimResponse:
    claim = service.get_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    return claim


@router.post(
    "/{claim_id}/normalize",
    response_model=NormalizationResponse,
    summary="Normalize a claim OCR artifact",
    description="Loads the Day 7 raw OCR JSON, validates and normalizes it, then stores the result in S3 and PostgreSQL.",
)
def normalize_claim_extraction(
    claim_id: str,
    service: Annotated[ClaimNormalizationService, Depends(get_claim_normalization_service)],
) -> NormalizationResponse:
    try:
        return service.normalize_claim(claim_id)
    except ClaimNotFoundError:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found") from None
    except InvalidRawOCRArtifactError:
        logger.exception("Raw OCR artifact failed schema validation for claim %s", claim_id)
        raise HTTPException(status_code=422, detail="Raw OCR artifact is invalid") from None
    except ClientError as error:
        error_code = error.response.get("Error", {}).get("Code", "")
        if error_code in {"NoSuchKey", "404", "NotFound"}:
            raise HTTPException(status_code=404, detail="Raw OCR artifact was not found") from None
        logger.exception("S3 normalization operation failed for claim %s", claim_id)
        raise HTTPException(status_code=502, detail="S3 normalization operation failed") from None
    except (BotoCoreError, OSError, S3ConfigurationError):
        logger.exception("S3 normalization operation failed for claim %s", claim_id)
        raise HTTPException(status_code=502, detail="S3 normalization operation failed") from None
    except (ValueError, UnicodeError):
        logger.exception("Raw OCR artifact is not valid JSON for claim %s", claim_id)
        raise HTTPException(status_code=422, detail="Raw OCR artifact is invalid") from None
    except Exception as error:
        logger.exception(
            "Normalization persistence failed for claim %s; uploaded artifact: %s",
            claim_id,
            getattr(error, "orphaned_s3_object", "none"),
        )
        raise HTTPException(status_code=503, detail="Normalization persistence failed") from None


@router.post(
    "/{claim_id}/ocr",
    response_model=OCRResponse,
    summary="Run local Tesseract OCR for a claim",
    description="Uses the claim's private S3 PDF and writes the raw OCR artifact without changing claim status.",
)
def run_claim_ocr(
    claim_id: str,
    service: Annotated[ClaimOCRService, Depends(get_claim_ocr_service)],
) -> OCRResponse:
    try:
        return service.run_ocr(claim_id)
    except ClaimNotFoundError:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found") from None
    except ClaimDocumentNotFoundError:
        raise HTTPException(status_code=400, detail="Claim does not have an uploaded PDF") from None
    except ClientError as error:
        error_code = error.response.get("Error", {}).get("Code", "")
        if error_code in {"NoSuchKey", "404", "NotFound"}:
            raise HTTPException(status_code=404, detail="Claim PDF was not found in S3") from None
        logger.exception("S3 OCR operation failed for claim %s", claim_id)
        raise HTTPException(status_code=502, detail="S3 OCR operation failed") from None
    except (BotoCoreError, OSError, S3ConfigurationError):
        logger.exception("S3 OCR operation failed for claim %s", claim_id)
        raise HTTPException(status_code=502, detail="S3 OCR operation failed") from None
    except (TesseractError, TesseractNotFoundError, ValueError):
        logger.exception("Tesseract OCR failed for claim %s", claim_id)
        raise HTTPException(status_code=500, detail="Tesseract OCR failed") from None


@router.get(
    "/{claim_id}/extraction",
    response_model=NormalizationResponse,
    summary="Get the latest normalized extraction",
)
def get_latest_claim_extraction(
    claim_id: str,
    service: Annotated[ClaimNormalizationService, Depends(get_claim_normalization_service)],
) -> NormalizationResponse:
    try:
        return service.get_latest_extraction(claim_id)
    except ClaimNotFoundError:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found") from None
    except LookupError:
        raise HTTPException(status_code=404, detail="No normalized extraction exists for this claim") from None
    except Exception:
        logger.exception("Failed to load normalized extraction for claim %s", claim_id)
        raise HTTPException(status_code=503, detail="Extraction database is unavailable") from None
