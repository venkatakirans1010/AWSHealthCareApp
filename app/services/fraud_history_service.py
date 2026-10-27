"""Authorized claim-history and synthetic baseline boundaries for fraud screening."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from app.schemas.fraud import HistoricalClaim
from app.services.enterprise_api_clients import get_claim_history

BASELINE_PATH = Path(__file__).resolve().parents[1] / "mock_services" / "data" / "fraud_baselines.json"


class FraudHistoryService:
    def __init__(
        self,
        history_loader: Callable[..., dict[str, Any]] = get_claim_history,
        baseline_path: Path = BASELINE_PATH,
    ) -> None:
        self._history_loader = history_loader
        self._baseline_path = baseline_path

    def get_history(self, member_id: str, correlation_id: str) -> list[HistoricalClaim]:
        payload = self._history_loader(member_id, correlation_id=correlation_id)
        claims = payload.get("claims")
        if not isinstance(claims, list):
            raise ValueError("Claim-history response does not contain a claims list")
        return [HistoricalClaim.model_validate(item) for item in claims]

    def get_amount_baseline(self, claim_type: str, procedure_code: str) -> tuple[Decimal, str] | None:
        payload = json.loads(self._baseline_path.read_text(encoding="utf-8"))
        item = payload.get(f"{claim_type}:{procedure_code}")
        if not isinstance(item, dict) or "typical_amount" not in item:
            return None
        return Decimal(str(item["typical_amount"])), str(item.get("description", "Synthetic baseline"))
