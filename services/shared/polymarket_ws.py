"""
Polymarket WebSocket manager for live orderbook updates.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, ConnectionClosedError

from shared.config import settings

logger = logging.getLogger(__name__)


@dataclass
class BookState:
    asset_id: str
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    timestamp: datetime
    hash: str | None


class PolymarketWSManager:
    """Maintain in-memory CLOB book state via WebSocket."""

    def __init__(
        self,
        ws_url: str | None = None,
        ping_interval_seconds: int | None = None,
        reconnect_base_seconds: float | None = None,
        reconnect_max_seconds: float | None = None,
        max_depth_levels: int = 50,
    ) -> None:
        self._ws_url = ws_url or settings.polymarket_ws_url
        self._ping_interval = ping_interval_seconds or settings.ws_ping_interval_seconds
        self._reconnect_base = reconnect_base_seconds or settings.ws_reconnect_base_seconds
        self._reconnect_max = reconnect_max_seconds or settings.ws_reconnect_max_seconds
        self._max_depth = max_depth_levels

        self._book_state: dict[str, BookState] = {}
        self._assets_ids: set[str] = set()
        self._subscribed_assets: set[str] = set()
        # NOTE: asyncio primitives are created lazily in run() to ensure they're
        # bound to the correct event loop (manager may be created in main thread
        # but run in a different thread with its own event loop)
        self._lock: asyncio.Lock | None = None
        self._state_lock: asyncio.Lock | None = None
        self._stop: asyncio.Event | None = None
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._has_subscribed = False
        self._last_message_ts: datetime | None = None
        self._message_count: int = 0

    async def get_state_snapshot(self) -> dict[str, BookState]:
        """Return a shallow copy of current book state."""
        async with self._state_lock:
            return dict(self._book_state)

    def is_connected(self) -> bool:
        return self._ws is not None

    async def update_subscriptions(self, assets_ids: list[str]) -> None:
        """Update desired asset subscriptions for the WS connection."""
        async with self._lock:
            desired = set(assets_ids)
            if desired == self._assets_ids:
                return
            self._assets_ids = desired
            ws = self._ws
        if ws:
            await self._send_subscribe(ws)

    async def stop(self) -> None:
        if self._stop:
            self._stop.set()

    def request_stop(self) -> None:
        if self._stop is None:
            return
        if self._loop:
            self._loop.call_soon_threadsafe(self._stop.set)
        else:
            self._stop.set()

    async def run(self) -> None:
        """Main connection loop."""
        # Create asyncio primitives in the running event loop
        # (they must be created in the same loop where they're used)
        if self._lock is None:
            self._lock = asyncio.Lock()
        if self._state_lock is None:
            self._state_lock = asyncio.Lock()
        if self._stop is None:
            self._stop = asyncio.Event()
        
        backoff = self._reconnect_base
        while not self._stop.is_set():
            try:
                await self._connect_and_listen()
                backoff = self._reconnect_base
            except Exception as exc:  # pragma: no cover - network guardrail
                logger.warning("WS connection error: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(self._reconnect_max, backoff * 2)

    async def _connect_and_listen(self) -> None:
        url = f"{self._ws_url}/ws/market"
        logger.debug("WS connecting to %s", url)
        # Disable websockets library's automatic ping/pong - we handle it at application level
        # ping_interval=None disables sending pings
        # ping_timeout=None disables waiting for pongs
        # close_timeout=10 gives time for graceful close
        async with websockets.connect(
            url,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=10,
            compression=None,  # Disable compression - can cause issues with some servers
            additional_headers={
                "Origin": "https://polymarket.com",
                "User-Agent": "Mozilla/5.0 (compatible; PolymarketBot/1.0)",
            },
        ) as ws:
            logger.info("Polymarket WS connected")
            self._ws = ws
            self._loop = asyncio.get_running_loop()
            self._has_subscribed = False
            
            # CRITICAL: Must send subscribe BEFORE any pings - server closes otherwise
            try:
                await self._send_subscribe(ws, reason="connect")
            except Exception as e:
                logger.error("WS subscribe failed: %s", e)
                raise
            ping_task = asyncio.create_task(self._ping_loop(ws))
            try:
                while not self._stop.is_set():
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        await self._handle_message(ws, message)
                    except asyncio.TimeoutError:
                        continue
            except ConnectionClosedOK:
                logger.info("WS connection closed normally")
            except ConnectionClosedError as e:
                logger.warning("WS connection closed with error: code=%s reason=%s", e.code, e.reason)
            except ConnectionClosed as e:
                logger.warning("WS connection closed: code=%s reason=%s", e.code, e.reason)
            finally:
                ping_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ping_task
                self._ws = None
            logger.info("Polymarket WS disconnected")

    async def _send_subscribe(
        self,
        ws: websockets.WebSocketClientProtocol,
        *,
        force: bool = False,
        reason: str = "update",
    ) -> None:
        async with self._lock:
            assets_ids = list(self._assets_ids)
            if not assets_ids:
                return
            # Always send subscription message - server requires it before accepting pings
            if (not force) and assets_ids and set(assets_ids) == self._subscribed_assets:
                return  # Already subscribed to these exact assets
            self._subscribed_assets = set(assets_ids)
        if reason == "connect" and not self._has_subscribed:
            payload = {"assets_ids": assets_ids, "type": "market"}
        else:
            payload = {"assets_ids": assets_ids, "operation": "subscribe"}
        await ws.send(json.dumps(payload))
        logger.info("WS subscribed to %d assets", len(assets_ids))
        self._has_subscribed = True

    async def force_resubscribe(self) -> None:
        ws = self._ws
        if not ws:
            return
        await self._send_subscribe(ws, force=True, reason="force")

    async def _ping_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while not self._stop.is_set() and not self._has_subscribed:
            await asyncio.sleep(0.1)
        # Wait before first ping to give recv loop time to start
        await asyncio.sleep(self._ping_interval)
        while not self._stop.is_set():
            try:
                pong_waiter = ws.ping()
                # If the server doesn't answer, force reconnect.
                await asyncio.wait_for(pong_waiter, timeout=max(5.0, float(self._ping_interval)))
            except Exception:
                return
            await asyncio.sleep(self._ping_interval)

    async def _handle_message(self, ws: websockets.WebSocketClientProtocol, message: str) -> None:
        # Handle ping/pong keepalive (case insensitive)
        msg_upper = message.upper() if isinstance(message, str) else ""
        if msg_upper == "PING":
            await ws.send("PONG")
            return
        if msg_upper == "PONG":
            # Server acknowledged our ping - nothing to do
            return

        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            logger.debug("WS non-JSON message: %s", message[:100] if message else message)
            return

        if isinstance(payload, list):
            # Some servers batch events as a JSON array.
            for item in payload:
                if isinstance(item, dict):
                    await self._handle_payload_dict(item)
            return

        if not isinstance(payload, dict):
            return

        await self._handle_payload_dict(payload)

    async def _handle_payload_dict(self, payload: dict[str, Any]) -> None:
        """Handle a single decoded WS event payload."""

        event_type = str(payload.get("event_type") or "").lower()
        self._last_message_ts = datetime.now(tz=timezone.utc)
        self._message_count += 1
        if event_type == "book":
            await self._handle_book(payload)
        elif event_type == "price_change":
            await self._handle_price_change(payload)
        elif event_type == "last_trade_price":
            await self._handle_last_trade_price(payload)
        elif payload.get("type") == "error":
            logger.warning("WS error message: %s", payload)

    async def _handle_book(self, payload: dict[str, Any]) -> None:
        asset_id = str(payload.get("asset_id") or "")
        if not asset_id:
            return
        bids = self._parse_levels(payload.get("bids") or payload.get("buys"), descending=True)
        asks = self._parse_levels(payload.get("asks") or payload.get("sells"), descending=False)
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        spread = None
        if best_bid is not None and best_ask is not None:
            spread = max(best_ask - best_bid, 0.0)
        timestamp = self._parse_timestamp(payload.get("timestamp"))
        book = BookState(
            asset_id=asset_id,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            bids=bids,
            asks=asks,
            timestamp=timestamp,
            hash=str(payload.get("hash") or "") or None,
        )
        async with self._state_lock:
            self._book_state[asset_id] = book

    async def _handle_price_change(self, payload: dict[str, Any]) -> None:
        changes = payload.get("price_changes") or []
        if not isinstance(changes, list):
            return
        timestamp = self._parse_timestamp(payload.get("timestamp"))
        for change in changes:
            asset_id = str(change.get("asset_id") or "")
            if not asset_id:
                continue
            async with self._state_lock:
                book = self._book_state.get(asset_id)
            if not book:
                book = BookState(
                    asset_id=asset_id,
                    best_bid=None,
                    best_ask=None,
                    spread=None,
                    bids=[],
                    asks=[],
                    timestamp=timestamp,
                    hash=None,
                )
            best_bid = _safe_float(change.get("best_bid")) or book.best_bid
            best_ask = _safe_float(change.get("best_ask")) or book.best_ask
            spread = None
            if best_bid is not None and best_ask is not None:
                spread = max(best_ask - best_bid, 0.0)
            book.best_bid = best_bid
            book.best_ask = best_ask
            book.spread = spread
            book.timestamp = timestamp
            async with self._state_lock:
                self._book_state[asset_id] = book

    async def _handle_last_trade_price(self, payload: dict[str, Any]) -> None:
        asset_id = str(payload.get("asset_id") or "")
        if not asset_id:
            return
        async with self._state_lock:
            book = self._book_state.get(asset_id)
        if not book:
            return
        book.timestamp = self._parse_timestamp(payload.get("timestamp"))
        async with self._state_lock:
            self._book_state[asset_id] = book

    def _parse_levels(self, levels: Any, descending: bool) -> list[tuple[float, float]]:
        parsed: list[tuple[float, float]] = []
        if isinstance(levels, list):
            for level in levels[: self._max_depth]:
                if not isinstance(level, dict):
                    continue
                price = _safe_float(level.get("price"))
                size = _safe_float(level.get("size"))
                if price is None or size is None:
                    continue
                parsed.append((price, size))
        parsed.sort(key=lambda item: item[0], reverse=descending)
        return parsed

    def get_message_stats(self) -> tuple[datetime | None, int]:
        return self._last_message_ts, self._message_count

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        if value is None:
            return datetime.now(tz=timezone.utc)
        try:
            ts = float(value)
            if ts > 10_000_000_000:  # ms to seconds
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (TypeError, ValueError):
            return datetime.now(tz=timezone.utc)


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

