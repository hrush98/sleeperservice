"""
Polymarket WebSocket manager for authenticated user events (orders/trades).
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
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

from shared.config import settings

logger = logging.getLogger(__name__)


@dataclass
class OrderEvent:
    order_id: str
    asset_id: str
    side: str
    price: str
    original_size: str
    size_matched: str
    status: str  # PLACEMENT / UPDATE / CANCELLATION
    timestamp: str
    raw: dict


@dataclass
class TradeEvent:
    trade_id: str
    asset_id: str
    taker_order_id: str
    side: str
    price: str
    size: str
    status: str  # MATCHED / MINED / CONFIRMED / RETRYING / FAILED
    timestamp: str
    raw: dict


class PolymarketUserWSManager:
    """Maintain in-memory order/trade state via authenticated user WS."""

    def __init__(
        self,
        *,
        auth: dict[str, str],
        ws_url: str | None = None,
        ping_interval_seconds: int | None = None,
        reconnect_base_seconds: float | None = None,
        reconnect_max_seconds: float | None = None,
    ) -> None:
        self._auth = dict(auth)
        self._ws_url = ws_url or settings.polymarket_ws_url
        self._ping_interval = ping_interval_seconds or settings.ws_ping_interval_seconds
        self._reconnect_base = reconnect_base_seconds or settings.ws_reconnect_base_seconds
        self._reconnect_max = reconnect_max_seconds or settings.ws_reconnect_max_seconds

        self._order_events: dict[str, OrderEvent] = {}
        self._trade_events: dict[str, TradeEvent] = {}
        self._condition_ids: set[str] = set()
        self._subscribed_conditions: set[str] = set()

        self._lock: asyncio.Lock | None = None
        self._state_lock: asyncio.Lock | None = None
        self._stop: asyncio.Event | None = None
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._has_subscribed = False
        self._placement_queue: asyncio.Queue[OrderEvent] | None = None

    def is_connected(self) -> bool:
        return self._ws is not None

    async def get_order(self, order_id: str) -> OrderEvent | None:
        if not self._state_lock:
            return None
        async with self._state_lock:
            return self._order_events.get(order_id)

    async def get_trades_for_order(self, order_id: str) -> list[TradeEvent]:
        if not self._state_lock:
            return []
        async with self._state_lock:
            return [t for t in self._trade_events.values() if t.taker_order_id == order_id]

    async def get_latest_order_by_asset(self, asset_id: str, side: str) -> OrderEvent | None:
        if not self._state_lock:
            return None
        desired_asset = str(asset_id)
        desired_side = str(side).upper()
        async with self._state_lock:
            candidates = [
                order
                for order in self._order_events.values()
                if order.asset_id == desired_asset and order.side == desired_side
            ]
        if not candidates:
            return None
        candidates.sort(key=lambda order: self._parse_timestamp(order.timestamp), reverse=True)
        return candidates[0]

    async def update_subscriptions(self, condition_ids: list[str]) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            desired = set(condition_ids)
            if desired == self._condition_ids:
                return
            self._condition_ids = desired
            ws = self._ws
        if ws:
            await self._send_subscribe(ws)

    async def wait_for_placement(
        self,
        *,
        asset_id: str,
        side: str,
        timeout: float,
    ) -> OrderEvent | None:
        if not self._placement_queue:
            return None
        deadline = asyncio.get_running_loop().time() + timeout
        desired_asset = str(asset_id)
        desired_side = str(side).upper()
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                event = await asyncio.wait_for(self._placement_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if event.asset_id == desired_asset and event.side == desired_side:
                return event

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
        if self._lock is None:
            self._lock = asyncio.Lock()
        if self._state_lock is None:
            self._state_lock = asyncio.Lock()
        if self._stop is None:
            self._stop = asyncio.Event()
        if self._placement_queue is None:
            self._placement_queue = asyncio.Queue()

        backoff = self._reconnect_base
        while not self._stop.is_set():
            try:
                await self._connect_and_listen()
                backoff = self._reconnect_base
            except Exception as exc:  # pragma: no cover - network guardrail
                logger.warning("User WS connection error: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(self._reconnect_max, backoff * 2)

    async def _connect_and_listen(self) -> None:
        url = f"{self._ws_url}/ws/user"
        logger.debug("User WS connecting to %s", url)
        async with websockets.connect(
            url,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=10,
            compression=None,
            additional_headers={
                "Origin": "https://polymarket.com",
                "User-Agent": "Mozilla/5.0 (compatible; PolymarketBot/1.0)",
            },
        ) as ws:
            logger.info("Polymarket User WS connected")
            self._ws = ws
            self._loop = asyncio.get_running_loop()
            self._has_subscribed = False
            try:
                await self._send_subscribe(ws, reason="connect")
            except Exception as exc:
                logger.error("User WS subscribe failed: %s", exc)
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
                logger.info("User WS connection closed normally")
            except ConnectionClosedError as exc:
                logger.warning(
                    "User WS connection closed with error: code=%s reason=%s",
                    exc.code,
                    exc.reason,
                )
            except ConnectionClosed as exc:
                logger.warning(
                    "User WS connection closed: code=%s reason=%s",
                    exc.code,
                    exc.reason,
                )
            finally:
                ping_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ping_task
                self._ws = None
            logger.info("Polymarket User WS disconnected")

    async def _send_subscribe(
        self,
        ws: websockets.WebSocketClientProtocol,
        *,
        force: bool = False,
        reason: str = "update",
    ) -> None:
        async with self._lock:
            condition_ids = list(self._condition_ids)
            if not condition_ids:
                return
            if (not force) and condition_ids and set(condition_ids) == self._subscribed_conditions:
                return
            self._subscribed_conditions = set(condition_ids)
        if reason == "connect" and not self._has_subscribed:
            payload = {
                "markets": condition_ids,
                "type": "user",
                "auth": self._auth,
            }
        else:
            payload = {
                "markets": condition_ids,
                "operation": "subscribe",
                "auth": self._auth,
            }
        await ws.send(json.dumps(payload))
        logger.info("User WS subscribed to %d markets", len(condition_ids))
        self._has_subscribed = True

    async def _ping_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        while not self._stop.is_set() and not self._has_subscribed:
            await asyncio.sleep(0.1)
        await asyncio.sleep(self._ping_interval)
        while not self._stop.is_set():
            try:
                pong_waiter = ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=max(5.0, float(self._ping_interval)))
            except Exception:
                return
            await asyncio.sleep(self._ping_interval)

    async def _handle_message(self, ws: websockets.WebSocketClientProtocol, message: str) -> None:
        msg_upper = message.upper() if isinstance(message, str) else ""
        if msg_upper == "PING":
            await ws.send("PONG")
            return
        if msg_upper == "PONG":
            return
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            logger.debug("User WS non-JSON message: %s", message[:100] if message else message)
            return
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    await self._handle_payload_dict(item)
            return
        if not isinstance(payload, dict):
            return
        await self._handle_payload_dict(payload)

    async def _handle_payload_dict(self, payload: dict[str, Any]) -> None:
        event_type = str(payload.get("event_type") or "").lower()
        if event_type == "order":
            await self._handle_order(payload)
        elif event_type == "trade":
            await self._handle_trade(payload)
        elif payload.get("type") == "error":
            logger.warning("User WS error message: %s", payload)

    async def _handle_order(self, payload: dict[str, Any]) -> None:
        order_id = str(payload.get("id") or "")
        asset_id = str(payload.get("asset_id") or "")
        if not order_id or not asset_id:
            return
        side = str(payload.get("side") or "").upper()
        status = str(payload.get("type") or payload.get("status") or "").upper()
        event = OrderEvent(
            order_id=order_id,
            asset_id=asset_id,
            side=side,
            price=str(payload.get("price") or ""),
            original_size=str(payload.get("original_size") or payload.get("size") or ""),
            size_matched=str(payload.get("size_matched") or ""),
            status=status,
            timestamp=str(payload.get("timestamp") or ""),
            raw=payload,
        )
        async with self._state_lock:
            self._order_events[order_id] = event
        if status == "PLACEMENT" and self._placement_queue:
            await self._placement_queue.put(event)

    async def _handle_trade(self, payload: dict[str, Any]) -> None:
        trade_id = str(payload.get("id") or "")
        asset_id = str(payload.get("asset_id") or "")
        if not trade_id or not asset_id:
            return
        event = TradeEvent(
            trade_id=trade_id,
            asset_id=asset_id,
            taker_order_id=str(payload.get("taker_order_id") or ""),
            side=str(payload.get("side") or "").upper(),
            price=str(payload.get("price") or ""),
            size=str(payload.get("size") or ""),
            status=str(payload.get("status") or "").upper(),
            timestamp=str(payload.get("timestamp") or ""),
            raw=payload,
        )
        async with self._state_lock:
            self._trade_events[trade_id] = event

    def get_message_stats(self) -> tuple[datetime | None, int]:
        return None, len(self._trade_events) + len(self._order_events)

    @staticmethod
    def _parse_timestamp(value: Any) -> float:
        try:
            ts = float(value)
            if ts > 10_000_000_000:
                ts = ts / 1000.0
            return ts
        except (TypeError, ValueError):
            return 0.0
