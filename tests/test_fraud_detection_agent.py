from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.fraud import FraudClaimInput, FraudScreeningStatus, HistoricalClaim
from app.services.fraud_detection_agent import FraudDetectionAgent, RULE_WEIGHTS


def claim(**overrides) -> FraudClaimInput:
    values = {
        "claim_id": "CLM20001", "member_id": "MEM1001", "provider_id": "PROV1001",
        "payer_id": "PAY1001", "claim_type": "INPATIENT", "service_date": "2026-09-20",
        "diagnosis_code": "J18.9", "procedure_code": "99223", "billed_amount": "45000.00",
    }
    values.update(overrides)
    return FraudClaimInput.model_validate(values)


def historical(**overrides) -> HistoricalClaim:
    values = {
        "claim_id": "CLM10001", "member_id": "MEM1001", "provider_id": "PROV1001",
        "payer_id": "PAY1001", "claim_type": "INPATIENT", "service_date": "2026-08-01",
        "diagnosis_code": "J18.9", "procedure_code": "99223", "amount": "45000.00",
    }
    values.update(overrides)
    return HistoricalClaim.model_validate(values)


class FakeHistoryService:
    def __init__(self, history=None, baseline=(Decimal("45000.00"), "Synthetic baseline"), error=None):
        self.history = history if history is not None else []
        self.baseline = baseline
        self.error = error

    def get_history(self, _member_id, _correlation_id):
        if self.error:
            raise self.error
        return self.history

    def get_amount_baseline(self, _claim_type, _procedure_code):
        return self.baseline


def run(input_claim=None, service=None):
    return FraudDetectionAgent(service or FakeHistoryService()).screen(
        input_claim or claim(), "corr-fraud-1"
    )


def test_possible_duplicate_uses_all_required_comparison_fields() -> None:
    duplicate = historical(service_date="2026-09-20")
    result = run(service=FakeHistoryService([duplicate]))
    rule_ids = {item.rule_id for item in result.triggered_rules}
    assert "POSSIBLE_EXACT_DUPLICATE" in rule_ids
    assert result.risk_score == 70  # duplicate plus overlapping service
    assert result.fraud_screening_status == FraudScreeningStatus.REVIEW_REQUIRED
    assert result.evidence[0].reference == "CLM10001"
    assert "not a confirmed fraud" in result.disclaimer


def test_unusually_high_amount_uses_synthetic_baseline() -> None:
    result = run(claim(billed_amount="100000.01"), FakeHistoryService([]))
    assert [item.rule_id for item in result.triggered_rules] == ["UNUSUALLY_HIGH_AMOUNT"]
    assert result.risk_score == RULE_WEIGHTS["UNUSUALLY_HIGH_AMOUNT"]
    assert result.evidence[0].reference == "baseline:INPATIENT:99223"
    assert result.fraud_screening_status == FraudScreeningStatus.REVIEW_REQUIRED


def test_ordinary_claim_is_clear_when_history_is_available() -> None:
    result = run(service=FakeHistoryService([historical()]))
    assert result.fraud_screening_status == FraudScreeningStatus.CLEAR
    assert result.risk_score == 0
    assert result.triggered_rules == []
    assert result.manual_review_required is False


def test_missing_historical_data_is_insufficient_not_clear() -> None:
    result = run(service=FakeHistoryService(error=OSError("history unavailable")))
    assert result.fraud_screening_status == FraudScreeningStatus.INSUFFICIENT_DATA
    assert result.manual_review_required


def test_conflicting_claim_details_are_explainable() -> None:
    result = run(claim(conflicting_fields=["amount", "procedure_code"]))
    rule = result.triggered_rules[0]
    assert rule.rule_id == "CONFLICTING_CLAIM_FIELDS"
    assert rule.weight == 20
    assert result.fraud_screening_status == FraudScreeningStatus.REVIEW_REQUIRED


def test_missing_fields_produce_insufficient_data() -> None:
    result = run(claim(member_id=None, service_date=None, diagnosis_code=None))
    assert result.fraud_screening_status == FraudScreeningStatus.INSUFFICIENT_DATA
    assert {"member_id", "service_date", "diagnosis_code"} <= set(result.missing_information)
    assert result.risk_score == 0


def test_rule_weight_sum_is_capped_at_100() -> None:
    input_claim = claim(
        claim_type="INPATIENT", procedure_code="99213", billed_amount="1000",
        conflicting_fields=["amount"],
    )
    duplicate = historical(
        claim_type="INPATIENT", procedure_code="99213", amount="1000", service_date="2026-09-20",
    )
    result = run(input_claim, FakeHistoryService([duplicate], baseline=(Decimal("100"), "Boundary baseline")))
    assert {item.rule_id for item in result.triggered_rules} == set(RULE_WEIGHTS)
    assert sum(item.weight for item in result.triggered_rules) > 100
    assert result.risk_score == 100


def test_database_or_history_error_is_contained() -> None:
    result = run(service=FakeHistoryService(error=RuntimeError("database password detail")))
    assert result.fraud_screening_status == FraudScreeningStatus.INSUFFICIENT_DATA
    assert "password" not in result.model_dump_json()


@pytest.mark.parametrize(
    "payload",
    [
        {"claim_id": "", "payer_id": "PAY1001", "claim_type": "INPATIENT"},
        {"claim_id": "CLM1", "payer_id": "PAY1001", "claim_type": "DENTAL"},
        {"claim_id": "CLM1", "payer_id": "PAY1001", "claim_type": "INPATIENT", "billed_amount": "-1"},
    ],
)
def test_malformed_inputs_are_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        run(payload)
