"""Coordinating LangGraph for deterministic claim decision recommendations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from app.schemas.coding import CodeValidationStatus
from app.schemas.decision import (
    ClaimsDecisionInput,
    ClaimsDecisionResult,
    DecisionRecommendation,
    DecisionWorkflowStatus,
    SpecialistRunStatus,
)
from app.schemas.extraction import ExtractionStatus
from app.schemas.fraud import FraudScreeningStatus
from app.schemas.policy import CoverageStatus

logger = logging.getLogger(__name__)


class DecisionPersister(Protocol):
    def persist_recommendation(self, claim_id, recommendation, reasons, evidence, correlation_id): ...


class ClaimsDecisionState(TypedDict, total=False):
    workflow_input: ClaimsDecisionInput
    correlation_id: str
    identity_conflicts: list[str]
    agent_failures: list[str]
    missing_information: list[str]
    manual_reasons: list[str]
    information_reasons: list[str]
    recommendation: DecisionRecommendation
    decision_reasons: list[str]
    persisted: bool
    persisted_claim_status: str | None
    persistence_failed: bool
    result: ClaimsDecisionResult


class ClaimsDecisionAgent:
    def __init__(self, persister: DecisionPersister) -> None:
        self._persister = persister
        self._graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(ClaimsDecisionState)
        graph.add_node("initialize_workflow", self._initialize_workflow)
        graph.add_node("validate_inputs", self._validate_inputs)
        graph.add_node("check_missing_and_consistency", self._check_missing_and_consistency)
        graph.add_node("evaluate_rules", self._evaluate_rules)
        graph.add_node("route_request_information", self._route_request_information)
        graph.add_node("route_manual_review", self._route_manual_review)
        graph.add_node("route_approval_review", self._route_approval_review)
        graph.add_node("route_denial_review", self._route_denial_review)
        graph.add_node("persist_recommendation", self._persist_recommendation)
        graph.add_node("produce_result", self._produce_result)
        graph.add_edge(START, "initialize_workflow")
        graph.add_edge("initialize_workflow", "validate_inputs")
        graph.add_edge("validate_inputs", "check_missing_and_consistency")
        graph.add_edge("check_missing_and_consistency", "evaluate_rules")
        graph.add_conditional_edges("evaluate_rules", self._select_route, {
            "REQUEST_INFORMATION": "route_request_information",
            "MANUAL_REVIEW": "route_manual_review",
            "ELIGIBLE_FOR_APPROVAL_REVIEW": "route_approval_review",
            "ELIGIBLE_FOR_DENIAL_REVIEW": "route_denial_review",
        })
        for node in ("route_request_information", "route_manual_review", "route_approval_review", "route_denial_review"):
            graph.add_edge(node, "persist_recommendation")
        graph.add_edge("persist_recommendation", "produce_result")
        graph.add_edge("produce_result", END)
        return graph.compile()

    def decide(self, workflow_input: ClaimsDecisionInput | dict[str, Any], correlation_id: str | None = None) -> ClaimsDecisionResult:
        validated = ClaimsDecisionInput.model_validate(workflow_input)
        return self._graph.invoke({"workflow_input": validated, "correlation_id": correlation_id or ""})["result"]

    @staticmethod
    def _initialize_workflow(state: ClaimsDecisionState) -> dict[str, Any]:
        cleaned = "".join(character for character in state.get("correlation_id", "").strip() if character.isprintable())
        return {"correlation_id": cleaned[:128] or str(uuid4())}

    @staticmethod
    def _validate_inputs(state: ClaimsDecisionState) -> dict[str, Any]:
        data = state["workflow_input"]
        claim_id = data.normalized_claim.claim_id
        conflicts: list[str] = []
        failures: list[str] = []
        outputs = {
            "document_analysis": data.document_analysis,
            "policy_validation": data.policy_validation,
            "medical_coding": data.medical_coding,
            "fraud_screening": data.fraud_screening,
        }
        for name, output in outputs.items():
            execution = getattr(data.executions, name)
            if execution.status != SpecialistRunStatus.SUCCESS:
                failures.append(f"{name}:{execution.status.value}" + (f":{execution.error_code}" if execution.error_code else ""))
            elif output is None:
                failures.append(f"{name}:INCOMPLETE")
            if output is not None:
                if output.claim_id != claim_id:
                    conflicts.append(f"{name}:CLAIM_ID_MISMATCH")
                if output.correlation_id != state["correlation_id"]:
                    conflicts.append(f"{name}:CORRELATION_ID_MISMATCH")
        if data.medical_coding is not None:
            if data.medical_coding.payer_id != data.normalized_claim.payer_id:
                conflicts.append("medical_coding:PAYER_ID_MISMATCH")
            if data.medical_coding.claim_type != data.claim_type:
                conflicts.append("medical_coding:CLAIM_TYPE_MISMATCH")
        return {"identity_conflicts": sorted(set(conflicts)), "agent_failures": sorted(set(failures))}

    @staticmethod
    def _check_missing_and_consistency(state: ClaimsDecisionState) -> dict[str, Any]:
        data = state["workflow_input"]
        normalized = data.normalized_claim
        missing = [
            name for name in ("patient_id", "provider_id", "payer_id", "date_of_service", "diagnosis_code", "procedure_code", "amount")
            if getattr(normalized, name) is None
        ]
        manual: list[str] = []
        information: list[str] = []
        if state.get("identity_conflicts"):
            manual.append("Specialist outputs conflict with the normalized claim or workflow identity.")
        if state.get("agent_failures"):
            manual.append("One or more specialist agents failed, timed out, or returned incomplete output.")
        if normalized.review_required or any(flag.code == "CONFLICTING_VALUES" for flag in normalized.review_flags):
            manual.append("The normalized claim contains unresolved validation or conflicting-field flags.")

        document = data.document_analysis
        if document is not None:
            missing.extend(document.missing_fields)
            if document.extraction_status == ExtractionStatus.FAILED:
                information.append("Claim documentation could not be read.")
            elif document.manual_review_required and not document.missing_fields:
                manual.append("Document analysis requires manual review.")

        policy = data.policy_validation
        if policy is not None:
            missing.extend(policy.missing_information)
            unknown_authorization = policy.authorization_required is True and policy.authorization_status in {None, "UNKNOWN"}
            if policy.coverage_status != CoverageStatus.INSUFFICIENT_INFORMATION and not policy.evidence:
                missing.append("policy_evidence")
                information.append("Policy coverage status lacks an applicable evidence reference.")
            if policy.coverage_status == CoverageStatus.INSUFFICIENT_INFORMATION:
                information.append("Applicable policy evidence is insufficient.")
            if unknown_authorization:
                missing.append("authorization_status")
                information.append("Required authorization status is unknown.")
            if policy.manual_review_required and policy.coverage_status != CoverageStatus.INSUFFICIENT_INFORMATION and not unknown_authorization:
                manual.append("Policy validation contains unresolved evidence or conditions.")

        coding = data.medical_coding
        if coding is not None:
            coding_results = coding.diagnosis_results + coding.procedure_results
            statuses = {item.validation_status for item in coding_results}
            if statuses & {CodeValidationStatus.MISSING_CODE, CodeValidationStatus.INVALID_CODE}:
                information.append("Required diagnosis or procedure coding information is missing or invalid.")
            if statuses & {CodeValidationStatus.AMBIGUOUS, CodeValidationStatus.CONFLICTING_INPUT, CodeValidationStatus.INACTIVE_CODE, CodeValidationStatus.REVIEW_REQUIRED}:
                manual.append("Medical coding contains ambiguity, conflicts, inactive codes, or unresolved validation.")
            normalized_codes = {value for value in (normalized.diagnosis_code, normalized.procedure_code) if value}
            result_codes = {item.code for item in coding_results if item.code}
            if normalized_codes and not normalized_codes <= result_codes:
                manual.append("Validated coding results conflict with normalized claim codes.")

        fraud = data.fraud_screening
        if fraud is not None:
            if fraud.fraud_screening_status != FraudScreeningStatus.CLEAR or fraud.manual_review_required:
                manual.append("Fraud screening contains unresolved investigation indicators or insufficient history.")
        return {
            "missing_information": sorted(set(missing)),
            "manual_reasons": sorted(set(manual)),
            "information_reasons": sorted(set(information)),
        }

    @staticmethod
    def _evaluate_rules(state: ClaimsDecisionState) -> dict[str, Any]:
        return {}

    @staticmethod
    def _select_route(state: ClaimsDecisionState) -> str:
        data = state["workflow_input"]
        if state.get("identity_conflicts") or state.get("agent_failures") or state.get("manual_reasons"):
            return DecisionRecommendation.MANUAL_REVIEW.value
        if state.get("missing_information") or state.get("information_reasons"):
            return DecisionRecommendation.REQUEST_INFORMATION.value
        policy = data.policy_validation
        if policy and policy.coverage_status == CoverageStatus.NOT_COVERED:
            return DecisionRecommendation.ELIGIBLE_FOR_DENIAL_REVIEW.value
        return DecisionRecommendation.ELIGIBLE_FOR_APPROVAL_REVIEW.value

    @staticmethod
    def _route_request_information(state: ClaimsDecisionState) -> dict[str, Any]:
        reasons = state.get("information_reasons", []) or ["Required claim information is missing."]
        return {"recommendation": DecisionRecommendation.REQUEST_INFORMATION, "decision_reasons": reasons}

    @staticmethod
    def _route_manual_review(state: ClaimsDecisionState) -> dict[str, Any]:
        reasons = state.get("manual_reasons", [])
        reasons.extend(f"Specialist execution issue: {item}" for item in state.get("agent_failures", []))
        reasons.extend(f"Input consistency issue: {item}" for item in state.get("identity_conflicts", []))
        return {"recommendation": DecisionRecommendation.MANUAL_REVIEW, "decision_reasons": sorted(set(reasons))}

    @staticmethod
    def _route_approval_review(_state: ClaimsDecisionState) -> dict[str, Any]:
        return {
            "recommendation": DecisionRecommendation.ELIGIBLE_FOR_APPROVAL_REVIEW,
            "decision_reasons": ["All configured deterministic prerequisites passed; authorized approval review is still required."],
        }

    @staticmethod
    def _route_denial_review(_state: ClaimsDecisionState) -> dict[str, Any]:
        return {
            "recommendation": DecisionRecommendation.ELIGIBLE_FOR_DENIAL_REVIEW,
            "decision_reasons": ["Applicable policy evidence reports the service as not covered; authorized denial review is required."],
        }

    def _persist_recommendation(self, state: ClaimsDecisionState) -> dict[str, Any]:
        data = state["workflow_input"]
        fraud = data.fraud_screening
        evidence = {
            "policy_evidence_references": [item.source_reference for item in data.policy_validation.evidence] if data.policy_validation else [],
            "fraud_screening_status": fraud.fraud_screening_status.value if fraud else "MISSING",
            "fraud_rule_ids": [item.rule_id for item in fraud.triggered_rules] if fraud else [],
        }
        try:
            status = self._persister.persist_recommendation(
                data.normalized_claim.claim_id, state["recommendation"], state["decision_reasons"],
                evidence, state["correlation_id"],
            )
            return {"persisted": True, "persisted_claim_status": status.value, "persistence_failed": False}
        except Exception:
            logger.exception(
                "Decision recommendation persistence failed (claim=%s, correlation_id=%s)",
                data.normalized_claim.claim_id, state["correlation_id"],
            )
            return {"persisted": False, "persisted_claim_status": None, "persistence_failed": True}

    @staticmethod
    def _produce_result(state: ClaimsDecisionState) -> dict[str, Any]:
        data = state["workflow_input"]
        document = data.document_analysis
        policy = data.policy_validation
        coding = data.medical_coding
        fraud = data.fraud_screening
        recommendation = state["recommendation"]
        reasons = list(state["decision_reasons"])
        if state.get("persistence_failed"):
            recommendation = DecisionRecommendation.MANUAL_REVIEW
            reasons.append("Decision recommendation could not be persisted; no final workflow state was recorded.")
        coding_statuses = sorted({item.validation_status.value for item in (coding.diagnosis_results + coding.procedure_results)}) if coding else ["MISSING"]
        summary = (
            f"Claim {data.normalized_claim.claim_id} ({data.claim_type.value}) routed to {recommendation.value}. "
            f"Policy: {policy.coverage_status.value if policy else 'MISSING'}; "
            f"coding: {','.join(coding_statuses)}; fraud screening: {fraud.fraud_screening_status.value if fraud else 'MISSING'}."
        )
        workflow_status = (
            DecisionWorkflowStatus.PERSISTENCE_FAILED if state.get("persistence_failed")
            else DecisionWorkflowStatus.REVIEW_REQUIRED if recommendation == DecisionRecommendation.MANUAL_REVIEW
            else DecisionWorkflowStatus.COMPLETED
        )
        output_by_name = {
            "document_analysis": data.document_analysis,
            "policy_validation": data.policy_validation,
            "medical_coding": data.medical_coding,
            "fraud_screening": data.fraud_screening,
        }
        execution_statuses = {}
        for name, output in output_by_name.items():
            configured_status = getattr(data.executions, name).status
            execution_statuses[name] = (
                SpecialistRunStatus.INCOMPLETE if configured_status == SpecialistRunStatus.SUCCESS and output is None
                else configured_status
            )
        result = ClaimsDecisionResult.model_validate({
            "claim_id": data.normalized_claim.claim_id,
            "correlation_id": state["correlation_id"],
            "workflow_status": workflow_status,
            "decision_recommendation": recommendation,
            "decision_reasons": sorted(set(reasons)),
            "specialist_execution_statuses": execution_statuses,
            "document_analysis_status": document.extraction_status.value if document else "MISSING",
            "policy_validation_status": policy.coverage_status.value if policy else "MISSING",
            "policy_evidence_references": [item.source_reference for item in policy.evidence] if policy else [],
            "medical_coding_status": ",".join(coding_statuses),
            "fraud_screening_status": fraud.fraud_screening_status.value if fraud else "MISSING",
            "fraud_risk_score": fraud.risk_score if fraud else None,
            "fraud_risk_indicators": [item.rule_id for item in fraud.triggered_rules] if fraud else [],
            "missing_information": state.get("missing_information", []),
            "manual_review_required": recommendation == DecisionRecommendation.MANUAL_REVIEW,
            "claim_summary": summary,
            "audit_metadata": {
                "persisted": state.get("persisted", False),
                "event_type": "CLAIM_DECISION_RECOMMENDED",
                "persisted_claim_status": state.get("persisted_claim_status"),
                "created_at": datetime.now(timezone.utc),
            },
        })
        return {"result": result}
