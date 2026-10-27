from collections.abc import Callable
from typing import BinaryIO

from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.claim import Claim, ClaimDocument
from app.repositories.claim_repository import ClaimRepository
from app.schemas.claim import ClaimCreateRequest, ClaimListResponse, ClaimResponse, ClaimStatus


class ClaimService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = ClaimRepository(session)

    @staticmethod
    def _response(claim: Claim) -> ClaimResponse:
        document = claim.documents[0] if claim.documents else None
        return ClaimResponse(
            claim_id=claim.claim_id, patient_id=claim.patient_id, provider_id=claim.provider_id,
            payer_id=claim.payer_id, claim_type=claim.claim_type, document_name=claim.document_name,
            status=claim.status, created_at=claim.created_at, updated_at=claim.updated_at,
            s3_bucket=document.s3_bucket if document else None,
            s3_object_key=document.s3_object_key if document else None,
        )

    def create_claim(self, request: ClaimCreateRequest) -> ClaimResponse:
        try:
            claim = Claim(claim_id=self._repository.next_claim_id(), **request.model_dump(), status=ClaimStatus.RECEIVED.value)
            self._repository.add_claim(claim)
            self._session.flush()
            self._repository.add_audit(claim.claim_id, "CLAIM_CREATED", {"status": ClaimStatus.RECEIVED.value})
            self._session.commit()
            self._session.refresh(claim)
            return self._response(claim)
        except Exception:
            self._session.rollback()
            raise

    def create_claim_with_upload(self, request: ClaimCreateRequest, file: BinaryIO, bucket: str, upload: Callable[[BinaryIO, str, str], str]) -> ClaimResponse:
        uploaded_key: str | None = None
        try:
            claim = Claim(claim_id=self._repository.next_claim_id(), **request.model_dump(), status=ClaimStatus.RECEIVED.value)
            self._repository.add_claim(claim)
            self._session.flush()
            uploaded_key = upload(file, claim.claim_id, request.document_name)
            self._repository.add_document(ClaimDocument(claim_id=claim.claim_id, filename=request.document_name, s3_bucket=bucket, s3_object_key=uploaded_key, content_type="application/pdf"))
            self._repository.add_audit(claim.claim_id, "CLAIM_CREATED", {"status": ClaimStatus.RECEIVED.value})
            self._repository.add_audit(claim.claim_id, "DOCUMENT_UPLOADED", {"filename": request.document_name, "object_key": uploaded_key})
            self._session.commit()
            self._session.refresh(claim)
            return self._response(claim)
        except Exception as error:
            self._session.rollback()
            if uploaded_key:
                setattr(error, "orphaned_s3_object", f"s3://{bucket}/{uploaded_key}")
            raise

    def get_claim(self, claim_id: str) -> ClaimResponse | None:
        claim = self._repository.get(claim_id.upper())
        return self._response(claim) if claim else None

    def list_claims(self, *, status: ClaimStatus | None, limit: int, offset: int) -> ClaimListResponse:
        claims, total = self._repository.list(status=status, limit=limit, offset=offset)
        return ClaimListResponse(claims=[self._response(claim) for claim in claims], total=total)


def get_claim_service(session: Session = Depends(get_db)) -> ClaimService:
    return ClaimService(session)
