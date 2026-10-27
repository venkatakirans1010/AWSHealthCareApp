"""Small, reusable clients for the synthetic enterprise APIs.

An agent can call, for example, ``get_patient_history("PAT1001")``. Failures
are raised as typed exceptions and are never converted to successful data.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app import config


class EnterpriseAPIError(RuntimeError):
    """Base error for enterprise API boundary failures."""


class EnterpriseNotFoundError(EnterpriseAPIError):
    pass


class EnterpriseTimeoutError(EnterpriseAPIError):
    pass


class EnterpriseConnectionError(EnterpriseAPIError):
    pass


def _get(base_url: str, path: str, *, params: dict[str, str] | None = None, correlation_id: str | None = None) -> dict[str, Any]:
    headers = {"X-Correlation-ID": correlation_id} if correlation_id else {}
    timeout = httpx.Timeout(config.MOCK_API_RESPONSE_TIMEOUT, connect=config.MOCK_API_CONNECT_TIMEOUT)
    try:
        with httpx.Client(base_url=base_url, timeout=timeout, headers=headers) as client:
            response = client.get(path, params=params)
            if response.status_code == 404:
                raise EnterpriseNotFoundError(response.json().get("detail", "Resource not found"))
            response.raise_for_status()
            return response.json()
    except httpx.TimeoutException as error:
        raise EnterpriseTimeoutError(f"Enterprise API timed out: {path}") from error
    except httpx.ConnectError as error:
        raise EnterpriseConnectionError(f"Enterprise API connection failed: {path}") from error
    except httpx.HTTPStatusError as error:
        raise EnterpriseAPIError(f"Enterprise API returned HTTP {error.response.status_code}: {path}") from error


def get_patient_history(patient_id: str, *, correlation_id: str | None = None) -> dict[str, Any]:
    return _get(config.EHR_API_BASE_URL, f"/patients/{patient_id}/history", correlation_id=correlation_id)


def get_patient_medications(patient_id: str, *, correlation_id: str | None = None) -> dict[str, Any]:
    return _get(config.EHR_API_BASE_URL, f"/patients/{patient_id}/medications", correlation_id=correlation_id)


def get_member_eligibility(member_id: str, service_date: date, *, correlation_id: str | None = None) -> dict[str, Any]:
    return _get(config.PAYER_API_BASE_URL, f"/members/{member_id}/eligibility", params={"service_date": service_date.isoformat()}, correlation_id=correlation_id)


def get_member_coverage(member_id: str, claim_type: str, service_date: date, *, correlation_id: str | None = None) -> dict[str, Any]:
    return _get(config.PAYER_API_BASE_URL, f"/members/{member_id}/coverage", params={"claim_type": claim_type, "service_date": service_date.isoformat()}, correlation_id=correlation_id)


def get_claim_history(member_id: str, *, correlation_id: str | None = None) -> dict[str, Any]:
    return _get(config.PAYER_API_BASE_URL, f"/members/{member_id}/claim-history", correlation_id=correlation_id)


def lookup_icd10(code: str, *, correlation_id: str | None = None) -> dict[str, Any]:
    return _get(config.CODING_API_BASE_URL, f"/codes/icd10/{code}", correlation_id=correlation_id)


def lookup_cpt(code: str, *, correlation_id: str | None = None) -> dict[str, Any]:
    return _get(config.CODING_API_BASE_URL, f"/codes/cpt/{code}", correlation_id=correlation_id)


def search_codes(code_system: str, query: str, *, correlation_id: str | None = None) -> list[dict[str, Any]]:
    result = _get(
        config.CODING_API_BASE_URL,
        "/codes/search",
        params={"code_system": code_system, "q": query},
        correlation_id=correlation_id,
    )
    if not isinstance(result, list):
        raise EnterpriseAPIError("Coding search returned an invalid response")
    return result
