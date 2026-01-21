import hashlib
import json
from datetime import datetime
from typing import Any


def normalize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def compute_spec_version_hash(source: str | None, resolution_time: datetime | None, criteria_text: str | None) -> str:
    normalized = {
        "source": normalize_value(source),
        "resolution_time": normalize_value(resolution_time),
        "criteria_text": normalize_value(criteria_text),
    }
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
