"""
Standalone Goalserve gold-edge strategy commands.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert

from shared.clob_executor import ClobExecutor
from shared.config import settings
from shared.db import SessionLocal
from shared.goalserve_client import GoalserveClient, parse_game_stats
from shared.models import Fixture, GameResult, GameSnapshot, GoldEdgeTrade
from shared.polymarket_client import PolymarketClient
from shared.secret_utils import decrypt_age_keyfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GoldRule:
    minute_min: int
    gold_diff_min: float
    max_ask: float
    requires_baron: bool


def probe_command(duration_minutes: int, poll_seconds: float | None = None) -> None:
    """
    Phase 0: Confirm live Goalserve updates for LoL in-game fields.
    """
    poll_interval = poll_seconds or settings.goalserve_probe_seconds
    end_at = datetime.now(tz=timezone.utc) + timedelta(minutes=duration_minutes)
    seen_gold: dict[tuple[str, int | None], tuple[float | None, float | None]] = {}

    client = GoalserveClient()
    try:
        print(f"\nGoalserve live probe started ({duration_minutes} min, every {poll_interval:.1f}s)\n")
        while datetime.now(tz=timezone.utc) < end_at:
            payload = client.get_home()
            matches = client.filter_lol(client.extract_matches(payload))
            live = [m for m in matches if str(m.get("@status", "")).lower() in {"started", "live", "in play"}]
            print(
                f"[{datetime.now(tz=timezone.utc).strftime('%H:%M:%S')} UTC] "
                f"LoL matches={len(matches)} live={len(live)}"
            )
            for match in live:
                rows = parse_game_stats(match)
                latest = rows[-1] if rows else None
                key = (str(match.get("@id") or ""), latest.get("game_no") if latest else None)
                home = (match.get("localteam") or {}).get("@name", "?")
                away = (match.get("awayteam") or {}).get("@name", "?")
                if not latest:
                    print(f"  - {home} vs {away}: live but no game stats row yet")
                    continue
                current = (latest.get("localteam_gold"), latest.get("awayteam_gold"))
                prior = seen_gold.get(key)
                moved = prior is not None and prior != current
                seen_gold[key] = current
                print(
                    f"  - {home} vs {away} G{latest.get('game_no')}: "
                    f"gold={current[0]}:{current[1]} moved={moved}"
                )
            time.sleep(poll_interval)
    finally:
        client.close()


def collect_live_command(
    duration_minutes: int,
    poll_seconds: float | None = None,
    trade_mode: str = "none",
    stake_usd: float | None = None,
) -> None:
    """
    Phase 1 + Phase 4/5:
    - collect live snapshots continuously
    - optional paper/live trade execution using simple gold rules.
    """
    mode = trade_mode.strip().lower()
    if mode not in {"none", "paper", "live"}:
        raise ValueError("trade_mode must be none|paper|live")

    rules = _parse_rules()
    poll_interval = poll_seconds or settings.goalserve_poll_seconds
    end_at = datetime.now(tz=timezone.utc) + timedelta(minutes=duration_minutes)
    stake = stake_usd if stake_usd is not None else settings.gold_edge_default_stake_usd

    gs = GoalserveClient()
    pm = PolymarketClient()
    executor: ClobExecutor | None = None
    if mode == "live":
        private_key = decrypt_age_keyfile(settings.polymarket_keyfile_path)
        executor = ClobExecutor(
            private_key=private_key,
            funder=settings.polymarket_funder_address,
            signature_type=settings.polymarket_signature_type,
            chain_id=settings.polymarket_chain_id,
            clob_url=settings.polymarket_clob_url,
            kill_switch_path=settings.live_kill_switch_path,
        )

    print(
        f"\nGold-edge live collector started ({duration_minutes} min, every {poll_interval:.1f}s, mode={mode})\n"
    )
    try:
        while datetime.now(tz=timezone.utc) < end_at:
            payload = gs.get_home()
            matches = gs.filter_lol(gs.extract_matches(payload))
            snapshots_written = 0
            with SessionLocal() as db:
                for match in matches:
                    status = str(match.get("@status") or "")
                    if status.lower() not in {"started", "live", "in play", "not started", "finished"}:
                        continue
                    rows = parse_game_stats(match)
                    if not rows:
                        continue
                    latest = rows[-1]
                    pm_ctx = _resolve_pm_context(db, match, latest.get("game_no"))
                    books = _pm_books_for_context(pm, pm_ctx)
                    snap = _build_snapshot_row(match, latest, pm_ctx, books)
                    db.add(snap)
                    snapshots_written += 1

                    _upsert_results_from_match(db, match, rows)

                    if mode in {"paper", "live"} and status.lower() in {"started", "live", "in play"}:
                        decision = _evaluate_entry_decision(latest, books, rules)
                        if decision and pm_ctx and pm_ctx["pm_fixture"] is not None:
                            _maybe_open_trade(
                                db=db,
                                mode=mode,
                                match=match,
                                game_row=latest,
                                pm_ctx=pm_ctx,
                                decision=decision,
                                stake_usd=stake,
                                executor=executor,
                            )

                _resolve_open_trades(db)
                db.commit()

            print(
                f"[{datetime.now(tz=timezone.utc).strftime('%H:%M:%S')} UTC] "
                f"processed_lol={len(matches)} snapshots={snapshots_written}"
            )
            time.sleep(poll_interval)
    finally:
        pm.close()
        gs.close()


def cache_daily_command(days_back: int = 0) -> None:
    """
    Phase 2: persist finished game outcomes from daily Goalserve endpoint.
    """
    target_day = datetime.now(tz=timezone.utc).date() - timedelta(days=days_back)
    gs = GoalserveClient()
    try:
        payload = gs.get_home(target_date=target_day)
        matches = gs.filter_lol(gs.extract_matches(payload))
        upserted = 0
        with SessionLocal() as db:
            for match in matches:
                rows = parse_game_stats(match)
                upserted += _upsert_results_from_match(db, match, rows)
            db.commit()
        print(f"Daily cache complete for {target_day}: matches={len(matches)} upserted_games={upserted}")
    finally:
        gs.close()


def analyze_command(days: int = 14, min_samples: int = 20) -> None:
    """
    Phase 3: summarize gold-diff correlation and simple threshold buckets.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
    with SessionLocal() as db:
        rows = db.execute(
            select(
                GameSnapshot.game_duration_seconds,
                GameSnapshot.gold_diff,
                GameResult.winner_side,
            )
            .join(
                GameResult,
                and_(
                    GameResult.source_match_id == GameSnapshot.source_match_id,
                    GameResult.game_no == GameSnapshot.game_no,
                ),
            )
            .where(GameSnapshot.ts >= cutoff)
            .where(GameSnapshot.gold_diff.is_not(None))
            .where(GameResult.winner_side.is_not(None))
        ).all()

    if not rows:
        print("No snapshot/result joins available for analysis yet.")
        return

    buckets: dict[tuple[int, int], list[int]] = {}
    for game_seconds, gold_diff, winner in rows:
        minute = max(int((game_seconds or 0) / 60), 0)
        minute_bucket = (minute // 5) * 5
        gold_bucket = int(abs(float(gold_diff)) // 1000) * 1000
        key = (minute_bucket, gold_bucket)
        lead_side = "A" if float(gold_diff) >= 0 else "B"
        is_win = 1 if lead_side == winner else 0
        buckets.setdefault(key, []).append(is_win)

    print(f"\nGold-edge analysis (last {days} days)")
    print("minute_bucket | abs_gold_bucket | samples | leader_win_rate")
    suggestions: list[tuple[int, int, float, int]] = []
    for (minute_bucket, gold_bucket), outcomes in sorted(buckets.items()):
        samples = len(outcomes)
        if samples < min_samples:
            continue
        win_rate = sum(outcomes) / samples
        print(f"{minute_bucket:>12} | {gold_bucket:>14} | {samples:>7} | {win_rate:>15.2%}")
        if win_rate >= 0.80:
            suggestions.append((minute_bucket, gold_bucket, win_rate, samples))

    if suggestions:
        print("\nRule candidates (leader win-rate >= 80%):")
        for minute_bucket, gold_bucket, win_rate, samples in suggestions[:15]:
            print(
                f"- minute>={minute_bucket}, abs_gold>={gold_bucket}, "
                f"leader_win_rate={win_rate:.2%} ({samples} samples)"
            )
    else:
        print("\nNo stable 80%+ leader buckets yet at the chosen sample threshold.")


def _resolve_pm_context(db, match: dict[str, Any], game_no: int | None) -> dict[str, Any] | None:
    home = (match.get("localteam") or {}).get("@name", "")
    away = (match.get("awayteam") or {}).get("@name", "")
    if not home or not away or game_no is None:
        return None

    candidates = db.execute(
        select(Fixture)
        .where(Fixture.source == "polymarket")
        .where(Fixture.market_type == "game_winner")
        .where(Fixture.game_number == game_no)
        .order_by(Fixture.start_time.asc().nulls_last())
    ).scalars().all()
    if not candidates:
        return None

    best: Fixture | None = None
    best_score = 0.0
    for fixture in candidates:
        score_direct = (_similarity(home, fixture.team_a_name) + _similarity(away, fixture.team_b_name)) / 2.0
        score_swap = (_similarity(home, fixture.team_b_name) + _similarity(away, fixture.team_a_name)) / 2.0
        score = max(score_direct, score_swap)
        if score > best_score:
            best_score = score
            best = fixture
    if best is None or best_score < 0.65:
        return None

    token_map = _extract_team_token_map(best.raw_json or {}, best.team_a_name or "", best.team_b_name or "")
    if not token_map:
        return {"pm_fixture": best, "token_a": None, "token_b": None}
    return {
        "pm_fixture": best,
        "token_a": token_map.get("token_a"),
        "token_b": token_map.get("token_b"),
    }


def _extract_team_token_map(market_raw: dict[str, Any], team_a: str, team_b: str) -> dict[str, str] | None:
    tokens = market_raw.get("tokens") or []
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens)
        except (TypeError, ValueError):
            tokens = []
    if not isinstance(tokens, list) or len(tokens) < 2:
        return None

    pairs: list[tuple[str, str]] = []
    for token in tokens:
        if not isinstance(token, dict):
            continue
        name = str(token.get("outcome") or token.get("name") or "")
        token_id = str(token.get("token_id") or token.get("tokenId") or token.get("id") or "")
        if name and token_id:
            pairs.append((name, token_id))
    if len(pairs) < 2:
        return None

    a_best = max(pairs, key=lambda x: _similarity(team_a, x[0]))
    b_best = max(pairs, key=lambda x: _similarity(team_b, x[0]))
    if a_best[1] == b_best[1]:
        return {"token_a": pairs[0][1], "token_b": pairs[1][1]}
    return {"token_a": a_best[1], "token_b": b_best[1]}


def _pm_books_for_context(pm: PolymarketClient, pm_ctx: dict[str, Any] | None) -> dict[str, Any]:
    if not pm_ctx:
        return {}
    token_a = pm_ctx.get("token_a")
    token_b = pm_ctx.get("token_b")
    books: dict[str, Any] = {}
    if token_a:
        books["a"] = pm.get_clob_orderbook(token_a)
    if token_b:
        books["b"] = pm.get_clob_orderbook(token_b)
    return books


def _build_snapshot_row(
    match: dict[str, Any],
    game_row: dict[str, Any],
    pm_ctx: dict[str, Any] | None,
    books: dict[str, Any],
) -> GameSnapshot:
    local = match.get("localteam") or {}
    away = match.get("awayteam") or {}
    pm_fixture = pm_ctx.get("pm_fixture") if pm_ctx else None
    book_a = books.get("a") if isinstance(books, dict) else None
    book_b = books.get("b") if isinstance(books, dict) else None
    duration_seconds = _duration_to_seconds(game_row.get("duration"))
    return GameSnapshot(
        id=uuid4(),
        ts=datetime.now(tz=timezone.utc),
        source_match_id=str(match.get("@id") or ""),
        source_league=str(match.get("@league") or ""),
        source_date=str(match.get("@date") or ""),
        source_time=str(match.get("@time") or ""),
        game_no=game_row.get("game_no"),
        team_a_name=str(local.get("@name") or ""),
        team_b_name=str(away.get("@name") or ""),
        team_a_score=_to_float(local.get("@score")),
        team_b_score=_to_float(away.get("@score")),
        game_duration_seconds=duration_seconds,
        gold_a=game_row.get("localteam_gold"),
        gold_b=game_row.get("awayteam_gold"),
        gold_diff=game_row.get("gold_diff"),
        kills_a=game_row.get("localteam_kills"),
        kills_b=game_row.get("awayteam_kills"),
        towers_a=game_row.get("localteam_towers"),
        towers_b=game_row.get("awayteam_towers"),
        dragons_a=game_row.get("localteam_dragons"),
        dragons_b=game_row.get("awayteam_dragons"),
        barons_a=game_row.get("localteam_barons"),
        barons_b=game_row.get("awayteam_barons"),
        inhibitors_a=game_row.get("localteam_inhibitors"),
        inhibitors_b=game_row.get("awayteam_inhibitors"),
        pm_fixture_id=pm_fixture.id if pm_fixture else None,
        pm_token_id_a=pm_ctx.get("token_a") if pm_ctx else None,
        pm_token_id_b=pm_ctx.get("token_b") if pm_ctx else None,
        pm_bid_a=_book_price(book_a, "best_bid"),
        pm_ask_a=_book_price(book_a, "best_ask"),
        pm_bid_b=_book_price(book_b, "best_bid"),
        pm_ask_b=_book_price(book_b, "best_ask"),
        raw_json={
            "match": match,
            "game_row": game_row,
            "pm_fixture_source_id": str(pm_fixture.source_id) if pm_fixture else None,
            "books": books,
        },
    )


def _upsert_results_from_match(db, match: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    upserts = 0
    local = match.get("localteam") or {}
    away = match.get("awayteam") or {}
    source_match_id = str(match.get("@id") or "")
    source_date = str(match.get("@date") or "")
    source_league = str(match.get("@league") or "")
    final_series_a = _to_float(local.get("@score"))
    final_series_b = _to_float(away.get("@score"))
    for row in rows:
        game_no = row.get("game_no")
        if game_no is None:
            continue
        kills_a = row.get("localteam_kills")
        kills_b = row.get("awayteam_kills")
        winner_side = None
        if kills_a is not None and kills_b is not None:
            if kills_a > kills_b:
                winner_side = "A"
            elif kills_b > kills_a:
                winner_side = "B"
        stmt = insert(GameResult).values(
            id=uuid4(),
            source_match_id=source_match_id,
            game_no=game_no,
            source_date=source_date,
            source_league=source_league,
            team_a_name=str(local.get("@name") or ""),
            team_b_name=str(away.get("@name") or ""),
            winner_side=winner_side,
            final_duration_seconds=_duration_to_seconds(row.get("duration")),
            final_gold_a=row.get("localteam_gold"),
            final_gold_b=row.get("awayteam_gold"),
            final_kills_a=kills_a,
            final_kills_b=kills_b,
            final_towers_a=row.get("localteam_towers"),
            final_towers_b=row.get("awayteam_towers"),
            final_dragons_a=row.get("localteam_dragons"),
            final_dragons_b=row.get("awayteam_dragons"),
            final_barons_a=row.get("localteam_barons"),
            final_barons_b=row.get("awayteam_barons"),
            raw_json={
                "match": match,
                "game_row": row,
                "series_score_a": final_series_a,
                "series_score_b": final_series_b,
            },
        ).on_conflict_do_update(
            index_elements=["source_match_id", "game_no"],
            set_={
                "source_date": source_date,
                "source_league": source_league,
                "team_a_name": str(local.get("@name") or ""),
                "team_b_name": str(away.get("@name") or ""),
                "winner_side": winner_side,
                "final_duration_seconds": _duration_to_seconds(row.get("duration")),
                "final_gold_a": row.get("localteam_gold"),
                "final_gold_b": row.get("awayteam_gold"),
                "final_kills_a": kills_a,
                "final_kills_b": kills_b,
                "final_towers_a": row.get("localteam_towers"),
                "final_towers_b": row.get("awayteam_towers"),
                "final_dragons_a": row.get("localteam_dragons"),
                "final_dragons_b": row.get("awayteam_dragons"),
                "final_barons_a": row.get("localteam_barons"),
                "final_barons_b": row.get("awayteam_barons"),
                "raw_json": {
                    "match": match,
                    "game_row": row,
                    "series_score_a": final_series_a,
                    "series_score_b": final_series_b,
                },
            },
        )
        db.execute(stmt)
        upserts += 1
    return upserts


def _evaluate_entry_decision(
    game_row: dict[str, Any],
    books: dict[str, Any],
    rules: list[GoldRule],
) -> dict[str, Any] | None:
    gold_diff = game_row.get("gold_diff")
    if gold_diff is None:
        return None
    duration_seconds = _duration_to_seconds(game_row.get("duration"))
    game_minute = int(duration_seconds / 60) if duration_seconds is not None else 0
    side = "A" if gold_diff >= 0 else "B"
    abs_gold = abs(float(gold_diff))
    if side == "A":
        ask = _book_price(books.get("a"), "best_ask")
        baron_diff = _safe_diff(game_row.get("localteam_barons"), game_row.get("awayteam_barons"))
    else:
        ask = _book_price(books.get("b"), "best_ask")
        baron_diff = _safe_diff(game_row.get("awayteam_barons"), game_row.get("localteam_barons"))
    if ask is None:
        return None

    for rule in rules:
        if game_minute < rule.minute_min:
            continue
        if abs_gold < rule.gold_diff_min:
            continue
        if ask > rule.max_ask:
            continue
        if rule.requires_baron and (baron_diff is None or baron_diff < 1.0):
            continue
        model_prob = _rule_model_prob(abs_gold=abs_gold, game_minute=game_minute, baron_diff=baron_diff or 0.0)
        edge = model_prob - ask
        if edge >= settings.gold_edge_required_edge:
            return {
                "picked_side": side,
                "ask_price": ask,
                "model_prob": model_prob,
                "edge": edge,
                "rule": rule,
            }
    return None


def _maybe_open_trade(
    *,
    db,
    mode: str,
    match: dict[str, Any],
    game_row: dict[str, Any],
    pm_ctx: dict[str, Any],
    decision: dict[str, Any],
    stake_usd: float,
    executor: ClobExecutor | None,
) -> None:
    source_match_id = str(match.get("@id") or "")
    game_no = game_row.get("game_no")
    open_existing = db.execute(
        select(GoldEdgeTrade.id)
        .where(GoldEdgeTrade.source_match_id == source_match_id)
        .where(GoldEdgeTrade.game_no == game_no)
        .where(GoldEdgeTrade.status == "open")
        .where(GoldEdgeTrade.mode == mode)
        .limit(1)
    ).scalar_one_or_none()
    if open_existing is not None:
        return

    pm_fixture: Fixture | None = pm_ctx.get("pm_fixture")
    picked_side = decision["picked_side"]
    token_id = pm_ctx.get("token_a") if picked_side == "A" else pm_ctx.get("token_b")
    entry_price = float(decision["ask_price"])
    model_prob = float(decision["model_prob"])
    edge = float(decision["edge"])
    shares = stake_usd / entry_price if entry_price > 0 else 0.0
    order_id = None
    order_status = "paper"
    trade_raw: dict[str, Any] = {
        "match": match,
        "game_row": game_row,
        "decision": {
            "picked_side": picked_side,
            "entry_price": entry_price,
            "model_prob": model_prob,
            "edge": edge,
            "rule": {
                "minute_min": decision["rule"].minute_min,
                "gold_diff_min": decision["rule"].gold_diff_min,
                "max_ask": decision["rule"].max_ask,
                "requires_baron": decision["rule"].requires_baron,
            },
        },
    }

    if mode == "live" and executor and token_id:
        result = executor.place_fak_order(
            token_id=token_id,
            side="BUY",
            price=entry_price,
            amount=stake_usd,
        )
        order_id = result.order_id
        order_status = result.status or ("ok" if result.success else "error")
        trade_raw["live_submission"] = result.raw
        if not result.success:
            order_status = f"error:{result.error_msg or 'submit_failed'}"

    db.add(
        GoldEdgeTrade(
            id=uuid4(),
            ts=datetime.now(tz=timezone.utc),
            mode=mode,
            status="open",
            source_match_id=source_match_id,
            game_no=game_no,
            team_a_name=str((match.get("localteam") or {}).get("@name") or ""),
            team_b_name=str((match.get("awayteam") or {}).get("@name") or ""),
            picked_side=picked_side,
            pm_fixture_id=pm_fixture.id if pm_fixture else None,
            token_id=token_id,
            entry_price=entry_price,
            model_prob=model_prob,
            edge_at_entry=edge,
            stake_usd=stake_usd,
            shares=shares if shares > 0 else None,
            order_id=order_id,
            order_status=order_status,
            raw_json=trade_raw,
        )
    )


def _resolve_open_trades(db) -> None:
    open_trades = db.execute(
        select(GoldEdgeTrade).where(GoldEdgeTrade.status == "open")
    ).scalars().all()
    if not open_trades:
        return
    for trade in open_trades:
        result = db.execute(
            select(GameResult).where(
                GameResult.source_match_id == trade.source_match_id,
                GameResult.game_no == trade.game_no,
            )
        ).scalar_one_or_none()
        if result is None or result.winner_side is None:
            continue
        shares = trade.shares if trade.shares is not None else (
            trade.stake_usd / trade.entry_price if trade.entry_price > 0 else 0.0
        )
        if trade.picked_side == result.winner_side:
            pnl = shares * (1.0 - trade.entry_price)
        else:
            pnl = -shares * trade.entry_price
        trade.status = "resolved"
        trade.winner_side = result.winner_side
        trade.resolved_at = datetime.now(tz=timezone.utc)
        trade.pnl_usd = pnl


def _parse_rules() -> list[GoldRule]:
    parsed: list[GoldRule] = []
    raw_rules = [r.strip() for r in settings.gold_edge_rules.split(";") if r.strip()]
    for raw in raw_rules:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) != 4:
            continue
        try:
            parsed.append(
                GoldRule(
                    minute_min=int(parts[0]),
                    gold_diff_min=float(parts[1]),
                    max_ask=float(parts[2]),
                    requires_baron=parts[3] in {"1", "true", "True"},
                )
            )
        except ValueError:
            continue
    return parsed


def _rule_model_prob(*, abs_gold: float, game_minute: int, baron_diff: float) -> float:
    # Simple deterministic baseline for rule-based EV checks.
    # Keep this intentionally conservative until enough data exists for calibration.
    prob = 0.50
    prob += min(abs_gold / 20000.0, 0.35)
    prob += min(max(game_minute - 10, 0) / 40.0, 0.10)
    if baron_diff >= 1:
        prob += 0.08
    return max(0.50, min(prob, 0.97))


def _duration_to_seconds(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parts = [int(p) for p in value.split(":")]
    except ValueError:
        return None
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    return None


def _similarity(left: str | None, right: str | None) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, _normalize(left), _normalize(right)).ratio()


def _normalize(value: str) -> str:
    return "".join(ch.lower() for ch in value if ch.isalnum())


def _book_price(book: dict[str, Any] | None, key: str) -> float | None:
    if not isinstance(book, dict):
        return None
    value = book.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
