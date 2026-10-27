import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.mock_services.coding import app as coding_app
from app.mock_services.ehr import app as ehr_app
from app.mock_services.payer import app as payer_app
from app.services import enterprise_api_clients as clients


ehr = TestClient(ehr_app)
payer = TestClient(payer_app)
coding = TestClient(coding_app)


@pytest.mark.parametrize("client,service", [(ehr,"ehr"),(payer,"payer"),(coding,"coding")])
def test_health(client, service):
    assert client.get("/health").json() == {"status":"healthy", "service":service}


def test_ehr_lookups_and_unknowns():
    assert ehr.get("/patients/PAT1001").json()["member_id"] == "MEM1001"
    assert ehr.get("/patients/PAT1001/history").json()["encounters"][0]["diagnoses"][0]["code"] == "J18.9"
    assert ehr.get("/patients/PAT1003/medications").json()["medications"][0]["status"] == "DISCONTINUED"
    assert ehr.get("/providers/PROV1001").json()["network_status"] == "IN_NETWORK"
    assert ehr.get("/patients/PAT9999").status_code == 404
    assert ehr.get("/providers/PROV9999").status_code == 404


def test_eligibility_dates_and_statuses():
    assert payer.get("/members/MEM1001/eligibility", params={"service_date":"2026-09-20"}).json()["status"] == "ACTIVE"
    assert payer.get("/members/MEM1002/eligibility", params={"service_date":"2026-05-20"}).json()["status"] == "INACTIVE"
    assert payer.get("/members/MEM1001/eligibility", params={"service_date":"2027-01-01"}).json()["status"] == "INACTIVE"


def test_coverage_authorizations_and_claims():
    inpatient = payer.get("/members/MEM1003/coverage", params={"claim_type":"INPATIENT","service_date":"2026-08-01"}).json()
    assert inpatient["coverage_status"] == "COVERED" and inpatient["authorization_requirement"] == "REQUIRED"
    outpatient = payer.get("/members/MEM1001/coverage", params={"claim_type":"OUTPATIENT","service_date":"2026-08-01"}).json()
    assert outpatient["coverage_status"] == "COVERED"
    assert payer.get("/members/MEM1003/coverage", params={"claim_type":"OUTPATIENT","service_date":"2026-08-01"}).json()["coverage_status"] == "UNKNOWN"
    assert payer.get("/members/MEM1003/authorizations").json()["status"] == "UNKNOWN"
    history = payer.get("/members/MEM1001/claim-history").json()["claims"]
    assert any(item["potential_duplicate_of"] for item in history)
    assert payer.get("/claims/CLM10001/status").json()["status"] == "APPROVED"
    assert payer.get("/members/MEM9999/claim-history").status_code == 404
    assert payer.get("/claims/CLM99999/status").status_code == 404


def test_coding_lookup_search_and_validation():
    assert coding.get("/codes/icd10/J18.9").json()["active"] is True
    assert coding.get("/codes/cpt/99223").json()["code_system"] == "CPT"
    assert coding.get("/codes/icd10/NOPE").status_code == 404
    assert coding.get("/codes/search", params={"code_system":"ICD10","q":"pneumonia"}).json()[0]["code"] == "J18.9"
    result = coding.post("/codes/validate", json={"codes":[{"code_system":"CPT","code":"99213"},{"code_system":"ICD10","code":"BAD"}]}).json()["results"]
    assert [item["valid"] for item in result] == [True, False]


def test_seed_consistency_and_payer_identifier():
    folder = Path("app/mock_services/data")
    ehr_data = json.loads((folder / "ehr.json").read_text())
    payer_data = json.loads((folder / "payer.json").read_text())
    assert len(ehr_data["patients"]) >= 3 and len(ehr_data["providers"]) >= 2
    assert all(member["payer_id"] == "PAY1001" for member in payer_data["members"].values())
    assert all(claim["payer_id"] == "PAY1001" for claim in payer_data["claims"].values())
    assert {p["demographics"]["member_id"] for p in ehr_data["patients"].values()} == set(payer_data["members"])


def test_client_success_and_correlation(monkeypatch):
    seen = {}
    class CaptureClient:
        def __init__(self, **kwargs): seen["headers"] = kwargs["headers"]
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, path, params=None):
            seen["path"] = path
            return httpx.Response(200, json={"patient_id":"PAT1001","encounters":[]}, request=httpx.Request("GET", "http://test" + path))
    monkeypatch.setattr(httpx, "Client", CaptureClient)
    result = clients.get_patient_history("PAT1001", correlation_id="corr-123")
    assert result["patient_id"] == "PAT1001"
    assert seen == {"headers":{"X-Correlation-ID":"corr-123"}, "path":"/patients/PAT1001/history"}


@pytest.mark.parametrize("error,expected", [(httpx.ReadTimeout("late"), clients.EnterpriseTimeoutError),(httpx.ConnectError("down"), clients.EnterpriseConnectionError)])
def test_client_transport_errors(monkeypatch, error, expected):
    class BrokenClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, *_args, **_kwargs): raise error
    monkeypatch.setattr(httpx, "Client", BrokenClient)
    with pytest.raises(expected):
        clients.get_patient_history("PAT1001")


def test_client_http_errors(monkeypatch):
    class ResponseClient:
        def __init__(self, status, **kwargs): self.status = status
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, *_args, **_kwargs): return httpx.Response(self.status, json={"detail":"failure"}, request=httpx.Request("GET", "http://test/x"))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: ResponseClient(404))
    with pytest.raises(clients.EnterpriseNotFoundError): clients.lookup_cpt("BAD")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: ResponseClient(503))
    with pytest.raises(clients.EnterpriseAPIError): clients.lookup_cpt("99213")
