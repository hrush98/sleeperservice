"""Normalization contracts and SQL helpers for historical research materialization."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from services.research.dataset_profile import DatasetCollection
from services.research.features import (
    NOTIONAL_BUCKETS_USD,
    PRICE_BUCKETS,
    QUANTITY_BUCKETS,
    TIME_TO_RESOLUTION_BUCKETS,
    TOPIC_KEYWORDS,
)

SOURCE_VIEW_KINDS: tuple[str, ...] = (
    "all",
    "trades",
    "markets",
    "resolutions",
    "legacy_trades",
    "blocks",
)
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
    "market_id": ColumnSpec(("market_id", "condition_id", "contract_id", "market", "ticker"), "VARCHAR"),
    "event_id": ColumnSpec(("event_id", "series_id", "event", "event_ticker"), "VARCHAR"),
    "trade_timestamp": ColumnSpec(
        ("timestamp", "created_at", "created_time", "time", "ts", "executed_at", "trade_time"),
        "TIMESTAMP",
    ),
    "price_raw": ColumnSpec(
        ("price", "avg_price", "fill_price", "execution_price", "probability", "last_price"),
        "DOUBLE",
    ),
    "quantity_raw": ColumnSpec(("size", "shares", "quantity", "contracts", "volume", "count"), "DOUBLE"),
    "notional_raw": ColumnSpec(("notional", "notional_usd", "usd_amount", "trade_value", "amount"), "DOUBLE"),
    "maker_taker_role_raw": ColumnSpec(("maker_taker_role", "liquidity_role", "role"), "VARCHAR"),
    "is_taker_raw": ColumnSpec(("is_taker", "taker", "aggressor"), "VARCHAR"),
    "taker_side": ColumnSpec(("taker_side", "aggressor_side", "side", "trade_side", "direction"), "VARCHAR"),
    "contract_side": ColumnSpec(("contract_side", "outcome", "outcome_name"), "VARCHAR"),
    "question": ColumnSpec(("question", "market_question", "prompt", "title"), "VARCHAR"),
    "title": ColumnSpec(("title", "market_title", "name", "question"), "VARCHAR"),
    "topic_raw": ColumnSpec(("topic", "category", "tag", "market_category"), "VARCHAR"),
}
MARKET_COLUMN_SPECS: dict[str, ColumnSpec] = {
    "market_id": ColumnSpec(("market_id", "condition_id", "contract_id", "market", "ticker", "id"), "VARCHAR"),
    "event_id": ColumnSpec(("event_id", "series_id", "event", "event_ticker"), "VARCHAR"),
    "market_slug": ColumnSpec(("market_slug", "slug", "market_name_slug", "ticker"), "VARCHAR"),
    "question": ColumnSpec(("question", "market_question", "prompt", "title"), "VARCHAR"),
    "title": ColumnSpec(("title", "market_title", "name", "question"), "VARCHAR"),
    "topic_raw": ColumnSpec(("topic", "category", "tag", "market_category"), "VARCHAR"),
    "status": ColumnSpec(("status", "state", "market_status"), "VARCHAR"),
    "open_timestamp": ColumnSpec(
        ("open_time", "open_timestamp", "created_at", "created_time", "market_start_time"),
        "TIMESTAMP",
    ),
    "close_timestamp": ColumnSpec(("close_time", "close_timestamp", "closed_at", "end_time"), "TIMESTAMP"),
    "resolution_timestamp": ColumnSpec(
        ("resolution_time", "resolution_timestamp", "resolved_at", "closed_at", "end_time"),
        "TIMESTAMP",
    ),
    "resolved_outcome": ColumnSpec(("resolved_outcome", "resolution", "result", "winner", "outcome"), "VARCHAR"),
}
RESOLUTION_COLUMN_SPECS: dict[str, ColumnSpec] = {
    "market_id": ColumnSpec(("market_id", "condition_id", "contract_id", "market", "ticker", "id"), "VARCHAR"),
    "resolution_timestamp": ColumnSpec(("resolution_time", "resolution_timestamp", "resolved_at", "closed_at"), "TIMESTAMP"),
    "resolved_outcome": ColumnSpec(("resolved_outcome", "resolution", "result", "winner", "outcome"), "VARCHAR"),
    "resolution_value": ColumnSpec(("resolution_value", "payout", "settlement_price", "final_price"), "DOUBLE"),
}


def build_source_collection_map(
    collections: Iterable[DatasetCollection],
) -> dict[str, dict[str, list[DatasetCollection]]]:
    grouped: dict[str, dict[str, list[DatasetCollection]]] = {}
    for collection in collections:
        venue = sanitize_identifier(collection.guessed_venue)
        venue_group = grouped.setdefault(
            venue,
            {kind: [] for kind in SOURCE_VIEW_KINDS},
        )
        venue_group["all"].append(collection)
        if collection.kind in venue_group:
            venue_group[collection.kind].append(collection)

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


def build_trade_select_sql(
    venue: str,
    relation: str,
    existing_columns: set[str],
) -> str:
    if venue == "kalshi" and {"ticker", "trade_id", "yes_price", "no_price"}.issubset(existing_columns):
        return build_kalshi_trade_select_sql(relation)
    return build_generic_trade_select_sql(venue, relation, existing_columns)


def build_market_select_sql(
    venue: str,
    relation: str,
    existing_columns: set[str],
) -> str:
    if venue == "kalshi" and {"ticker", "title"}.issubset(existing_columns):
        return build_kalshi_market_select_sql(relation)
    if venue == "polymarket" and {"id", "question", "clob_token_ids"}.issubset(existing_columns):
        return build_polymarket_market_select_sql(relation)
    return build_generic_market_select_sql(venue, relation, existing_columns)


def build_resolution_select_sql(
    venue: str,
    relation: str,
    existing_columns: set[str],
) -> str:
    if venue == "kalshi" and {"ticker", "result"}.issubset(existing_columns):
        return build_kalshi_resolution_select_sql(relation)
    if venue == "polymarket" and {"id", "outcome_prices", "outcomes"}.issubset(existing_columns):
        return build_polymarket_resolution_select_sql(relation)
    return build_generic_resolution_select_sql(venue, relation, existing_columns)


def build_polymarket_trade_select_sql(
    trade_relation: str,
    market_relation: str,
    *,
    blocks_relation: str | None = None,
    legacy_relation: str | None = None,
) -> str:
    block_lookup_sql = build_polymarket_block_lookup_sql(blocks_relation)
    legacy_union_sql = ""
    if legacy_relation is not None:
        legacy_union_sql = f"""
        UNION ALL
        SELECT
            'polymarket' AS venue,
            CAST(t.transaction_hash AS VARCHAR) || ':' || CAST(t.log_index AS VARCHAR) AS trade_id,
            tr.market_id AS market_id,
            tr.event_id AS event_id,
            COALESCE(b.block_timestamp, {timestamp_cast_sql('t.timestamp')}) AS trade_timestamp,
            CASE
                WHEN TRY_CAST(t.outcome_tokens AS DOUBLE) > 0
                THEN TRY_CAST(t.amount AS DOUBLE) / TRY_CAST(t.outcome_tokens AS DOUBLE)
                ELSE NULL
            END AS price_probability,
            CAST(NULL AS DOUBLE) AS quantity,
            TRY_CAST(t.amount AS DOUBLE) / 1000000.0 AS notional_usd,
            'taker' AS maker_taker_role,
            CASE WHEN t.is_buy THEN 'buy' ELSE 'sell' END AS taker_side,
            CASE
                WHEN TRY_CAST(t.outcome_index AS BIGINT) = 0 THEN tr.outcome_0_name
                WHEN TRY_CAST(t.outcome_index AS BIGINT) = 1 THEN tr.outcome_1_name
                ELSE NULL
            END AS contract_side,
            tr.question AS question,
            tr.title AS title,
            tr.topic_raw AS topic_raw
        FROM {quote_identifier(legacy_relation)} AS t
        INNER JOIN token_reference AS tr
          ON LOWER(CAST(t.fpmm_address AS VARCHAR)) = tr.market_maker_address
        LEFT JOIN block_lookup AS b
          ON t.block_number = b.block_number
        WHERE TRY_CAST(t.amount AS DOUBLE) > 0
        """

    return f"""
        WITH token_reference AS (
            {build_polymarket_market_reference_sql(market_relation)}
        ),
        block_lookup AS (
            {block_lookup_sql}
        ),
        ctf_trades AS (
            SELECT
                'polymarket' AS venue,
                CAST(t.transaction_hash AS VARCHAR) || ':' || CAST(t.log_index AS VARCHAR) AS trade_id,
                tr.market_id AS market_id,
                tr.event_id AS event_id,
                COALESCE(b.block_timestamp, {timestamp_cast_sql('t.timestamp')}) AS trade_timestamp,
                CASE
                    WHEN t.maker_asset_id = '0' AND TRY_CAST(t.taker_amount AS DOUBLE) > 0
                    THEN TRY_CAST(t.maker_amount AS DOUBLE) / TRY_CAST(t.taker_amount AS DOUBLE)
                    WHEN t.taker_asset_id = '0' AND TRY_CAST(t.maker_amount AS DOUBLE) > 0
                    THEN TRY_CAST(t.taker_amount AS DOUBLE) / TRY_CAST(t.maker_amount AS DOUBLE)
                    ELSE NULL
                END AS price_probability,
                CASE
                    WHEN t.maker_asset_id = '0' THEN TRY_CAST(t.taker_amount AS DOUBLE) / 1000000.0
                    WHEN t.taker_asset_id = '0' THEN TRY_CAST(t.maker_amount AS DOUBLE) / 1000000.0
                    ELSE NULL
                END AS quantity,
                CASE
                    WHEN t.maker_asset_id = '0' THEN TRY_CAST(t.maker_amount AS DOUBLE) / 1000000.0
                    WHEN t.taker_asset_id = '0' THEN TRY_CAST(t.taker_amount AS DOUBLE) / 1000000.0
                    ELSE NULL
                END AS notional_usd,
                'taker' AS maker_taker_role,
                CASE
                    WHEN t.maker_asset_id = '0' THEN 'sell'
                    WHEN t.taker_asset_id = '0' THEN 'buy'
                    ELSE NULL
                END AS taker_side,
                CASE
                    WHEN CASE WHEN t.maker_asset_id = '0' THEN t.taker_asset_id ELSE t.maker_asset_id END = tr.token_0_id
                    THEN tr.outcome_0_name
                    WHEN CASE WHEN t.maker_asset_id = '0' THEN t.taker_asset_id ELSE t.maker_asset_id END = tr.token_1_id
                    THEN tr.outcome_1_name
                    ELSE NULL
                END AS contract_side,
                tr.question AS question,
                tr.title AS title,
                tr.topic_raw AS topic_raw
            FROM {quote_identifier(trade_relation)} AS t
            INNER JOIN token_reference AS tr
              ON (
                   CASE WHEN t.maker_asset_id = '0' THEN t.taker_asset_id ELSE t.maker_asset_id END = tr.token_0_id
                OR CASE WHEN t.maker_asset_id = '0' THEN t.taker_asset_id ELSE t.maker_asset_id END = tr.token_1_id
              )
            LEFT JOIN block_lookup AS b
              ON t.block_number = b.block_number
            WHERE t.maker_asset_id = '0' OR t.taker_asset_id = '0'
        )
        SELECT * FROM ctf_trades
        {legacy_union_sql}
    """


def build_generic_trade_select_sql(venue: str, relation: str, existing_columns: set[str]) -> str:
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
    contract_side_expr = coalesce_existing(existing_columns, TRADE_COLUMN_SPECS["contract_side"])
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
            {contract_side_expr} AS contract_side,
            {question_expr} AS question,
            {title_expr} AS title,
            {topic_raw_expr} AS topic_raw
        FROM {quote_identifier(relation)}
        WHERE {market_id_expr} IS NOT NULL
           OR {trade_timestamp_expr} IS NOT NULL
           OR {price_probability_expr} IS NOT NULL
    """


def build_kalshi_trade_select_sql(relation: str) -> str:
    return f"""
        SELECT
            'kalshi' AS venue,
            CAST(trade_id AS VARCHAR) AS trade_id,
            CAST(ticker AS VARCHAR) AS market_id,
            CAST(NULL AS VARCHAR) AS event_id,
            {timestamp_cast_sql('created_time')} AS trade_timestamp,
            CASE
                WHEN LOWER(CAST(taker_side AS VARCHAR)) = 'yes' THEN TRY_CAST(yes_price AS DOUBLE) / 100.0
                WHEN LOWER(CAST(taker_side AS VARCHAR)) = 'no' THEN TRY_CAST(no_price AS DOUBLE) / 100.0
                ELSE NULL
            END AS price_probability,
            TRY_CAST(count AS DOUBLE) AS quantity,
            CASE
                WHEN LOWER(CAST(taker_side AS VARCHAR)) = 'yes'
                THEN TRY_CAST(count AS DOUBLE) * TRY_CAST(yes_price AS DOUBLE) / 100.0
                WHEN LOWER(CAST(taker_side AS VARCHAR)) = 'no'
                THEN TRY_CAST(count AS DOUBLE) * TRY_CAST(no_price AS DOUBLE) / 100.0
                ELSE NULL
            END AS notional_usd,
            'taker' AS maker_taker_role,
            'buy' AS taker_side,
            LOWER(CAST(taker_side AS VARCHAR)) AS contract_side,
            CAST(NULL AS VARCHAR) AS question,
            CAST(NULL AS VARCHAR) AS title,
            CAST(NULL AS VARCHAR) AS topic_raw
        FROM {quote_identifier(relation)}
        WHERE ticker IS NOT NULL
    """


def build_generic_market_select_sql(venue: str, relation: str, existing_columns: set[str]) -> str:
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


def build_kalshi_market_select_sql(relation: str) -> str:
    return f"""
        SELECT
            'kalshi' AS venue,
            CAST(ticker AS VARCHAR) AS market_id,
            CAST(event_ticker AS VARCHAR) AS event_id,
            CAST(ticker AS VARCHAR) AS market_slug,
            CAST(title AS VARCHAR) AS question,
            CAST(title AS VARCHAR) AS title,
            CAST(NULL AS VARCHAR) AS topic_raw,
            CAST(status AS VARCHAR) AS status,
            {timestamp_cast_sql('created_time')} AS open_timestamp,
            {timestamp_cast_sql('close_time')} AS close_timestamp,
            CASE
                WHEN COALESCE(CAST(result AS VARCHAR), '') <> ''
                THEN COALESCE({timestamp_cast_sql('close_time')}, {timestamp_cast_sql('created_time')})
                ELSE NULL
            END AS resolution_timestamp,
            CASE
                WHEN LOWER(CAST(result AS VARCHAR)) IN ('yes', 'no') THEN LOWER(CAST(result AS VARCHAR))
                ELSE NULL
            END AS resolved_outcome
        FROM {quote_identifier(relation)}
        WHERE ticker IS NOT NULL
    """


def build_polymarket_market_select_sql(relation: str) -> str:
    return f"""
        WITH market_reference AS (
            {build_polymarket_market_reference_sql(relation)}
        )
        SELECT
            'polymarket' AS venue,
            market_id,
            event_id,
            market_slug,
            question,
            title,
            topic_raw,
            status,
            open_timestamp,
            close_timestamp,
            CASE
                WHEN resolved_outcome IS NOT NULL THEN COALESCE(close_timestamp, open_timestamp)
                ELSE NULL
            END AS resolution_timestamp,
            resolved_outcome
        FROM market_reference
    """


def build_generic_resolution_select_sql(venue: str, relation: str, existing_columns: set[str]) -> str:
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


def build_kalshi_resolution_select_sql(relation: str) -> str:
    return f"""
        SELECT
            'kalshi' AS venue,
            CAST(ticker AS VARCHAR) AS market_id,
            CASE
                WHEN COALESCE(CAST(result AS VARCHAR), '') <> ''
                THEN COALESCE({timestamp_cast_sql('close_time')}, {timestamp_cast_sql('created_time')})
                ELSE NULL
            END AS resolution_timestamp,
            CASE
                WHEN LOWER(CAST(result AS VARCHAR)) IN ('yes', 'no') THEN LOWER(CAST(result AS VARCHAR))
                ELSE NULL
            END AS resolved_outcome,
            CASE
                WHEN LOWER(CAST(result AS VARCHAR)) = 'yes' THEN 1.0
                WHEN LOWER(CAST(result AS VARCHAR)) = 'no' THEN 0.0
                ELSE NULL
            END AS resolution_value
        FROM {quote_identifier(relation)}
        WHERE ticker IS NOT NULL
          AND COALESCE(CAST(result AS VARCHAR), '') <> ''
    """


def build_polymarket_resolution_select_sql(relation: str) -> str:
    return f"""
        WITH market_reference AS (
            {build_polymarket_market_reference_sql(relation)}
        )
        SELECT
            'polymarket' AS venue,
            market_id,
            CASE
                WHEN resolved_outcome IS NOT NULL THEN COALESCE(close_timestamp, open_timestamp)
                ELSE NULL
            END AS resolution_timestamp,
            resolved_outcome,
            CASE WHEN resolved_outcome IS NOT NULL THEN 1.0 ELSE NULL END AS resolution_value
        FROM market_reference
        WHERE resolved_outcome IS NOT NULL
    """


def build_polymarket_market_reference_sql(relation: str) -> str:
    outcome_0_price = "TRY_CAST(json_extract_string(outcome_prices, '$[0]') AS DOUBLE)"
    outcome_1_price = "TRY_CAST(json_extract_string(outcome_prices, '$[1]') AS DOUBLE)"
    outcome_0_name = "CAST(json_extract_string(outcomes, '$[0]') AS VARCHAR)"
    outcome_1_name = "CAST(json_extract_string(outcomes, '$[1]') AS VARCHAR)"

    return f"""
        SELECT
            CAST(id AS VARCHAR) AS market_id,
            CAST(condition_id AS VARCHAR) AS event_id,
            CAST(slug AS VARCHAR) AS market_slug,
            CAST(question AS VARCHAR) AS question,
            CAST(question AS VARCHAR) AS title,
            CAST(NULL AS VARCHAR) AS topic_raw,
            CASE
                WHEN closed THEN 'closed'
                WHEN active THEN 'active'
                ELSE 'inactive'
            END AS status,
            {timestamp_cast_sql('created_at')} AS open_timestamp,
            {timestamp_cast_sql('end_date')} AS close_timestamp,
            LOWER(CAST(market_maker_address AS VARCHAR)) AS market_maker_address,
            CAST(json_extract_string(clob_token_ids, '$[0]') AS VARCHAR) AS token_0_id,
            CAST(json_extract_string(clob_token_ids, '$[1]') AS VARCHAR) AS token_1_id,
            {outcome_0_name} AS outcome_0_name,
            {outcome_1_name} AS outcome_1_name,
            CASE
                WHEN {outcome_0_price} > 0.99 AND {outcome_1_price} < 0.01 THEN {outcome_0_name}
                WHEN {outcome_0_price} < 0.01 AND {outcome_1_price} > 0.99 THEN {outcome_1_name}
                ELSE NULL
            END AS resolved_outcome
        FROM {quote_identifier(relation)}
    """


def build_polymarket_block_lookup_sql(blocks_relation: str | None) -> str:
    if blocks_relation is None:
        return "SELECT CAST(NULL AS BIGINT) AS block_number, CAST(NULL AS TIMESTAMP) AS block_timestamp WHERE FALSE"
    return f"""
        SELECT
            CAST(block_number AS BIGINT) AS block_number,
            {timestamp_cast_sql('timestamp')} AS block_timestamp
        FROM {quote_identifier(blocks_relation)}
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
    wrapped_selects = [
        f"SELECT * FROM ({select_sql}) AS union_select_{index}"
        for index, select_sql in enumerate(selects)
    ]
    return "\nUNION ALL\n".join(wrapped_selects)


def coalesce_existing(existing_columns: set[str], spec: ColumnSpec) -> str:
    available = []
    for alias in spec.aliases:
        if alias.lower() not in existing_columns:
            continue
        if spec.sql_type == "TIMESTAMP":
            available.append(timestamp_cast_sql(quote_identifier(alias)))
        else:
            available.append(try_cast_identifier(alias, spec.sql_type))
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


def timestamp_cast_sql(expr: str) -> str:
    return (
        "CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN TRY_CAST({expr} AS TIMESTAMP) IS NOT NULL THEN TRY_CAST({expr} AS TIMESTAMP) "
        f"WHEN TRY_CAST({expr} AS DOUBLE) IS NOT NULL THEN CAST(to_timestamp(TRY_CAST({expr} AS DOUBLE)) AS TIMESTAMP) "
        "ELSE NULL END"
    )


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def try_cast_identifier(identifier: str, sql_type: str) -> str:
    return f"TRY_CAST({quote_identifier(identifier)} AS {sql_type})"


def escape_sql_like(value: str) -> str:
    return value.replace("'", "''")
