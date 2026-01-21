import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _extract_markets(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return payload["data"]
        if isinstance(payload.get("markets"), list):
            return payload["markets"]
    return []


def fetch_markets(base_url: str, limit: int = 100) -> list[dict]:
    markets: list[dict] = []
    offset = 0

    with httpx.Client(base_url=base_url, timeout=30) as client:
        while True:
            response = client.get("/markets", params={"limit": limit, "offset": offset})
            response.raise_for_status()
            payload = response.json()
            batch = _extract_markets(payload)
            if not batch:
                break
            markets.extend(batch)
            if len(batch) < limit:
                break
            offset += limit

    logger.info("Fetched %s markets from Polymarket", len(markets))
    return markets
