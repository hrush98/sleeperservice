"""Edge-dispersion and sizing-prior study over normalized historical trade features."""

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

STUDY_NAME = "sizing_priors"
STUDY_VERSION = "v1"
MIN_TRADE_COUNT_FOR_CANDIDATE = 25


def run_sizing_priors_study(connection: object, context: StudyRunContext) -> StudyArtifacts:
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

    base_view = "study_sizing_base"
    role_rows_view = "study_sizing_role_rows"
    dispersion_view = "study_sizing_dispersion"
    prior_view = "study_sizing_priors"

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
            quantity,
            notional_usd,
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
            quantity,
            notional_usd,
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
            quantity,
            notional_usd,
            maker_counterparty_pnl_per_contract AS pnl_per_contract
        FROM {quote_identifier(base_view)}
        WHERE maker_counterparty_pnl_per_contract IS NOT NULL
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW {quote_identifier(dispersion_view)} AS
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
            AVG(pnl_per_contract) AS avg_pnl_per_contract,
            AVG(ABS(pnl_per_contract)) AS avg_abs_pnl_per_contract,
            STDDEV_SAMP(pnl_per_contract) AS pnl_per_contract_stddev,
            MIN(pnl_per_contract) AS min_pnl_per_contract,
            MAX(pnl_per_contract) AS max_pnl_per_contract,
            AVG(CASE WHEN pnl_per_contract > 0 THEN 1.0 ELSE 0.0 END) AS profitable_trade_share,
            QUANTILE_CONT(pnl_per_contract, 0.25) AS pnl_per_contract_q25,
            QUANTILE_CONT(pnl_per_contract, 0.50) AS pnl_per_contract_median,
            QUANTILE_CONT(pnl_per_contract, 0.75) AS pnl_per_contract_q75
        FROM {quote_identifier(role_rows_view)}
        GROUP BY 1, 2, 3, 4, 6
        """
    )
    connection.execute(
        f"""
        CREATE OR REPLACE TEMP VIEW {quote_identifier(prior_view)} AS
        WITH priors AS (
            SELECT
                role_basis,
                venue,
                maker_taker_role,
                price_bucket,
                price_bucket_order,
                time_to_resolution_bucket,
                time_to_resolution_bucket_order,
                trade_count,
                market_count,
                avg_pnl_per_contract,
                avg_abs_pnl_per_contract,
                COALESCE(pnl_per_contract_stddev, 0.0) AS pnl_per_contract_stddev,
                min_pnl_per_contract,
                max_pnl_per_contract,
                profitable_trade_share,
                pnl_per_contract_q25,
                pnl_per_contract_median,
                pnl_per_contract_q75,
                CASE
                    WHEN COALESCE(pnl_per_contract_stddev, 0.0) = 0.0 THEN
                        CASE
                            WHEN avg_pnl_per_contract > 0 THEN 999.0
                            WHEN avg_pnl_per_contract < 0 THEN 0.0
                            ELSE NULL
                        END
                    ELSE ABS(avg_pnl_per_contract) / pnl_per_contract_stddev
                END AS edge_to_noise_ratio
            FROM {quote_identifier(dispersion_view)}
        )
        SELECT
            *,
            CASE
                WHEN trade_count < {MIN_TRADE_COUNT_FOR_CANDIDATE} THEN 0.10
                WHEN avg_pnl_per_contract <= 0 THEN 0.0
                WHEN edge_to_noise_ratio >= 0.75 THEN 1.0
                WHEN edge_to_noise_ratio >= 0.35 THEN 0.5
                WHEN edge_to_noise_ratio >= 0.15 THEN 0.25
                ELSE 0.10
            END AS recommended_haircut_multiplier,
            CASE
                WHEN trade_count < {MIN_TRADE_COUNT_FOR_CANDIDATE} THEN 'experimental_sparse'
                WHEN avg_pnl_per_contract <= 0 THEN 'do_not_promote'
                WHEN edge_to_noise_ratio >= 0.35 THEN 'candidate'
                ELSE 'candidate_with_haircut'
            END AS promotion_status
        FROM priors
        """
    )

    input_rows = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(base_view)}").fetchone()[0])
    dispersion_rows = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(dispersion_view)}").fetchone()[0])
    prior_rows = int(connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(prior_view)}").fetchone()[0])

    dispersion_path = study_dir / "dispersion_surface.parquet"
    prior_path = study_dir / "sizing_priors.parquet"
    connection.execute(
        f"""
        COPY (
            SELECT *
            FROM {quote_identifier(dispersion_view)}
            ORDER BY role_basis, venue, maker_taker_role, price_bucket_order, time_to_resolution_bucket_order
        )
        TO {sql_string_literal(str(dispersion_path))}
        (FORMAT PARQUET)
        """
    )
    connection.execute(
        f"""
        COPY (
            SELECT *
            FROM {quote_identifier(prior_view)}
            ORDER BY role_basis, venue, maker_taker_role, price_bucket_order, time_to_resolution_bucket_order
        )
        TO {sql_string_literal(str(prior_path))}
        (FORMAT PARQUET)
        """
    )

    coverage_rows = connection.execute(
        f"""
        SELECT role_basis, venue, maker_taker_role, SUM(trade_count) AS trade_count
        FROM {quote_identifier(prior_view)}
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
            ROUND(avg_pnl_per_contract, 4) AS avg_pnl_per_contract,
            ROUND(pnl_per_contract_stddev, 4) AS pnl_per_contract_stddev,
            ROUND(edge_to_noise_ratio, 4) AS edge_to_noise_ratio,
            recommended_haircut_multiplier,
            promotion_status
        FROM {quote_identifier(prior_view)}
        ORDER BY recommended_haircut_multiplier DESC, edge_to_noise_ratio DESC, trade_count DESC
        LIMIT 8
        """
    ).fetchall()

    warnings: list[str] = []
    if prior_rows == 0:
        warnings.append("No resolved trade rows with usable direction were available for sizing-prior output.")
    if prior_rows > 0:
        sparse_rows = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM {quote_identifier(prior_view)}
                WHERE promotion_status = 'experimental_sparse'
                """
            ).fetchone()[0]
        )
        if sparse_rows == prior_rows:
            warnings.append(
                "All sizing-prior rows are below the minimum trade-count threshold and should remain experimental."
            )

    metadata = {
        "study_name": STUDY_NAME,
        "study_version": STUDY_VERSION,
        "run_id": context.run_id,
        "bundle_name": context.bundle_name,
        "generated_at": context.generated_at.isoformat(),
        "promotion_recommendation": "candidate_with_haircuts" if prior_rows else "experimental",
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
            "min_trade_count_for_candidate": MIN_TRADE_COUNT_FOR_CANDIDATE,
            "haircut_thresholds": {
                "ratio_ge_0_75": 1.0,
                "ratio_ge_0_35": 0.5,
                "ratio_ge_0_15": 0.25,
                "otherwise": 0.10,
            },
        },
        "source_views": ["historical_trade_features"],
        "artifacts": {
            "dispersion_surface": str(dispersion_path),
            "sizing_priors": str(prior_path),
        },
        "row_counts": {
            "input_rows": input_rows,
            "dispersion_rows": dispersion_rows,
            "prior_rows": prior_rows,
        },
        "exclusions": [
            "Rows without resolved outcome, contract side, normalized price, or interpretable taker direction are excluded.",
            "Counterparty maker rows are inferred from matched trades and do not model passive fill probability or queue selection.",
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
        data_paths=(dispersion_path, prior_path),
        metadata=metadata,
    )


def _render_summary(
    *,
    metadata: dict[str, Any],
    coverage_rows: list[tuple[Any, ...]],
    notable_rows: list[tuple[Any, ...]],
) -> str:
    lines = [
        "# Sizing Priors Study",
        "",
        f"- Run id: `{metadata['run_id']}`",
        f"- Generated at: {metadata['generated_at']}",
        f"- Promotion recommendation: `{metadata['promotion_recommendation']}`",
        f"- Input rows: {metadata['row_counts']['input_rows']}",
        f"- Dispersion rows: {metadata['row_counts']['dispersion_rows']}",
        f"- Prior rows: {metadata['row_counts']['prior_rows']}",
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
        lines.append("- No sizing-prior rows were produced.")

    lines.extend(
        [
            "",
            "## Notable Priors",
            "",
        ]
    )
    if notable_rows:
        for row in notable_rows:
            role_basis, venue, maker_taker_role, price_bucket, time_bucket, trade_count, avg_pnl, stddev, ratio, haircut, status = row
            lines.append(
                f"- `{role_basis}` / `{venue}` / `{maker_taker_role}` / `{price_bucket}` / `{time_bucket}`: "
                f"{trade_count} trades, avg pnl/share {avg_pnl:+}, stddev {stddev}, "
                f"edge/noise {ratio}, haircut {haircut}, status `{status}`"
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
