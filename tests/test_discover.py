"""Tests for discovery engine helpers."""

from types import SimpleNamespace

from cli.discover import (
    _anchor_from_goalserve,
    _build_market_side_map,
    _classify_polymarket_market,
    _extract_market_liquidity,
)


class TestExtractMarketLiquidity:
    """Tests for _extract_market_liquidity."""

    def test_none_input(self) -> None:
        assert _extract_market_liquidity(None) == 0.0

    def test_empty_dict(self) -> None:
        assert _extract_market_liquidity({}) == 0.0

    def test_event_level_volume_numeric(self) -> None:
        assert _extract_market_liquidity({"volume": 12345.67}) == 12345.67

    def test_event_level_volume_string(self) -> None:
        assert _extract_market_liquidity({"volume": "98765"}) == 98765.0

    def test_event_level_volume_invalid_falls_through(self) -> None:
        # Invalid event-level volume should fall through to markets
        raw = {"volume": "bad", "markets": [{"volume": 100}]}
        assert _extract_market_liquidity(raw) == 100.0

    def test_market_level_volume_sum(self) -> None:
        raw = {
            "markets": [
                {"volume": "500"},
                {"volume": 300},
                {"volume": "200"},
            ]
        }
        assert _extract_market_liquidity(raw) == 1000.0

    def test_market_level_liquidity_fallback(self) -> None:
        """When volume is missing, fall back to liquidity field."""
        raw = {
            "markets": [
                {"liquidity": 400},
                {"liquidity": "600"},
            ]
        }
        assert _extract_market_liquidity(raw) == 1000.0

    def test_volume_preferred_over_liquidity(self) -> None:
        """Volume should be preferred per-market when both exist."""
        raw = {
            "markets": [
                {"volume": 100, "liquidity": 9999},
            ]
        }
        assert _extract_market_liquidity(raw) == 100.0

    def test_non_list_markets_ignored(self) -> None:
        assert _extract_market_liquidity({"markets": "bad"}) == 0.0


def _fixture(team_a: str, team_b: str) -> SimpleNamespace:
    return SimpleNamespace(team_a_name=team_a, team_b_name=team_b, start_time=None)


def test_anchor_from_goalserve_locks_when_clear_match() -> None:
    op_fix = _fixture("French Flair", "BK Rog eSports")
    rows = [
        {
            "match_id": "m1",
            "date": None,
            "home_team": "French Flair",
            "away_team": "BK ROG Esports",
        }
    ]
    anchor = _anchor_from_goalserve(op_fix, rows)
    assert anchor["orientation_locked"] is True
    assert anchor["team_a_is_home"] is True
    assert anchor["orientation_anchor_source"] == "goalserve_pre"


def test_anchor_from_goalserve_unlocked_on_low_similarity() -> None:
    op_fix = _fixture("French Flair", "BK Rog eSports")
    rows = [
        {
            "match_id": "m1",
            "date": None,
            "home_team": "Some Team",
            "away_team": "Another Team",
        }
    ]
    anchor = _anchor_from_goalserve(op_fix, rows)
    assert anchor["orientation_locked"] is False


def test_classify_polymarket_market_totals() -> None:
    market = {
        "sportsMarketType": "totals",
        "question": "Total Maps O/U 3.5",
        "outcomes": ["Over 3.5 Maps", "Under 3.5 Maps"],
    }
    classified = _classify_polymarket_market(market)
    assert classified == ("totals", None, 3.5)


def test_build_market_side_map_totals_over_under() -> None:
    event_raw = {
        "markets": [
            {
                "sportsMarketType": "totals",
                "question": "Total Maps O/U 3.5",
                "tokens": [
                    {"token_id": "tok_over", "outcome": "Over 3.5 Maps"},
                    {"token_id": "tok_under", "outcome": "Under 3.5 Maps"},
                ],
                "outcomes": ["Over 3.5 Maps", "Under 3.5 Maps"],
            }
        ]
    }
    side_map = _build_market_side_map(event_raw, teams_swapped=False)
    assert "totals:3.5" in side_map
    assert side_map["totals:3.5"]["token_id_a"] == "tok_over"
    assert side_map["totals:3.5"]["token_id_b"] == "tok_under"
