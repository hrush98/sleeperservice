"""Pure feature derivation helpers for historical research views."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

PRICE_BUCKETS: tuple[tuple[float, str], ...] = (
    (0.01, "lt_1c"),
    (0.05, "1c_to_5c"),
    (0.10, "5c_to_10c"),
    (0.25, "10c_to_25c"),
    (0.40, "25c_to_40c"),
    (0.60, "40c_to_60c"),
    (0.75, "60c_to_75c"),
    (0.90, "75c_to_90c"),
    (0.99, "90c_to_99c"),
    (1.00, "99c_to_100c"),
)
NOTIONAL_BUCKETS_USD: tuple[tuple[float, str], ...] = (
    (10.0, "lt_10_usd"),
    (50.0, "10_to_50_usd"),
    (100.0, "50_to_100_usd"),
    (500.0, "100_to_500_usd"),
    (1000.0, "500_to_1000_usd"),
    (5000.0, "1000_to_5000_usd"),
)
QUANTITY_BUCKETS: tuple[tuple[float, str], ...] = (
    (10.0, "lt_10_shares"),
    (100.0, "10_to_100_shares"),
    (1000.0, "100_to_1000_shares"),
    (10000.0, "1000_to_10000_shares"),
)
TIME_TO_RESOLUTION_BUCKETS: tuple[tuple[float, str], ...] = (
    (3600.0, "lt_1h"),
    (6 * 3600.0, "1h_to_6h"),
    (24 * 3600.0, "6h_to_24h"),
    (7 * 24 * 3600.0, "1d_to_7d"),
    (30 * 24 * 3600.0, "7d_to_30d"),
    (90 * 24 * 3600.0, "30d_to_90d"),
)
TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("politics", ("election", "president", "senate", "house", "congress", "governor", "primary", "vote")),
    ("crypto", ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "token")),
    ("macro", ("fed", "fomc", "cpi", "inflation", "rates", "recession", "gdp", "jobs report")),
    ("sports", ("world cup", "nba", "nfl", "mlb", "nhl", "tennis", "soccer", "match", "game")),
    ("tech", ("apple", "tesla", "nvidia", "microsoft", "openai", "ai", "earnings", "launch")),
    ("geopolitics", ("war", "ceasefire", "ukraine", "china", "taiwan", "israel", "gaza", "iran")),
    ("culture", ("oscar", "grammy", "movie", "album", "box office", "award", "tv show")),
)


def coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def normalize_contract_price(value: Any) -> float | None:
    numeric_value = coerce_float(value)
    if numeric_value is None or numeric_value < 0:
        return None
    if numeric_value <= 1.0:
        return numeric_value
    if numeric_value <= 100.0:
        return numeric_value / 100.0
    return None


def bucket_probability_price(value: Any) -> str | None:
    normalized_value = normalize_contract_price(value)
    if normalized_value is None:
        return None
    for upper_bound, label in PRICE_BUCKETS:
        if normalized_value <= upper_bound:
            return label
    return "gt_100c"


def bucket_notional_usd(value: Any) -> str | None:
    numeric_value = coerce_float(value)
    if numeric_value is None or numeric_value < 0:
        return None
    for upper_bound, label in NOTIONAL_BUCKETS_USD:
        if numeric_value < upper_bound:
            return label
    return "gte_5000_usd"


def bucket_quantity(value: Any) -> str | None:
    numeric_value = coerce_float(value)
    if numeric_value is None or numeric_value < 0:
        return None
    for upper_bound, label in QUANTITY_BUCKETS:
        if numeric_value < upper_bound:
            return label
    return "gte_10000_shares"


def bucket_trade_size(*, quantity: Any = None, notional_usd: Any = None) -> str | None:
    notional_bucket = bucket_notional_usd(notional_usd)
    if notional_bucket is not None:
        return notional_bucket
    return bucket_quantity(quantity)


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, (int, float)):
        timestamp_value = float(value)
        if timestamp_value > 10_000_000_000:
            timestamp_value /= 1000.0
        return datetime.fromtimestamp(timestamp_value, tz=UTC)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            numeric_value = coerce_float(normalized)
            if numeric_value is None:
                return None
            return parse_timestamp(numeric_value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def time_to_resolution_seconds(trade_timestamp: Any, resolution_timestamp: Any) -> float | None:
    trade_dt = parse_timestamp(trade_timestamp)
    resolution_dt = parse_timestamp(resolution_timestamp)
    if trade_dt is None or resolution_dt is None:
        return None
    return (resolution_dt - trade_dt).total_seconds()


def bucket_time_to_resolution(seconds: Any) -> str | None:
    numeric_value = coerce_float(seconds)
    if numeric_value is None:
        return None
    if numeric_value < 0:
        return "negative"
    for upper_bound, label in TIME_TO_RESOLUTION_BUCKETS:
        if numeric_value < upper_bound:
            return label
    return "gte_90d"


def normalize_maker_taker_role(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "taker" if value else "maker"

    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized in {"taker", "aggressor", "cross", "taking", "1", "true", "t", "yes"}:
        return "taker"
    if normalized in {"maker", "passive", "posted", "providing", "0", "false", "f", "no"}:
        return "maker"
    return None


def classify_topic_text(*text_values: Any) -> str:
    normalized_text = " ".join(str(value).strip().lower() for value in text_values if value)
    if not normalized_text:
        return "unknown"

    for topic, keywords in TOPIC_KEYWORDS:
        if any(keyword in normalized_text for keyword in keywords):
            return topic
    return "unknown"

