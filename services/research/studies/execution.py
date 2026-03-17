"""Execution expectancy study over normalized historical trade features."""

from __future__ import annotations

from typing import Any

from services.research.studies.common import (
    contract_outcome_indicator_sql,
    invert_trade_direction_sql,
    normalize_trade_direction_sql,
    position_pnl_per_contract_sql,
    price_bucket_order_case,
    quote_identifier,
    sql_string_literal,
    time_bucket_order_case,
    write_json,
    write_text,
)
from services.research.studies.contracts import StudyArtifacts, StudyRunContext

STUDY_NAME = "maker_taker_expectancy"
STUDY_VERSION = "v1"


def run_maker_taker_expectancy_study(connection: object, context: StudyRunContext) -> StudyArtifacts:
    study_dir = context.run_dir / STUDY_NAME
    study_dir.mkdir(parents=True, exist_ok=True)

    venue_filter_sql = ""
    if context.venue is not None:
        venue_filter_sql = f"AND venue = {sql_string_literal(context.venue)}"

    contract_outcome_expr = contract_outcome_indicator_sql("contract_side", "resolved_outcome")
    normalized_taker_direction_expr = normalize_trade_direction_sql("taker_side")
    observed_position_expr = (
        "CASE "
        "WHEN maker_taker_role = 'taker' THEN "
        f"{normalized_taker_direction_expr} "
        "WHEN maker_taker_role = 'maker' THEN "
        f"{invert_trade_direction_sql(normalized_taker_direction_expr)} "
        "ELSE NULL END"
    )
    observed_pnl_expr = position_pnl_per_contract_sql(
        position_side_expr=observed_position_expr,
        price_probability_expr="price_probability",
        contract_outcome_expr=contract_outcome_expr,
    )
    taker_pnl_expr = position_pnl_per_contract_sql(
        position_side_expr=normalized_taker_direction_expr,
        price_probability_expr="price_probability",
        contract_outcome_expr=contract_outcome_expr,
    )
    maker_counterparty_pnl_expr = f"-1.0 * ({taker_pnl_expr})"

    base_view = "study_execution_base"
    role_rows_view = "study_execution_role_rows"
    surface_view = "study_execution_surface"

    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW {quote_identifier(base_view)} AS
        SELECT
            venue,
            market_id,
            trade_id,
            COALESCE(price_bucket, 'unknown') AS price_bucket,
            COALESCE(time_to_resolution_bucket, 'unknown') AS time_to_resolution_bucket,
            price_probability,
            quantity,
            notional_usd,
            maker_taker_role,
            {normalized_taker_direction_expr} AS normalized_taker_direction,
            {contract_outcome_expr} AS contract_outcome,
            {observed_position_expr} AS observed_position_side,
            {observed_pnl_expr} AS observed_pnl_per_contract,
            {taker_pnl_expr} AS taker_pnl_per_contract,
            {maker_counterparty_pnl_expr} AS maker_counterparty_pnl_per_contract
        FROM historical_trade_features
        WHERE price_probability IS NOT NULL
          AND {contract_outcome_expr} IS NOT NULL
          AND {normalized_taker_direction_expr} IS NOT NULL
          {venue_filter_sql}
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW {quote_identifier(role_rows_view)} AS
        SELECT
            'observed' AS role_basis,
            venue,
            maker_taker_role,
            price_bucket,
            time_to_resolution_bucket,
            market_id,
            trade_id,
            price_probability,
            quantity,
            notional_usd,
            contract_outcome,
            observed_pnl_per_contract AS pnl_per_contract
        FROM {quote_identifier(base_view)}
        WHERE maker_taker_role IN ('maker', 'taker')
          AND observed_position_side IS NOT NULL
          AND observed_pnl_per_contract IS NOT NULL
        UNION ALL
        SELECT
            'counterparty_inferred' AS role_basis,
            venue,
            'taker' AS maker_taker_role,
            price_bucket,
            time_to_resolution_bucket,
            market_id,
            trade_id,
            price_probability,
            quantity,
            notional_usd,
            contract_outcome,
            taker_pnl_per_contract AS pnl_per_contract
        FROM {quote_identifier(base_view)}
        WHERE taker_pnl_per_contract IS NOT NULL
        UNION ALL
        SELECT
            'counterparty_inferred' AS role_basis,
            venue,
            'maker' AS maker_taker_role,
            price_bucket,
            time_to_resolution_bucket,
            market_id,
            trade_id,
            price_probability,
            quantity,
            notional_usd,
            contract_outcome,
            maker_counterparty_pnl_per_contract AS pnl_per_contract
        FROM {quote_identifier(base_view)}
        WHERE maker_counterparty_pnl_per_contract IS NOT NULL
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW {quote_identifier(surface_view)} AS
        SELECT
            role_basis,
            venue,
            maker_taker_role,
            price_bucket,
            {price_bucket_order_case('price_bucket')} AS price_bucket_order,
            time_to_resolution_bucket,
            {time_bucket_order_case('time_to_resolution_bucket')} AS time_to_resolution_bucket_order,
            COUNT(*) AS trade_count,
            COUNT(DISTINCT market_id) AS market_count,
            AVG(price_probability) AS avg_price_probability,
            AVG(contract_outcome) AS avg_contract_win_rate,
            AVG(pnl_per_contract) AS avg_pnl_per_contract,
            AVG(ABS(pnl_per_contract)) AS avg_abs_pnl_per_contract,
            STDDEV_SAMP(pnl_per_contract) AS pnl_per_contract_stddev,
            SUM(CASE WHEN quantity IS NOT NULL THEN quantity ELSE 0.0 END) AS total_quantity,
            SUM(CASE WHEN quantity IS NOT NULL THEN pnl_per_contract * quantity ELSE 0.0 END) AS total_pnl_usd,
            SUM(CASE WHEN quantity IS NOT NULL THEN 1 ELSE 0 END) AS quantity_covered_trades
        FROM {quote_identifier(role_rows_view)}
        GROUP BY 1, 2, 3, 4, 6
        """
    )

    input_rows = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(base_view)}").fetchone()[0])
    surface_rows = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(surface_view)}").fetchone()[0])
    observed_maker_rows = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM {quote_identifier(role_rows_view)}
            WHERE role_basis = 'observed'
              AND maker_taker_role = 'maker'
            """
        ).fetchone()[0]
    )

    surface_path = study_dir / "expectancy_surface.parquet"
    connection.execute(
        f"""
        COPY (
            SELECT *
            FROM {quote_identifier(surface_view)}
            ORDER BY role_basis, venue, maker_taker_role, price_bucket_order, time_to_resolution_bucket_order
        )
        TO {sql_string_literal(str(surface_path))}
        (FORMAT PARQUET)
        """
    )

    coverage_rows = connection.execute(
        f"""
        SELECT role_basis, venue, maker_taker_role, SUM(trade_count) AS trade_count
        FROM {quote_identifier(surface_view)}
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
        """
    ).fetchall()
    notable_rows = connection.execute(
        f"""
        SELECT
            role_basis,
            venue,
            maker_taker_role,
            price_bucket,
            time_to_resolution_bucket,
            trade_count,
            ROUND(avg_pnl_per_contract, 4) AS avg_pnl_per_contract
        FROM {quote_identifier(surface_view)}
        ORDER BY ABS(avg_pnl_per_contract) DESC, trade_count DESC, role_basis, venue
        LIMIT 6
        """
    ).fetchall()

    warnings: list[str] = []
    if observed_maker_rows == 0:
        warnings.append(
            "No observed maker rows were available; maker comparison relies on inferred counterparty rows."
        )
    if surface_rows == 0:
        warnings.append("No resolved trade rows with usable direction were available for expectancy output.")

    metadata = {
        "study_name": STUDY_NAME,
        "study_version": STUDY_VERSION,
        "run_id": context.run_id,
        "bundle_name": context.bundle_name,
        "generated_at": context.generated_at.isoformat(),
        "promotion_recommendation": (
            "candidate_execution_prior_with_caveats" if surface_rows else "experimental"
        ),
        "database": context.source_metadata["database"],
        "materialization": context.source_metadata.get("materialization"),
        "dataset_manifest": context.source_metadata.get("dataset_manifest"),
        "code_identity": context.code_identity,
        "parameters": {
            "venue_filter": context.venue,
            "dimensions": [
                "role_basis",
                "venue",
                "maker_taker_role",
                "price_bucket",
                "time_to_resolution_bucket",
            ],
            "role_bases": ["observed", "counterparty_inferred"],
        },
        "source_views": ["historical_trade_features"],
        "artifacts": {
            "surface": str(surface_path),
        },
        "row_counts": {
            "input_rows": input_rows,
            "surface_rows": surface_rows,
            "observed_maker_rows": observed_maker_rows,
        },
        "exclusions": [
            "Rows without resolved outcome, contract side, normalized price, or interpretable taker direction are excluded.",
            "Counterparty maker rows are inferred from matched trades and do not model passive fill probability or queue selection.",
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
        "# Maker/Taker Expectancy Study",
        "",
        f"- Run id: `{metadata['run_id']}`",
        f"- Generated at: {metadata['generated_at']}",
        f"- Promotion recommendation: `{metadata['promotion_recommendation']}`",
        f"- Input rows: {metadata['row_counts']['input_rows']}",
        f"- Surface rows: {metadata['row_counts']['surface_rows']}",
        f"- Observed maker rows: {metadata['row_counts']['observed_maker_rows']}",
        "",
        "## Coverage",
        "",
    ]

    if coverage_rows:
        for role_basis, venue, maker_taker_role, trade_count in coverage_rows:
            lines.append(
                f"- `{role_basis}` / `{venue}` / `{maker_taker_role}`: {trade_count} resolved trades"
            )
    else:
        lines.append("- No expectancy rows were produced.")

    lines.extend(
        [
            "",
            "## Notable Buckets",
            "",
        ]
    )
    if notable_rows:
        for row in notable_rows:
            role_basis, venue, maker_taker_role, price_bucket, time_bucket, trade_count, avg_pnl = row
            lines.append(
                f"- `{role_basis}` / `{venue}` / `{maker_taker_role}` / `{price_bucket}` / `{time_bucket}`: "
                f"{trade_count} trades, avg pnl/share {avg_pnl:+}"
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
