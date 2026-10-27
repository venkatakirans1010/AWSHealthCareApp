from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.repositories.claim_repository import ClaimRepository
from app.schemas.extraction import OCRResponse
from app.services.claim_normalization_service import ClaimNotFoundError
from app.services.tesseract_service import analyze_claim_document


class ClaimDocumentNotFoundError(LookupError):
    pass


class ClaimOCRService:
    def __init__(self, session: Session) -> None:
        self._repository = ClaimRepository(session)

    def run_ocr(self, claim_id: str) -> OCRResponse:
        normalized_claim_id = claim_id.strip().upper()
        claim = self._repository.get(normalized_claim_id)
        if claim is None:
            raise ClaimNotFoundError(normalized_claim_id)
        document = claim.documents[0] if claim.documents else None
        if document is None or not document.s3_object_key:
            raise ClaimDocumentNotFoundError(normalized_claim_id)
        return OCRResponse.model_validate(
            analyze_claim_document(
                normalized_claim_id,
                document.s3_object_key,
                upload_to_s3=True,
            )
        )


def get_claim_ocr_service(session: Session = Depends(get_db)) -> ClaimOCRService:
    return ClaimOCRService(session)
