"""DuckDB-backed normalized research materialization for Phase 0.5."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from services.research.dataset_profile import build_dataset_collections, discover_dataset_files
from services.research.normalize import (
    SOURCE_VIEW_KINDS,
    TARGET_VIEW_NAMES,
    build_market_select_sql,
    build_polymarket_trade_select_sql,
    build_resolution_select_sql,
    build_source_collection_map,
    build_trade_select_sql,
    describe_view_columns,
    empty_select_sql,
    notional_bucket_case,
    price_bucket_case,
    quantity_bucket_case,
    quote_identifier,
    sanitize_identifier,
    time_bucket_case,
    topic_class_case,
    union_selects_or_empty,
)
from services.research.settings import HistoricalResearchSettings


@dataclass(frozen=True)
class MaterializationArtifacts:
    database_path: Path
    metadata_path: Path
    summary_path: Path
    metadata: dict[str, Any]


def materialize_historical_research(
    research_settings: HistoricalResearchSettings,
    *,
    database_path: Path | None = None,
    include_hidden: bool = False,
    max_files: int | None = None,
    collect_view_row_counts: bool = True,
) -> MaterializationArtifacts:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is required for historical research materialization. "
            "Install it in the sleeperservice environment before running this command."
        ) from exc

    dataset_root = research_settings.require_dataset_root()
    if not dataset_root.exists():
        raise FileNotFoundError(f"Historical dataset root does not exist: {dataset_root}")

    output_root = research_settings.output_root
    db_path = database_path or (output_root / "duckdb" / "historical_research.duckdb")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    parquet_files = [
        record
        for record in discover_dataset_files(
            dataset_root,
            include_hidden=include_hidden,
            max_files=max_files,
        )
        if record.extension == ".parquet"
    ]
    if not parquet_files:
        raise ValueError("No parquet files were discovered under the configured dataset root.")

    collections = build_dataset_collections(parquet_files)
    if not collections:
        raise ValueError("No parquet collections could be formed under the configured dataset root.")

    source_collection_map = build_source_collection_map(collections)
    if not source_collection_map:
        raise ValueError("No parquet collections could be grouped into venue source views.")

    connection = duckdb.connect(str(db_path))
    try:
        created_views = _create_source_views(connection, source_collection_map)
        _create_normalized_views(connection, source_collection_map, created_views)
        if collect_view_row_counts:
            view_row_counts = {
                view_name: int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(view_name)}").fetchone()[0])
                for view_name in TARGET_VIEW_NAMES
            }
        else:
            view_row_counts = {
                view_name: None
                for view_name in TARGET_VIEW_NAMES
            }
        duckdb_version = str(connection.execute("SELECT version()").fetchone()[0])
    finally:
        connection.close()

    generated_at = datetime.now(tz=UTC)
    run_id = generated_at.strftime("%Y%m%dT%H%M%SZ")

    metadata = {
        "generated_at": generated_at.isoformat(),
        "run_id": run_id,
        "dataset_root": str(dataset_root),
        "output_root": str(output_root),
        "database_path": str(db_path),
        "duckdb_version": duckdb_version,
        "view_row_counts_collected": collect_view_row_counts,
        "source_files": {
            venue: {
                kind: _summarize_collections(kind_map[kind])
                for kind in SOURCE_VIEW_KINDS
                if kind != "all" and kind_map[kind]
            }
            for venue, kind_map in source_collection_map.items()
        },
        "view_row_counts": view_row_counts,
    }

    metadata_path, summary_path = _write_materialization_outputs(output_root, run_id, metadata)
    return MaterializationArtifacts(
        database_path=db_path,
        metadata_path=metadata_path,
        summary_path=summary_path,
        metadata=metadata,
    )


def _create_source_views(
    connection: object,
    source_collection_map: dict[str, dict[str, list[object]]],
) -> set[str]:
    created_views: set[str] = set()
    for venue, kind_map in source_collection_map.items():
        venue_identifier = sanitize_identifier(venue)
        for kind in SOURCE_VIEW_KINDS:
            collections = kind_map[kind]
            if not collections:
                continue
            view_name = f"source_{venue_identifier}_{kind}"
            read_targets: list[str] = []
            for collection in collections:
                if collection.can_use_glob:
                    read_targets.append(collection.parquet_glob)
                else:
                    read_targets.extend(collection.absolute_paths)
            patterns_sql = ", ".join(_sql_string_literal(target) for target in read_targets)
            connection.execute(
                f"""
                CREATE OR REPLACE VIEW {quote_identifier(view_name)} AS
                SELECT * FROM read_parquet([{patterns_sql}], union_by_name=true)
                """
            )
            created_views.add(view_name)
    return created_views


def _create_normalized_views(
    connection: object,
    source_collection_map: dict[str, dict[str, list[object]]],
    created_views: set[str],
) -> None:
    trade_selects: list[str] = []
    market_selects: list[str] = []
    resolution_selects: list[str] = []

    for venue, kind_map in source_collection_map.items():
        venue_identifier = sanitize_identifier(venue)
        trade_view = f"source_{venue_identifier}_trades"
        market_view = f"source_{venue_identifier}_markets"
        resolution_view = f"source_{venue_identifier}_resolutions"
        blocks_view = f"source_{venue_identifier}_blocks" if kind_map["blocks"] else None
        legacy_view = f"source_{venue_identifier}_legacy_trades" if kind_map["legacy_trades"] else None

        if market_view not in created_views:
            continue

        market_columns = describe_view_columns(connection, market_view)
        market_selects.append(build_market_select_sql(venue_identifier, market_view, market_columns))

        trade_columns = describe_view_columns(connection, trade_view) if trade_view in created_views else set()
        is_becker_polymarket = venue_identifier == "polymarket" and {
            "id",
            "clob_token_ids",
            "outcome_prices",
        }.issubset(market_columns) and {
            "maker_asset_id",
            "taker_asset_id",
        }.issubset(trade_columns)

        if is_becker_polymarket:
            trade_selects.append(
                build_polymarket_trade_select_sql(
                    trade_view,
                    market_view,
                    blocks_relation=blocks_view if blocks_view in created_views else None,
                    legacy_relation=legacy_view if legacy_view in created_views else None,
                )
            )
            resolution_selects.append(build_resolution_select_sql(venue_identifier, market_view, market_columns))
            continue

        if trade_view in created_views:
            trade_selects.append(build_trade_select_sql(venue_identifier, trade_view, trade_columns))

        resolution_source_view = market_view if venue_identifier == "kalshi" else resolution_view
        if resolution_source_view in created_views:
            resolution_columns = describe_view_columns(connection, resolution_source_view)
            resolution_selects.append(
                build_resolution_select_sql(
                    venue_identifier,
                    resolution_source_view,
                    resolution_columns,
                )
            )

    connection.execute(
        f"""
        CREATE OR REPLACE VIEW historical_trades AS
        {union_selects_or_empty(
            trade_selects,
            empty_select_sql(
                {
                    "venue": "VARCHAR",
                    "trade_id": "VARCHAR",
                    "market_id": "VARCHAR",
                    "event_id": "VARCHAR",
                    "trade_timestamp": "TIMESTAMP",
                    "price_probability": "DOUBLE",
                    "quantity": "DOUBLE",
                    "notional_usd": "DOUBLE",
                    "maker_taker_role": "VARCHAR",
                    "taker_side": "VARCHAR",
                    "contract_side": "VARCHAR",
                    "question": "VARCHAR",
                    "title": "VARCHAR",
                    "topic_raw": "VARCHAR",
                }
            ),
        )}
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE VIEW historical_markets_base AS
        {union_selects_or_empty(
            market_selects,
            empty_select_sql(
                {
                    "venue": "VARCHAR",
                    "market_id": "VARCHAR",
                    "event_id": "VARCHAR",
                    "market_slug": "VARCHAR",
                    "question": "VARCHAR",
                    "title": "VARCHAR",
                    "topic_raw": "VARCHAR",
                    "status": "VARCHAR",
                    "open_timestamp": "TIMESTAMP",
                    "close_timestamp": "TIMESTAMP",
                    "resolution_timestamp": "TIMESTAMP",
                    "resolved_outcome": "VARCHAR",
                }
            ),
        )}
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW historical_markets AS
        SELECT
            venue,
            market_id,
            ANY_VALUE(event_id) AS event_id,
            ANY_VALUE(market_slug) AS market_slug,
            ANY_VALUE(question) AS question,
            ANY_VALUE(title) AS title,
            ANY_VALUE(topic_raw) AS topic_raw,
            ANY_VALUE(status) AS status,
            MIN(open_timestamp) AS open_timestamp,
            MAX(close_timestamp) AS close_timestamp,
            MAX(resolution_timestamp) AS resolution_timestamp,
            ANY_VALUE(resolved_outcome) AS resolved_outcome
        FROM historical_markets_base
        WHERE market_id IS NOT NULL
        GROUP BY venue, market_id
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE VIEW historical_resolutions_base AS
        {union_selects_or_empty(
            resolution_selects,
            empty_select_sql(
                {
                    "venue": "VARCHAR",
                    "market_id": "VARCHAR",
                    "resolution_timestamp": "TIMESTAMP",
                    "resolved_outcome": "VARCHAR",
                    "resolution_value": "DOUBLE",
                }
            ),
        )}
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW historical_resolutions AS
        SELECT
            venue,
            market_id,
            MAX(resolution_timestamp) AS resolution_timestamp,
            ANY_VALUE(resolved_outcome) AS resolved_outcome,
            MAX(resolution_value) AS resolution_value
        FROM historical_resolutions_base
        WHERE market_id IS NOT NULL
        GROUP BY venue, market_id
        """
    )

    time_to_resolution_seconds_expr = (
        "CASE "
        "WHEN COALESCE(r.resolution_timestamp, m.resolution_timestamp) IS NULL OR t.trade_timestamp IS NULL THEN NULL "
        "ELSE date_diff('second', t.trade_timestamp, COALESCE(r.resolution_timestamp, m.resolution_timestamp)) "
        "END"
    )
    price_bucket_expr = price_bucket_case("t.price_probability")
    notional_bucket_expr = notional_bucket_case("t.notional_usd")
    quantity_bucket_expr = quantity_bucket_case("t.quantity")
    size_bucket_expr = f"COALESCE({notional_bucket_expr}, {quantity_bucket_expr})"
    time_bucket_expr = time_bucket_case(time_to_resolution_seconds_expr)
    topic_class_expr = topic_class_case(
        "COALESCE(t.topic_raw, m.topic_raw)",
        "COALESCE(t.question, m.question)",
        "COALESCE(t.title, m.title)",
    )

    connection.execute(
        f"""
        CREATE OR REPLACE VIEW historical_trade_features AS
        SELECT
            t.venue,
            t.trade_id,
            t.market_id,
            COALESCE(t.event_id, m.event_id) AS event_id,
            t.trade_timestamp,
            t.price_probability,
            t.quantity,
            t.notional_usd,
            t.maker_taker_role,
            t.taker_side,
            t.contract_side,
            COALESCE(t.question, m.question) AS question,
            COALESCE(t.title, m.title) AS title,
            COALESCE(t.topic_raw, m.topic_raw) AS topic_raw,
            m.market_slug,
            m.status,
            m.open_timestamp,
            m.close_timestamp,
            COALESCE(r.resolution_timestamp, m.resolution_timestamp) AS resolution_timestamp,
            COALESCE(r.resolved_outcome, m.resolved_outcome) AS resolved_outcome,
            {time_to_resolution_seconds_expr} AS time_to_resolution_seconds,
            {time_bucket_expr} AS time_to_resolution_bucket,
            {price_bucket_expr} AS price_bucket,
            {size_bucket_expr} AS size_bucket,
            {topic_class_expr} AS topic_class
        FROM historical_trades AS t
        LEFT JOIN historical_markets AS m
          ON t.venue = m.venue
         AND t.market_id = m.market_id
        LEFT JOIN historical_resolutions AS r
          ON t.venue = r.venue
         AND t.market_id = r.market_id
        """
    )
    connection.execute(
        """
        CREATE OR REPLACE VIEW historical_bucket_stats AS
        SELECT
            venue,
            COALESCE(price_bucket, 'unknown') AS price_bucket,
            COALESCE(time_to_resolution_bucket, 'unknown') AS time_to_resolution_bucket,
            COALESCE(size_bucket, 'unknown') AS size_bucket,
            COALESCE(maker_taker_role, 'unknown') AS maker_taker_role,
            COALESCE(topic_class, 'unknown') AS topic_class,
            COUNT(*) AS trade_count,
            SUM(COALESCE(notional_usd, 0.0)) AS total_notional_usd,
            AVG(price_probability) AS avg_price_probability,
            AVG(time_to_resolution_seconds) AS avg_time_to_resolution_seconds
        FROM historical_trade_features
        GROUP BY 1, 2, 3, 4, 5, 6
        """
    )


def _write_materialization_outputs(
    output_root: Path,
    run_id: str,
    metadata: dict[str, Any],
) -> tuple[Path, Path]:
    normalized_dir = output_root / "normalized"
    summaries_dir = output_root / "summaries"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    summaries_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = normalized_dir / f"materialization_{run_id}.json"
    summary_path = summaries_dir / f"materialization_{run_id}.md"
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
    summary_path.write_text(_render_summary(metadata), encoding="utf-8")
    return metadata_path, summary_path


def _render_summary(metadata: dict[str, Any]) -> str:
    source_lines: list[str] = []
    for venue, kind_map in metadata["source_files"].items():
        source_lines.append(f"### `{venue}`")
        for kind, summary in kind_map.items():
            source_lines.append(
                f"- {kind}: {summary['file_count']} files across {summary['collection_count']} collections"
            )
            source_lines.append(
                f"  dirs: {', '.join(summary['relative_dirs'])}"
            )

    row_lines = [
        f"- {view_name}: {row_count}"
        for view_name, row_count in metadata["view_row_counts"].items()
    ]

    return "\n".join(
        [
            "# Historical Research Materialization",
            "",
            f"- Generated at: {metadata['generated_at']}",
            f"- Dataset root: `{metadata['dataset_root']}`",
            f"- Output root: `{metadata['output_root']}`",
            f"- DuckDB database: `{metadata['database_path']}`",
            f"- DuckDB version: `{metadata['duckdb_version']}`",
            f"- View row counts collected: {metadata['view_row_counts_collected']}",
            "",
            "## Source Files",
            "",
            *(source_lines or ["- None"]),
            "",
            "## View Row Counts",
            "",
            *(row_lines or ["- None"]),
            "",
        ]
    )


def _summarize_collections(collections: list[object]) -> dict[str, Any]:
    sample_files: list[str] = []
    for collection in collections:
        sample_files.extend(collection.sample_relative_paths)

    return {
        "collection_count": len(collections),
        "file_count": sum(collection.file_count for collection in collections),
        "relative_dirs": [collection.relative_dir for collection in collections],
        "sample_files": sample_files[:10],
    }


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
