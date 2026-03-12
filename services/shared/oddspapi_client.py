"""
OddsPapi v4 client for LoL Lead-Lag Arbitrage Bot.

Endpoints used:
- /v4/tournaments?sportId=18 — LoL leagues
- /v4/participants?sportId=18 — team names
- /v4/fixtures?tournamentId=X&from=...&to=... — upcoming matches
- /v4/odds?fixtureId=X&bookmakers=pinnacle — live odds

Cooldowns (per endpoint):
- /v4/tournaments: 1000ms
- /v4/participants: 1000ms
- /v4/fixtures: 2000ms
- /v4/odds: 500ms
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import threading

import httpx

from services.shared.config import settings

logger = logging.getLogger(__name__)


class CooldownTracker:
    """
    Global cooldown tracker for OddsPapi.
    
    Tracks both per-endpoint and global cooldowns to avoid 429 errors.
    """

    def __init__(self, global_cooldown_ms: int):
        self._last_call: dict[str, float] = {}
        self._last_global: float = 0
        self._global_cooldown_ms: int = global_cooldown_ms

    def wait(self, endpoint: str, cooldown_ms: int) -> None:
        """Wait if necessary to respect both endpoint and global cooldowns."""
        now = time.time()
        
        # Check global cooldown first
        global_elapsed_ms = (now - self._last_global) * 1000
        if global_elapsed_ms < self._global_cooldown_ms:
            sleep_ms = self._global_cooldown_ms - global_elapsed_ms
            logger.debug("Global cooldown: sleeping %.0fms", sleep_ms)
            time.sleep(sleep_ms / 1000)
            now = time.time()
        
        # Then check endpoint-specific cooldown
        last = self._last_call.get(endpoint, 0)
        elapsed_ms = (now - last) * 1000

        if elapsed_ms < cooldown_ms:
            sleep_ms = cooldown_ms - elapsed_ms
            logger.debug("Endpoint cooldown: sleeping %.0fms for %s", sleep_ms, endpoint)
            time.sleep(sleep_ms / 1000)

        # Update timestamps AFTER sleeping
        self._last_call[endpoint] = time.time()
        self._last_global = time.time()


class OddsPapiClient:
    """Client for OddsPapi v4 API focused on LoL esports."""

    MAX_RETRIES = 3
    RETRY_BACKOFF_BASE = 2.0  # seconds

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        sport_id: int | None = None,
        global_cooldown_ms: int | None = None,
    ):
        self.base_url = base_url or settings.oddspapi_base_url
        self.api_key = api_key or settings.odds_api_key
        self.sport_id = sport_id or settings.oddspapi_lol_sport_id
        self._cooldown = CooldownTracker(
            global_cooldown_ms or settings.oddspapi_global_cooldown_ms_discovery
        )
        self._client = httpx.Client(timeout=30)
        self._client_lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def _auth_params(self) -> dict[str, str]:
        """Return auth params for OddsPapi (apiKey query param)."""
        if not self.api_key:
            return {}
        return {"apiKey": self.api_key}

    def _request(
        self,
        endpoint: str,
        params: dict[str, Any],
        cooldown_ms: int,
    ) -> Any:
        """Make a GET request with cooldown handling and retry on 429."""
        url = f"{self.base_url}{endpoint}"
        all_params = {**params, **self._auth_params()}

        for attempt in range(self.MAX_RETRIES):
            # Wait for cooldown before each attempt
            self._cooldown.wait(endpoint, cooldown_ms)

            logger.debug(
                "OddsPapi request: %s params=%s (attempt %d)",
                endpoint,
                {k: v for k, v in all_params.items() if k != "apiKey"},
                attempt + 1,
            )

            with self._client_lock:
                response = self._client.get(url, params=all_params)

                if response.status_code == 429:
                    # Rate limited - exponential backoff
                    backoff = self.RETRY_BACKOFF_BASE * (2 ** attempt)
                    logger.warning(
                        "Rate limited (429) on %s, backing off %.1fs (attempt %d/%d)",
                        endpoint,
                        backoff,
                        attempt + 1,
                        self.MAX_RETRIES,
                    )
                    time.sleep(backoff)
                    continue

                response.raise_for_status()
                return response.json()

        # If we get here, we exhausted retries
        raise httpx.HTTPStatusError(
            f"Rate limited after {self.MAX_RETRIES} retries",
            request=response.request,
            response=response,
        )

    def get_tournaments(self, language: str = "en") -> list[dict]:
        """
        Get all tournaments for LoL (sportId=18).

        Returns list of:
        - tournamentId (number)
        - tournamentSlug (string)
        - tournamentName (string)
        - categorySlug (string)
        - categoryName (string)
        - futureFixtures (number)
        - upcomingFixtures (number)
        - liveFixtures (number)
        """
        data = self._request(
            "/v4/tournaments",
            {"sportId": self.sport_id, "language": language},
            settings.cooldown_tournaments_ms,
        )

        if isinstance(data, list):
            logger.info("OddsPapi: Found %d LoL tournaments", len(data))
            return data

        logger.warning("OddsPapi tournaments: unexpected response type %s", type(data))
        return []

    def get_participants(self, language: str = "en") -> dict[str, str]:
        """
        Get all participants (teams) for LoL (sportId=18).

        Returns dict of participantId -> name.
        """
        data = self._request(
            "/v4/participants",
            {"sportId": self.sport_id, "language": language},
            settings.cooldown_participants_ms,
        )

        if isinstance(data, dict):
            logger.info("OddsPapi: Found %d LoL participants", len(data))
            return data

        logger.warning("OddsPapi participants: unexpected response type %s", type(data))
        return {}

    def get_fixtures(
        self,
        tournament_id: int,
        from_date: datetime,
        to_date: datetime,
        has_odds: bool = True,
        language: str = "en",
    ) -> list[dict]:
        """
        Get fixtures for a tournament within a date range.

        Note: If only from/to provided without tournamentId, range must be < 48h.
        With tournamentId, range can be up to 10 days.

        Returns list of fixture objects with:
        - fixtureId, sportId, tournamentId
        - startTime, statusId, hasOdds
        - participant1Id, participant2Id
        - participant1Name, participant2Name
        - tournamentName, categoryName
        """
        params = {
            "tournamentId": tournament_id,
            "from": from_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hasOdds": str(has_odds).lower(),
            "language": language,
        }

        try:
            data = self._request("/v4/fixtures", params, settings.cooldown_fixtures_ms)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # 404 often means no fixtures for this tournament in the time range
                logger.info(
                    "OddsPapi: No fixtures for tournament %d (404)",
                    tournament_id,
                )
                return []
            raise

        if isinstance(data, list):
            logger.info(
                "OddsPapi: Found %d fixtures for tournament %d",
                len(data),
                tournament_id,
            )
            return data

        logger.warning("OddsPapi fixtures: unexpected response type %s", type(data))
        return []

    def get_odds(
        self,
        fixture_id: str,
        bookmakers: str = "pinnacle",
        odds_format: str = "decimal",
        verbosity: int = 3,
    ) -> dict:
        """
        Get odds for a fixture from specified bookmakers.

        Returns odds payload with:
        - fixtureId, participant1Id, participant2Id
        - statusId, startTime
        - bookmakerOdds.{bookmaker}.markets.{marketId}.outcomes
        """
        params = {
            "fixtureId": fixture_id,
            "bookmakers": bookmakers,
            "oddsFormat": odds_format,
            "verbosity": verbosity,
        }

        data = self._request("/v4/odds", params, settings.cooldown_odds_ms)

        if isinstance(data, dict):
            return data

        return {"raw": data, "fetched_at": datetime.now(tz=timezone.utc).isoformat()}

    def get_odds_by_tournaments(
        self,
        tournament_ids: list[int],
        bookmaker: str = "pinnacle",
        odds_format: str = "decimal",
        verbosity: int = 3,
    ) -> list[dict]:
        """
        Get odds for all fixtures in specified tournaments.

        Args:
            tournament_ids: List of tournament IDs.
            bookmaker: Bookmaker slug (e.g., "pinnacle").
            odds_format: decimal | american | fractional.
            verbosity: Response verbosity.
        """
        if not tournament_ids:
            return []

        params = {
            "tournamentIds": ",".join(str(t) for t in tournament_ids),
            "bookmaker": bookmaker,
            "oddsFormat": odds_format,
            "verbosity": verbosity,
        }

        data = self._request(
            "/v4/odds-by-tournaments",
            params,
            settings.cooldown_odds_by_tournaments_ms,
        )

        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]

        return []

    @staticmethod
    def extract_pinnacle_moneyline(odds_payload: dict) -> dict[str, Any]:
        """
        Extract Pinnacle moneyline odds from odds payload.

        Returns:
        {
            "home": {"price": float, "implied_prob": float, "changed_at": str},
            "away": {"price": float, "implied_prob": float, "changed_at": str},
            "draw": {...} if applicable
        }
        """
        bookmaker_odds = odds_payload.get("bookmakerOdds") or {}
        pinnacle = bookmaker_odds.get("pinnacle") or {}
        markets = pinnacle.get("markets") or {}

        candidates: list[dict[str, Any]] = []
        for market_id, market in markets.items():
            if not isinstance(market, dict):
                continue
            if OddsPapiClient._market_is_game(market, market_id):
                continue
            parsed = OddsPapiClient._parse_home_away_market(market)
            if parsed:
                candidates.append(parsed)

        if len(candidates) == 1:
            return candidates[0]

        if len(candidates) > 1:
            logger.warning(
                "OddsPapi: multiple home/away markets found; refusing to guess."
            )

        return {}

    @staticmethod
    def extract_pinnacle_game_winner(
        odds_payload: dict,
        game_number: int,
    ) -> dict[str, Any]:
        """
        Extract Pinnacle game winner odds for a specific game number.

        Only returns data if outcome IDs explicitly contain game/map markers.
        """
        bookmaker_odds = odds_payload.get("bookmakerOdds") or {}
        pinnacle = bookmaker_odds.get("pinnacle") or {}
        markets = pinnacle.get("markets") or {}

        candidates: list[dict[str, Any]] = []
        for market_id, market in markets.items():
            if not isinstance(market, dict):
                continue
            parsed = OddsPapiClient._parse_game_market(market, game_number, market_id)
            if parsed:
                candidates.append(parsed)

        if len(candidates) == 1:
            return candidates[0]

        if len(candidates) > 1:
            logger.warning(
                "OddsPapi: multiple game %s markets found; refusing to guess.",
                game_number,
            )

        return {}

    @staticmethod
    def extract_pinnacle_totals(
        odds_payload: dict,
        line_value: float | None,
        game_number: int | None = None,
    ) -> dict[str, Any]:
        """Extract Pinnacle over/under totals for a requested line.

        Returns:
        {
            "line_value": float,
            "over": {"price": float, "implied_prob": float, "changed_at": str},
            "under": {"price": float, "implied_prob": float, "changed_at": str},
        }
        """
        if line_value is None:
            return {}
        bookmaker_odds = odds_payload.get("bookmakerOdds") or {}
        pinnacle = bookmaker_odds.get("pinnacle") or {}
        markets = pinnacle.get("markets") or {}
        target = float(line_value)

        candidates: list[dict[str, Any]] = []
        for market_id, market in markets.items():
            if not isinstance(market, dict):
                continue
            parsed = OddsPapiClient._parse_totals_market(
                market=market,
                target_line=target,
                game_number=game_number,
                market_id=market_id,
            )
            if parsed:
                candidates.append(parsed)

        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            logger.warning(
                "OddsPapi: multiple totals markets found for line=%s game=%s; refusing to guess.",
                target,
                game_number,
            )
        return {}

    @staticmethod
    def _parse_home_away_market(market: dict) -> dict[str, Any]:
        """Parse a market only if it contains exact home/away selections."""
        outcomes = market.get("outcomes") or {}
        result: dict[str, Any] = {}
        invalid = False

        for outcome in outcomes.values():
            if not isinstance(outcome, dict):
                continue
            players = outcome.get("players") or {}
            player0 = players.get("0") or {}
            if not isinstance(player0, dict):
                continue
            selection = player0.get("bookmakerOutcomeId")
            price = player0.get("price")
            changed_at = player0.get("changedAt")
            player_id = player0.get("playerId")

            if selection in ("home", "away") and price is not None:
                try:
                    price_float = float(price)
                    implied_prob = 1.0 / price_float if price_float > 0 else None
                    result[str(selection)] = {
                        "price": price_float,
                        "implied_prob": implied_prob,
                        "changed_at": changed_at,
                        "player_id": player_id,
                    }
                except (ValueError, TypeError):
                    invalid = True
            elif selection:
                invalid = True

        if invalid:
            return {}
        if result.get("home") and result.get("away"):
            return result
        return {}

    @staticmethod
    def _parse_totals_market(
        market: dict,
        target_line: float,
        game_number: int | None,
        market_id: str | None = None,
    ) -> dict[str, Any]:
        outcomes = market.get("outcomes") or {}
        result: dict[str, Any] = {}
        matched_line: float | None = None
        hinted_game = OddsPapiClient._market_game_number(market, market_id)
        if game_number is not None and hinted_game is not None and hinted_game != game_number:
            return {}

        for outcome in outcomes.values():
            if not isinstance(outcome, dict):
                continue
            players = outcome.get("players") or {}
            player0 = players.get("0") or {}
            if not isinstance(player0, dict):
                continue
            selection = str(player0.get("bookmakerOutcomeId") or "")
            parsed = OddsPapiClient._parse_totals_outcome_id(selection)
            if not parsed:
                continue
            line_value, side = parsed
            if abs(line_value - target_line) > 1e-6:
                continue
            price = player0.get("price")
            changed_at = player0.get("changedAt")
            if price is None:
                continue
            try:
                price_float = float(price)
            except (TypeError, ValueError):
                return {}
            matched_line = line_value
            implied_prob = 1.0 / price_float if price_float > 0 else None
            result[side] = {
                "price": price_float,
                "implied_prob": implied_prob,
                "changed_at": changed_at,
                "player_id": player0.get("playerId"),
            }

        if result.get("over") and result.get("under"):
            result["line_value"] = matched_line
            return result
        return {}

    @staticmethod
    def _parse_totals_outcome_id(bookmaker_outcome_id: str) -> tuple[float, str] | None:
        raw = (bookmaker_outcome_id or "").strip().lower()
        match = re.match(r"^(-?\d+(?:\.\d+)?)/(over|under)$", raw)
        if not match:
            return None
        try:
            return float(match.group(1)), match.group(2)
        except ValueError:
            return None

    @staticmethod
    def _parse_game_market(
        market: dict,
        game_number: int,
        market_id: str | None = None,
    ) -> dict[str, Any]:
        """Parse a market only if it belongs to the requested game/map."""
        outcomes = market.get("outcomes") or {}
        result: dict[str, Any] = {}
        required_home = {f"game{game_number}/home", f"map{game_number}/home"}
        required_away = {f"game{game_number}/away", f"map{game_number}/away"}
        hinted_game = OddsPapiClient._market_game_number(market, market_id)
        game_hint = hinted_game == game_number

        for outcome in outcomes.values():
            if not isinstance(outcome, dict):
                continue
            players = outcome.get("players") or {}
            player0 = players.get("0") or {}
            if not isinstance(player0, dict):
                continue
            selection = str(player0.get("bookmakerOutcomeId") or "")
            price = player0.get("price")
            changed_at = player0.get("changedAt")
            player_id = player0.get("playerId")

            if selection in required_home and price is not None:
                try:
                    price_float = float(price)
                    implied_prob = 1.0 / price_float if price_float > 0 else None
                    result["home"] = {
                        "price": price_float,
                        "implied_prob": implied_prob,
                        "changed_at": changed_at,
                        "player_id": player_id,
                    }
                except (ValueError, TypeError):
                    return {}
            elif selection in required_away and price is not None:
                try:
                    price_float = float(price)
                    implied_prob = 1.0 / price_float if price_float > 0 else None
                    result["away"] = {
                        "price": price_float,
                        "implied_prob": implied_prob,
                        "changed_at": changed_at,
                        "player_id": player_id,
                    }
                except (ValueError, TypeError):
                    return {}
            elif game_hint and selection in {"home", "away"} and price is not None:
                try:
                    price_float = float(price)
                    implied_prob = 1.0 / price_float if price_float > 0 else None
                    result[selection] = {
                        "price": price_float,
                        "implied_prob": implied_prob,
                        "changed_at": changed_at,
                        "player_id": player_id,
                    }
                except (ValueError, TypeError):
                    return {}

        if result.get("home") and result.get("away"):
            return result
        return {}

    @staticmethod
    def _market_game_number(market: dict, market_id: str | None = None) -> int | None:
        values = [market_id, market.get("bookmakerMarketId")]
        for value in values:
            if not value:
                continue
            s = str(value)
            # Path-style: .../0/moneyline (match), .../1/moneyline (game 1), etc.
            path_match = re.search(r"/(\d)/(?:moneyline|totals|spreads)", s)
            if path_match:
                try:
                    return int(path_match.group(1))
                except ValueError:
                    pass
            normalized = "".join(ch.lower() for ch in s if ch.isalnum())
            match = re.search(r"(map|game)(\d)", normalized)
            if match:
                try:
                    return int(match.group(2))
                except ValueError:
                    continue
        return None

    @staticmethod
    def _market_is_game(market: dict, market_id: str | None = None) -> bool:
        gn = OddsPapiClient._market_game_number(market, market_id)
        return gn is not None and gn != 0

    @staticmethod
    def parse_fixture_status(status_id: int | None) -> str:
        """Convert OddsPapi statusId to our status string.

        NOTE: statusId is unreliable for some esports fixtures.
        Prefer ``infer_fixture_status`` which also checks trueStartTime/trueEndTime.
        """
        status_map = {
            0: "upcoming",  # Not started
            1: "live",  # Live
            2: "finished",  # Finished
            3: "cancelled",  # Cancelled
        }
        return status_map.get(status_id, "upcoming") if status_id is not None else "upcoming"

    @staticmethod
    def infer_fixture_status(payload: dict) -> str:
        """Infer fixture status using trueStartTime/trueEndTime, falling back to statusId.

        OddsPapi sometimes keeps statusId=0 even after a match has started.
        The trueStartTime/trueEndTime fields are the authoritative signal.
        """
        status_id = payload.get("statusId")
        true_start = payload.get("trueStartTime")
        true_end = payload.get("trueEndTime")

        # Explicit finished/cancelled via statusId takes priority
        if status_id in (2, 3):
            return "finished" if status_id == 2 else "cancelled"

        # trueEndTime present → finished
        if true_end:
            return "finished"

        # trueStartTime present, no end → live
        if true_start:
            return "live"

        # Fall back to statusId
        return OddsPapiClient.parse_fixture_status(status_id)

    @staticmethod
    def is_inplay_from_payload(payload: dict) -> bool:
        """Return True if the payload indicates the fixture is currently in-play.

        Uses trueStartTime/trueEndTime as the primary signal.
        trueEndTime being set always means the match is over, even if statusId lags.
        """
        true_end = payload.get("trueEndTime")
        if true_end:
            return False
        true_start = payload.get("trueStartTime")
        if true_start:
            return True
        return payload.get("statusId") == 1

    @staticmethod
    def get_pinnacle_p_ref_changed_at(
        odds_payload: dict,
        market_type: str,
        game_number: int | None = None,
    ) -> datetime | None:
        """Return the latest Pinnacle update time for the market used for P_ref (for staleness checks).

        Uses home/away changed_at from match moneyline or game winner market; fallback to payload updatedAt.
        Returns timezone-aware UTC datetime, or None if no timestamp available.
        """
        def _parse_iso(s: str | None) -> datetime | None:
            if not s or not isinstance(s, str):
                return None
            s = s.strip().replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, TypeError):
                return None

        if market_type == "match_winner":
            parsed = OddsPapiClient.extract_pinnacle_moneyline(odds_payload)
        elif market_type == "game_winner" and game_number is not None:
            parsed = OddsPapiClient.extract_pinnacle_game_winner(odds_payload, game_number)
            if not parsed:
                parsed = OddsPapiClient.extract_pinnacle_moneyline(odds_payload)
        else:
            parsed = OddsPapiClient.extract_pinnacle_moneyline(odds_payload)

        if isinstance(parsed, dict):
            home = parsed.get("home") if isinstance(parsed.get("home"), dict) else {}
            away = parsed.get("away") if isinstance(parsed.get("away"), dict) else {}
            t1 = _parse_iso(home.get("changed_at"))
            t2 = _parse_iso(away.get("changed_at"))
            if t1 and t2:
                return max(t1, t2)
            if t1:
                return t1
            if t2:
                return t2

        fallback = odds_payload.get("updatedAt")
        return _parse_iso(fallback)


class AsyncCooldownTracker:
    """
    Async cooldown tracker for OddsPapi.

    Serializes waits to respect global + per-endpoint cooldowns.
    """

    def __init__(self, global_cooldown_ms: int):
        self._last_call: dict[str, float] = {}
        self._last_global: float = 0
        self._global_cooldown_ms: int = global_cooldown_ms
        self._lock = asyncio.Lock()

    async def wait(self, endpoint: str, cooldown_ms: int) -> None:
        async with self._lock:
            now = time.time()
            global_elapsed_ms = (now - self._last_global) * 1000
            if global_elapsed_ms < self._global_cooldown_ms:
                sleep_ms = self._global_cooldown_ms - global_elapsed_ms
                logger.debug("Global cooldown: sleeping %.0fms", sleep_ms)
                await asyncio.sleep(sleep_ms / 1000)
                now = time.time()

            last = self._last_call.get(endpoint, 0)
            elapsed_ms = (now - last) * 1000
            if elapsed_ms < cooldown_ms:
                sleep_ms = cooldown_ms - elapsed_ms
                logger.debug("Endpoint cooldown: sleeping %.0fms for %s", sleep_ms, endpoint)
                await asyncio.sleep(sleep_ms / 1000)

            self._last_call[endpoint] = time.time()
            self._last_global = time.time()


class AsyncOddsPapiClient:
    """Async client for OddsPapi v4 API focused on LoL esports."""

    MAX_RETRIES = 3
    RETRY_BACKOFF_BASE = 2.0  # seconds

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        sport_id: int | None = None,
        global_cooldown_ms: int | None = None,
    ):
        self.base_url = base_url or settings.oddspapi_base_url
        self.api_key = api_key or settings.odds_api_key
        self.sport_id = sport_id or settings.oddspapi_lol_sport_id
        self._cooldown = AsyncCooldownTracker(
            global_cooldown_ms or settings.oddspapi_global_cooldown_ms_discovery
        )
        self._client = httpx.AsyncClient(timeout=30)

    async def close(self) -> None:
        await self._client.aclose()

    def _auth_params(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"apiKey": self.api_key}

    async def _request(
        self,
        endpoint: str,
        params: dict[str, Any],
        cooldown_ms: int,
    ) -> Any:
        url = f"{self.base_url}{endpoint}"
        all_params = {**params, **self._auth_params()}

        for attempt in range(self.MAX_RETRIES):
            await self._cooldown.wait(endpoint, cooldown_ms)

            logger.debug(
                "OddsPapi request: %s params=%s (attempt %d)",
                endpoint,
                {k: v for k, v in all_params.items() if k != "apiKey"},
                attempt + 1,
            )

            response = await self._client.get(url, params=all_params)

            if response.status_code == 429:
                backoff = self.RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Rate limited (429) on %s, backing off %.1fs (attempt %d/%d)",
                    endpoint,
                    backoff,
                    attempt + 1,
                    self.MAX_RETRIES,
                )
                await asyncio.sleep(backoff)
                continue

            response.raise_for_status()
            return response.json()

        raise httpx.HTTPStatusError(
            f"Rate limited after {self.MAX_RETRIES} retries",
            request=response.request,
            response=response,
        )

    async def get_tournaments(self, language: str = "en") -> list[dict]:
        data = await self._request(
            "/v4/tournaments",
            {"sportId": self.sport_id, "language": language},
            settings.cooldown_tournaments_ms,
        )

        if isinstance(data, list):
            logger.info("OddsPapi: Found %d LoL tournaments", len(data))
            return data

        logger.warning("OddsPapi tournaments: unexpected response type %s", type(data))
        return []

    async def get_participants(self, language: str = "en") -> dict[str, str]:
        data = await self._request(
            "/v4/participants",
            {"sportId": self.sport_id, "language": language},
            settings.cooldown_participants_ms,
        )

        if isinstance(data, dict):
            logger.info("OddsPapi: Found %d LoL participants", len(data))
            return data

        logger.warning("OddsPapi participants: unexpected response type %s", type(data))
        return {}

    async def get_fixtures(
        self,
        tournament_id: int,
        from_date: datetime,
        to_date: datetime,
        has_odds: bool = True,
        language: str = "en",
    ) -> list[dict]:
        params = {
            "tournamentId": tournament_id,
            "from": from_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": to_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hasOdds": str(has_odds).lower(),
            "language": language,
        }

        try:
            data = await self._request("/v4/fixtures", params, settings.cooldown_fixtures_ms)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.info(
                    "OddsPapi: No fixtures for tournament %d (404)",
                    tournament_id,
                )
                return []
            raise

        if isinstance(data, list):
            logger.info(
                "OddsPapi: Found %d fixtures for tournament %d",
                len(data),
                tournament_id,
            )
            return data

        logger.warning("OddsPapi fixtures: unexpected response type %s", type(data))
        return []

    async def get_odds(
        self,
        fixture_id: str,
        bookmakers: str = "pinnacle",
        odds_format: str = "decimal",
        verbosity: int = 3,
    ) -> dict:
        params = {
            "fixtureId": fixture_id,
            "bookmakers": bookmakers,
            "oddsFormat": odds_format,
            "verbosity": verbosity,
        }

        data = await self._request("/v4/odds", params, settings.cooldown_odds_ms)

        if isinstance(data, dict):
            return data

        return {"raw": data, "fetched_at": datetime.now(tz=timezone.utc).isoformat()}

    async def get_odds_by_tournaments(
        self,
        tournament_ids: list[int],
        bookmaker: str = "pinnacle",
        odds_format: str = "decimal",
        verbosity: int = 3,
    ) -> list[dict]:
        if not tournament_ids:
            return []

        params = {
            "tournamentIds": ",".join(str(t) for t in tournament_ids),
            "bookmaker": bookmaker,
            "oddsFormat": odds_format,
            "verbosity": verbosity,
        }

        data = await self._request(
            "/v4/odds-by-tournaments",
            params,
            settings.cooldown_odds_by_tournaments_ms,
        )

        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            return data["data"]

        return []


# Module-level convenience instance
_client: OddsPapiClient | None = None


def get_client() -> OddsPapiClient:
    """Get or create the default OddsPapi client."""
    global _client
    if _client is None:
        _client = OddsPapiClient()
    return _client
