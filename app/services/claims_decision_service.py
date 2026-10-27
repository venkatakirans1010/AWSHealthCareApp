"""Transactional persistence boundary for decision recommendations."""

from typing import Any

from sqlalchemy.orm import Session

from app.models.claim import ClaimDecision
from app.repositories.claim_repository import ClaimRepository
from app.schemas.claim import ClaimStatus
from app.schemas.decision import DecisionRecommendation
from app.services.claim_normalization_service import ClaimNotFoundError


class ClaimsDecisionService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = ClaimRepository(session)

    def persist_recommendation(
        self,
        claim_id: str,
        recommendation: DecisionRecommendation,
        reasons: list[str],
        evidence: dict[str, Any],
        correlation_id: str,
    ) -> ClaimStatus:
        claim = self._repository.get(claim_id)
        if claim is None:
            raise ClaimNotFoundError(claim_id)
        if recommendation == DecisionRecommendation.REQUEST_INFORMATION:
            claim_status = ClaimStatus.REQUEST_INFORMATION
        elif recommendation == DecisionRecommendation.MANUAL_REVIEW and evidence.get("fraud_screening_status") != "CLEAR":
            claim_status = ClaimStatus.FRAUD_REVIEW
        else:
            # Eligibility recommendations are not final approvals or denials.
            claim_status = ClaimStatus.PROCESSING
        try:
            self._repository.add_decision(ClaimDecision(
                claim_id=claim_id,
                decision=recommendation.value,
                reason="; ".join(reasons),
                evidence=evidence,
            ))
            self._repository.update_claim_status(claim, claim_status)
            self._repository.add_audit(claim_id, "CLAIM_DECISION_RECOMMENDED", {
                "recommendation": recommendation.value,
                "claim_status": claim_status.value,
                "correlation_id": correlation_id,
            })
            self._session.commit()
            return claim_status
        except Exception:
            self._session.rollback()
            raise
