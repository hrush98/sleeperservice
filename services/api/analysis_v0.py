"""V0 read-only analysis service built on promoted historical artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any

from services.research.artifacts import HistoricalArtifactStore
from services.research.features import (
    bucket_probability_price,
    bucket_time_to_resolution,
    classify_topic_text,
    parse_timestamp,
)


class MarketNotFoundError(ValueError):
    """Raised when a requested market cannot be found from the live client."""


class V0AnalysisService:
    """Combine live Polymarket state with promoted historical-study outputs."""

    def __init__(
        self,
        *,
        artifact_store: HistoricalArtifactStore,
        polymarket_client: Any,
    ):
        self.artifact_store = artifact_store
        self.polymarket_client = polymarket_client

    def list_ranked_opportunities(
        self,
        *,
        limit: int = 10,
        categories: set[str] | None = None,
        opportunity_types: set[str] | None = None,
        min_confidence: float = 0.5,
        min_edge_value: float = 0.0,
        time_horizon: str | None = None,
        include_trace: bool = False,
    ) -> dict[str, Any]:
        requested_types = opportunity_types or {"calibration_watch"}
        if "calibration_watch" not in requested_types:
            return self._empty_ranked_response(include_trace=include_trace)

        markets = self.polymarket_client.get_markets(active=True, limit=100)
        now = datetime.now(tz=UTC)
        candidates: list[dict[str, Any]] = []

        for market in markets:
            normalized = _normalize_market_payload(market, now=now)
            if normalized is None:
                continue
            if categories and normalized["category"] not in categories:
                continue
            if not _matches_time_horizon(normalized["time_to_resolution_seconds"], time_horizon):
                continue

            calibration_context = self.artifact_store.lookup_calibration_context(
                venue="polymarket",
                price_bucket=normalized["price_bucket"],
                time_to_resolution_bucket=normalized["time_to_resolution_bucket"],
            )
            if calibration_context is None:
                continue

            maker_taker_context = self.artifact_store.lookup_maker_taker_context(
                venue="polymarket",
                price_bucket=normalized["price_bucket"],
                time_to_resolution_bucket=normalized["time_to_resolution_bucket"],
            )
            sizing_context = self.artifact_store.lookup_sizing_context(
                venue="polymarket",
                price_bucket=normalized["price_bucket"],
                time_to_resolution_bucket=normalized["time_to_resolution_bucket"],
            )
            edge = _build_edge(calibration_context=calibration_context, sizing_context=sizing_context)
            confidence = _compute_confidence(
                calibration_context=calibration_context,
                sizing_context=sizing_context,
            )
            if confidence < min_confidence or edge["value"] < min_edge_value:
                continue

            warnings = _build_context_warnings(
                maker_taker_context=maker_taker_context,
                sizing_context=sizing_context,
            )
            candidates.append(
                {
                    "normalized": normalized,
                    "calibration_context": calibration_context,
                    "maker_taker_context": maker_taker_context,
                    "sizing_context": sizing_context,
                    "edge": edge,
                    "confidence": confidence,
                    "warnings": warnings,
                    "ranking_score": edge["value"] * confidence,
                }
            )

        candidates.sort(key=lambda item: item["ranking_score"], reverse=True)
        selected = candidates[: max(0, min(limit, 50))]

        opportunities = []
        for item in selected:
            normalized = item["normalized"]
            price_snapshot, market_state, live_warnings = self._live_snapshot_for_market(
                market=normalized["market"],
                fallback_midpoint=normalized["midpoint"],
                now=now,
            )
            warnings = [*item["warnings"], *live_warnings]
            opportunities.append(
                {
                    "market_id": normalized["market_id"],
                    "slug": normalized["slug"],
                    "title": normalized["title"],
                    "category": normalized["category"],
                    "opportunity_type": "calibration_watch",
                    "suggested_side": item["edge"]["suggested_side"],
                    "price_snapshot": price_snapshot,
                    "edge": item["edge"],
                    "confidence": item["confidence"],
                    "explanation_summary": _build_opportunity_summary(
                        calibration_context=item["calibration_context"],
                        sizing_context=item["sizing_context"],
                    ),
                    "market_state": market_state,
                    "calibration_context": item["calibration_context"],
                    "maker_taker_context": item["maker_taker_context"],
                    "sizing_context": item["sizing_context"],
                    "warnings": warnings,
                    "evidence_trace": (
                        _build_evidence_trace(
                            calibration_context=item["calibration_context"],
                            maker_taker_context=item["maker_taker_context"],
                            sizing_context=item["sizing_context"],
                        )
                        if include_trace
                        else []
                    ),
                }
            )

        return {
            "opportunities": opportunities,
            "warnings": [] if opportunities else _ranked_response_warnings(self.artifact_store),
            "metadata": {
                "generated_at": now.isoformat(),
                "total_scanned": len(markets),
                "trace_included": include_trace,
                "artifacts": self.artifact_store.bundle_provenance(),
            },
        }

    def get_market_analysis(
        self,
        market_id: str,
        *,
        include_trace: bool = False,
    ) -> dict[str, Any]:
        market = self.polymarket_client.get_market_by_id(market_id)
        if not isinstance(market, dict):
            raise MarketNotFoundError(f"Unknown market id: {market_id}")

        now = datetime.now(tz=UTC)
        normalized = _normalize_market_payload(market, now=now)
        if normalized is None:
            raise MarketNotFoundError(f"Market is not a supported active binary market: {market_id}")

        price_snapshot, market_state, live_warnings = self._live_snapshot_for_market(
            market=market,
            fallback_midpoint=normalized["midpoint"],
            now=now,
        )
        calibration_context = self.artifact_store.lookup_calibration_context(
            venue="polymarket",
            price_bucket=normalized["price_bucket"],
            time_to_resolution_bucket=normalized["time_to_resolution_bucket"],
        )
        maker_taker_context = self.artifact_store.lookup_maker_taker_context(
            venue="polymarket",
            price_bucket=normalized["price_bucket"],
            time_to_resolution_bucket=normalized["time_to_resolution_bucket"],
        )
        sizing_context = self.artifact_store.lookup_sizing_context(
            venue="polymarket",
            price_bucket=normalized["price_bucket"],
            time_to_resolution_bucket=normalized["time_to_resolution_bucket"],
        )
        warnings = [
            *live_warnings,
            *_build_context_warnings(
                maker_taker_context=maker_taker_context,
                sizing_context=sizing_context,
            ),
        ]
        if calibration_context is None:
            warnings.append(
                "No promoted historical calibration context is available for the current price and time bucket."
            )

        return {
            "market_id": normalized["market_id"],
            "slug": normalized["slug"],
            "title": normalized["title"],
            "category": normalized["category"],
            "price_snapshot": price_snapshot,
            "market_state": market_state,
            "coherence_findings": [],
            "calibration_context": calibration_context,
            "maker_taker_context": maker_taker_context,
            "sizing_context": sizing_context,
            "warnings": warnings,
            "evidence_trace": (
                _build_evidence_trace(
                    calibration_context=calibration_context,
                    maker_taker_context=maker_taker_context,
                    sizing_context=sizing_context,
                )
                if include_trace
                else []
            ),
            "metadata": {
                "generated_at": now.isoformat(),
                "trace_included": include_trace,
                "artifacts": self.artifact_store.bundle_provenance(),
            },
        }

    def _live_snapshot_for_market(
        self,
        *,
        market: dict[str, Any],
        fallback_midpoint: float | None,
        now: datetime,
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        warnings: list[str] = []
        yes_token_id = _extract_yes_token_id(market)
        orderbook = None
        if yes_token_id:
            orderbook = self.polymarket_client.get_clob_orderbook(yes_token_id)
        else:
            warnings.append("Market is missing a parseable Yes token id; book state is limited.")

        price_snapshot = _build_price_snapshot(
            orderbook=orderbook,
            fallback_midpoint=fallback_midpoint,
            as_of=now,
        )
        market_state = _build_market_state(
            orderbook=orderbook,
            price_snapshot=price_snapshot,
            as_of=now,
        )
        if price_snapshot["bid"] is None or price_snapshot["ask"] is None:
            warnings.append("Live bid/ask data is unavailable; midpoint may fall back to Gamma market pricing.")
        return price_snapshot, market_state, warnings

    def _empty_ranked_response(self, *, include_trace: bool) -> dict[str, Any]:
        return {
            "opportunities": [],
            "warnings": _ranked_response_warnings(self.artifact_store),
            "metadata": {
                "generated_at": datetime.now(tz=UTC).isoformat(),
                "total_scanned": 0,
                "trace_included": include_trace,
                "artifacts": self.artifact_store.bundle_provenance(),
            },
        }


def _normalize_market_payload(market: dict[str, Any], *, now: datetime) -> dict[str, Any] | None:
    outcomes = _parse_json_list(market.get("outcomes"))
    outcome_prices = _parse_json_list(market.get("outcomePrices"))
    token_ids = _parse_json_list(market.get("clobTokenIds"))
    normalized_outcomes = [str(value).strip().lower() for value in outcomes]
    if len(normalized_outcomes) < 2 or set(normalized_outcomes[:2]) != {"yes", "no"}:
        return None
    yes_index = normalized_outcomes.index("yes")
    no_index = normalized_outcomes.index("no")
    try:
        yes_price = float(outcome_prices[yes_index])
        no_price = float(outcome_prices[no_index])
    except (IndexError, TypeError, ValueError):
        return None
    if yes_price <= 0.0 or yes_price >= 1.0 or no_price <= 0.0 or no_price >= 1.0:
        return None

    end_dt = parse_timestamp(market.get("endDate"))
    if end_dt is None:
        return None
    time_to_resolution = (end_dt - now).total_seconds()
    time_bucket = bucket_time_to_resolution(time_to_resolution)
    price_bucket = bucket_probability_price(yes_price)
    if time_bucket is None or price_bucket is None:
        return None

    tag_labels = [
        str(tag.get("label") or "")
        for tag in (market.get("tags") or [])
        if isinstance(tag, dict)
    ]
    category = classify_topic_text(
        market.get("question"),
        market.get("title"),
        market.get("description"),
        *tag_labels,
    )

    return {
        "market": market,
        "market_id": str(market.get("id") or ""),
        "slug": str(market.get("slug") or market.get("market_slug") or ""),
        "title": str(market.get("question") or market.get("title") or ""),
        "category": category,
        "midpoint": yes_price,
        "price_bucket": price_bucket,
        "time_to_resolution_bucket": time_bucket,
        "time_to_resolution_seconds": time_to_resolution,
        "yes_token_id": str(token_ids[yes_index]) if yes_index < len(token_ids) else "",
    }


def _matches_time_horizon(time_to_resolution_seconds: float, time_horizon: str | None) -> bool:
    if time_horizon is None:
        return True
    limit_map = {
        "24h": 24 * 3600,
        "7d": 7 * 24 * 3600,
        "30d": 30 * 24 * 3600,
    }
    max_seconds = limit_map.get(time_horizon)
    if max_seconds is None:
        return True
    return 0 <= time_to_resolution_seconds <= max_seconds


def _build_edge(
    *,
    calibration_context: dict[str, Any],
    sizing_context: dict[str, Any] | None,
) -> dict[str, Any]:
    avg_miscalibration = float(calibration_context["avg_miscalibration"])
    haircut = float(sizing_context["recommended_haircut_multiplier"]) if sizing_context else 0.25
    signed_points = -avg_miscalibration * 100.0
    return {
        "metric": "historical_miscalibration_points",
        "value": round(abs(signed_points) * haircut, 2),
        "basis": "historical_calibration_miscalibration",
        "suggested_side": "yes" if signed_points >= 0 else "no",
    }


def _compute_confidence(
    *,
    calibration_context: dict[str, Any],
    sizing_context: dict[str, Any] | None,
) -> float:
    trade_count = float(calibration_context.get("historical_trade_count") or 0.0)
    market_count = float(calibration_context.get("historical_market_count") or 0.0)
    coverage_score = min(1.0, trade_count / 500.0)
    breadth_score = min(1.0, market_count / 100.0)
    confidence = 0.55 + 0.20 * coverage_score + 0.10 * breadth_score

    if sizing_context is not None:
        promotion_status = sizing_context.get("promotion_status")
        if promotion_status == "candidate":
            confidence += 0.15
        elif promotion_status == "candidate_with_haircut":
            confidence += 0.10
        elif promotion_status == "experimental_sparse":
            confidence -= 0.05
        elif promotion_status == "do_not_promote":
            confidence -= 0.15

    return round(min(0.99, max(0.05, confidence)), 2)


def _build_opportunity_summary(
    *,
    calibration_context: dict[str, Any],
    sizing_context: dict[str, Any] | None,
) -> str:
    avg_miscalibration = float(calibration_context["avg_miscalibration"]) * 100.0
    if avg_miscalibration < 0:
        direction = "overpriced on the Yes side"
    elif avg_miscalibration > 0:
        direction = "underpriced on the Yes side"
    else:
        direction = "roughly in line with historical resolution rates"

    summary = (
        f"This bucket has historically been {direction} by {abs(avg_miscalibration):.1f} points."
    )
    if sizing_context is not None:
        summary += (
            f" Current sizing prior recommends a {int(float(sizing_context['recommended_haircut_multiplier']) * 100)}% multiplier."
        )
    return summary


def _build_context_warnings(
    *,
    maker_taker_context: dict[str, Any] | None,
    sizing_context: dict[str, Any] | None,
) -> list[str]:
    warnings: list[str] = []
    if maker_taker_context is not None and maker_taker_context.get("role_basis") == "counterparty_inferred":
        warnings.append(
            "Maker/taker context is inferred from matched counterparty rows rather than direct passive-fill observation."
        )
    if sizing_context is not None and sizing_context.get("promotion_status") == "experimental_sparse":
        warnings.append("Sizing context is still sparse and should remain experimental.")
    return warnings


def _build_evidence_trace(
    *,
    calibration_context: dict[str, Any] | None,
    maker_taker_context: dict[str, Any] | None,
    sizing_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    if calibration_context is not None:
        trace.append(
            {
                "kind": "historical_calibration",
                "summary": (
                    "Current bucket matched promoted calibration output for "
                    f"{calibration_context['price_bucket']} / {calibration_context['time_to_resolution_bucket']}."
                ),
                "source": "historical_study.calibration.v1",
                "metrics": {
                    "avg_miscalibration": calibration_context["avg_miscalibration"],
                    "historical_trade_count": calibration_context["historical_trade_count"],
                    "historical_market_count": calibration_context["historical_market_count"],
                },
            }
        )
    if maker_taker_context is not None:
        trace.append(
            {
                "kind": "maker_taker_expectancy",
                "summary": maker_taker_context["summary"],
                "source": "historical_study.maker_taker_expectancy.v1",
                "metrics": {
                    "maker_avg_pnl_per_contract": maker_taker_context["maker_avg_pnl_per_contract"],
                    "taker_avg_pnl_per_contract": maker_taker_context["taker_avg_pnl_per_contract"],
                    "role_basis": maker_taker_context["role_basis"],
                },
            }
        )
    if sizing_context is not None:
        trace.append(
            {
                "kind": "sizing_prior",
                "summary": sizing_context["summary"],
                "source": "historical_study.sizing_priors.v1",
                "metrics": {
                    "recommended_haircut_multiplier": sizing_context["recommended_haircut_multiplier"],
                    "promotion_status": sizing_context["promotion_status"],
                    "edge_to_noise_ratio": sizing_context["edge_to_noise_ratio"],
                },
            }
        )
    return trace


def _build_price_snapshot(
    *,
    orderbook: dict[str, Any] | None,
    fallback_midpoint: float | None,
    as_of: datetime,
) -> dict[str, Any]:
    best_bid = _safe_float(orderbook.get("best_bid")) if isinstance(orderbook, dict) else None
    best_ask = _safe_float(orderbook.get("best_ask")) if isinstance(orderbook, dict) else None
    midpoint = _safe_float(orderbook.get("mid")) if isinstance(orderbook, dict) else None
    if midpoint is None:
        midpoint = fallback_midpoint
    spread_cents = None
    if best_bid is not None and best_ask is not None:
        spread_cents = round((best_ask - best_bid) * 100.0, 2)
    return {
        "bid": best_bid,
        "ask": best_ask,
        "midpoint": round(midpoint, 4) if midpoint is not None else None,
        "spread_cents": spread_cents,
        "as_of": as_of.isoformat(),
    }


def _build_market_state(
    *,
    orderbook: dict[str, Any] | None,
    price_snapshot: dict[str, Any],
    as_of: datetime,
) -> dict[str, Any]:
    midpoint = price_snapshot.get("midpoint")
    bids = orderbook.get("bids") if isinstance(orderbook, dict) else []
    asks = orderbook.get("asks") if isinstance(orderbook, dict) else []
    if midpoint is None or not isinstance(bids, list) or not isinstance(asks, list):
        return {
            "price_velocity_15m_points": None,
            "price_velocity_1h_points": None,
            "volume_1h_usd": None,
            "volume_zscore_1h": None,
            "depth_near_mid_usd": None,
            "book_imbalance_ratio": None,
            "volatility_regime": "unknown",
            "state_summary": "Book-state context is unavailable for this market.",
            "as_of": as_of.isoformat(),
        }

    depth_window = 0.02
    bid_depth = sum(
        _safe_float(level.get("price"), default=0.0) * _safe_float(level.get("size"), default=0.0)
        for level in bids
        if _safe_float(level.get("price")) is not None
        and _safe_float(level.get("price")) >= midpoint - depth_window
    )
    ask_depth = sum(
        _safe_float(level.get("price"), default=0.0) * _safe_float(level.get("size"), default=0.0)
        for level in asks
        if _safe_float(level.get("price")) is not None
        and _safe_float(level.get("price")) <= midpoint + depth_window
    )
    total_depth = bid_depth + ask_depth
    imbalance_ratio = None
    if ask_depth > 0:
        imbalance_ratio = round(bid_depth / ask_depth, 2)

    spread_cents = price_snapshot.get("spread_cents")
    if spread_cents is None:
        regime = "unknown"
    elif spread_cents >= 5.0 or total_depth < 250.0:
        regime = "elevated"
    elif spread_cents <= 2.0 and total_depth >= 1000.0:
        regime = "calm"
    else:
        regime = "normal"

    summary = (
        f"Book-state only: spread {spread_cents if spread_cents is not None else 'n/a'}c, "
        f"near-mid depth ${total_depth:.0f}, "
        f"bid/ask depth ratio {imbalance_ratio if imbalance_ratio is not None else 'n/a'}."
    )
    return {
        "price_velocity_15m_points": None,
        "price_velocity_1h_points": None,
        "volume_1h_usd": None,
        "volume_zscore_1h": None,
        "depth_near_mid_usd": round(total_depth, 2),
        "book_imbalance_ratio": imbalance_ratio,
        "volatility_regime": regime,
        "state_summary": summary,
        "as_of": as_of.isoformat(),
    }


def _extract_yes_token_id(market: dict[str, Any]) -> str:
    outcomes = _parse_json_list(market.get("outcomes"))
    token_ids = _parse_json_list(market.get("clobTokenIds"))
    normalized_outcomes = [str(value).strip().lower() for value in outcomes]
    if "yes" not in normalized_outcomes:
        return ""
    yes_index = normalized_outcomes.index("yes")
    if yes_index >= len(token_ids):
        return ""
    return str(token_ids[yes_index])


def _parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _safe_float(value: Any, *, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ranked_response_warnings(artifact_store: HistoricalArtifactStore) -> list[str]:
    if artifact_store.latest_bundle() is None:
        return [
            "No promoted historical-study bundle is available yet; ranked opportunities are unavailable."
        ]
    return [
        "No active markets matched the current filters and promoted historical contexts."
    ]
