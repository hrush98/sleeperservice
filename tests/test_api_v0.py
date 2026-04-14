from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb

from services.api.analysis_v0 import V0AnalysisService
from services.api.routers.analysis_v0 import get_market_analysis, get_ranked_opportunities
from services.research.artifacts import HistoricalArtifactStore
from services.research.materialize import materialize_historical_research
from services.research.settings import HistoricalResearchSettings
from services.research.studies import run_historical_studies


def test_historical_artifact_store_loads_promoted_contexts(tmp_path):
    output_root = _build_promoted_outputs(tmp_path)
    store = HistoricalArtifactStore(output_root=output_root)

    bundle = store.latest_bundle()
    assert bundle is not None
    assert bundle.run_id == "20260318T000000Z"

    calibration = store.lookup_calibration_context(
        venue="polymarket",
        price_bucket="60c_to_75c",
        time_to_resolution_bucket="1d_to_7d",
    )
    maker_taker = store.lookup_maker_taker_context(
        venue="polymarket",
        price_bucket="60c_to_75c",
        time_to_resolution_bucket="1d_to_7d",
    )
    sizing = store.lookup_sizing_context(
        venue="polymarket",
        price_bucket="60c_to_75c",
        time_to_resolution_bucket="7d_to_30d",
    )

    assert calibration is not None
    assert calibration["historical_trade_count"] == 1
    assert calibration["avg_miscalibration"] == 0.4
    assert maker_taker is not None
    assert maker_taker["role_basis"] == "counterparty_inferred"
    assert sizing is not None
    assert sizing["promotion_status"] == "experimental_sparse"


def test_v0_ranked_opportunities_returns_historical_prior_rankings(tmp_path):
    output_root = _build_promoted_outputs(tmp_path)
    fake_client = _FakePolymarketClient(_sample_live_markets())
    service = V0AnalysisService(
        artifact_store=HistoricalArtifactStore(output_root=output_root),
        polymarket_client=fake_client,
    )

    payload = get_ranked_opportunities(
        limit=10,
        categories="politics",
        opportunity_types=None,
        min_confidence=0.5,
        min_edge_value=0.0,
        time_horizon=None,
        include_trace=True,
        service=service,
    )
    assert payload["metadata"]["trace_included"] is True
    assert payload["metadata"]["artifacts"]["run_id"] == "20260318T000000Z"
    assert len(payload["opportunities"]) == 1
    opportunity = payload["opportunities"][0]
    assert opportunity["market_id"] == "pm_live_politics"
    assert opportunity["category"] == "politics"
    assert opportunity["opportunity_type"] == "calibration_watch"
    assert opportunity["edge"]["metric"] == "historical_miscalibration_points"
    assert opportunity["price_snapshot"]["midpoint"] == 0.6
    assert opportunity["market_state"]["depth_near_mid_usd"] > 0
    assert opportunity["evidence_trace"]


def test_v0_market_analysis_combines_live_snapshot_and_artifacts(tmp_path):
    output_root = _build_promoted_outputs(tmp_path)
    fake_client = _FakePolymarketClient(_sample_live_markets())
    service = V0AnalysisService(
        artifact_store=HistoricalArtifactStore(output_root=output_root),
        polymarket_client=fake_client,
    )

    payload = get_market_analysis(
        market_id="pm_live_crypto",
        include_trace=True,
        service=service,
    )
    assert payload["market_id"] == "pm_live_crypto"
    assert payload["category"] == "crypto"
    assert payload["price_snapshot"]["bid"] == 0.73
    assert payload["calibration_context"]["price_bucket"] == "60c_to_75c"
    assert payload["calibration_context"]["time_to_resolution_bucket"] == "1d_to_7d"
    assert payload["maker_taker_context"]["role_basis"] == "counterparty_inferred"
    assert payload["metadata"]["artifacts"]["run_id"] == "20260318T000000Z"
    assert payload["evidence_trace"]


def _build_promoted_outputs(tmp_path: Path) -> Path:
    dataset_root = tmp_path / "dataset"
    output_root = tmp_path / "outputs"
    _write_study_dataset(dataset_root)

    settings = HistoricalResearchSettings(
        dataset_root=dataset_root,
        output_root=output_root,
    )
    materialization = materialize_historical_research(settings)
    run_historical_studies(
        settings,
        database_path=materialization.database_path,
        run_id="20260318T000000Z",
    )
    return output_root


class _FakePolymarketClient:
    def __init__(self, markets: list[dict]):
        self.markets = {str(market["id"]): market for market in markets}

    def get_markets(self, *, active: bool = True, limit: int = 100):  # noqa: ARG002
        return list(self.markets.values())

    def get_market_by_id(self, market_id: str):
        return self.markets.get(market_id)

    def get_clob_orderbook(self, token_id: str):
        books = {
            "yes-politics": {
                "bids": [{"price": 0.58, "size": 1000.0}],
                "asks": [{"price": 0.62, "size": 900.0}],
                "best_bid": 0.58,
                "best_ask": 0.62,
                "mid": 0.60,
            },
            "yes-crypto": {
                "bids": [{"price": 0.73, "size": 700.0}],
                "asks": [{"price": 0.77, "size": 500.0}],
                "best_bid": 0.73,
                "best_ask": 0.77,
                "mid": 0.75,
            },
        }
        return books.get(
            token_id,
            {
                "bids": [],
                "asks": [],
                "best_bid": None,
                "best_ask": None,
                "mid": None,
            },
        )


def _sample_live_markets() -> list[dict]:
    now = datetime.now(tz=UTC)
    politics_end = (now + timedelta(days=3)).isoformat()
    crypto_end = (now + timedelta(days=4)).isoformat()
    return [
        {
            "id": "pm_live_politics",
            "slug": "will-the-election-be-decided-in-january",
            "question": "Will the election be decided in January?",
            "title": "Election timing",
            "description": "Politics market",
            "tags": [{"label": "Politics"}],
            "outcomes": '["Yes", "No"]',
            "outcomePrices": "[0.60, 0.40]",
            "clobTokenIds": '["yes-politics", "no-politics"]',
            "endDate": politics_end,
            "volume": 100000,
            "liquidity": 4000,
        },
        {
            "id": "pm_live_crypto",
            "slug": "will-btc-close-above-100k-this-week",
            "question": "Will BTC close above 100k this week?",
            "title": "BTC threshold",
            "description": "Crypto market",
            "tags": [{"label": "Crypto"}],
            "outcomes": '["Yes", "No"]',
            "outcomePrices": "[0.75, 0.25]",
            "clobTokenIds": '["yes-crypto", "no-crypto"]',
            "endDate": crypto_end,
            "volume": 85000,
            "liquidity": 3200,
        },
    ]


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
