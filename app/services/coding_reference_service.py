"""Independently testable boundary around the existing coding reference API."""

from typing import Any, Callable

from app.services.enterprise_api_clients import lookup_cpt, lookup_icd10, search_codes


class CodingReferenceService:
    def __init__(
        self,
        icd10_lookup: Callable[..., dict[str, Any]] = lookup_icd10,
        cpt_lookup: Callable[..., dict[str, Any]] = lookup_cpt,
        code_search: Callable[..., list[dict[str, Any]]] = search_codes,
    ) -> None:
        self._icd10_lookup = icd10_lookup
        self._cpt_lookup = cpt_lookup
        self._code_search = code_search

    def lookup(self, code_system: str, code: str, correlation_id: str) -> dict[str, Any]:
        lookup = self._icd10_lookup if code_system == "ICD10" else self._cpt_lookup
        return lookup(code, correlation_id=correlation_id)

    def search(self, code_system: str, description: str, correlation_id: str) -> list[dict[str, Any]]:
        return self._code_search(code_system, description, correlation_id=correlation_id)
