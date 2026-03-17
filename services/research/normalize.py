"""Normalization contracts and SQL helpers for historical research materialization."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from services.research.dataset_profile import DiscoveredDatasetFile
from services.research.features import (
    NOTIONAL_BUCKETS_USD,
    PRICE_BUCKETS,
    QUANTITY_BUCKETS,
    TIME_TO_RESOLUTION_BUCKETS,
    TOPIC_KEYWORDS,
)

FILE_KIND_HINTS: dict[str, tuple[str, ...]] = {
    "trades": ("trade", "trades", "fills", "executions"),
    "markets": ("market", "markets", "contract", "contracts", "metadata"),
    "resolutions": ("resolution", "resolutions", "resolved", "outcome", "outcomes"),
}
TARGET_VIEW_NAMES: tuple[str, ...] = (
    "historical_markets",
    "historical_trades",
    "historical_resolutions",
    "historical_trade_features",
    "historical_bucket_stats",
)


@dataclass(frozen=True)
class ColumnSpec:
    aliases: tuple[str, ...]
    sql_type: str


TRADE_COLUMN_SPECS: dict[str, ColumnSpec] = {
    "trade_id": ColumnSpec(("trade_id", "id", "fill_id", "execution_id"), "VARCHAR"),
    "market_id": ColumnSpec(("market_id", "condition_id", "contract_id", "market"), "VARCHAR"),
    "event_id": ColumnSpec(("event_id", "series_id", "event"), "VARCHAR"),
    "trade_timestamp": ColumnSpec(("timestamp", "created_at", "created_time", "time", "ts", "executed_at", "trade_time"), "TIMESTAMP"),
    "price_raw": ColumnSpec(("price", "avg_price", "fill_price", "execution_price", "probability"), "DOUBLE"),
    "quantity_raw": ColumnSpec(("size", "shares", "quantity", "contracts", "volume"), "DOUBLE"),
    "notional_raw": ColumnSpec(("notional", "notional_usd", "usd_amount", "trade_value", "amount"), "DOUBLE"),
    "maker_taker_role_raw": ColumnSpec(("maker_taker_role", "liquidity_role", "role"), "VARCHAR"),
    "is_taker_raw": ColumnSpec(("is_taker", "taker", "aggressor"), "VARCHAR"),
    "taker_side": ColumnSpec(("taker_side", "aggressor_side", "side", "trade_side", "direction"), "VARCHAR"),
    "question": ColumnSpec(("question", "market_question", "prompt"), "VARCHAR"),
    "title": ColumnSpec(("title", "market_title", "name"), "VARCHAR"),
    "topic_raw": ColumnSpec(("topic", "category", "tag", "market_category"), "VARCHAR"),
}
MARKET_COLUMN_SPECS: dict[str, ColumnSpec] = {
    "market_id": ColumnSpec(("market_id", "condition_id", "contract_id", "market"), "VARCHAR"),
    "event_id": ColumnSpec(("event_id", "series_id", "event"), "VARCHAR"),
    "market_slug": ColumnSpec(("market_slug", "slug", "market_name_slug"), "VARCHAR"),
    "question": ColumnSpec(("question", "market_question", "prompt"), "VARCHAR"),
    "title": ColumnSpec(("title", "market_title", "name"), "VARCHAR"),
    "topic_raw": ColumnSpec(("topic", "category", "tag", "market_category"), "VARCHAR"),
    "status": ColumnSpec(("status", "state", "market_status"), "VARCHAR"),
    "open_timestamp": ColumnSpec(("open_time", "open_timestamp", "created_at", "created_time", "market_start_time"), "TIMESTAMP"),
    "close_timestamp": ColumnSpec(("close_time", "close_timestamp", "closed_at", "end_time"), "TIMESTAMP"),
    "resolution_timestamp": ColumnSpec(("resolution_time", "resolution_timestamp", "resolved_at", "closed_at", "end_time"), "TIMESTAMP"),
    "resolved_outcome": ColumnSpec(("resolved_outcome", "resolution", "result", "winner", "outcome"), "VARCHAR"),
}
RESOLUTION_COLUMN_SPECS: dict[str, ColumnSpec] = {
    "market_id": ColumnSpec(("market_id", "condition_id", "contract_id", "market"), "VARCHAR"),
    "resolution_timestamp": ColumnSpec(("resolution_time", "resolution_timestamp", "resolved_at", "closed_at"), "TIMESTAMP"),
    "resolved_outcome": ColumnSpec(("resolved_outcome", "resolution", "result", "winner", "outcome"), "VARCHAR"),
    "resolution_value": ColumnSpec(("resolution_value", "payout", "settlement_price", "final_price"), "DOUBLE"),
}


def classify_dataset_file_kind(relative_path: str) -> str:
    normalized = relative_path.lower()
    for kind, hints in FILE_KIND_HINTS.items():
        if any(hint in normalized for hint in hints):
            return kind
    return "all"


def build_source_file_map(
    parquet_files: Iterable[DiscoveredDatasetFile],
) -> dict[str, dict[str, list[DiscoveredDatasetFile]]]:
    grouped: dict[str, dict[str, list[DiscoveredDatasetFile]]] = {}
    for parquet_file in parquet_files:
        venue = sanitize_identifier(parquet_file.guessed_venue)
        kind = classify_dataset_file_kind(parquet_file.relative_path)

        venue_group = grouped.setdefault(
            venue,
            {"all": [], "trades": [], "markets": [], "resolutions": []},
        )
        venue_group["all"].append(parquet_file)
        if kind in venue_group:
            venue_group[kind].append(parquet_file)

    for venue_group in grouped.values():
        for kind in ("trades", "markets", "resolutions"):
            if not venue_group[kind]:
                venue_group[kind] = list(venue_group["all"])
    return grouped


def sanitize_identifier(value: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower())
    sanitized = sanitized.strip("_")
    return sanitized or "unknown"


def describe_view_columns(connection: object, view_name: str) -> set[str]:
    rows = connection.execute(f"DESCRIBE SELECT * FROM {quote_identifier(view_name)}").fetchall()
    return {row[0].lower() for row in rows}


def build_trade_select_sql(venue: str, relation: str, existing_columns: set[str]) -> str:
    trade_id_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["trade_id"])
    market_id_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["market_id"])
    event_id_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["event_id"])
    trade_timestamp_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["trade_timestamp"])
    price_raw_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["price_raw"])
    price_probability_expr = normalized_price_sql(price_raw_expr)
    quantity_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["quantity_raw"])
    notional_raw_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["notional_raw"])
    notional_expr = notional_sql(notional_raw_expr, quantity_expr, price_probability_expr)
    explicit_role_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["maker_taker_role_raw"])
    is_taker_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["is_taker_raw"])
    maker_taker_role_expr = maker_taker_role_sql(explicit_role_expr, is_taker_expr)
    taker_side_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["taker_side"])
    question_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["question"])
    title_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["title"])
    topic_raw_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["topic_raw"])

    return f"""
        SELECT
            '{venue}' AS venue,
            {trade_id_expr} AS trade_id,
            {market_id_expr} AS market_id,
            {event_id_expr} AS event_id,
            {trade_timestamp_expr} AS trade_timestamp,
            {price_probability_expr} AS price_probability,
            {quantity_expr} AS quantity,
            {notional_expr} AS notional_usd,
            {maker_taker_role_expr} AS maker_taker_role,
            {taker_side_expr} AS taker_side,
            {question_expr} AS question,
            {title_expr} AS title,
            {topic_raw_expr} AS topic_raw
        FROM {quote_identifier(relation)}
        WHERE {market_id_expr} IS NOT NULL
           OR {trade_timestamp_expr} IS NOT NULL
           OR {price_probability_expr} IS NOT NULL
    """


def build_market_select_sql(venue: str, relation: str, existing_columns: set[str]) -> str:
    market_id_expr = coalesce_existing(existing_columns, MARKET_COLUMN_SPECS["market_id"])
    event_id_expr = coalesce_existing(existing_columns, MARKET_COLUMN_SPECS["event_id"])
    market_slug_expr = coalesce_existing(existing_columns, MARKET_COLUMN_SPECS["market_slug"])
    question_expr = coalesce_existing(existing_columns, MARKET_COLUMN_SPECS["question"])
    title_expr = coalesce_existing(existing_columns, MARKET_COLUMN_SPECS["title"])
    topic_raw_expr = coalesce_existing(existing_columns, MARKET_COLUMN_SPECS["topic_raw"])
    status_expr = coalesce_existing(existing_columns, MARKET_COLUMN_SPECS["status"])
    open_timestamp_expr = coalesce_existing(existing_columns, MARKET_COLUMN_SPECS["open_timestamp"])
    close_timestamp_expr = coalesce_existing(existing_columns, MARKET_COLUMN_SPECS["close_timestamp"])
    resolution_timestamp_expr = coalesce_existing(existing_columns, MARKET_COLUMN_SPECS["resolution_timestamp"])
    resolved_outcome_expr = coalesce_existing(existing_columns, MARKET_COLUMN_SPECS["resolved_outcome"])

    return f"""
        SELECT
            '{venue}' AS venue,
            {market_id_expr} AS market_id,
            {event_id_expr} AS event_id,
            {market_slug_expr} AS market_slug,
            {question_expr} AS question,
            {title_expr} AS title,
            {topic_raw_expr} AS topic_raw,
            {status_expr} AS status,
            {open_timestamp_expr} AS open_timestamp,
            {close_timestamp_expr} AS close_timestamp,
            {resolution_timestamp_expr} AS resolution_timestamp,
            {resolved_outcome_expr} AS resolved_outcome
        FROM {quote_identifier(relation)}
        WHERE {market_id_expr} IS NOT NULL
           OR {question_expr} IS NOT NULL
           OR {title_expr} IS NOT NULL
    """


def build_resolution_select_sql(venue: str, relation: str, existing_columns: set[str]) -> str:
    market_id_expr = coalesce_existing(existing_columns, RESOLUTION_COLUMN_SPECS["market_id"])
    resolution_timestamp_expr = coalesce_existing(existing_columns, RESOLUTION_COLUMN_SPECS["resolution_timestamp"])
    resolved_outcome_expr = coalesce_existing(existing_columns, RESOLUTION_COLUMN_SPECS["resolved_outcome"])
    resolution_value_expr = coalesce_existing(existing_columns, RESOLUTION_COLUMN_SPECS["resolution_value"])

    return f"""
        SELECT
            '{venue}' AS venue,
            {market_id_expr} AS market_id,
            {resolution_timestamp_expr} AS resolution_timestamp,
            {resolved_outcome_expr} AS resolved_outcome,
            {resolution_value_expr} AS resolution_value
        FROM {quote_identifier(relation)}
        WHERE {market_id_expr} IS NOT NULL
           OR {resolution_timestamp_expr} IS NOT NULL
           OR {resolved_outcome_expr} IS NOT NULL
    """


def empty_select_sql(column_types: dict[str, str]) -> str:
    columns = ", ".join(
        f"CAST(NULL AS {sql_type}) AS {quote_identifier(column_name)}"
        for column_name, sql_type in column_types.items()
    )
    return f"SELECT {columns} WHERE FALSE"


def union_selects_or_empty(selects: list[str], empty_select: str) -> str:
    if not selects:
        return empty_select
    return "\nUNION ALL\n".join(selects)


def coalesce_existing(existing_columns: set[str], spec: ColumnSpec) -> str:
    available = [
        try_cast_identifier(alias, spec.sql_type)
        for alias in spec.aliases
        if alias.lower() in existing_columns
    ]
    if not available:
        return f"CAST(NULL AS {spec.sql_type})"
    if len(available) == 1:
        return available[0]
    return f"COALESCE({', '.join(available)})"


def normalized_price_sql(price_expr: str) -> str:
    return (
        "CASE "
        f"WHEN {price_expr} IS NULL THEN NULL "
        f"WHEN {price_expr} >= 0 AND {price_expr} <= 1 THEN {price_expr} "
        f"WHEN {price_expr} > 1 AND {price_expr} <= 100 THEN {price_expr} / 100.0 "
        "ELSE NULL END"
    )


def notional_sql(notional_expr: str, quantity_expr: str, price_probability_expr: str) -> str:
    return (
        "CASE "
        f"WHEN {notional_expr} IS NOT NULL THEN {notional_expr} "
        f"WHEN {quantity_expr} IS NOT NULL AND {price_probability_expr} IS NOT NULL "
        f"THEN {quantity_expr} * {price_probability_expr} "
        "ELSE NULL END"
    )


def maker_taker_role_sql(explicit_role_expr: str, is_taker_expr: str) -> str:
    explicit_role_case = normalized_role_case(explicit_role_expr)
    bool_role_case = (
        "CASE "
        f"WHEN LOWER(CAST({is_taker_expr} AS VARCHAR)) IN ('1', 'true', 't', 'yes') THEN 'taker' "
        f"WHEN LOWER(CAST({is_taker_expr} AS VARCHAR)) IN ('0', 'false', 'f', 'no') THEN 'maker' "
        "ELSE NULL END"
    )
    return f"COALESCE({explicit_role_case}, {bool_role_case})"


def normalized_role_case(expr: str) -> str:
    return (
        "CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN LOWER(CAST({expr} AS VARCHAR)) IN ('taker', 'aggressor', 'cross', 'taking') THEN 'taker' "
        f"WHEN LOWER(CAST({expr} AS VARCHAR)) IN ('maker', 'passive', 'posted', 'providing') THEN 'maker' "
        "ELSE NULL END"
    )


def price_bucket_case(price_expr: str) -> str:
    return bucket_case(price_expr, PRICE_BUCKETS, "gt_100c")


def notional_bucket_case(notional_expr: str) -> str:
    return bucket_case(notional_expr, NOTIONAL_BUCKETS_USD, "gte_5000_usd")


def quantity_bucket_case(quantity_expr: str) -> str:
    return bucket_case(quantity_expr, QUANTITY_BUCKETS, "gte_10000_shares")


def time_bucket_case(seconds_expr: str) -> str:
    return (
        "CASE "
        f"WHEN {seconds_expr} IS NULL THEN NULL "
        f"WHEN {seconds_expr} < 0 THEN 'negative' "
        + "".join(f"WHEN {seconds_expr} < {upper_bound} THEN '{label}' " for upper_bound, label in TIME_TO_RESOLUTION_BUCKETS)
        + "ELSE 'gte_90d' END"
    )


def bucket_case(expr: str, thresholds: tuple[tuple[float, str], ...], fallback_label: str) -> str:
    return (
        "CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN {expr} < 0 THEN NULL "
        + "".join(f"WHEN {expr} < {upper_bound} THEN '{label}' " for upper_bound, label in thresholds)
        + f"ELSE '{fallback_label}' END"
    )


def topic_class_case(topic_expr: str, question_expr: str, title_expr: str) -> str:
    text_expr = f"LOWER(COALESCE({topic_expr}, '') || ' ' || COALESCE({question_expr}, '') || ' ' || COALESCE({title_expr}, ''))"
    cases = []
    for topic, keywords in TOPIC_KEYWORDS:
        conditions = " OR ".join(f"{text_expr} LIKE '%{escape_sql_like(keyword.lower())}%'" for keyword in keywords)
        cases.append(f"WHEN {conditions} THEN '{topic}'")
    return "CASE " + " ".join(cases) + " ELSE 'unknown' END"


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def try_cast_identifier(identifier: str, sql_type: str) -> str:
    return f"TRY_CAST({quote_identifier(identifier)} AS {sql_type})"


def escape_sql_like(value: str) -> str:
    return value.replace("'", "''")

