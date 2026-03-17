"""Calibration study over normalized historical trade features."""

from __future__ import annotations

from typing import Any

from services.research.studies.common import (
    contract_outcome_indicator_sql,
    price_bucket_order_case,
    quote_identifier,
    sql_string_literal,
    time_bucket_order_case,
    write_json,
    write_text,
)
from services.research.studies.contracts import StudyArtifacts, StudyRunContext

STUDY_NAME = "calibration"
STUDY_VERSION = "v1"


def run_calibration_study(connection: object, context: StudyRunContext) -> StudyArtifacts:
    study_dir = context.run_dir / STUDY_NAME
    study_dir.mkdir(parents=True, exist_ok=True)

    venue_filter_sql = ""
    if context.venue is not None:
        venue_filter_sql = f"AND venue = {sql_string_literal(context.venue)}"

    contract_outcome_expr = contract_outcome_indicator_sql("contract_side", "resolved_outcome")
    base_view = "study_calibration_base"
    surface_view = "study_calibration_surface"

    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW {quote_identifier(base_view)} AS
        SELECT
            venue,
            market_id,
            trade_id,
            COALESCE(price_bucket, 'unknown') AS price_bucket,
            COALESCE(time_to_resolution_bucket, 'unknown') AS time_to_resolution_bucket,
            time_to_resolution_seconds,
            price_probability,
            notional_usd,
            {contract_outcome_expr} AS contract_outcome
        FROM historical_trade_features
        WHERE price_probability IS NOT NULL
          AND {contract_outcome_expr} IS NOT NULL
          {venue_filter_sql}
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW {quote_identifier(surface_view)} AS
        SELECT
            venue,
            price_bucket,
            {price_bucket_order_case('price_bucket')} AS price_bucket_order,
            time_to_resolution_bucket,
            {time_bucket_order_case('time_to_resolution_bucket')} AS time_to_resolution_bucket_order,
            COUNT(*) AS trade_count,
            COUNT(DISTINCT market_id) AS market_count,
            AVG(price_probability) AS avg_implied_probability,
            AVG(contract_outcome) AS realized_win_rate,
            AVG(contract_outcome - price_probability) AS avg_miscalibration,
            AVG(ABS(contract_outcome - price_probability)) AS mean_absolute_error,
            AVG(POWER(contract_outcome - price_probability, 2)) AS mean_squared_error,
            AVG(time_to_resolution_seconds) AS avg_time_to_resolution_seconds,
            SUM(COALESCE(notional_usd, 0.0)) AS total_notional_usd
        FROM {quote_identifier(base_view)}
        GROUP BY 1, 2, 4
        """
    )

    input_rows = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(base_view)}").fetchone()[0])
    surface_rows = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(surface_view)}").fetchone()[0])

    surface_path = study_dir / "calibration_surface.parquet"
    connection.execute(
        f"""
        COPY (
            SELECT *
            FROM {quote_identifier(surface_view)}
            ORDER BY venue, price_bucket_order, time_to_resolution_bucket_order
        )
        TO {sql_string_literal(str(surface_path))}
        (FORMAT PARQUET)
        """
    )

    coverage_rows = connection.execute(
        f"""
        SELECT venue, SUM(trade_count) AS resolved_trade_count, COUNT(*) AS bucket_count
        FROM {quote_identifier(surface_view)}
        GROUP BY 1
        ORDER BY 1
        """
    ).fetchall()
    notable_rows = connection.execute(
        f"""
        SELECT
            venue,
            price_bucket,
            time_to_resolution_bucket,
            trade_count,
            ROUND(avg_implied_probability, 4) AS avg_implied_probability,
            ROUND(realized_win_rate, 4) AS realized_win_rate,
            ROUND(avg_miscalibration, 4) AS avg_miscalibration
        FROM {quote_identifier(surface_view)}
        ORDER BY ABS(avg_miscalibration) DESC, trade_count DESC, venue
        LIMIT 5
        """
    ).fetchall()

    warnings: list[str] = []
    if surface_rows == 0:
        warnings.append("No resolved trade rows were available for calibration output.")

    metadata = {
        "study_name": STUDY_NAME,
        "study_version": STUDY_VERSION,
        "run_id": context.run_id,
        "bundle_name": context.bundle_name,
        "generated_at": context.generated_at.isoformat(),
        "promotion_recommendation": "descriptive_candidate" if surface_rows else "experimental",
        "database": context.source_metadata["database"],
        "materialization": context.source_metadata.get("materialization"),
        "dataset_manifest": context.source_metadata.get("dataset_manifest"),
        "code_identity": context.code_identity,
        "parameters": {
            "venue_filter": context.venue,
            "dimensions": ["venue", "price_bucket", "time_to_resolution_bucket"],
        },
        "source_views": ["historical_trade_features"],
        "artifacts": {
            "surface": str(surface_path),
        },
        "row_counts": {
            "input_rows": input_rows,
            "surface_rows": surface_rows,
        },
        "exclusions": [
            "Rows without resolved outcome, contract side, or normalized price are excluded.",
            "Calibration is measured at the traded-contract level rather than from full market-mid snapshots.",
        ],
        "warnings": warnings,
    }

    metadata_path = study_dir / "metadata.json"
    summary_path = study_dir / "summary.md"
    write_json(metadata_path, metadata)
    write_text(
        summary_path,
        _render_summary(
            metadata=metadata,
            coverage_rows=coverage_rows,
            notable_rows=notable_rows,
        ),
    )

    return StudyArtifacts(
        study_name=STUDY_NAME,
        study_version=STUDY_VERSION,
        run_id=context.run_id,
        output_dir=study_dir,
        metadata_path=metadata_path,
        summary_path=summary_path,
        data_paths=(surface_path,),
        metadata=metadata,
    )


def _render_summary(
    *,
    metadata: dict[str, Any],
    coverage_rows: list[tuple[Any, ...]],
    notable_rows: list[tuple[Any, ...]],
) -> str:
    lines = [
        "# Calibration Study",
        "",
        f"- Run id: `{metadata['run_id']}`",
        f"- Generated at: {metadata['generated_at']}",
        f"- Promotion recommendation: `{metadata['promotion_recommendation']}`",
        f"- Input rows: {metadata['row_counts']['input_rows']}",
        f"- Surface rows: {metadata['row_counts']['surface_rows']}",
        "",
        "## Coverage",
        "",
    ]

    if coverage_rows:
        for venue, resolved_trade_count, bucket_count in coverage_rows:
            lines.append(
                f"- `{venue}`: {resolved_trade_count} resolved trades across {bucket_count} populated buckets"
            )
    else:
        lines.append("- No calibration rows were produced.")

    lines.extend(
        [
            "",
            "## Notable Buckets",
            "",
        ]
    )
    if notable_rows:
        for row in notable_rows:
            venue, price_bucket, time_bucket, trade_count, implied_prob, realized_win_rate, miscalibration = row
            lines.append(
                f"- `{venue}` / `{price_bucket}` / `{time_bucket}`: "
                f"{trade_count} trades, implied {implied_prob}, realized {realized_win_rate}, "
                f"miscalibration {miscalibration:+}"
            )
    else:
        lines.append("- None")

    warnings = metadata["warnings"]
    if warnings:
        lines.extend(
            [
                "",
                "## Warnings",
                "",
                *(f"- {warning}" for warning in warnings),
            ]
        )

    return "\n".join(lines) + "\n"
