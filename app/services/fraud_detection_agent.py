"""Explainable deterministic LangGraph anomaly screening for claims."""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.schemas.fraud import (
    FraudClaimInput,
    FraudEvidence,
    FraudScreeningResult,
    FraudScreeningStatus,
    HistoricalClaim,
    TriggeredFraudRule,
)
from app.services.fraud_history_service import FraudHistoryService

logger = logging.getLogger(__name__)

RULE_WEIGHTS = {
    "POSSIBLE_EXACT_DUPLICATE": 50,
    "OVERLAPPING_OR_FREQUENT_SERVICE": 20,
    "UNUSUALLY_HIGH_AMOUNT": 25,
    "CONFLICTING_CLAIM_FIELDS": 20,
    "SUSPICIOUS_SERVICE_COMBINATION": 15,
}
REVIEW_THRESHOLD = 30


class FraudDetectionState(TypedDict, total=False):
    claim: FraudClaimInput
    correlation_id: str
    missing_information: list[str]
    history: list[HistoricalClaim]
    history_available: bool
    baseline: tuple[Decimal, str] | None
    triggered_rules: list[TriggeredFraudRule]
    evidence: list[FraudEvidence]
    risk_score: int
    result: FraudScreeningResult


class FraudDetectionAgent:
    def __init__(self, history_service: FraudHistoryService | None = None) -> None:
        self._history_service = history_service or FraudHistoryService()
        self._graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(FraudDetectionState)
        graph.add_node("validate_claim", self._validate_claim)
        graph.add_node("retrieve_history", self._retrieve_history)
        graph.add_node("apply_rules", self._apply_rules)
        graph.add_node("calculate_risk_score", self._calculate_risk_score)
        graph.add_node("validate_and_route", self._validate_and_route)
        graph.add_node("produce_result", self._produce_result)
        graph.add_edge(START, "validate_claim")
        graph.add_edge("validate_claim", "retrieve_history")
        graph.add_edge("retrieve_history", "apply_rules")
        graph.add_edge("apply_rules", "calculate_risk_score")
        graph.add_edge("calculate_risk_score", "validate_and_route")
        graph.add_conditional_edges(
            "validate_and_route", self._route,
            {"clear": "produce_result", "review": "produce_result", "insufficient": "produce_result"},
        )
        graph.add_edge("produce_result", END)
        return graph.compile()

    def screen(self, claim: FraudClaimInput | dict[str, Any], correlation_id: str) -> FraudScreeningResult:
        validated = FraudClaimInput.model_validate(claim)
        return self._graph.invoke({"claim": validated, "correlation_id": correlation_id})["result"]

    @staticmethod
    def _validate_claim(state: FraudDetectionState) -> dict[str, Any]:
        claim = FraudClaimInput.model_validate(state["claim"])
        required = ("member_id", "provider_id", "service_date", "procedure_code", "billed_amount")
        missing = [name for name in required if getattr(claim, name) is None]
        if claim.diagnosis_code is None:
            missing.append("diagnosis_code")
        return {"claim": claim, "missing_information": missing}

    def _retrieve_history(self, state: FraudDetectionState) -> dict[str, Any]:
        claim = state["claim"]
        if not claim.member_id:
            return {"history": [], "history_available": False, "baseline": self._baseline(claim)}
        try:
            history = self._history_service.get_history(claim.member_id, state["correlation_id"])
            history = [item for item in history if item.claim_id != claim.claim_id]
            return {"history": history, "history_available": True, "baseline": self._baseline(claim)}
        except Exception as error:
            logger.warning(
                "Fraud comparison retrieval failed (claim=%s, correlation_id=%s, error=%s)",
                claim.claim_id, state["correlation_id"], type(error).__name__,
            )
            return {"history": [], "history_available": False, "baseline": self._baseline(claim)}

    def _baseline(self, claim: FraudClaimInput):
        if not claim.procedure_code:
            return None
        try:
            return self._history_service.get_amount_baseline(claim.claim_type.value, claim.procedure_code)
        except Exception as error:
            logger.warning("Fraud baseline unavailable (claim=%s, error=%s)", claim.claim_id, type(error).__name__)
            return None

    @staticmethod
    def _rule(rule_id: str, explanation: str, references: list[str]) -> TriggeredFraudRule:
        return TriggeredFraudRule(
            rule_id=rule_id, weight=RULE_WEIGHTS[rule_id],
            explanation=explanation, evidence_references=references,
        )

    def _apply_rules(self, state: FraudDetectionState) -> dict[str, Any]:
        claim = state["claim"]
        rules: list[TriggeredFraudRule] = []
        evidence: list[FraudEvidence] = []
        history = state.get("history", [])
        if all((claim.member_id, claim.provider_id, claim.service_date, claim.procedure_code, claim.billed_amount is not None)):
            duplicates = [item for item in history if all((
                item.member_id == claim.member_id, item.provider_id == claim.provider_id,
                item.service_date == claim.service_date, item.procedure_code == claim.procedure_code,
                item.amount == claim.billed_amount,
            ))]
            if duplicates:
                refs = [item.claim_id for item in duplicates if item.claim_id]
                rules.append(self._rule("POSSIBLE_EXACT_DUPLICATE", "A prior comparison claim matches member, provider, service date, procedure, and billed amount.", refs))
                evidence.extend(FraudEvidence(evidence_type="HISTORICAL_CLAIM", reference=ref, details="Exact comparison-field match") for ref in refs)
        if claim.member_id and claim.procedure_code and claim.service_date:
            nearby = [item for item in history if item.member_id == claim.member_id and item.procedure_code == claim.procedure_code and item.service_date is not None and abs((item.service_date - claim.service_date).days) <= 7]
            frequent = [item for item in history if item.member_id == claim.member_id and item.service_date is not None and claim.service_date - timedelta(days=30) <= item.service_date <= claim.service_date]
            if nearby or len(frequent) >= 3:
                refs = sorted({item.claim_id for item in nearby + frequent if item.claim_id})
                rules.append(self._rule("OVERLAPPING_OR_FREQUENT_SERVICE", "Comparable services overlap within 7 days or at least three prior services occur within 30 days.", refs))
                evidence.extend(FraudEvidence(evidence_type="HISTORICAL_CLAIM", reference=ref, details="Temporal frequency comparison") for ref in refs)
        baseline = state.get("baseline")
        if baseline and claim.billed_amount is not None and claim.billed_amount > baseline[0] * Decimal("2"):
            reference = f"baseline:{claim.claim_type.value}:{claim.procedure_code}"
            rules.append(self._rule("UNUSUALLY_HIGH_AMOUNT", "Billed amount is greater than twice the applicable synthetic baseline.", [reference]))
            evidence.append(FraudEvidence(evidence_type="SYNTHETIC_BASELINE", reference=reference, details=f"Typical amount {baseline[0]} ({baseline[1]})"))
        if claim.conflicting_fields:
            rules.append(self._rule("CONFLICTING_CLAIM_FIELDS", "The normalized pipeline reported conflicting claim fields.", []))
        suspicious = (claim.claim_type.value == "INPATIENT" and claim.procedure_code == "99213") or (claim.claim_type.value == "OUTPATIENT" and claim.procedure_code == "99223")
        if suspicious:
            rules.append(self._rule("SUSPICIOUS_SERVICE_COMBINATION", "The synthetic procedure/service-type combination is inconsistent and requires investigation.", []))
        return {"triggered_rules": rules, "evidence": evidence}

    @staticmethod
    def _calculate_risk_score(state: FraudDetectionState) -> dict[str, Any]:
        return {"risk_score": min(100, sum(rule.weight for rule in state.get("triggered_rules", [])))}

    @staticmethod
    def _validate_and_route(state: FraudDetectionState) -> dict[str, Any]:
        # Result construction performs Pydantic bounds validation; routing remains explicit in the graph.
        return {}

    @staticmethod
    def _route(state: FraudDetectionState) -> str:
        if state.get("missing_information") or not state.get("history_available", False):
            return "insufficient"
        if state.get("risk_score", 0) >= REVIEW_THRESHOLD or state.get("triggered_rules"):
            return "review"
        return "clear"

    @staticmethod
    def _produce_result(state: FraudDetectionState) -> dict[str, Any]:
        route = FraudDetectionAgent._route(state)
        status = {
            "clear": FraudScreeningStatus.CLEAR,
            "review": FraudScreeningStatus.REVIEW_REQUIRED,
            "insufficient": FraudScreeningStatus.INSUFFICIENT_DATA,
        }[route]
        result = FraudScreeningResult.model_validate({
            "claim_id": state["claim"].claim_id,
            "correlation_id": state["correlation_id"],
            "fraud_screening_status": status,
            "risk_score": state.get("risk_score", 0),
            "triggered_rules": state.get("triggered_rules", []),
            "evidence": state.get("evidence", []),
            "missing_information": sorted(set(state.get("missing_information", []))),
            "manual_review_required": status != FraudScreeningStatus.CLEAR,
        })
        return {"result": result}
