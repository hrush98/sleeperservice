"""Longshot and favorite bias study over normalized historical trade features."""

from __future__ import annotations

from typing import Any

from services.research.studies.common import (
    contract_outcome_indicator_sql,
    quote_identifier,
    sql_string_literal,
    write_json,
    write_text,
)
from services.research.studies.contracts import StudyArtifacts, StudyRunContext

STUDY_NAME = "longshot_favorite_bias"
STUDY_VERSION = "v1"


def run_longshot_favorite_bias_study(connection: object, context: StudyRunContext) -> StudyArtifacts:
    study_dir = context.run_dir / STUDY_NAME
    study_dir.mkdir(parents=True, exist_ok=True)

    venue_filter_sql = ""
    if context.venue is not None:
        venue_filter_sql = f"AND venue = {sql_string_literal(context.venue)}"

    contract_outcome_expr = contract_outcome_indicator_sql("contract_side", "resolved_outcome")
    tail_regime_expr = (
        "CASE "
        "WHEN price_probability < 0.10 THEN 'longshot' "
        "WHEN price_probability > 0.90 THEN 'favorite' "
        "ELSE NULL END"
    )
    trade_year_expr = "CASE WHEN trade_timestamp IS NULL THEN NULL ELSE CAST(EXTRACT(year FROM trade_timestamp) AS INTEGER) END"

    base_view = "study_bias_base"
    stability_view = "study_bias_stability"
    summary_view = "study_bias_summary"

    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW {quote_identifier(base_view)} AS
        SELECT
            venue,
            market_id,
            trade_id,
            COALESCE(topic_class, 'unknown') AS topic_class,
            {trade_year_expr} AS trade_year,
            {tail_regime_expr} AS tail_regime,
            price_probability,
            {contract_outcome_expr} AS contract_outcome,
            {contract_outcome_expr} - price_probability AS miscalibration
        FROM historical_trade_features
        WHERE price_probability IS NOT NULL
          AND {contract_outcome_expr} IS NOT NULL
          AND {tail_regime_expr} IS NOT NULL
          {venue_filter_sql}
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW {quote_identifier(stability_view)} AS
        SELECT
            venue,
            topic_class,
            tail_regime,
            trade_year,
            COUNT(*) AS trade_count,
            COUNT(DISTINCT market_id) AS market_count,
            AVG(price_probability) AS avg_implied_probability,
            AVG(contract_outcome) AS realized_win_rate,
            AVG(miscalibration) AS avg_miscalibration
        FROM {quote_identifier(base_view)}
        WHERE trade_year IS NOT NULL
        GROUP BY 1, 2, 3, 4
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW {quote_identifier(summary_view)} AS
        WITH overall AS (
            SELECT
                venue,
                topic_class,
                tail_regime,
                COUNT(*) AS trade_count,
                COUNT(DISTINCT market_id) AS market_count,
                AVG(price_probability) AS avg_implied_probability,
                AVG(contract_outcome) AS realized_win_rate,
                AVG(miscalibration) AS avg_miscalibration,
                AVG(ABS(miscalibration)) AS mean_absolute_miscalibration
            FROM {quote_identifier(base_view)}
            GROUP BY 1, 2, 3
        ),
        stability AS (
            SELECT
                venue,
                topic_class,
                tail_regime,
                COUNT(*) AS year_count,
                SUM(CASE WHEN avg_miscalibration > 0 THEN 1 ELSE 0 END) AS positive_years,
                SUM(CASE WHEN avg_miscalibration < 0 THEN 1 ELSE 0 END) AS negative_years,
                CASE
                    WHEN SUM(CASE WHEN avg_miscalibration != 0 THEN 1 ELSE 0 END) = 0 THEN NULL
                    ELSE GREATEST(
                        SUM(CASE WHEN avg_miscalibration > 0 THEN 1 ELSE 0 END),
                        SUM(CASE WHEN avg_miscalibration < 0 THEN 1 ELSE 0 END)
                    ) * 1.0 / SUM(CASE WHEN avg_miscalibration != 0 THEN 1 ELSE 0 END)
                END AS sign_consistency_ratio
            FROM {quote_identifier(stability_view)}
            GROUP BY 1, 2, 3
        )
        SELECT
            o.venue,
            o.topic_class,
            o.tail_regime,
            o.trade_count,
            o.market_count,
            o.avg_implied_probability,
            o.realized_win_rate,
            o.avg_miscalibration,
            o.mean_absolute_miscalibration,
            COALESCE(s.year_count, 0) AS year_count,
            COALESCE(s.positive_years, 0) AS positive_years,
            COALESCE(s.negative_years, 0) AS negative_years,
            s.sign_consistency_ratio
        FROM overall AS o
        LEFT JOIN stability AS s
          ON o.venue = s.venue
         AND o.topic_class = s.topic_class
         AND o.tail_regime = s.tail_regime
        """
    )

    input_rows = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(base_view)}").fetchone()[0])
    summary_rows = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(summary_view)}").fetchone()[0])
    stability_rows = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(stability_view)}").fetchone()[0])

    summary_path = study_dir / "bias_summary.parquet"
    stability_path = study_dir / "bias_stability.parquet"
    connection.execute(
        f"""
        COPY (
            SELECT *
            FROM {quote_identifier(summary_view)}
            ORDER BY venue, topic_class, tail_regime
        )
        TO {sql_string_literal(str(summary_path))}
        (FORMAT PARQUET)
        """
    )
    connection.execute(
        f"""
        COPY (
            SELECT *
            FROM {quote_identifier(stability_view)}
            ORDER BY venue, topic_class, tail_regime, trade_year
        )
        TO {sql_string_literal(str(stability_path))}
        (FORMAT PARQUET)
        """
    )

    coverage_rows = connection.execute(
        f"""
        SELECT venue, tail_regime, SUM(trade_count) AS trade_count
        FROM {quote_identifier(summary_view)}
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).fetchall()
    notable_rows = connection.execute(
        f"""
        SELECT
            venue,
            topic_class,
            tail_regime,
            trade_count,
            ROUND(avg_implied_probability, 4) AS avg_implied_probability,
            ROUND(realized_win_rate, 4) AS realized_win_rate,
            ROUND(avg_miscalibration, 4) AS avg_miscalibration,
            year_count,
            ROUND(sign_consistency_ratio, 4) AS sign_consistency_ratio
        FROM {quote_identifier(summary_view)}
        ORDER BY ABS(avg_miscalibration) DESC, trade_count DESC, venue, topic_class
        LIMIT 6
        """
    ).fetchall()

    warnings: list[str] = []
    if summary_rows == 0:
        warnings.append("No tail-trade rows were available for longshot/favorite bias output.")
    if stability_rows == 0:
        warnings.append("No multi-period stability rows were available; trade timestamps may be missing.")

    metadata = {
        "study_name": STUDY_NAME,
        "study_version": STUDY_VERSION,
        "run_id": context.run_id,
        "bundle_name": context.bundle_name,
        "generated_at": context.generated_at.isoformat(),
        "promotion_recommendation": "descriptive_candidate" if summary_rows else "experimental",
        "database": context.source_metadata["database"],
        "materialization": context.source_metadata.get("materialization"),
        "dataset_manifest": context.source_metadata.get("dataset_manifest"),
        "code_identity": context.code_identity,
        "parameters": {
            "venue_filter": context.venue,
            "tail_thresholds": {
                "longshot_lt_probability": 0.10,
                "favorite_gt_probability": 0.90,
            },
            "dimensions": ["venue", "topic_class", "tail_regime"],
            "stability_dimension": "trade_year",
        },
        "source_views": ["historical_trade_features"],
        "artifacts": {
            "bias_summary": str(summary_path),
            "bias_stability": str(stability_path),
        },
        "row_counts": {
            "input_rows": input_rows,
            "summary_rows": summary_rows,
            "stability_rows": stability_rows,
        },
        "exclusions": [
            "Rows without resolved outcome, contract side, normalized price, or trade timestamp are excluded from stability output.",
            "Only tail trades are included: probability < 0.10 for longshots and > 0.90 for favorites.",
        ],
        "warnings": warnings,
    }

    metadata_path = study_dir / "metadata.json"
    markdown_summary_path = study_dir / "summary.md"
    write_json(metadata_path, metadata)
    write_text(
        markdown_summary_path,
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
        summary_path=markdown_summary_path,
        data_paths=(summary_path, stability_path),
        metadata=metadata,
    )


def _render_summary(
    *,
    metadata: dict[str, Any],
    coverage_rows: list[tuple[Any, ...]],
    notable_rows: list[tuple[Any, ...]],
) -> str:
    lines = [
        "# Longshot/Favorite Bias Study",
        "",
        f"- Run id: `{metadata['run_id']}`",
        f"- Generated at: {metadata['generated_at']}",
        f"- Promotion recommendation: `{metadata['promotion_recommendation']}`",
        f"- Input rows: {metadata['row_counts']['input_rows']}",
        f"- Summary rows: {metadata['row_counts']['summary_rows']}",
        f"- Stability rows: {metadata['row_counts']['stability_rows']}",
        "",
        "## Coverage",
        "",
    ]

    if coverage_rows:
        for venue, tail_regime, trade_count in coverage_rows:
            lines.append(f"- `{venue}` / `{tail_regime}`: {trade_count} tail trades")
    else:
        lines.append("- No tail-bias rows were produced.")

    lines.extend(
        [
            "",
            "## Notable Rows",
            "",
        ]
    )
    if notable_rows:
        for row in notable_rows:
            venue, topic_class, tail_regime, trade_count, implied, realized, miscalibration, year_count, sign_ratio = row
            lines.append(
                f"- `{venue}` / `{topic_class}` / `{tail_regime}`: "
                f"{trade_count} trades, implied {implied}, realized {realized}, "
                f"miscalibration {miscalibration:+}, years {year_count}, "
                f"sign consistency {sign_ratio if sign_ratio is not None else 'n/a'}"
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
