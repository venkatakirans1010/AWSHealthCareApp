from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.coding import CodeValidationResult, CodeValidationStatus, MedicalCodingInput
from app.services.enterprise_api_clients import EnterpriseNotFoundError
from app.services.medical_coding_agent import MedicalCodingAgent


RECORDS = {
    ("ICD10", "J18.9"): {"code_system": "ICD10", "code": "J18.9", "description": "Pneumonia, unspecified organism", "active": True},
    ("ICD10", "I10"): {"code_system": "ICD10", "code": "I10", "description": "Essential (primary) hypertension", "active": True},
    ("ICD10", "M54.5"): {"code_system": "ICD10", "code": "M54.5", "description": "Low back pain", "active": False},
    ("CPT", "99213"): {"code_system": "CPT", "code": "99213", "description": "Established patient office or outpatient visit", "active": True},
    ("CPT", "99223"): {"code_system": "CPT", "code": "99223", "description": "Initial hospital inpatient or observation care", "active": True},
}


class FakeReferenceService:
    def __init__(self, *, searches=None, failure: Exception | None = None):
        self.searches = searches or {}
        self.failure = failure

    def lookup(self, system, code, _correlation_id):
        if self.failure:
            raise self.failure
        try:
            return RECORDS[(system, code)]
        except KeyError as error:
            raise EnterpriseNotFoundError(code) from error

    def search(self, system, description, _correlation_id):
        if self.failure:
            raise self.failure
        return self.searches.get((system, description), [])


def coding_input(**overrides):
    values = {
        "claim_id": "CLM10001", "payer_id": "PAY1001", "claim_type": "INPATIENT",
        "diagnosis_code": "J18.9", "procedure_code": "99223",
    }
    values.update(overrides)
    return MedicalCodingInput.model_validate(values)


def run(input_claim=None, reference=None):
    return MedicalCodingAgent(reference or FakeReferenceService()).validate(
        input_claim or coding_input(), "corr-coding-1"
    )


def test_valid_icd10_and_cpt_are_reference_matches_only() -> None:
    result = run()
    diagnosis = result.diagnosis_results[0]
    procedure = result.procedure_results[0]
    assert diagnosis.validation_status == CodeValidationStatus.VALID_REFERENCE_MATCH
    assert diagnosis.description == "Pneumonia, unspecified organism"
    assert procedure.description == "Initial hospital inpatient or observation care"
    assert diagnosis.clinical_correctness_assessed is False
    assert diagnosis.confidence is None
    assert result.manual_review_required is False


def test_invalid_code_is_distinct_from_reference_failure() -> None:
    result = run(coding_input(diagnosis_code="Z99.99"))
    assert result.diagnosis_results[0].validation_status == CodeValidationStatus.INVALID_CODE
    assert result.manual_review_required


def test_missing_codes_are_not_invented() -> None:
    result = run(coding_input(diagnosis_code=None, procedure_code=None))
    assert result.diagnosis_results[0].validation_status == CodeValidationStatus.MISSING_CODE
    assert result.procedure_results[0].validation_status == CodeValidationStatus.MISSING_CODE
    assert all(item.code is None for item in result.diagnosis_results + result.procedure_results)


def test_ambiguous_diagnosis_description_does_not_choose_a_code() -> None:
    query = "respiratory condition"
    reference = FakeReferenceService(searches={
        ("ICD10", query): [RECORDS[("ICD10", "J18.9")], RECORDS[("ICD10", "I10")]],
    })
    result = run(coding_input(diagnosis_code=None, diagnosis_description=query), reference)
    diagnosis = result.diagnosis_results[0]
    assert diagnosis.validation_status == CodeValidationStatus.AMBIGUOUS
    assert diagnosis.code is None
    assert result.ambiguous and result.manual_review_required


def test_documented_unique_description_can_supply_candidate() -> None:
    description = "Pneumonia, unspecified organism"
    reference = FakeReferenceService(searches={
        ("ICD10", description): [RECORDS[("ICD10", "J18.9")]],
    })
    result = run(coding_input(
        diagnosis_code=None,
        clinical_text=f"Diagnosis: {description}\nProcedure Code: 99223",
    ), reference)
    assert result.diagnosis_results[0].code == "J18.9"
    assert result.diagnosis_results[0].validation_status == CodeValidationStatus.VALID_REFERENCE_MATCH


def test_conflicting_supplied_code_and_documented_code_are_flagged() -> None:
    result = run(coding_input(clinical_text="ICD-10: I10\nCPT: 99223"))
    assert result.diagnosis_results[0].validation_status == CodeValidationStatus.CONFLICTING_INPUT
    assert "I10" in result.diagnosis_results[0].issues[0]
    assert result.manual_review_required


def test_conflicting_code_and_description_are_flagged() -> None:
    description = "Essential (primary) hypertension"
    reference = FakeReferenceService(searches={
        ("ICD10", description): [RECORDS[("ICD10", "I10")]],
    })
    result = run(coding_input(diagnosis_description=description), reference)
    assert result.diagnosis_results[0].validation_status == CodeValidationStatus.CONFLICTING_INPUT


def test_inactive_code_requires_review() -> None:
    result = run(coding_input(diagnosis_code="M54.5"))
    assert result.diagnosis_results[0].validation_status == CodeValidationStatus.INACTIVE_CODE
    assert result.manual_review_required


def test_malformed_input_is_rejected() -> None:
    with pytest.raises(ValidationError):
        run({"claim_id": "", "payer_id": "PAY1001", "claim_type": "DENTAL"})


def test_reference_data_failure_routes_to_review() -> None:
    result = run(reference=FakeReferenceService(failure=OSError("coding service unavailable")))
    assert result.diagnosis_results[0].validation_status == CodeValidationStatus.REVIEW_REQUIRED
    assert "REFERENCE_DATA_UNAVAILABLE" in result.issues
    assert result.manual_review_required


def test_structured_output_rejects_unjustified_confidence() -> None:
    with pytest.raises(ValidationError):
        CodeValidationResult(
            code_system="ICD10", code="J18.9", description="Pneumonia, unspecified organism",
            validation_status="VALID_REFERENCE_MATCH", confidence=1.5,
        )
