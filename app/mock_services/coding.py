from enum import StrEnum

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from app.mock_services.shared import load_fixture, not_found


class CodeSystem(StrEnum):
    ICD10 = "ICD10"
    CPT = "CPT"


class CodeRecord(BaseModel):
    code_system: CodeSystem
    code: str
    description: str
    active: bool


class ValidateRequest(BaseModel):
    codes: list[dict[str, str]] = Field(min_length=1, max_length=100)


data = load_fixture("coding.json")
app = FastAPI(title="Synthetic Mock Medical Coding API", description="Illustrative training fixtures only; not an authoritative or complete coding reference.", version="1.0.0")


def lookup(system: CodeSystem, code: str) -> dict:
    record = data[system.value].get(code.upper())
    if record is None:
        raise not_found(f"{system.value} code", code)
    return {"code_system": system, "code": code.upper(), **record}


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    return {"status": "healthy", "service": "coding"}


@app.get("/codes/icd10/{code}", response_model=CodeRecord)
def icd10(code: str) -> dict:
    return lookup(CodeSystem.ICD10, code)


@app.get("/codes/cpt/{code}", response_model=CodeRecord)
def cpt(code: str) -> dict:
    return lookup(CodeSystem.CPT, code)


@app.get("/codes/search", response_model=list[CodeRecord])
def search(code_system: CodeSystem = Query(), q: str = Query(min_length=1)) -> list[dict]:
    needle = q.casefold()
    return [{"code_system": code_system, "code": code, **record} for code, record in data[code_system.value].items() if needle in code.casefold() or needle in record["description"].casefold()]


@app.post("/codes/validate")
def validate(request: ValidateRequest) -> dict:
    results = []
    for supplied in request.codes:
        system, code = supplied.get("code_system", ""), supplied.get("code", "").upper()
        record = data.get(system, {}).get(code)
        results.append({"code_system": system, "code": code, "valid": bool(record and record["active"]), "description": record["description"] if record else None, "active": record["active"] if record else None})
    return {"results": results}
