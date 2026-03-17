from pathlib import Path

import duckdb
import pytest

from services.research.materialize import materialize_historical_research
from services.research.settings import HistoricalResearchSettings
from services.research.studies import run_historical_studies


def test_run_historical_studies_writes_artifacts_and_expected_metrics(tmp_path):
    dataset_root = tmp_path / "dataset"
    output_root = tmp_path / "outputs"
    _write_study_dataset(dataset_root)

    settings = HistoricalResearchSettings(
        dataset_root=dataset_root,
        output_root=output_root,
    )
    materialization = materialize_historical_research(settings)

    artifacts = run_historical_studies(
        settings,
        database_path=materialization.database_path,
        run_id="20260317T000000Z",
    )

    assert artifacts.bundle_metadata_path.exists()
    assert artifacts.bundle_summary_path.exists()
    assert [study.study_name for study in artifacts.studies] == [
        "calibration",
        "maker_taker_expectancy",
        "longshot_favorite_bias",
        "sizing_priors",
    ]

    calibration_artifacts = next(study for study in artifacts.studies if study.study_name == "calibration")
    execution_artifacts = next(
        study for study in artifacts.studies if study.study_name == "maker_taker_expectancy"
    )
    bias_artifacts = next(study for study in artifacts.studies if study.study_name == "longshot_favorite_bias")
    sizing_artifacts = next(study for study in artifacts.studies if study.study_name == "sizing_priors")

    connection = duckdb.connect(database=":memory:")
    try:
        calibration_rows = connection.execute(
            f"""
            SELECT
                venue,
                price_bucket,
                time_to_resolution_bucket,
                trade_count,
                ROUND(avg_implied_probability, 4),
                ROUND(realized_win_rate, 4),
                ROUND(avg_miscalibration, 4)
            FROM read_parquet('{calibration_artifacts.data_paths[0].as_posix()}')
            ORDER BY venue, price_bucket, time_to_resolution_bucket
            """
        ).fetchall()
        execution_rows = connection.execute(
            f"""
            SELECT
                role_basis,
                venue,
                maker_taker_role,
                price_bucket,
                time_to_resolution_bucket,
                trade_count,
                ROUND(avg_pnl_per_contract, 4)
            FROM read_parquet('{execution_artifacts.data_paths[0].as_posix()}')
            ORDER BY role_basis, venue, maker_taker_role, price_bucket, time_to_resolution_bucket
            """
        ).fetchall()
        bias_rows = connection.execute(
            f"""
            SELECT
                venue,
                topic_class,
                tail_regime,
                trade_count,
                ROUND(avg_implied_probability, 4),
                ROUND(realized_win_rate, 4),
                ROUND(avg_miscalibration, 4),
                year_count,
                ROUND(sign_consistency_ratio, 4)
            FROM read_parquet('{bias_artifacts.data_paths[0].as_posix()}')
            ORDER BY venue, topic_class, tail_regime
            """
        ).fetchall()
        sizing_rows = connection.execute(
            f"""
            SELECT
                role_basis,
                venue,
                maker_taker_role,
                price_bucket,
                time_to_resolution_bucket,
                trade_count,
                ROUND(avg_pnl_per_contract, 4),
                ROUND(edge_to_noise_ratio, 4),
                ROUND(CAST(recommended_haircut_multiplier AS DOUBLE), 2),
                promotion_status
            FROM read_parquet('{sizing_artifacts.data_paths[1].as_posix()}')
            ORDER BY role_basis, venue, maker_taker_role, price_bucket, time_to_resolution_bucket
            """
        ).fetchall()
    finally:
        connection.close()

    assert ("polymarket", "40c_to_60c", "1d_to_7d", 1, 0.55, 1.0, 0.45) in calibration_rows
    assert ("polymarket", "60c_to_75c", "1d_to_7d", 1, 0.6, 1.0, 0.4) in calibration_rows
    assert ("kalshi", "10c_to_25c", "1d_to_7d", 1, 0.2, 1.0, 0.8) in calibration_rows
    assert (
        "observed",
        "polymarket",
        "maker",
        "40c_to_60c",
        "1d_to_7d",
        1,
        -0.45,
    ) in execution_rows
    assert (
        "observed",
        "polymarket",
        "taker",
        "60c_to_75c",
        "1d_to_7d",
        1,
        0.4,
    ) in execution_rows
    assert (
        "counterparty_inferred",
        "polymarket",
        "maker",
        "75c_to_90c",
        "1d_to_7d",
        1,
        -0.75,
    ) in execution_rows
    assert (
        "polymarket",
        "crypto",
        "favorite",
        2,
        0.935,
        0.5,
        -0.435,
        2,
        0.5,
    ) in bias_rows
    assert (
        "polymarket",
        "politics",
        "longshot",
        2,
        0.07,
        0.5,
        0.43,
        2,
        0.5,
    ) in bias_rows
    assert (
        "observed",
        "polymarket",
        "taker",
        "60c_to_75c",
        "7d_to_30d",
        2,
        0.34,
        6.0104,
        0.1,
        "experimental_sparse",
    ) in sizing_rows

    assert calibration_artifacts.metadata["row_counts"]["surface_rows"] == len(calibration_rows)
    assert execution_artifacts.metadata["row_counts"]["observed_maker_rows"] == 1
    assert bias_artifacts.metadata["row_counts"]["summary_rows"] == len(bias_rows)
    assert sizing_artifacts.metadata["row_counts"]["prior_rows"] == len(sizing_rows)


def test_run_historical_studies_supports_single_study_and_venue_filter(tmp_path):
    dataset_root = tmp_path / "dataset"
    output_root = tmp_path / "outputs"
    _write_study_dataset(dataset_root)

    settings = HistoricalResearchSettings(
        dataset_root=dataset_root,
        output_root=output_root,
    )
    materialization = materialize_historical_research(settings)

    artifacts = run_historical_studies(
        settings,
        database_path=materialization.database_path,
        study_names=["calibration"],
        venue="kalshi",
        run_id="20260317T000100Z",
    )

    assert [study.study_name for study in artifacts.studies] == ["calibration"]

    calibration_artifacts = artifacts.studies[0]
    connection = duckdb.connect(database=":memory:")
    try:
        venues = connection.execute(
            f"""
            SELECT DISTINCT venue
            FROM read_parquet('{calibration_artifacts.data_paths[0].as_posix()}')
            """
        ).fetchall()
    finally:
        connection.close()

    assert venues == [("kalshi",)]


def test_run_historical_studies_help_does_not_require_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("database_url", raising=False)

    from services.tools import run_historical_studies as run_historical_studies_cli

    with pytest.raises(SystemExit) as exc_info:
        run_historical_studies_cli.main(["--help"])

    assert exc_info.value.code == 0
    help_output = capsys.readouterr().out
    assert "HISTORICAL_RESEARCH_OUTPUT_ROOT" in help_output


def _write_study_dataset(dataset_root: Path) -> None:
    dataset_root.mkdir(parents=True, exist_ok=True)
    polymarket_dir = dataset_root / "polymarket"
    kalshi_dir = dataset_root / "kalshi"
    polymarket_dir.mkdir(parents=True, exist_ok=True)
    kalshi_dir.mkdir(parents=True, exist_ok=True)

    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(
            f"""
            COPY (
                SELECT * FROM (
                    VALUES
                        ('pm_taker_1', 'pm_market_1', 'pm_event_1', TIMESTAMP '2025-01-05 00:00:00', 60.0, 10.0, 'taker', 'buy', 'YES', 'Will the election be decided in January?', 'Election timing', 'Politics'),
                        ('pm_maker_1', 'pm_market_1', 'pm_event_1', TIMESTAMP '2025-01-05 01:00:00', 55.0, 8.0, 'maker', 'buy', 'YES', 'Will the election be decided in January?', 'Election timing', 'Politics'),
                        ('pm_taker_2', 'pm_market_2', 'pm_event_2', TIMESTAMP '2025-02-01 00:00:00', 75.0, 4.0, 'taker', 'sell', 'YES', 'Will BTC close above 100k this week?', 'BTC threshold', 'Crypto'),
                        ('pm_longshot_2024', 'pm_market_3', 'pm_event_3', TIMESTAMP '2024-06-05 00:00:00', 8.0, 6.0, 'taker', 'buy', 'YES', 'Will the election be decided this week?', 'Election weekly', 'Politics'),
                        ('pm_longshot_2025', 'pm_market_4', 'pm_event_4', TIMESTAMP '2025-06-05 00:00:00', 6.0, 6.0, 'taker', 'buy', 'YES', 'Will the election be decided this week?', 'Election weekly', 'Politics'),
                        ('pm_favorite_2024', 'pm_market_5', 'pm_event_5', TIMESTAMP '2024-07-05 00:00:00', 95.0, 5.0, 'taker', 'buy', 'YES', 'Will BTC close above 100k this week?', 'BTC threshold', 'Crypto'),
                        ('pm_favorite_2025', 'pm_market_6', 'pm_event_6', TIMESTAMP '2025-07-05 00:00:00', 92.0, 5.0, 'taker', 'buy', 'YES', 'Will BTC close above 100k this week?', 'BTC threshold', 'Crypto'),
                        ('pm_taker_3', 'pm_market_7', 'pm_event_7', TIMESTAMP '2025-02-01 00:00:00', 62.0, 7.0, 'taker', 'buy', 'YES', 'Will the AI launch happen this month?', 'AI launch', 'Tech'),
                        ('pm_taker_4', 'pm_market_8', 'pm_event_8', TIMESTAMP '2025-02-02 00:00:00', 70.0, 7.0, 'taker', 'buy', 'YES', 'Will the AI launch happen this month?', 'AI launch', 'Tech')
                ) AS t(trade_id, market_id, event_id, timestamp, price, size, maker_taker_role, side, contract_side, question, title, category)
            )
            TO '{(polymarket_dir / "trades.parquet").as_posix()}'
            (FORMAT parquet)
            """
        )
        connection.execute(
            f"""
            COPY (
                SELECT * FROM (
                    VALUES
                        ('pm_market_1', 'pm_event_1', 'pm-election-jan', 'Will the election be decided in January?', 'Election timing', 'Politics', 'closed', TIMESTAMP '2024-12-20 00:00:00', TIMESTAMP '2025-01-10 00:00:00'),
                        ('pm_market_2', 'pm_event_2', 'pm-btc-threshold', 'Will BTC close above 100k this week?', 'BTC threshold', 'Crypto', 'closed', TIMESTAMP '2025-01-20 00:00:00', TIMESTAMP '2025-02-03 00:00:00'),
                        ('pm_market_3', 'pm_event_3', 'pm-election-weekly-2024', 'Will the election be decided this week?', 'Election weekly', 'Politics', 'closed', TIMESTAMP '2024-05-20 00:00:00', TIMESTAMP '2024-06-10 00:00:00'),
                        ('pm_market_4', 'pm_event_4', 'pm-election-weekly-2025', 'Will the election be decided this week?', 'Election weekly', 'Politics', 'closed', TIMESTAMP '2025-05-20 00:00:00', TIMESTAMP '2025-06-10 00:00:00'),
                        ('pm_market_5', 'pm_event_5', 'pm-btc-favorite-2024', 'Will BTC close above 100k this week?', 'BTC threshold', 'Crypto', 'closed', TIMESTAMP '2024-06-20 00:00:00', TIMESTAMP '2024-07-10 00:00:00'),
                        ('pm_market_6', 'pm_event_6', 'pm-btc-favorite-2025', 'Will BTC close above 100k this week?', 'BTC threshold', 'Crypto', 'closed', TIMESTAMP '2025-06-20 00:00:00', TIMESTAMP '2025-07-10 00:00:00'),
                        ('pm_market_7', 'pm_event_7', 'pm-ai-launch-1', 'Will the AI launch happen this month?', 'AI launch', 'Tech', 'closed', TIMESTAMP '2025-01-15 00:00:00', TIMESTAMP '2025-02-20 00:00:00'),
                        ('pm_market_8', 'pm_event_8', 'pm-ai-launch-2', 'Will the AI launch happen this month?', 'AI launch', 'Tech', 'closed', TIMESTAMP '2025-01-16 00:00:00', TIMESTAMP '2025-02-20 00:00:00')
                ) AS t(market_id, event_id, market_slug, question, title, category, status, open_time, close_time)
            )
            TO '{(polymarket_dir / "markets.parquet").as_posix()}'
            (FORMAT parquet)
            """
        )
        connection.execute(
            f"""
            COPY (
                SELECT * FROM (
                    VALUES
                        ('pm_market_1', TIMESTAMP '2025-01-10 00:00:00', 'YES', 1.0),
                        ('pm_market_2', TIMESTAMP '2025-02-03 00:00:00', 'NO', 0.0),
                        ('pm_market_3', TIMESTAMP '2024-06-10 00:00:00', 'YES', 1.0),
                        ('pm_market_4', TIMESTAMP '2025-06-10 00:00:00', 'NO', 0.0),
                        ('pm_market_5', TIMESTAMP '2024-07-10 00:00:00', 'NO', 0.0),
                        ('pm_market_6', TIMESTAMP '2025-07-10 00:00:00', 'YES', 1.0),
                        ('pm_market_7', TIMESTAMP '2025-02-20 00:00:00', 'YES', 1.0),
                        ('pm_market_8', TIMESTAMP '2025-02-20 00:00:00', 'YES', 1.0)
                ) AS t(market_id, resolution_timestamp, resolved_outcome, resolution_value)
            )
            TO '{(polymarket_dir / "resolutions.parquet").as_posix()}'
            (FORMAT parquet)
            """
        )
        connection.execute(
            f"""
            COPY (
                SELECT * FROM (
                    VALUES
                        ('k_taker_1', 'k_market_1', 'k_event_1', TIMESTAMP '2025-02-01 00:00:00', 20.0, 6.0, 'taker', 'buy', 'no', 'Will BTC close below 80k this week?', 'BTC low threshold', 'Crypto')
                ) AS t(trade_id, market_id, event_id, timestamp, price, size, maker_taker_role, side, contract_side, question, title, category)
            )
            TO '{(kalshi_dir / "trades.parquet").as_posix()}'
            (FORMAT parquet)
            """
        )
        connection.execute(
            f"""
            COPY (
                SELECT * FROM (
                    VALUES
                        ('k_market_1', 'k_event_1', 'k-btc-low-threshold', 'Will BTC close below 80k this week?', 'BTC low threshold', 'Crypto', 'closed', TIMESTAMP '2025-01-25 00:00:00', TIMESTAMP '2025-02-05 00:00:00', TIMESTAMP '2025-02-05 00:00:00', 'no')
                ) AS t(market_id, event_id, market_slug, question, title, category, status, open_time, close_time, resolution_timestamp, result)
            )
            TO '{(kalshi_dir / "markets.parquet").as_posix()}'
            (FORMAT parquet)
            """
        )
        connection.execute(
            f"""
            COPY (
                SELECT * FROM (
                    VALUES
                        ('k_market_1', TIMESTAMP '2025-02-05 00:00:00', 'no', 0.0)
                ) AS t(market_id, resolution_timestamp, resolved_outcome, resolution_value)
            )
            TO '{(kalshi_dir / "resolutions.parquet").as_posix()}'
            (FORMAT parquet)
            """
        )
    finally:
        connection.close()
