"""
Match polling engine for live monitor (v3).

Polls OddsPapi + Gamma + WS for a single mapping.  Supports multiple
PM fixtures per mapping (match_winner + game_winner children) sharing
one OddsPapi odds source and one WebSocket manager.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from threading import Lock

from shared.edge import NetEdgeResult, compute_net_edges, devig_two_way_decimal
from shared.models import Fixture, Mapping
from shared.oddspapi_client import AsyncOddsPapiClient, OddsPapiClient
from shared.polymarket_client import AsyncPolymarketClient
from shared.polymarket_ws import BookState, PolymarketWSManager

from .monitor_types import FocusSnapshot, LogBuffer, PerfStats

logger = logging.getLogger("cli.live")
WS_STALE_SECONDS = 5.0
WS_FALLBACK_MIN_INTERVAL = 5.0


@dataclass
class PollerPerf:
    last_loop_ms: float | None = None
    oddspapi_ms_total: float = 0.0
    oddspapi_calls: int = 0
    gamma_ms_total: float = 0.0
    gamma_calls: int = 0


class SingleMatchPoller:
    """Poll OddsPapi + Gamma + WS for a single mapping (all markets)."""

    def __init__(
        self,
        mapping: Mapping,
        op_fixture: Fixture,
        pm_fixtures: list[Fixture],
        league_name: str,
        oddspapi: AsyncOddsPapiClient,
        polymarket: AsyncPolymarketClient,
        ws_manager: PolymarketWSManager,
        log_buffer: LogBuffer,
        spread_factor: float,
        allow_rest_fallback: bool = True,
        resubscribe_seconds: float = 8.0,
        league_poll_seconds_live: float = 1.0,
        league_poll_seconds_pre: float = 5.0,
        hot_fixture_poll_seconds: float = 0.5,
        gamma_poll_seconds: float = 12.0,
    ) -> None:
        self._mapping = mapping
        self._op_fixture = op_fixture
        self._pm_fixtures = pm_fixtures
        self._pm_fixture = pm_fixtures[0]  # primary (match_winner)
        self._league_name = league_name
        self._oddspapi = oddspapi
        self._polymarket = polymarket
        self._ws_manager = ws_manager
        self._log_buffer = log_buffer
        self._spread_factor = spread_factor
        self._allow_rest_fallback = allow_rest_fallback
        self._resubscribe_seconds = resubscribe_seconds

        self._league_poll_seconds_live = league_poll_seconds_live
        self._league_poll_seconds_pre = league_poll_seconds_pre
        self._hot_fixture_poll_seconds = hot_fixture_poll_seconds
        self._gamma_poll_seconds = gamma_poll_seconds

        self._odds_cache: dict[str, dict] = {}
        self._pm_latest_by_market: dict[str, dict] = {}
        self._last_odds_update: datetime | None = None
        self._last_gamma_update: datetime | None = None
        self._snapshots: list[FocusSnapshot] = []
        self._perf = PollerPerf()
        self._perf_lock = Lock()
        self._state_lock = Lock()
        self._stop: asyncio.Event | None = None
        self._tasks: list[asyncio.Task] = []
        self._league_poll_ready = False
        self._tournament_id = _extract_tournament_id(self._op_fixture.raw_json or {})
        self._last_fallback_ts: float = 0.0
        self._last_resubscribe_ts: float = 0.0

    async def start(self) -> None:
        if self._stop is None:
            self._stop = asyncio.Event()
        self._tasks = [
            asyncio.create_task(self._ws_manager.run()),
            asyncio.create_task(self._league_poll_loop()),
            asyncio.create_task(self._fixture_poll_loop()),
            asyncio.create_task(self._gamma_refresh_loop()),
            asyncio.create_task(self._snapshot_loop()),
        ]

    async def stop(self) -> None:
        if self._stop:
            self._stop.set()
        await self._ws_manager.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    def get_snapshot(self) -> FocusSnapshot | None:
        with self._state_lock:
            return self._snapshots[0] if self._snapshots else None

    def get_all_snapshots(self) -> list[FocusSnapshot]:
        with self._state_lock:
            return list(self._snapshots)

    def get_perf(self) -> PerfStats:
        with self._perf_lock:
            return PerfStats(
                loop_ms=self._perf.last_loop_ms,
                oddspapi_ms_total=self._perf.oddspapi_ms_total,
                oddspapi_calls=self._perf.oddspapi_calls,
                gamma_ms_total=self._perf.gamma_ms_total,
                gamma_calls=self._perf.gamma_calls,
            )

    async def _league_poll_loop(self) -> None:
        if not self._tournament_id:
            return
        while self._stop and not self._stop.is_set():
            try:
                start = time.perf_counter()
                payloads = await self._oddspapi.get_odds_by_tournaments([self._tournament_id])
                elapsed = (time.perf_counter() - start) * 1000
                with self._perf_lock:
                    self._perf.oddspapi_ms_total += elapsed
                    self._perf.oddspapi_calls += 1
                self._league_poll_ready = True
                if isinstance(payloads, list):
                    for payload in payloads:
                        fixture_id = payload.get("fixtureId")
                        if fixture_id is not None:
                            self._odds_cache[str(fixture_id)] = payload
            except Exception as exc:  # pragma: no cover - network guardrail
                self._log_buffer.add(f"OddsPapi league poll error: {exc}")
            await asyncio.sleep(self._league_poll_interval())

    async def _fixture_poll_loop(self) -> None:
        fixture_id = str(self._op_fixture.source_id)
        while self._stop and not self._stop.is_set():
            try:
                start = time.perf_counter()
                payload = await self._oddspapi.get_odds(fixture_id)
                elapsed = (time.perf_counter() - start) * 1000
                with self._perf_lock:
                    self._perf.oddspapi_ms_total += elapsed
                    self._perf.oddspapi_calls += 1
                if isinstance(payload, dict):
                    self._odds_cache[fixture_id] = payload
                    self._last_odds_update = datetime.now(tz=timezone.utc)
            except Exception as exc:  # pragma: no cover - network guardrail
                self._log_buffer.add(f"OddsPapi fixture poll error: {exc}")
            await asyncio.sleep(self._hot_fixture_poll_seconds)

    async def _gamma_refresh_loop(self) -> None:
        while self._stop and not self._stop.is_set():
            all_token_ids: list[str] = []
            for pm_fix in self._pm_fixtures:
                market_id = str(pm_fix.source_id)
                try:
                    start = time.perf_counter()
                    latest = await self._polymarket.get_market_by_id(market_id)
                    elapsed = (time.perf_counter() - start) * 1000
                    with self._perf_lock:
                        self._perf.gamma_ms_total += elapsed
                        self._perf.gamma_calls += 1
                    if isinstance(latest, dict):
                        self._pm_latest_by_market[market_id] = latest
                        self._last_gamma_update = datetime.now(tz=timezone.utc)
                        all_token_ids.extend(_extract_token_ids(latest))
                except Exception as exc:  # pragma: no cover - network guardrail
                    self._log_buffer.add(f"Gamma refresh error ({market_id}): {exc}")
            if all_token_ids:
                await self._ws_manager.update_subscriptions(sorted(set(all_token_ids)))
            await asyncio.sleep(self._gamma_poll_seconds)

    async def _snapshot_loop(self) -> None:
        while self._stop and not self._stop.is_set():
            loop_start = time.perf_counter()
            snapshots = await self.build_all_snapshots()
            with self._state_lock:
                self._snapshots = snapshots
            with self._perf_lock:
                self._perf.last_loop_ms = (time.perf_counter() - loop_start) * 1000
            await asyncio.sleep(0.5)

    async def build_snapshot(self) -> FocusSnapshot | None:
        """Build snapshot for the primary (match_winner) fixture."""
        snapshots = await self.build_all_snapshots()
        return snapshots[0] if snapshots else None

    async def build_all_snapshots(self) -> list[FocusSnapshot]:
        """Build snapshots for all PM fixtures sharing one odds source."""
        now = datetime.now(tz=timezone.utc)
        odds_payload = self._odds_cache.get(str(self._op_fixture.source_id))
        if odds_payload is None and not self._league_poll_ready:
            return []

        ws_state = await self._ws_manager.get_state_snapshot()
        last_msg_ts, _ = self._ws_manager.get_message_stats()
        now_ts = time.time()
        last_message_age = None
        if last_msg_ts:
            last_message_age = (datetime.now(tz=timezone.utc) - last_msg_ts).total_seconds()
        ws_stale = last_message_age is None or last_message_age > WS_STALE_SECONDS

        # Collect all token IDs across all fixtures for WS / fallback
        all_token_ids: list[str] = []
        for pm_fix in self._pm_fixtures:
            market_id = str(pm_fix.source_id)
            pm_latest = self._pm_latest_by_market.get(market_id)
            market_raw = pm_latest if isinstance(pm_latest, dict) else (pm_fix.raw_json or {})
            pairs = _extract_outcome_token_pairs(market_raw)
            if pairs:
                all_token_ids.extend(tid for _, tid in pairs if tid)
            else:
                all_token_ids.extend(_extract_token_ids(market_raw))

        if ws_stale and all_token_ids and (now_ts - self._last_resubscribe_ts) > self._resubscribe_seconds:
            await self._ws_manager.force_resubscribe()
            self._last_resubscribe_ts = now_ts

        fallback_books: dict[str, BookState] = {}
        if (
            ws_stale
            and self._allow_rest_fallback
            and all_token_ids
            and (now_ts - self._last_fallback_ts) > WS_FALLBACK_MIN_INTERVAL
        ):
            books = await self._polymarket.get_orderbooks_batch(list(set(all_token_ids)))
            fallback_books = _convert_books_to_state(books)
            self._last_fallback_ts = now_ts

        results: list[FocusSnapshot] = []
        for pm_fix in self._pm_fixtures:
            snap = self._build_snapshot_for(
                pm_fixture=pm_fix,
                odds_payload=odds_payload,
                ws_state=ws_state,
                fallback_books=fallback_books,
                now=now,
            )
            if snap:
                results.append(snap)
        return results

    def _build_snapshot_for(
        self,
        pm_fixture: Fixture,
        odds_payload: dict | None,
        ws_state: dict[str, BookState],
        fallback_books: dict[str, BookState],
        now: datetime,
    ) -> FocusSnapshot | None:
        market_type = pm_fixture.market_type or "unknown"
        game_number = pm_fixture.game_number
        p_ref_a, p_ref_b, odds_a, odds_b = _extract_p_refs_from_odds(
            odds_payload or {},
            market_type=market_type,
            game_number=game_number,
            op_fixture=self._op_fixture,
            pm_fixture=pm_fixture,
            log_buffer=self._log_buffer,
        )
        market_id = str(pm_fixture.source_id)
        pm_latest = self._pm_latest_by_market.get(market_id)
        pm_resolution = _extract_pm_resolution_status(pm_latest) if isinstance(pm_latest, dict) else ""
        pm_winner = _extract_pm_winner(pm_latest) if isinstance(pm_latest, dict) else None

        market_raw = pm_latest if isinstance(pm_latest, dict) else (pm_fixture.raw_json or {})
        tick_size = _extract_tick_size(market_raw)
        outcome_pairs = _extract_outcome_token_pairs(market_raw)
        if not outcome_pairs:
            outcomes = _parse_outcomes(market_raw)
            token_ids = _extract_token_ids(market_raw)
            outcome_pairs = _pair_outcomes_with_tokens(self._op_fixture, outcomes, token_ids)

        bid_a = ask_a = bid_b = ask_b = None
        swap_hint = False
        mkt_key = f"{self._op_fixture.source_id}:{market_type}:{game_number}"
        if len(outcome_pairs) >= 2:
            left_name, left_token = outcome_pairs[0]
            right_name, right_token = outcome_pairs[1]
            score_left_a = _similarity(left_name, self._op_fixture.team_a_name)
            score_left_b = _similarity(left_name, self._op_fixture.team_b_name)
            score_right_a = _similarity(right_name, self._op_fixture.team_a_name)
            score_right_b = _similarity(right_name, self._op_fixture.team_b_name)
            if score_left_b > score_left_a and score_right_a > score_right_b:
                swap_hint = True
                self._log_buffer.add(
                    f"Detected team-side swap for {mkt_key}",
                    key=f"team-swap:{mkt_key}",
                    cooldown_seconds=60,
                )

            left_book = ws_state.get(left_token) or fallback_books.get(left_token)
            right_book = ws_state.get(right_token) or fallback_books.get(right_token)
            left_price = left_book.best_ask if left_book and left_book.best_ask is not None else (
                left_book.best_bid if left_book else None
            )
            right_price = right_book.best_ask if right_book and right_book.best_ask is not None else (
                right_book.best_bid if right_book else None
            )
            if (
                left_price is not None
                and right_price is not None
                and p_ref_a is not None
                and p_ref_b is not None
            ):
                error_current = abs(p_ref_a - left_price) + abs(p_ref_b - right_price)
                error_swap = abs(p_ref_a - right_price) + abs(p_ref_b - left_price)
                if (error_swap + 0.05) < error_current:
                    swap_hint = True
                    self._log_buffer.add(
                        f"Detected price-based swap for {mkt_key}",
                        key=f"price-swap:{mkt_key}",
                        cooldown_seconds=60,
                    )
        for idx, (outcome_name, token_id) in enumerate(outcome_pairs):
            book = ws_state.get(token_id) or fallback_books.get(token_id)
            if swap_hint and len(outcome_pairs) >= 2:
                if idx == 0:
                    side = "B"
                elif idx == 1:
                    side = "A"
                else:
                    side = _match_outcome_side(self._op_fixture, outcome_name)
            else:
                side = _match_outcome_side(self._op_fixture, outcome_name)
            if side == "A":
                bid_a, ask_a = (book.best_bid, book.best_ask) if book else (None, None)
            elif side == "B":
                bid_b, ask_b = (book.best_bid, book.best_ask) if book else (None, None)

        mid_a = (bid_a + ask_a) / 2 if bid_a is not None and ask_a is not None else None
        mid_b = (bid_b + ask_b) / 2 if bid_b is not None and ask_b is not None else None
        edge = compute_net_edges(
            p_ref_a=p_ref_a,
            p_ref_b=p_ref_b,
            bid_a=bid_a,
            ask_a=ask_a,
            bid_b=bid_b,
            ask_b=ask_b,
            spread_factor=self._spread_factor,
        )
        match_name = f"{self._op_fixture.team_a_name} vs {self._op_fixture.team_b_name}"

        return FocusSnapshot(
            mapping_id=str(self._mapping.id),
            league=self._league_name,
            match=match_name,
            start_time=self._op_fixture.start_time,
            market_type=market_type,
            game_number=game_number,
            tick_size=tick_size,
            pm_resolution_status=pm_resolution,
            pm_winner=pm_winner,
            p_ref_a=p_ref_a,
            p_ref_b=p_ref_b,
            odds_a=odds_a,
            odds_b=odds_b,
            bid_a=bid_a,
            ask_a=ask_a,
            bid_b=bid_b,
            ask_b=ask_b,
            mid_a=mid_a,
            mid_b=mid_b,
            edge=edge if isinstance(edge, NetEdgeResult) else NetEdgeResult(None, None, None, None),
            updated_at=now,
            ws_connected=self._ws_manager.is_connected(),
            last_odds_update=self._last_odds_update,
            last_gamma_update=self._last_gamma_update,
        )

    def _league_poll_interval(self) -> float:
        start_time = self._op_fixture.start_time
        if not start_time:
            return self._league_poll_seconds_pre
        now = datetime.now(tz=timezone.utc)
        if now >= start_time:
            return self._league_poll_seconds_live
        return self._league_poll_seconds_pre


def _extract_tournament_id(raw: dict) -> int | None:
    for key in ("tournamentId", "tournament_id"):
        value = raw.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _convert_books_to_state(books_by_token: dict[str, dict]) -> dict[str, BookState]:
    results: dict[str, BookState] = {}
    for token_id, book in books_by_token.items():
        bids = [(float(b["price"]), float(b["size"])) for b in book.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in book.get("asks", [])]
        best_bid = book.get("best_bid")
        best_ask = book.get("best_ask")
        spread = None
        if best_bid is not None and best_ask is not None:
            spread = max(best_ask - best_bid, 0.0)
        results[token_id] = BookState(
            asset_id=token_id,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            bids=bids,
            asks=asks,
            timestamp=datetime.now(tz=timezone.utc),
            hash=None,
        )
    return results


def _extract_tick_size(market_raw: dict) -> float | None:
    for key in (
        "orderPriceMinTickSize",
        "orderMinPriceTickSize",
        "order_min_price_tick_size",
        "tick_size",
        "tickSize",
        "min_tick_size",
    ):
        value = market_raw.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _extract_p_refs_from_odds(
    odds_payload: dict,
    market_type: str,
    game_number: int | None,
    op_fixture: Fixture | None = None,
    pm_fixture: Fixture | None = None,
    log_buffer: LogBuffer | None = None,
) -> tuple[float | None, float | None, float | None, float | None]:
    odds_a = None
    odds_b = None
    p_ref_a = None
    p_ref_b = None
    if market_type == "match_winner":
        pinnacle_odds = OddsPapiClient.extract_pinnacle_moneyline(odds_payload)
    elif market_type == "game_winner" and game_number:
        pinnacle_odds = OddsPapiClient.extract_pinnacle_game_winner(odds_payload, game_number)
        if not pinnacle_odds and pm_fixture:
            series_len = _series_length(getattr(pm_fixture, "series_type", None))
            moneyline = OddsPapiClient.extract_pinnacle_moneyline(odds_payload)
            if moneyline and series_len and game_number == series_len:
                pinnacle_odds = moneyline
                if log_buffer and op_fixture:
                    log_buffer.add(
                        f"OddsPapi: using match moneyline for final game {game_number} "
                        f"({op_fixture.source_id})",
                        key=f"odds-final-game:{op_fixture.source_id}:{game_number}",
                        cooldown_seconds=120,
                    )
    else:
        pinnacle_odds = {}

    home = pinnacle_odds.get("home") if isinstance(pinnacle_odds, dict) else None
    away = pinnacle_odds.get("away") if isinstance(pinnacle_odds, dict) else None
    if home and away:
        participant1_id = odds_payload.get("participant1Id")
        participant2_id = odds_payload.get("participant2Id")
        home_player_id = home.get("player_id")
        away_player_id = away.get("player_id")
        swap_order = False
        if (
            participant1_id is not None
            and participant2_id is not None
            and home_player_id is not None
            and away_player_id is not None
        ):
            p1_id = str(participant1_id)
            p2_id = str(participant2_id)
            home_id = str(home_player_id)
            away_id = str(away_player_id)
            swap_order = home_id == p2_id and away_id == p1_id
            if swap_order and log_buffer and op_fixture:
                log_buffer.add(
                    "OddsPapi: swapping home/away to match participant ordering "
                    f"({op_fixture.source_id})",
                    key=f"odds-participant-swap:{op_fixture.source_id}:{game_number}",
                    cooldown_seconds=120,
                )
        if swap_order:
            odds_a = away.get("price")
            odds_b = home.get("price")
        else:
            odds_a = home.get("price")
            odds_b = away.get("price")
        if odds_a is not None and odds_b is not None:
            p_ref_a, p_ref_b = devig_two_way_decimal(odds_a, odds_b)
    return p_ref_a, p_ref_b, odds_a, odds_b


def _series_length(series_type: str | None) -> int | None:
    if not series_type:
        return None
    normalized = series_type.lower()
    if normalized == "bo1":
        return 1
    if normalized == "bo3":
        return 3
    if normalized == "bo5":
        return 5
    return None


def _parse_outcomes(raw: dict) -> list[str]:
    outcomes = raw.get("outcomes") or []
    if isinstance(outcomes, str):
        try:
            import json

            outcomes = json.loads(outcomes)
        except (json.JSONDecodeError, TypeError):
            outcomes = []
    return [str(o) for o in outcomes if o]


def _parse_token_ids(raw: dict) -> list[str]:
    token_ids = raw.get("clobTokenIds") or []
    if isinstance(token_ids, str):
        try:
            import json

            token_ids = json.loads(token_ids)
        except (json.JSONDecodeError, TypeError):
            token_ids = []
    return [str(t) for t in token_ids if t]


def _pair_outcomes_with_tokens(
    op_fixture: Fixture,
    outcomes: list[str],
    token_ids: list[str],
) -> list[tuple[str, str]]:
    if not outcomes:
        outcomes = [op_fixture.team_a_name or "Team A", op_fixture.team_b_name or "Team B"]
    pairs = []
    for idx, outcome in enumerate(outcomes):
        token_id = token_ids[idx] if idx < len(token_ids) else ""
        pairs.append((outcome, token_id))
    return pairs


def _normalize(value: str | None) -> str:
    return "".join(ch.lower() for ch in (value or "") if ch.isalnum())


def _similarity(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, _normalize(left), _normalize(right)).ratio()


def _match_outcome_side(fixture: Fixture, outcome: str) -> str | None:
    if _is_team_a(fixture, outcome):
        return "A"
    if _is_team_b(fixture, outcome):
        return "B"
    score_a = _similarity(outcome, fixture.team_a_name)
    score_b = _similarity(outcome, fixture.team_b_name)
    if score_a >= 0.6 and score_a > score_b:
        return "A"
    if score_b >= 0.6 and score_b > score_a:
        return "B"
    return None


def _is_team_a(fixture: Fixture, outcome: str) -> bool:
    out = _normalize(outcome)
    team = _normalize(fixture.team_a_name)
    return bool(out and team and (out == team or out in team or team in out))


def _is_team_b(fixture: Fixture, outcome: str) -> bool:
    out = _normalize(outcome)
    team = _normalize(fixture.team_b_name)
    return bool(out and team and (out == team or out in team or team in out))


def _extract_outcome_token_pairs(raw: dict) -> list[tuple[str, str]]:
    tokens = raw.get("tokens") or []
    if isinstance(tokens, str):
        try:
            import json

            tokens = json.loads(tokens)
        except (json.JSONDecodeError, TypeError):
            tokens = []
    pairs: list[tuple[str, str]] = []
    if isinstance(tokens, list):
        for token in tokens:
            if not isinstance(token, dict):
                continue
            token_id = token.get("token_id") or token.get("tokenId") or token.get("id")
            outcome = token.get("outcome") or token.get("name") or token.get("title")
            if token_id and outcome:
                pairs.append((str(outcome), str(token_id)))
    return pairs


def _extract_token_ids(raw: dict) -> list[str]:
    token_ids = [token_id for _, token_id in _extract_outcome_token_pairs(raw) if token_id]
    if token_ids:
        return token_ids
    return _parse_token_ids(raw)


def _extract_pm_resolution_status(pm_latest: dict | None) -> str:
    if not isinstance(pm_latest, dict):
        return ""
    status = pm_latest.get("umaResolutionStatus")
    if isinstance(status, str):
        return status.upper()
    statuses = pm_latest.get("umaResolutionStatuses")
    if isinstance(statuses, list) and statuses:
        last = statuses[-1]
        if isinstance(last, dict) and "status" in last:
            return str(last.get("status")).upper()
    return ""


def _extract_pm_winner(pm_latest: dict | None) -> str | None:
    if not isinstance(pm_latest, dict):
        return None
    winner = pm_latest.get("winner") or pm_latest.get("outcomeWinner") or pm_latest.get("resolvedOutcome")
    if isinstance(winner, str) and winner.strip():
        return winner.strip()
    return None
