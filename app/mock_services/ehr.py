from datetime import date
from typing import Literal

from fastapi import FastAPI, Path
from pydantic import BaseModel, ConfigDict

from app.mock_services.shared import load_fixture, not_found


class Diagnosis(BaseModel):
    code: str
    description: str
    diagnosed_on: date


class Encounter(BaseModel):
    date: date
    type: str
    diagnoses: list[Diagnosis]
    clinical_note: str


class Patient(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_id: str
    member_id: str
    name: str
    date_of_birth: date
    gender: Literal["F", "M", "X"]


class PatientHistory(BaseModel):
    patient_id: str
    encounters: list[Encounter]


class Medication(BaseModel):
    name: str
    dose: str
    status: Literal["ACTIVE", "DISCONTINUED"]
    start_date: date
    end_date: date | None = None


class MedicationHistory(BaseModel):
    patient_id: str
    medications: list[Medication]


class Provider(BaseModel):
    provider_id: str
    name: str
    specialty: str
    npi: str
    network_status: Literal["IN_NETWORK", "OUT_OF_NETWORK"]


data = load_fixture("ehr.json")
app = FastAPI(title="Synthetic Mock EHR API", version="1.0.0")


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    return {"status": "healthy", "service": "ehr"}


@app.get("/patients/{patient_id}", response_model=Patient)
def get_patient(patient_id: str = Path(pattern=r"^PAT[0-9]{4}$")) -> dict:
    patient = data["patients"].get(patient_id)
    if patient is None:
        raise not_found("Patient", patient_id)
    return patient["demographics"]


@app.get("/patients/{patient_id}/history", response_model=PatientHistory)
def get_history(patient_id: str = Path(pattern=r"^PAT[0-9]{4}$")) -> dict:
    patient = data["patients"].get(patient_id)
    if patient is None:
        raise not_found("Patient", patient_id)
    return {"patient_id": patient_id, "encounters": patient["history"]}


@app.get("/patients/{patient_id}/medications", response_model=MedicationHistory)
def get_medications(patient_id: str = Path(pattern=r"^PAT[0-9]{4}$")) -> dict:
    patient = data["patients"].get(patient_id)
    if patient is None:
        raise not_found("Patient", patient_id)
    return {"patient_id": patient_id, "medications": patient["medications"]}


@app.get("/providers/{provider_id}", response_model=Provider)
def get_provider(provider_id: str = Path(pattern=r"^PROV[0-9]{4}$")) -> dict:
    provider = data["providers"].get(provider_id)
    if provider is None:
        raise not_found("Provider", provider_id)
    return provider
