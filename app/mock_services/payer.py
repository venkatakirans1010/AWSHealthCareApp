from datetime import date
from enum import StrEnum

from fastapi import FastAPI, Path, Query
from pydantic import BaseModel

from app.mock_services.shared import load_fixture, not_found


class ClaimType(StrEnum):
    INPATIENT = "INPATIENT"
    OUTPATIENT = "OUTPATIENT"


class EligibilityResponse(BaseModel):
    member_id: str
    payer_id: str
    policy_id: str
    service_date: date
    status: str
    evidence: str


class CoverageResponse(BaseModel):
    member_id: str
    payer_id: str
    policy_id: str
    claim_type: ClaimType
    service_date: date
    coverage_status: str
    authorization_requirement: str
    note: str


data = load_fixture("payer.json")
app = FastAPI(title="Synthetic Mock Payer API", version="1.0.0")


def member_or_404(member_id: str) -> dict:
    member = data["members"].get(member_id)
    if member is None:
        raise not_found("Member", member_id)
    return member


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    return {"status": "healthy", "service": "payer"}


@app.get("/members/{member_id}/eligibility", response_model=EligibilityResponse)
def eligibility(member_id: str = Path(pattern=r"^MEM[0-9]{4}$"), service_date: date = Query()) -> dict:
    member = member_or_404(member_id)
    policy = member["policy"]
    within = date.fromisoformat(policy["effective_from"]) <= service_date <= date.fromisoformat(policy["effective_to"])
    status = member["eligibility_status"] if within else "INACTIVE"
    evidence = "Seeded eligibility record applies on the requested date" if within else "Requested date is outside the policy effective period"
    return {"member_id": member_id, "payer_id": member["payer_id"], "policy_id": policy["policy_id"], "service_date": service_date, "status": status, "evidence": evidence}


@app.get("/members/{member_id}/coverage", response_model=CoverageResponse)
def coverage(member_id: str = Path(pattern=r"^MEM[0-9]{4}$"), claim_type: ClaimType = Query(), service_date: date = Query()) -> dict:
    member = member_or_404(member_id)
    policy = member["policy"]
    within = date.fromisoformat(policy["effective_from"]) <= service_date <= date.fromisoformat(policy["effective_to"])
    rule = policy.get("coverage", {}).get(claim_type.value)
    active = within and member["eligibility_status"] == "ACTIVE"
    status = rule["status"] if active and rule else "UNKNOWN" if active else "NOT_COVERED"
    auth = rule["authorization"] if active and rule else "UNKNOWN"
    note = "Coverage is a synthetic benefit indication, not a payment guarantee" if rule and active else "No applicable active policy evidence is available"
    return {"member_id": member_id, "payer_id": member["payer_id"], "policy_id": policy["policy_id"], "claim_type": claim_type, "service_date": service_date, "coverage_status": status, "authorization_requirement": auth, "note": note}


@app.get("/members/{member_id}/authorizations")
def authorizations(member_id: str = Path(pattern=r"^MEM[0-9]{4}$")) -> dict:
    member = member_or_404(member_id)
    return {"member_id": member_id, "authorizations": member.get("authorizations"), "status": "AVAILABLE" if member.get("authorizations") is not None else "UNKNOWN"}


@app.get("/members/{member_id}/claim-history")
def claim_history(member_id: str = Path(pattern=r"^MEM[0-9]{4}$")) -> dict:
    member_or_404(member_id)
    return {"member_id": member_id, "claims": [c for c in data["claims"].values() if c["member_id"] == member_id]}


@app.get("/claims/{claim_id}/status")
def claim_status(claim_id: str = Path(pattern=r"^CLM[0-9]{5}$")) -> dict:
    claim = data["claims"].get(claim_id)
    if claim is None:
        raise not_found("Claim", claim_id)
    return {"claim_id": claim_id, "status": claim["status"], "member_id": claim["member_id"], "payer_id": claim["payer_id"]}
