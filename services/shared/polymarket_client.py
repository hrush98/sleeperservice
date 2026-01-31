"""
Polymarket client for LoL Lead-Lag Arbitrage Bot.

Uses two APIs:
1. Gamma API (https://gamma-api.polymarket.com) for discovery:
   - /sports — LoL leagues (series_id)
   - /teams?league=... — team names
   - /events?series_id=X&tag_id=100639 — upcoming matches
   - /markets — market details

2. CLOB API (https://clob.polymarket.com) for live prices:
   - /book?token_id=X — orderbook for a token

Polymarket game bets tag_id: 100639
"""

import asyncio
import logging
from typing import Any

import threading

import httpx

from shared.config import settings

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Client for Polymarket Gamma and CLOB APIs focused on LoL esports."""

    def __init__(
        self,
        gamma_url: str | None = None,
        clob_url: str | None = None,
        api_key: str | None = None,
    ):
        self.gamma_url = gamma_url or settings.polymarket_base_url
        self.clob_url = clob_url or settings.polymarket_clob_url
        self.api_key = api_key or settings.poly_api_key
        self._client = httpx.Client(timeout=30)
        self._client_lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def _gamma_request(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to Gamma API."""
        url = f"{self.gamma_url}{endpoint}"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        logger.debug("Polymarket Gamma request: %s params=%s", endpoint, params)

        with self._client_lock:
            response = self._client.get(url, params=params or {}, headers=headers)
            response.raise_for_status()
            return response.json()

    def _clob_request(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to CLOB API."""
        url = f"{self.clob_url}{endpoint}"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        logger.debug("Polymarket CLOB request: %s params=%s", endpoint, params)

        with self._client_lock:
            response = self._client.get(url, params=params or {}, headers=headers)
            response.raise_for_status()
            return response.json()

    def _clob_request_post(self, endpoint: str, payload: list[dict[str, Any]]) -> Any:
        """Make a POST request to CLOB API with JSON payload."""
        url = f"{self.clob_url}{endpoint}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        logger.debug("Polymarket CLOB POST request: %s items=%d", endpoint, len(payload))

        with self._client_lock:
            response = self._client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    def get_sports(self) -> list[dict]:
        """
        Get sports metadata including LoL leagues.

        Returns list of sports/leagues with:
        - series_id, name, slug, etc.
        """
        try:
            data = self._gamma_request("/sports")

            if isinstance(data, list):
                logger.info("Polymarket: Found %d sports entries", len(data))
                return data
            if isinstance(data, dict):
                # Some responses wrap data in a dict
                if "data" in data:
                    return data["data"]
                return [data]

            return []
        except httpx.HTTPError as e:
            logger.error("Failed to fetch Polymarket sports: %s", e)
            return []

    def get_teams(self, league: str | None = None) -> list[dict]:
        """
        Get team mappings.

        Args:
            league: Optional league filter (e.g., "LCK", "LPL")

        Returns list of teams with:
        - teamId, name, abbreviation, etc.
        """
        params = {}
        if league:
            params["league"] = league

        try:
            data = self._gamma_request("/teams", params)

            if isinstance(data, list):
                logger.info("Polymarket: Found %d teams", len(data))
                return data
            if isinstance(data, dict) and "data" in data:
                return data["data"]

            return []
        except httpx.HTTPError as e:
            logger.error("Failed to fetch Polymarket teams: %s", e)
            return []

    def get_events(
        self,
        series_id: str | None = None,
        tag_id: int | None = None,
        active: bool = True,
        closed: bool | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        Get events (with nested markets) for discovery.

        Args:
            series_id: Filter by series/league ID
            tag_id: Filter by tag ID (100639 for game bets)
            active: Only active events
            closed: Filter by closed status (False = open events only)
            limit: Max results per page

        Returns list of events with nested markets.
        """
        params: dict[str, Any] = {"limit": limit}
        if series_id:
            params["series_id"] = series_id
        if tag_id:
            params["tag_id"] = tag_id
        if active:
            params["active"] = "true"
        if closed is not None:
            params["closed"] = "true" if closed else "false"

        try:
            data = self._gamma_request("/events", params)

            if isinstance(data, list):
                logger.info("Polymarket: Found %d events", len(data))
                return data
            if isinstance(data, dict) and "data" in data:
                return data["data"]

            return []
        except httpx.HTTPError as e:
            logger.error("Failed to fetch Polymarket events: %s", e)
            return []

    def get_markets(
        self,
        tag_id: int | None = None,
        active: bool = True,
        limit: int = 100,
    ) -> list[dict]:
        """
        Get markets directly.

        Args:
            tag_id: Filter by tag ID (100639 for game bets)
            active: Only active markets
            limit: Max results per page

        Returns list of market objects.
        """
        params: dict[str, Any] = {"limit": limit}
        if tag_id:
            params["tag_id"] = tag_id
        if active:
            params["active"] = "true"

        all_markets: list[dict] = []
        offset = 0

        try:
            while True:
                params["offset"] = offset
                data = self._gamma_request("/markets", params)

                batch = []
                if isinstance(data, list):
                    batch = data
                elif isinstance(data, dict):
                    if "data" in data:
                        batch = data["data"]
                    elif "markets" in data:
                        batch = data["markets"]

                if not batch:
                    break

                all_markets.extend(batch)
                if len(batch) < limit:
                    break

                offset += limit

            logger.info("Polymarket: Found %d markets total", len(all_markets))
            return all_markets

        except httpx.HTTPError as e:
            logger.error("Failed to fetch Polymarket markets: %s", e)
            return all_markets

    def get_market_by_id(self, market_id: str) -> dict | None:
        """Get a single market by ID."""
        try:
            data = self._gamma_request(f"/markets/{market_id}")
            if isinstance(data, dict):
                return data
            return None
        except httpx.HTTPError as e:
            logger.error("Failed to fetch market %s: %s", market_id, e)
            return None

    def get_clob_orderbook(self, token_id: str) -> dict:
        """
        Get CLOB orderbook for a token.

        The CLOB orderbook provides real bid/ask prices for execution.

        Args:
            token_id: The token ID (typically from market.clobTokenIds)

        Returns:
        {
            "bids": [{"price": float, "size": float}, ...],
            "asks": [{"price": float, "size": float}, ...],
            "best_bid": float | None,
            "best_ask": float | None,
            "mid": float | None
        }
        """
        try:
            data = self._clob_request("/book", {"token_id": token_id})

            result = {
                "bids": [],
                "asks": [],
                "best_bid": None,
                "best_ask": None,
                "mid": None,
                "raw": data,
            }

            # Parse bids (highest price first)
            if isinstance(data.get("bids"), list):
                result["bids"] = [
                    {"price": float(b.get("price", 0)), "size": float(b.get("size", 0))}
                    for b in data["bids"]
                    if b.get("price") is not None
                ]
                if result["bids"]:
                    result["best_bid"] = max(b["price"] for b in result["bids"])

            # Parse asks (lowest price first)
            if isinstance(data.get("asks"), list):
                result["asks"] = [
                    {"price": float(a.get("price", 0)), "size": float(a.get("size", 0))}
                    for a in data["asks"]
                    if a.get("price") is not None
                ]
                if result["asks"]:
                    result["best_ask"] = min(a["price"] for a in result["asks"])

            # Calculate mid price
            if result["best_bid"] and result["best_ask"]:
                result["mid"] = (result["best_bid"] + result["best_ask"]) / 2

            return result

        except httpx.HTTPError as e:
            logger.error("Failed to fetch CLOB orderbook for %s: %s", token_id, e)
            return {
                "bids": [],
                "asks": [],
                "best_bid": None,
                "best_ask": None,
                "mid": None,
                "error": str(e),
            }

    def get_orderbooks_batch(self, token_ids: list[str]) -> dict[str, dict]:
        """
        Get orderbooks for multiple tokens.

        Args:
            token_ids: List of token IDs

        Returns:
            Dict mapping token_id -> orderbook result
        """
        if not token_ids:
            return {}

        payload = [{"token_id": token_id} for token_id in token_ids]
        try:
            data = self._clob_request_post("/books", payload)
        except httpx.HTTPError as e:
            logger.error("Failed to fetch CLOB orderbooks batch: %s", e)
            return {token_id: self.get_clob_orderbook(token_id) for token_id in token_ids}

        results: dict[str, dict] = {}
        if isinstance(data, list):
            for item in data:
                token_id = str(item.get("asset_id") or item.get("token_id") or "")
                if not token_id:
                    continue
                results[token_id] = self._parse_book_item(item)

        # Fallback for any missing tokens
        for token_id in token_ids:
            if token_id not in results:
                results[token_id] = self.get_clob_orderbook(token_id)

        return results

    @staticmethod
    def _parse_book_item(data: dict) -> dict:
        """Parse a single /book or /books item into a normalized orderbook dict."""
        result = {
            "bids": [],
            "asks": [],
            "best_bid": None,
            "best_ask": None,
            "mid": None,
            "raw": data,
        }

        if isinstance(data.get("bids"), list):
            result["bids"] = [
                {"price": float(b.get("price", 0)), "size": float(b.get("size", 0))}
                for b in data["bids"]
                if b.get("price") is not None
            ]
            if result["bids"]:
                result["best_bid"] = max(b["price"] for b in result["bids"])

        if isinstance(data.get("asks"), list):
            result["asks"] = [
                {"price": float(a.get("price", 0)), "size": float(a.get("size", 0))}
                for a in data["asks"]
                if a.get("price") is not None
            ]
            if result["asks"]:
                result["best_ask"] = min(a["price"] for a in result["asks"])

        if result["best_bid"] is not None and result["best_ask"] is not None:
            result["mid"] = (result["best_bid"] + result["best_ask"]) / 2

        return result

    @staticmethod
    def extract_lol_markets(markets: list[dict]) -> list[dict]:
        """
        Filter markets to only LoL-related ones.

        Looks for markets with LoL-related tags or titles.
        """
        lol_keywords = ["lol", "league of legends", "lck", "lpl", "lec", "lcs", "lta", "lcp"]

        filtered = []
        for market in markets:
            title = (market.get("question") or market.get("title") or "").lower()
            tags = market.get("tags") or []
            tag_labels = [t.get("label", "").lower() for t in tags if isinstance(t, dict)]

            # Check if any LoL keyword appears
            is_lol = any(kw in title for kw in lol_keywords) or any(
                kw in " ".join(tag_labels) for kw in lol_keywords
            )

            if is_lol:
                filtered.append(market)

        logger.info("Polymarket: Filtered to %d LoL markets from %d total", len(filtered), len(markets))
        return filtered

    @staticmethod
    def parse_market_teams(market: dict) -> tuple[str | None, str | None]:
        """
        Try to extract team names from a market.

        Returns (team_a, team_b) or (None, None) if can't parse.
        """
        # Common patterns: "Team A vs Team B" or outcomes list
        question = market.get("question") or market.get("title") or ""

        # Try "X vs Y" pattern
        if " vs " in question.lower():
            parts = question.lower().split(" vs ")
            if len(parts) == 2:
                # Clean up
                team_a = parts[0].strip().split(":")[-1].strip()
                team_b = parts[1].strip().split("?")[0].strip()
                return team_a, team_b

        # Try outcomes
        outcomes = market.get("outcomes") or []
        if isinstance(outcomes, str):
            try:
                import json
                outcomes = json.loads(outcomes)
            except (json.JSONDecodeError, TypeError):
                outcomes = []

        if len(outcomes) >= 2:
            return str(outcomes[0]), str(outcomes[1])

        return None, None


class AsyncPolymarketClient:
    """Async client for Polymarket Gamma and CLOB APIs focused on LoL esports."""

    def __init__(
        self,
        gamma_url: str | None = None,
        clob_url: str | None = None,
        api_key: str | None = None,
    ):
        self.gamma_url = gamma_url or settings.polymarket_base_url
        self.clob_url = clob_url or settings.polymarket_clob_url
        self.api_key = api_key or settings.poly_api_key
        self._client = httpx.AsyncClient(timeout=30)
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        await self._client.aclose()

    async def _gamma_request(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.gamma_url}{endpoint}"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        logger.debug("Polymarket Gamma request: %s params=%s", endpoint, params)

        async with self._lock:
            response = await self._client.get(url, params=params or {}, headers=headers)
            response.raise_for_status()
            return response.json()

    async def _clob_request(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.clob_url}{endpoint}"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        logger.debug("Polymarket CLOB request: %s params=%s", endpoint, params)

        async with self._lock:
            response = await self._client.get(url, params=params or {}, headers=headers)
            response.raise_for_status()
            return response.json()

    async def _clob_request_post(self, endpoint: str, payload: list[dict[str, Any]]) -> Any:
        url = f"{self.clob_url}{endpoint}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        logger.debug("Polymarket CLOB POST request: %s items=%d", endpoint, len(payload))

        async with self._lock:
            response = await self._client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    async def get_sports(self) -> list[dict]:
        try:
            data = await self._gamma_request("/sports")

            if isinstance(data, list):
                logger.info("Polymarket: Found %d sports entries", len(data))
                return data
            if isinstance(data, dict):
                if "data" in data:
                    return data["data"]
                return [data]

            return []
        except httpx.HTTPError as e:
            logger.error("Failed to fetch Polymarket sports: %s", e)
            return []

    async def get_teams(self, league: str | None = None) -> list[dict]:
        params = {}
        if league:
            params["league"] = league

        try:
            data = await self._gamma_request("/teams", params)

            if isinstance(data, list):
                logger.info("Polymarket: Found %d teams", len(data))
                return data
            if isinstance(data, dict) and "data" in data:
                return data["data"]

            return []
        except httpx.HTTPError as e:
            logger.error("Failed to fetch Polymarket teams: %s", e)
            return []

    async def get_events(
        self,
        series_id: str | None = None,
        tag_id: int | None = None,
        active: bool = True,
        closed: bool | None = None,
        limit: int = 100,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if series_id:
            params["series_id"] = series_id
        if tag_id:
            params["tag_id"] = tag_id
        if active:
            params["active"] = "true"
        if closed is not None:
            params["closed"] = "true" if closed else "false"

        try:
            data = await self._gamma_request("/events", params)

            if isinstance(data, list):
                logger.info("Polymarket: Found %d events", len(data))
                return data
            if isinstance(data, dict) and "data" in data:
                return data["data"]

            return []
        except httpx.HTTPError as e:
            logger.error("Failed to fetch Polymarket events: %s", e)
            return []

    async def get_markets(
        self,
        tag_id: int | None = None,
        active: bool = True,
        limit: int = 100,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if tag_id:
            params["tag_id"] = tag_id
        if active:
            params["active"] = "true"

        all_markets: list[dict] = []
        offset = 0

        try:
            while True:
                params["offset"] = offset
                data = await self._gamma_request("/markets", params)

                batch = []
                if isinstance(data, list):
                    batch = data
                elif isinstance(data, dict):
                    if "data" in data:
                        batch = data["data"]
                    elif "markets" in data:
                        batch = data["markets"]

                if not batch:
                    break

                all_markets.extend(batch)
                if len(batch) < limit:
                    break

                offset += limit

            logger.info("Polymarket: Found %d markets total", len(all_markets))
            return all_markets

        except httpx.HTTPError as e:
            logger.error("Failed to fetch Polymarket markets: %s", e)
            return all_markets

    async def get_market_by_id(self, market_id: str) -> dict | None:
        try:
            data = await self._gamma_request(f"/markets/{market_id}")
            if isinstance(data, dict):
                return data
            return None
        except httpx.HTTPError as e:
            logger.error("Failed to fetch market %s: %s", market_id, e)
            return None

    async def get_clob_orderbook(self, token_id: str) -> dict:
        try:
            data = await self._clob_request("/book", {"token_id": token_id})

            result = {
                "bids": [],
                "asks": [],
                "best_bid": None,
                "best_ask": None,
                "mid": None,
                "raw": data,
            }

            if isinstance(data.get("bids"), list):
                result["bids"] = [
                    {"price": float(b.get("price", 0)), "size": float(b.get("size", 0))}
                    for b in data["bids"]
                    if b.get("price") is not None
                ]
                if result["bids"]:
                    result["best_bid"] = max(b["price"] for b in result["bids"])

            if isinstance(data.get("asks"), list):
                result["asks"] = [
                    {"price": float(a.get("price", 0)), "size": float(a.get("size", 0))}
                    for a in data["asks"]
                    if a.get("price") is not None
                ]
                if result["asks"]:
                    result["best_ask"] = min(a["price"] for a in result["asks"])

            if result["best_bid"] and result["best_ask"]:
                result["mid"] = (result["best_bid"] + result["best_ask"]) / 2

            return result

        except httpx.HTTPError as e:
            logger.error("Failed to fetch CLOB orderbook for %s: %s", token_id, e)
            return {
                "bids": [],
                "asks": [],
                "best_bid": None,
                "best_ask": None,
                "mid": None,
                "error": str(e),
            }

    async def get_orderbooks_batch(self, token_ids: list[str]) -> dict[str, dict]:
        if not token_ids:
            return {}

        payload = [{"token_id": token_id} for token_id in token_ids]
        try:
            data = await self._clob_request_post("/books", payload)
        except httpx.HTTPError as e:
            logger.error("Failed to fetch CLOB orderbooks batch: %s", e)
            results: dict[str, dict] = {}
            for token_id in token_ids:
                results[token_id] = await self.get_clob_orderbook(token_id)
            return results

        results: dict[str, dict] = {}
        if isinstance(data, list):
            for item in data:
                token_id = str(item.get("asset_id") or item.get("token_id") or "")
                if not token_id:
                    continue
                results[token_id] = self._parse_book_item(item)

        for token_id in token_ids:
            if token_id not in results:
                results[token_id] = await self.get_clob_orderbook(token_id)

        return results


# Module-level convenience instance
_client: PolymarketClient | None = None


def get_client() -> PolymarketClient:
    """Get or create the default Polymarket client."""
    global _client
    if _client is None:
        _client = PolymarketClient()
    return _client

