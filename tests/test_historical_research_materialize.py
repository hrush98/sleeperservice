from pathlib import Path

import duckdb
import pytest

from services.research.features import (
    bucket_probability_price,
    bucket_time_to_resolution,
    bucket_trade_size,
    classify_topic_text,
    normalize_contract_price,
    normalize_maker_taker_role,
    time_to_resolution_seconds,
)
from services.research.materialize import materialize_historical_research
from services.research.settings import HistoricalResearchSettings


def test_feature_derivations_cover_core_buckets():
    assert normalize_contract_price(62) == pytest.approx(0.62)
    assert bucket_probability_price(0.009) == "lt_1c"
    assert bucket_probability_price(0.62) == "60c_to_75c"
    assert bucket_trade_size(notional_usd=12.4) == "10_to_50_usd"
    assert bucket_trade_size(quantity=1200) == "1000_to_10000_shares"
    assert bucket_time_to_resolution(3 * 24 * 3600) == "1d_to_7d"
    assert normalize_maker_taker_role("aggressor") == "taker"
    assert normalize_maker_taker_role(False) == "maker"
    assert classify_topic_text("Will the election be decided by November?") == "politics"
    assert time_to_resolution_seconds("2025-01-05T00:00:00Z", "2025-01-10T00:00:00Z") == pytest.approx(432000.0)


def test_materialize_historical_research_builds_expected_views(tmp_path):
    dataset_root = tmp_path / "dataset"
    output_root = tmp_path / "outputs"
    _write_test_parquet_dataset(dataset_root)

    artifacts = materialize_historical_research(
        HistoricalResearchSettings(
            dataset_root=dataset_root,
            output_root=output_root,
        )
    )

    assert artifacts.database_path.exists()
    assert artifacts.metadata_path.exists()
    assert artifacts.summary_path.exists()

    connection = duckdb.connect(str(artifacts.database_path))
    try:
        trade_features = connection.execute(
            """
            SELECT venue, trade_id, market_id, price_probability, maker_taker_role,
                   price_bucket, time_to_resolution_bucket, size_bucket, topic_class
            FROM historical_trade_features
            ORDER BY venue, trade_id
            """
        ).fetchall()
        bucket_stats_count = connection.execute(
            "SELECT COUNT(*) FROM historical_bucket_stats"
        ).fetchone()[0]
    finally:
        connection.close()

    assert artifacts.metadata["view_row_counts"]["historical_trades"] == 2
    assert artifacts.metadata["view_row_counts"]["historical_markets"] == 2
    assert artifacts.metadata["view_row_counts"]["historical_trade_features"] == 2
    assert bucket_stats_count == 2
    assert trade_features == [
        ("kalshi", "k_trade_1", "k_market_1", 0.35, "maker", "25c_to_40c", "7d_to_30d", "lt_10_usd", "crypto"),
        ("polymarket", "pm_trade_1", "pm_market_1", 0.62, "taker", "60c_to_75c", "1d_to_7d", "10_to_50_usd", "politics"),
    ]


def test_materialize_historical_research_help_does_not_require_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("database_url", raising=False)

    from services.tools import materialize_historical_research

    with pytest.raises(SystemExit) as exc_info:
        materialize_historical_research.main(["--help"])

    assert exc_info.value.code == 0
    help_output = capsys.readouterr().out
    assert "HISTORICAL_RESEARCH_OUTPUT_ROOT" in help_output


def _write_test_parquet_dataset(dataset_root: Path) -> None:
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
                        ('pm_trade_1', 'pm_market_1', 'pm_event_1', TIMESTAMP '2025-01-05 00:00:00', 62.0, 20.0, 'true', 'buy', 'Will the presidential election be decided in November?', 'Election contract', 'Politics')
                ) AS t(trade_id, market_id, event_id, timestamp, price, size, is_taker, side, question, title, category)
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
                        ('pm_market_1', 'pm_event_1', 'presidential-election-november', 'Will the presidential election be decided in November?', 'Election contract', 'Politics', TIMESTAMP '2024-12-01 00:00:00', TIMESTAMP '2025-11-04 00:00:00', TIMESTAMP '2025-01-10 00:00:00', 'YES', 'active')
                ) AS t(market_id, event_id, market_slug, question, title, category, open_time, close_time, resolution_time, resolved_outcome, status)
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
                        ('k_trade_1', 'k_market_1', 'k_event_1', TIMESTAMP '2025-01-01 00:00:00', 0.35, 15.0, 'maker', 'sell', 'Will BTC close above 100k?', 'BTC threshold', 'Crypto')
                ) AS t(trade_id, market_id, event_id, timestamp, price, size, maker_taker_role, taker_side, question, title, topic)
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
                        ('k_market_1', TIMESTAMP '2025-01-20 00:00:00', 'YES', 1.0)
                ) AS t(market_id, resolution_timestamp, resolved_outcome, resolution_value)
            )
            TO '{(kalshi_dir / "resolutions.parquet").as_posix()}'
            (FORMAT parquet)
            """
        )
    finally:
        connection.close()
