"""
Goalserve esports client focused on live LoL game-state ingestion.
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import date
from typing import Any

import httpx

from shared.config import settings

logger = logging.getLogger(__name__)


class GoalserveClient:
    """Thin HTTP wrapper for Goalserve esports feeds."""

    def __init__(
        self,
        *,
        feed_key: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._feed_key = (feed_key or settings.goalserve_feed_key).strip()
        self._timeout_seconds = timeout_seconds
        self._client = httpx.Client(timeout=timeout_seconds, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def get_home(self, target_date: date | None = None) -> dict[str, Any]:
        """Fetch esports home feed as JSON dict."""
        if not self._feed_key:
            raise RuntimeError("GOALSERVE_FEED_KEY is required for Goalserve endpoints.")
        base_url = f"https://www.goalserve.com/getfeed/{self._feed_key}/esports/home"
        params: dict[str, str] = {"json": "1"}
        if target_date:
            params["date"] = target_date.strftime("%d.%m.%Y")
        response = self._client.get(base_url, params=params)
        response.raise_for_status()
        return _decode_goalserve_json(response.content)

    @staticmethod
    def extract_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
        scores = payload.get("scores", {})
        matches = scores.get("match", [])
        if isinstance(matches, dict):
            return [matches]
        if isinstance(matches, list):
            return [m for m in matches if isinstance(m, dict)]
        return []

    @staticmethod
    def filter_lol(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [m for m in matches if str(m.get("@type", "")).strip() == "League Of Legends"]


def _decode_goalserve_json(raw: bytes) -> dict[str, Any]:
    decoded = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    text = decoded.decode("utf-8-sig")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Unexpected Goalserve payload type.")
    return parsed


def parse_game_stats(match: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized per-game stats rows for LoL matches."""
    games_obj = match.get("games")
    if not isinstance(games_obj, dict):
        return []
    games = games_obj.get("game", [])
    if isinstance(games, dict):
        games = [games]
    rows: list[dict[str, Any]] = []
    for game in games:
        if not isinstance(game, dict):
            continue
        stats = game.get("stats", {})
        if not isinstance(stats, dict):
            continue
        local = stats.get("localteam", {}) if isinstance(stats.get("localteam"), dict) else {}
        away = stats.get("awayteam", {}) if isinstance(stats.get("awayteam"), dict) else {}
        row = {
            "game_no": _as_int(game.get("@no")),
            "duration": str(game.get("@duration") or ""),
            "started_at": str(game.get("@started_at") or ""),
            "localteam_gold": _as_float(local.get("@gold_earned")),
            "awayteam_gold": _as_float(away.get("@gold_earned")),
            "localteam_kills": _as_float(local.get("@kills")),
            "awayteam_kills": _as_float(away.get("@kills")),
            "localteam_towers": _as_float(local.get("@tower_kills")),
            "awayteam_towers": _as_float(away.get("@tower_kills")),
            "localteam_dragons": _as_float(local.get("@dragon_kills")),
            "awayteam_dragons": _as_float(away.get("@dragon_kills")),
            "localteam_barons": _as_float(local.get("@nashor_kills")),
            "awayteam_barons": _as_float(away.get("@nashor_kills")),
            "localteam_inhibitors": _as_float(local.get("@inhibitor_kills")),
            "awayteam_inhibitors": _as_float(away.get("@inhibitor_kills")),
        }
        row["gold_diff"] = _diff(row["localteam_gold"], row["awayteam_gold"])
        row["kills_diff"] = _diff(row["localteam_kills"], row["awayteam_kills"])
        row["towers_diff"] = _diff(row["localteam_towers"], row["awayteam_towers"])
        row["dragons_diff"] = _diff(row["localteam_dragons"], row["awayteam_dragons"])
        row["barons_diff"] = _diff(row["localteam_barons"], row["awayteam_barons"])
        rows.append(row)
    return rows


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right
