"""Typed LangGraph workflow for deterministic claim-document analysis."""

from __future__ import annotations

import logging
from statistics import fmean
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.config import NORMALIZATION_REQUIRED_FIELDS
from app.repositories.claim_repository import ClaimRepository
from app.schemas.extraction import (
    DocumentAnalysisResult,
    DocumentExtractionResult,
    ExtractionStatus,
    FieldEvidence,
    SourceDocument,
)
from app.services.claim_normalization_service import ClaimNotFoundError, normalize_ocr_result
from app.services.tesseract_service import analyze_claim_document, extract_claim_fields

logger = logging.getLogger(__name__)


class InvalidDocumentReferenceError(ValueError):
    pass


class DocumentAnalysisState(TypedDict, total=False):
    claim_id: str
    correlation_id: str
    requested_documents: list[SourceDocument] | None
    documents: list[SourceDocument]
    raw_results: list[dict[str, Any]]
    document_results: list[DocumentExtractionResult]
    candidates: dict[str, dict[str, Any]]
    normalized: Any
    missing_fields: list[str]
    validation_errors: list[Any]
    field_evidence: dict[str, list[FieldEvidence]]
    result: DocumentAnalysisResult


OCRRunner = Callable[[str, str], dict[str, Any]]


class DocumentAnalysisAgent:
    def __init__(self, session: Session, ocr_runner: OCRRunner | None = None) -> None:
        self._repository = ClaimRepository(session)
        self._ocr_runner = ocr_runner or self._run_existing_ocr
        self._graph = self._build_graph()

    @staticmethod
    def _run_existing_ocr(claim_id: str, key: str) -> dict[str, Any]:
        return analyze_claim_document(claim_id, key, upload_to_s3=False)["raw_ocr_result"]

    def _build_graph(self):
        graph = StateGraph(DocumentAnalysisState)
        graph.add_node("load_document_references", self._load_document_references)
        graph.add_node("extract_text", self._extract_text)
        graph.add_node("extract_candidate_fields", self._extract_candidate_fields)
        graph.add_node("normalize_and_validate", self._normalize_and_validate)
        graph.add_node("identify_review_items", self._identify_review_items)
        graph.add_node("produce_result", self._produce_result)
        graph.add_edge(START, "load_document_references")
        graph.add_edge("load_document_references", "extract_text")
        graph.add_conditional_edges(
            "extract_text", self._route_after_extraction,
            {"continue": "extract_candidate_fields", "failed": "identify_review_items"},
        )
        graph.add_edge("extract_candidate_fields", "normalize_and_validate")
        graph.add_edge("normalize_and_validate", "identify_review_items")
        graph.add_conditional_edges(
            "identify_review_items", self._route_after_review,
            {"complete": "produce_result", "manual_review": "produce_result"},
        )
        graph.add_edge("produce_result", END)
        return graph.compile()

    def analyze(
        self,
        claim_id: str,
        correlation_id: str,
        documents: list[SourceDocument] | None = None,
    ) -> DocumentAnalysisResult:
        state = self._graph.invoke({
            "claim_id": claim_id.strip().upper(),
            "correlation_id": correlation_id,
            "requested_documents": documents,
        })
        return state["result"]

    def _load_document_references(self, state: DocumentAnalysisState) -> dict[str, Any]:
        claim = self._repository.get(state["claim_id"])
        if claim is None:
            raise ClaimNotFoundError(state["claim_id"])
        available = {
            (item.s3_bucket, item.s3_object_key): SourceDocument(bucket=item.s3_bucket, key=item.s3_object_key)
            for item in claim.documents
        }
        requested = state.get("requested_documents")
        if requested is not None:
            unknown = [item for item in requested if (item.bucket, item.key) not in available]
            if unknown:
                raise InvalidDocumentReferenceError("A document reference is not attached to this claim")
            documents = requested
        else:
            documents = list(available.values())
        if not documents:
            raise InvalidDocumentReferenceError("Claim has no uploaded documents")
        return {"documents": documents}

    def _extract_text(self, state: DocumentAnalysisState) -> dict[str, Any]:
        raw_results: list[dict[str, Any]] = []
        results: list[DocumentExtractionResult] = []
        for document in state["documents"]:
            try:
                raw = self._ocr_runner(state["claim_id"], document.key)
                raw["_analysis_source_document"] = document.model_dump()
                raw_results.append(raw)
                usable = any(page.get("text", "").strip() for page in raw.get("pages", []))
                status = "COMPLETE" if raw.get("extraction_status") == "COMPLETED" and usable else "UNREADABLE"
                results.append(DocumentExtractionResult(
                    document=document,
                    status=status,
                    page_count=len(raw.get("pages", [])),
                    average_word_confidence=raw.get("average_word_confidence"),
                ))
            except Exception as error:
                logger.warning(
                    "Document extraction failed for claim %s (correlation_id=%s, key=%s)",
                    state["claim_id"], state["correlation_id"], document.key,
                    exc_info=True,
                )
                results.append(DocumentExtractionResult(
                    document=document, status="FAILED", error=type(error).__name__
                ))
        return {"raw_results": raw_results, "document_results": results}

    @staticmethod
    def _route_after_extraction(state: DocumentAnalysisState) -> str:
        return "continue" if any(item.status == "COMPLETE" for item in state["document_results"]) else "failed"

    @staticmethod
    def _combined_raw(state: DocumentAnalysisState) -> dict[str, Any]:
        pages: list[dict[str, Any]] = []
        confidences: list[float] = []
        for raw in state.get("raw_results", []):
            for page in raw.get("pages", []):
                pages.append({**page, "page_number": len(pages) + 1})
            confidence = raw.get("average_word_confidence")
            if confidence is not None:
                confidences.append(float(confidence))
        first = state["documents"][0]
        return {
            "claim_id": state["claim_id"], "source_bucket": first.bucket, "source_key": first.key,
            "extraction_status": "COMPLETED", "pages": pages,
            "average_word_confidence": fmean(confidences) if confidences else None,
        }

    def _extract_candidate_fields(self, state: DocumentAnalysisState) -> dict[str, Any]:
        return {"candidates": extract_claim_fields(self._combined_raw(state))}

    def _normalize_and_validate(self, state: DocumentAnalysisState) -> dict[str, Any]:
        return {"normalized": normalize_ocr_result(self._combined_raw(state))}

    def _identify_review_items(self, state: DocumentAnalysisState) -> dict[str, Any]:
        normalized = state.get("normalized")
        flags = list(normalized.review_flags) if normalized is not None else []
        missing = [
            name for name in NORMALIZATION_REQUIRED_FIELDS
            if normalized is None or getattr(normalized, name, None) is None
        ]
        evidence: dict[str, list[FieldEvidence]] = {}
        if state.get("candidates"):
            page_sources: list[SourceDocument] = []
            for raw in state.get("raw_results", []):
                document = SourceDocument.model_validate(raw["_analysis_source_document"])
                page_sources.extend([document] * len(raw.get("pages", [])))
            for name, field in state["candidates"].items():
                items = []
                for candidate in field.get("candidates", []):
                    item = candidate.get("evidence")
                    if item and 0 < item["page_number"] <= len(page_sources):
                        items.append(FieldEvidence(document=page_sources[item["page_number"] - 1], **item))
                if items:
                    evidence[name] = items
        return {"missing_fields": sorted(set(missing)), "validation_errors": flags, "field_evidence": evidence}

    @staticmethod
    def _route_after_review(state: DocumentAnalysisState) -> str:
        failed = not any(item.status == "COMPLETE" for item in state["document_results"])
        return "manual_review" if failed or state.get("missing_fields") or state.get("validation_errors") else "complete"

    def _produce_result(self, state: DocumentAnalysisState) -> dict[str, Any]:
        normalized = state.get("normalized")
        failures = [item for item in state["document_results"] if item.status != "COMPLETE"]
        if normalized is None:
            status = ExtractionStatus.FAILED
        elif failures or state["missing_fields"] or state["validation_errors"]:
            status = ExtractionStatus.REVIEW_REQUIRED
        else:
            status = ExtractionStatus.COMPLETE
        extracted = {}
        if normalized is not None:
            excluded = {"claim_id", "extraction_status", "review_required", "review_flags", "source_document", "failure_reason", "created_at", "updated_at"}
            extracted = {key: value for key, value in normalized.model_dump(mode="json").items() if key not in excluded}
        confidences = [item.average_word_confidence for item in state["document_results"] if item.average_word_confidence is not None]
        result = DocumentAnalysisResult(
            claim_id=state["claim_id"], correlation_id=state["correlation_id"],
            extracted_fields=extracted, document_references=state["documents"],
            document_results=state["document_results"], field_evidence=state.get("field_evidence", {}),
            extraction_status=status, missing_fields=state.get("missing_fields", []),
            validation_errors=state.get("validation_errors", []),
            average_word_confidence=round(fmean(confidences), 2) if confidences else None,
            manual_review_required=status != ExtractionStatus.COMPLETE,
        )
        return {"result": result}
