# Synthetic enterprise APIs

These deterministic FastAPI services are training fixtures. They contain no real patient data, do not submit or adjudicate claims, and the coding records are neither authoritative nor complete. Reset seed data by restoring the JSON files in `app/mock_services/data`; services load them at startup.

## Start and inspect

From the repository root, run each command in a separate terminal (ports may be changed with each command's `--port` option):

```powershell
python -m uvicorn app.mock_services.ehr:app --host 127.0.0.1 --port 8001
python -m uvicorn app.mock_services.payer:app --host 127.0.0.1 --port 8002
python -m uvicorn app.mock_services.coding:app --host 127.0.0.1 --port 8003
```

Swagger UI: `http://127.0.0.1:8001/docs`, `http://127.0.0.1:8002/docs`, and `http://127.0.0.1:8003/docs`.

## Endpoints and examples

EHR: `GET /health`, `/patients/{patient_id}`, `/patients/{patient_id}/history`, `/patients/{patient_id}/medications`, and `/providers/{provider_id}`.

Payer: `GET /health`, `/members/{member_id}/eligibility?service_date=2026-09-20`, `/members/{member_id}/coverage?claim_type=INPATIENT&service_date=2026-09-20`, `/members/{member_id}/authorizations`, `/members/{member_id}/claim-history`, and `/claims/{claim_id}/status`. Coverage reflects only the seeded policy and date; active eligibility is not a coverage guarantee. Missing evidence is returned as `UNKNOWN`.

Coding: `GET /health`, `/codes/icd10/{code}`, `/codes/cpt/{code}`, `/codes/search?code_system=ICD10&q=pneumonia`; batch validation uses:

```json
{"codes":[{"code_system":"ICD10","code":"J18.9"},{"code_system":"CPT","code":"99223"}]}
```

Example: `Invoke-RestMethod 'http://127.0.0.1:8002/members/MEM1001/eligibility?service_date=2026-09-20'`.

## Agent configuration and tests

Optional variables: `EHR_API_BASE_URL`, `PAYER_API_BASE_URL`, `CODING_API_BASE_URL`, `MOCK_API_CONNECT_TIMEOUT`, and `MOCK_API_RESPONSE_TIMEOUT`. Defaults target ports 8001-8003. Reusable synchronous operations are in `app/services/enterprise_api_clients.py`; callers may pass `correlation_id` to forward `X-Correlation-ID` without logging claim or patient payloads.

Run `python -m pytest tests/test_mock_services.py -q` or the complete suite with `python -m pytest -q`.
