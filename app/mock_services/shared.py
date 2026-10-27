from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

DATA_DIR = Path(__file__).with_name("data")


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def not_found(resource: str, identifier: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{resource} '{identifier}' was not found")
