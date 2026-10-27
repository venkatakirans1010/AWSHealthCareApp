"""LangGraph policy validation workflow with deterministic evidence gates."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.schemas.policy import (
    CoverageStatus,
    PolicyClaimInput,
    PolicyEvaluationDraft,
    PolicyEvidence,
    PolicyValidationResult,
)
from app.services.bedrock_knowledge_base_service import BedrockKnowledgeBaseService

logger = logging.getLogger(__name__)


class PolicyValidationState(TypedDict, total=False):
    claim: PolicyClaimInput
    correlation_id: str
    query: str
    retrieved: list[dict[str, Any]]
    applicable: list[dict[str, Any]]
    evidence_issues: list[str]
    draft: PolicyEvaluationDraft
    failure: str
    result: PolicyValidationResult


class PolicyValidationAgent:
    def __init__(self, knowledge_base: BedrockKnowledgeBaseService | None = None) -> None:
        self._knowledge_base = knowledge_base or BedrockKnowledgeBaseService()
        self._graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(PolicyValidationState)
        graph.add_node("validate_input_claim", self._validate_input_claim)
        graph.add_node("build_claim_aware_query", self._build_claim_aware_query)
        graph.add_node("retrieve_policy_evidence", self._retrieve_policy_evidence)
        graph.add_node("check_source_applicability", self._check_source_applicability)
        graph.add_node("evaluate_against_claim", self._evaluate_against_claim)
        graph.add_node("validate_result", self._validate_result)
        graph.add_edge(START, "validate_input_claim")
        graph.add_edge("validate_input_claim", "build_claim_aware_query")
        graph.add_edge("build_claim_aware_query", "retrieve_policy_evidence")
        graph.add_edge("retrieve_policy_evidence", "check_source_applicability")
        graph.add_conditional_edges(
            "check_source_applicability", self._route_applicability,
            {"evaluate": "evaluate_against_claim", "review": "validate_result"},
        )
        graph.add_edge("evaluate_against_claim", "validate_result")
        graph.add_edge("validate_result", END)
        return graph.compile()

    def validate(self, claim: PolicyClaimInput | dict[str, Any], correlation_id: str) -> PolicyValidationResult:
        validated = PolicyClaimInput.model_validate(claim)
        state = self._graph.invoke({"claim": validated, "correlation_id": correlation_id})
        return state["result"]

    @staticmethod
    def _validate_input_claim(state: PolicyValidationState) -> dict[str, Any]:
        # Pydantic validation happens before graph invocation; this node makes the contract explicit.
        return {"claim": PolicyClaimInput.model_validate(state["claim"])}

    @staticmethod
    def _build_claim_aware_query(state: PolicyValidationState) -> dict[str, Any]:
        claim = state["claim"]
        facts = [
            f"payer {claim.payer_id}", f"service type {claim.claim_type.value}",
            f"service date {claim.service_date.isoformat()}",
        ]
        if claim.diagnosis_code:
            facts.append(f"diagnosis {claim.diagnosis_code}")
        if claim.procedure_code:
            facts.append(f"procedure {claim.procedure_code}")
        facts.append(f"authorization status {claim.authorization_status or 'UNKNOWN'}")
        return {"query": "Policy coverage, exclusions, medical necessity, and prior authorization for " + "; ".join(facts)}

    def _retrieve_policy_evidence(self, state: PolicyValidationState) -> dict[str, Any]:
        try:
            return {"retrieved": self._knowledge_base.retrieve(state["query"])}
        except Exception as error:
            logger.warning(
                "Policy evidence retrieval failed for claim %s (correlation_id=%s, error=%s)",
                state["claim"].claim_id, state["correlation_id"], type(error).__name__,
            )
            return {"retrieved": [], "failure": "POLICY_RETRIEVAL_FAILED"}

    @staticmethod
    def _metadata_date(metadata: dict[str, Any], key: str) -> date | None:
        value = metadata.get(key)
        try:
            return date.fromisoformat(value) if isinstance(value, str) else None
        except ValueError:
            return None

    def _check_source_applicability(self, state: PolicyValidationState) -> dict[str, Any]:
        claim = state["claim"]
        applicable: list[dict[str, Any]] = []
        issues: list[str] = []
        for item in state.get("retrieved", []):
            metadata = item.get("metadata", {})
            payer = str(metadata.get("payer_id", "")).upper()
            service_type = str(metadata.get("claim_type", metadata.get("service_type", ""))).upper()
            effective_from = self._metadata_date(metadata, "effective_from")
            effective_to = self._metadata_date(metadata, "effective_to")
            if payer != claim.payer_id:
                issues.append(f"{item['evidence_id']}: wrong or missing payer metadata")
                continue
            if service_type != claim.claim_type.value:
                issues.append(f"{item['evidence_id']}: wrong or missing service type metadata")
                continue
            if effective_from is None or effective_to is None:
                issues.append(f"{item['evidence_id']}: missing or invalid effective dates")
                continue
            if not effective_from <= claim.service_date <= effective_to:
                issues.append(f"{item['evidence_id']}: policy is not effective on the service date")
                continue
            applicable.append({
                **item,
                "payer_id": payer,
                "claim_type": service_type,
                "effective_from": effective_from.isoformat(),
                "effective_to": effective_to.isoformat(),
                "policy_version": str(metadata.get("policy_version")) if metadata.get("policy_version") is not None else None,
            })
        versions = {item.get("policy_version") for item in applicable if item.get("policy_version")}
        if len(versions) > 1:
            issues.append("CONFLICTING_POLICY_VERSIONS")
        if not state.get("retrieved") and not state.get("failure"):
            issues.append("EMPTY_POLICY_RETRIEVAL")
        return {"applicable": applicable, "evidence_issues": sorted(set(issues))}

    @staticmethod
    def _route_applicability(state: PolicyValidationState) -> str:
        conflict = "CONFLICTING_POLICY_VERSIONS" in state.get("evidence_issues", [])
        return "evaluate" if state.get("applicable") and not conflict else "review"

    def _evaluate_against_claim(self, state: PolicyValidationState) -> dict[str, Any]:
        try:
            draft = self._knowledge_base.evaluate(state["claim"], state["applicable"])
            allowed = {item["evidence_id"] for item in state["applicable"]}
            if not set(draft.evidence_ids) <= allowed or not draft.evidence_ids:
                raise ValueError("Model cited unknown or no evidence")
            return {"draft": draft}
        except (ValidationError, ValueError, KeyError, TypeError, AttributeError) as error:
            logger.warning(
                "Policy model output rejected for claim %s (correlation_id=%s, error=%s)",
                state["claim"].claim_id, state["correlation_id"], type(error).__name__,
            )
            return {"failure": "MALFORMED_POLICY_MODEL_OUTPUT"}
        except Exception as error:
            logger.warning(
                "Policy model invocation failed for claim %s (correlation_id=%s, error=%s)",
                state["claim"].claim_id, state["correlation_id"], type(error).__name__,
            )
            return {"failure": "POLICY_MODEL_FAILED"}

    @staticmethod
    def _validate_result(state: PolicyValidationState) -> dict[str, Any]:
        claim = state["claim"]
        draft = state.get("draft")
        issues = list(state.get("evidence_issues", []))
        missing: list[str] = []
        if state.get("failure"):
            issues.append(state["failure"])
        if not state.get("applicable"):
            missing.append("applicable_policy_evidence")
        if draft is None:
            coverage = CoverageStatus.INSUFFICIENT_INFORMATION
            reason = "Applicable, current policy evidence was not sufficient for a validated assessment."
            authorization_required = None
            authorization_status = claim.authorization_status
            confidence = 0.0
        else:
            coverage = draft.coverage_status
            reason = draft.reason
            authorization_required = draft.authorization_required
            authorization_status = draft.authorization_status or claim.authorization_status
            confidence = draft.confidence
            missing.extend(draft.missing_information)
        if authorization_required is True and not authorization_status:
            missing.append("authorization_status")
        if "medical_documentation" in missing and claim.medical_documentation_available is True:
            missing.remove("medical_documentation")
        unresolved = bool(issues or missing or coverage == CoverageStatus.INSUFFICIENT_INFORMATION)
        if authorization_required is True and authorization_status in {None, "UNKNOWN"}:
            unresolved = True
            if "authorization_status" not in missing:
                missing.append("authorization_status")
        cited = set(draft.evidence_ids) if draft else set()
        evidence = [
            PolicyEvidence.model_validate({key: value for key, value in item.items() if key != "metadata"})
            for item in state.get("applicable", []) if item["evidence_id"] in cited
        ]
        result = PolicyValidationResult(
            claim_id=claim.claim_id, correlation_id=state["correlation_id"],
            coverage_status=coverage, reason=reason, evidence=evidence,
            authorization_required=authorization_required, authorization_status=authorization_status,
            missing_information=sorted(set(missing)), evidence_issues=sorted(set(issues)),
            manual_review_required=unresolved, confidence=confidence,
        )
        return {"result": result}
