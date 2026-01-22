import logging
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        cleaned = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            return None
    return None


def _auth_params(api_key: str | None) -> dict[str, str]:
    """
    OddsPapi v1/v4 endpoints require an apiKey query param:
      ?apiKey=YOUR_KEY
    """
    if not api_key:
        return {}
    return {"apiKey": api_key}


def fetch_active_fixtures(base_url: str, api_key: str | None, sport: str = "Esports") -> list[dict]:
    with httpx.Client(base_url=base_url, timeout=30) as client:
        response = client.get(
            "/v1/fixtures/active", params={"sport": sport, **_auth_params(api_key)}
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("data"), list):
            return payload["data"]
        logger.warning("Unexpected fixtures payload shape: %s", type(payload))
        return []


def fetch_odds(
    base_url: str,
    api_key: str | None,
    fixture_id: str,
    bookmakers: str = "pinnacle",
    odds_format: str = "decimal",
    verbosity: int = 3,
) -> dict:
    with httpx.Client(base_url=base_url, timeout=30) as client:
        response = client.get(
            "/v4/odds",
            params={
                "fixtureId": fixture_id,
                "bookmakers": bookmakers,
                "oddsFormat": odds_format,
                "verbosity": verbosity,
                **_auth_params(api_key),
            },
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        return {"raw": payload, "fetched_at": _now_utc().isoformat()}


def extract_moneyline_prices_pinnacle(payload: dict) -> list[dict[str, Any]]:
    """
    Best-effort extraction of Pinnacle moneyline (match winner) prices from OddsPapi.
    Returns a list of rows:
      {selection: "home"/"away"/"draw", odds_decimal: float|None, changed_at: datetime|None, raw_json: dict}
    """
    rows: list[dict[str, Any]] = []

    bookmaker_odds = payload.get("bookmakerOdds") or {}
    pinnacle = bookmaker_odds.get("pinnacle") or {}
    markets = (pinnacle.get("markets") or {}) if isinstance(pinnacle, dict) else {}

    moneyline_market = markets.get("101")
    if not isinstance(moneyline_market, dict):
        return rows

    outcomes = moneyline_market.get("outcomes") or {}
    if not isinstance(outcomes, dict):
        return rows

    for _outcome_key, outcome in outcomes.items():
        if not isinstance(outcome, dict):
            continue
        players = outcome.get("players") or {}
        if not isinstance(players, dict):
            continue
        player0 = players.get("0") or {}
        if not isinstance(player0, dict):
            continue

        selection = player0.get("bookmakerOutcomeId")
        price = player0.get("price")
        changed_at = _parse_iso_datetime(player0.get("changedAt"))
        rows.append(
            {
                "selection": str(selection) if selection is not None else None,
                "odds_decimal": float(price) if isinstance(price, (int, float)) else None,
                "changed_at": changed_at,
                "raw_json": player0,
            }
        )

    return rows


