"""Deterministic LangGraph medical coding reference-validation workflow."""

from __future__ import annotations

import logging
import re
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.schemas.coding import (
    CodeSystem,
    CodeValidationResult,
    CodeValidationStatus,
    MedicalCodingInput,
    MedicalCodingResult,
)
from app.services.coding_reference_service import CodingReferenceService
from app.services.enterprise_api_clients import EnterpriseNotFoundError

logger = logging.getLogger(__name__)

ICD_LABEL = re.compile(r"^\s*(?:ICD(?:-?10)?|Diagnosis\s+Code)\s*[:#-]\s*([A-Z][0-9]{2}(?:\.[A-Z0-9]{1,4})?)\s*$", re.I | re.M)
CPT_LABEL = re.compile(r"^\s*(?:CPT|Procedure\s+Code)\s*[:#-]\s*([A-Z0-9][A-Z0-9.-]{2,9})\s*$", re.I | re.M)
DIAGNOSIS_DESCRIPTION = re.compile(r"^\s*Diagnosis(?:\s+Description)?\s*[:#-]\s*(.+?)\s*$", re.I | re.M)
PROCEDURE_DESCRIPTION = re.compile(r"^\s*Procedure(?:\s+Description)?\s*[:#-]\s*(.+?)\s*$", re.I | re.M)


class MedicalCodingState(TypedDict, total=False):
    claim: MedicalCodingInput
    correlation_id: str
    diagnosis_candidates: list[str]
    procedure_candidates: list[str]
    diagnosis_description: str | None
    procedure_description: str | None
    diagnosis_results: list[CodeValidationResult]
    procedure_results: list[CodeValidationResult]
    issues: list[str]
    result: MedicalCodingResult


class MedicalCodingAgent:
    def __init__(self, reference_service: CodingReferenceService | None = None) -> None:
        self._reference = reference_service or CodingReferenceService()
        self._graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(MedicalCodingState)
        graph.add_node("validate_input", self._validate_input)
        graph.add_node("extract_candidates", self._extract_candidates)
        graph.add_node("validate_icd10", self._validate_icd10)
        graph.add_node("validate_cpt", self._validate_cpt)
        graph.add_node("detect_ambiguity_conflicts", self._detect_ambiguity_conflicts)
        graph.add_node("validate_structured_output", self._validate_structured_output)
        graph.add_edge(START, "validate_input")
        graph.add_edge("validate_input", "extract_candidates")
        graph.add_edge("extract_candidates", "validate_icd10")
        graph.add_edge("validate_icd10", "validate_cpt")
        graph.add_edge("validate_cpt", "detect_ambiguity_conflicts")
        graph.add_conditional_edges(
            "detect_ambiguity_conflicts", self._route_review,
            {"resolved": "validate_structured_output", "manual_review": "validate_structured_output"},
        )
        graph.add_edge("validate_structured_output", END)
        return graph.compile()

    def validate(self, claim: MedicalCodingInput | dict[str, Any], correlation_id: str) -> MedicalCodingResult:
        validated = MedicalCodingInput.model_validate(claim)
        return self._graph.invoke({"claim": validated, "correlation_id": correlation_id})["result"]

    @staticmethod
    def _validate_input(state: MedicalCodingState) -> dict[str, Any]:
        return {"claim": MedicalCodingInput.model_validate(state["claim"])}

    @staticmethod
    def _unique_matches(pattern: re.Pattern[str], text: str | None) -> list[str]:
        return sorted({match.upper() for match in pattern.findall(text or "")})

    def _extract_candidates(self, state: MedicalCodingState) -> dict[str, Any]:
        claim = state["claim"]
        diagnoses = ([claim.diagnosis_code] if claim.diagnosis_code else []) + self._unique_matches(ICD_LABEL, claim.clinical_text)
        procedures = ([claim.procedure_code] if claim.procedure_code else []) + self._unique_matches(CPT_LABEL, claim.clinical_text)
        diagnosis_description = claim.diagnosis_description
        procedure_description = claim.procedure_description
        if claim.clinical_text:
            diagnosis_matches = DIAGNOSIS_DESCRIPTION.findall(claim.clinical_text)
            procedure_matches = PROCEDURE_DESCRIPTION.findall(claim.clinical_text)
            if diagnosis_description is None and len(set(diagnosis_matches)) == 1:
                diagnosis_description = diagnosis_matches[0].strip()
            if procedure_description is None and len(set(procedure_matches)) == 1:
                procedure_description = procedure_matches[0].strip()
        return {
            "diagnosis_candidates": sorted(set(diagnoses)),
            "procedure_candidates": sorted(set(procedures)),
            "diagnosis_description": diagnosis_description,
            "procedure_description": procedure_description,
        }

    def _validate_system(
        self,
        state: MedicalCodingState,
        system: CodeSystem,
        candidates: list[str],
        description: str | None,
        confidence: float | None,
    ) -> list[CodeValidationResult]:
        if len(candidates) > 1:
            return [CodeValidationResult(
                code_system=system, validation_status=CodeValidationStatus.CONFLICTING_INPUT,
                confidence=confidence, ambiguous=True,
                issues=[f"Conflicting supplied candidates: {', '.join(candidates)}"],
            )]
        if not candidates and description:
            try:
                matches = self._reference.search(system.value, description, state["correlation_id"])
            except Exception as error:
                logger.warning("Coding reference search failed (claim=%s, system=%s, error=%s)", state["claim"].claim_id, system.value, type(error).__name__)
                return [CodeValidationResult(
                    code_system=system, validation_status=CodeValidationStatus.REVIEW_REQUIRED,
                    confidence=confidence, issues=["REFERENCE_DATA_UNAVAILABLE"],
                )]
            active_matches = [item for item in matches if item.get("active") is True]
            if len(active_matches) > 1:
                return [CodeValidationResult(
                    code_system=system, validation_status=CodeValidationStatus.AMBIGUOUS,
                    confidence=confidence, ambiguous=True,
                    issues=["Documentation matches multiple active reference codes"],
                )]
            if len(active_matches) == 1:
                candidates = [str(active_matches[0]["code"]).upper()]
            else:
                return [CodeValidationResult(
                    code_system=system, validation_status=CodeValidationStatus.INVALID_CODE,
                    confidence=confidence, issues=["No unique active reference code matches the supplied documentation"],
                )]
        if not candidates:
            return [CodeValidationResult(
                code_system=system, validation_status=CodeValidationStatus.MISSING_CODE,
                confidence=confidence, issues=["No supported code candidate was supplied or documented"],
            )]
        code = candidates[0]
        try:
            record = self._reference.lookup(system.value, code, state["correlation_id"])
        except EnterpriseNotFoundError:
            return [CodeValidationResult(
                code_system=system, code=code, validation_status=CodeValidationStatus.INVALID_CODE,
                confidence=confidence, issues=["Code is absent from the illustrative reference dataset"],
            )]
        except Exception as error:
            logger.warning("Coding reference lookup failed (claim=%s, system=%s, error=%s)", state["claim"].claim_id, system.value, type(error).__name__)
            return [CodeValidationResult(
                code_system=system, code=code, validation_status=CodeValidationStatus.REVIEW_REQUIRED,
                confidence=confidence, issues=["REFERENCE_DATA_UNAVAILABLE"],
            )]
        status = CodeValidationStatus.VALID_REFERENCE_MATCH if record.get("active") is True else CodeValidationStatus.INACTIVE_CODE
        issues = [] if record.get("active") is True else ["Reference code is inactive"]
        if description and record.get("description") and description.casefold() != str(record["description"]).casefold():
            try:
                description_matches = self._reference.search(system.value, description, state["correlation_id"])
            except Exception:
                description_matches = []
            if any(str(item.get("code", "")).upper() != code for item in description_matches):
                status = CodeValidationStatus.CONFLICTING_INPUT
                issues.append("Supplied code conflicts with the supplied description")
        return [CodeValidationResult(
            code_system=system, code=code, description=record.get("description"),
            validation_status=status, confidence=confidence,
            ambiguous=status == CodeValidationStatus.CONFLICTING_INPUT, issues=issues,
        )]

    def _validate_icd10(self, state: MedicalCodingState) -> dict[str, Any]:
        return {"diagnosis_results": self._validate_system(
            state, CodeSystem.ICD10, state["diagnosis_candidates"], state.get("diagnosis_description"),
            state["claim"].diagnosis_confidence,
        )}

    def _validate_cpt(self, state: MedicalCodingState) -> dict[str, Any]:
        return {"procedure_results": self._validate_system(
            state, CodeSystem.CPT, state["procedure_candidates"], state.get("procedure_description"),
            state["claim"].procedure_confidence,
        )}

    @staticmethod
    def _detect_ambiguity_conflicts(state: MedicalCodingState) -> dict[str, Any]:
        results = state["diagnosis_results"] + state["procedure_results"]
        issues = sorted({issue for result in results for issue in result.issues})
        return {"issues": issues}

    @staticmethod
    def _route_review(state: MedicalCodingState) -> str:
        results = state["diagnosis_results"] + state["procedure_results"]
        return "resolved" if all(item.validation_status == CodeValidationStatus.VALID_REFERENCE_MATCH for item in results) else "manual_review"

    @staticmethod
    def _validate_structured_output(state: MedicalCodingState) -> dict[str, Any]:
        results = state["diagnosis_results"] + state["procedure_results"]
        unresolved = any(item.validation_status != CodeValidationStatus.VALID_REFERENCE_MATCH for item in results)
        result = MedicalCodingResult.model_validate({
            "claim_id": state["claim"].claim_id,
            "payer_id": state["claim"].payer_id,
            "claim_type": state["claim"].claim_type,
            "correlation_id": state["correlation_id"],
            "diagnosis_results": state["diagnosis_results"],
            "procedure_results": state["procedure_results"],
            "ambiguous": any(item.ambiguous for item in results),
            "manual_review_required": unresolved,
            "issues": state.get("issues", []),
        })
        return {"result": result}
