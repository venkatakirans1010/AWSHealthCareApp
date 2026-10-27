from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.claim import AuditEvent, Claim, ClaimDecision, ClaimDocument, ClaimExtraction
from app.schemas.claim import ClaimStatus


class ClaimRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def next_claim_id(self) -> str:
        if self.session.bind is not None and self.session.bind.dialect.name == "postgresql":
            number = self.session.execute(text("SELECT nextval('claim_number_seq')")).scalar_one()
        else:
            ids = self.session.scalars(select(Claim.claim_id)).all()
            number = max((int(value[3:]) for value in ids), default=10000) + 1
        return f"CLM{number}"

    def add_claim(self, claim: Claim) -> None:
        self.session.add(claim)

    def add_document(self, document: ClaimDocument) -> None:
        self.session.add(document)

    def add_audit(self, claim_id: str, event_type: str, details: dict[str, str]) -> None:
        self.session.add(AuditEvent(claim_id=claim_id, event_type=event_type, details=details))

    def add_decision(self, decision: ClaimDecision) -> None:
        self.session.add(decision)

    def update_claim_status(self, claim: Claim, status: ClaimStatus) -> None:
        claim.status = status.value

    def upsert_extraction(
        self,
        *,
        claim_id: str,
        raw_ocr_artifact_key: str,
        raw_ocr_artifact_etag: str,
        values: dict,
    ) -> ClaimExtraction:
        extraction = self.session.scalar(
            select(ClaimExtraction).where(
                ClaimExtraction.claim_id == claim_id,
                ClaimExtraction.raw_ocr_artifact_key == raw_ocr_artifact_key,
                ClaimExtraction.raw_ocr_artifact_etag == raw_ocr_artifact_etag,
            )
        )
        if extraction is None:
            extraction = ClaimExtraction(
                claim_id=claim_id,
                raw_ocr_artifact_key=raw_ocr_artifact_key,
                raw_ocr_artifact_etag=raw_ocr_artifact_etag,
                **values,
            )
            self.session.add(extraction)
        else:
            for name, value in values.items():
                setattr(extraction, name, value)
        return extraction

    def get(self, claim_id: str) -> Claim | None:
        return self.session.get(Claim, claim_id)

    def get_latest_normalized_extraction(self, claim_id: str) -> ClaimExtraction | None:
        return self.session.scalar(
            select(ClaimExtraction)
            .where(
                ClaimExtraction.claim_id == claim_id,
                ClaimExtraction.normalized_artifact_key.is_not(None),
            )
            .order_by(ClaimExtraction.updated_at.desc(), ClaimExtraction.id.desc())
            .limit(1)
        )

    def list(self, *, status: ClaimStatus | None, limit: int, offset: int) -> tuple[list[Claim], int]:
        filters = [Claim.status == status.value] if status else []
        total = self.session.scalar(select(func.count()).select_from(Claim).where(*filters)) or 0
        query = select(Claim).where(*filters).order_by(Claim.created_at.desc()).limit(limit).offset(offset)
        return list(self.session.scalars(query)), total
