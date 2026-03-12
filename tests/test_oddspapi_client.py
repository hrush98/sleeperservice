from datetime import datetime, timezone

from services.cli.discover import _parse_datetime
from services.shared.oddspapi_client import OddsPapiClient


def _path_style_moneyline_payload() -> dict:
    """Payload with path-style bookmakerMarketId: match (/0/) and games (/1/, /3/)."""
    def market(bmid: str, home_price: float, away_price: float) -> dict:
        return {
            "bookmakerMarketId": bmid,
            "outcomes": {
                "1": {"players": {"0": {"bookmakerOutcomeId": "home", "price": home_price, "playerId": 1}}},
                "2": {"players": {"0": {"bookmakerOutcomeId": "away", "price": away_price, "playerId": 2}}},
            },
        }
    return {
        "bookmakerOdds": {
            "pinnacle": {
                "markets": {
                    "181": market("line/12/284168/1624611971/3466354853/0/moneyline", 1.598, 2.25),
                    "1847": market("line/12/284168/1624611971/3466108453/1/moneyline", 1.032, 12.11),
                    "1851": market("line/12/284168/1624611971/3466354850/3/moneyline", 1.598, 2.25),
                }
            }
        }
    }


def test_market_game_number_path_style() -> None:
    """Path-style bookmakerMarketId: /0/ is match, /1/ /3/ are games."""
    m0 = {"bookmakerMarketId": "line/12/foo/0/moneyline"}
    m1 = {"bookmakerMarketId": "line/12/foo/1/moneyline"}
    assert OddsPapiClient._market_game_number(m0, "181") == 0
    assert OddsPapiClient._market_game_number(m1, "1847") == 1
    assert OddsPapiClient._market_game_number({"bookmakerMarketId": "line/foo/3/moneyline"}, None) == 3


def test_market_is_game_excludes_match() -> None:
    """Game 0 (match) is not considered a game; game 1+ is."""
    m0 = {"bookmakerMarketId": "line/foo/0/moneyline"}
    m1 = {"bookmakerMarketId": "line/foo/1/moneyline"}
    assert OddsPapiClient._market_is_game(m0, "181") is False
    assert OddsPapiClient._market_is_game(m1, "1847") is True


def test_extract_pinnacle_moneyline_prefers_match_when_path_style() -> None:
    """With match (/0/) and game moneylines, only match market is used."""
    payload = _path_style_moneyline_payload()
    result = OddsPapiClient.extract_pinnacle_moneyline(payload)
    assert result
    assert result.get("home", {}).get("price") == 1.598
    assert result.get("away", {}).get("price") == 2.25


def test_parse_datetime_handles_zulu() -> None:
    parsed = _parse_datetime("2024-01-01T12:00:00Z")
    assert parsed == datetime(2024, 1, 1, 12, 0, 0, tzinfo=parsed.tzinfo)


def test_parse_datetime_rejects_invalid() -> None:
    assert _parse_datetime("not-a-date") is None
    assert _parse_datetime(123) is None


def test_auth_params() -> None:
    client = OddsPapiClient(api_key="key")
    assert client._auth_params() == {"apiKey": "key"}


def _payload_with_changed_at(
    home_changed: str | None = None,
    away_changed: str | None = None,
    updated_at: str | None = None,
) -> dict:
    """Payload with match moneyline and optional changedAt/updatedAt for staleness tests."""
    def market(bmid: str, home_price: float, away_price: float) -> dict:
        home_pl = {"bookmakerOutcomeId": "home", "price": home_price}
        if home_changed is not None:
            home_pl["changedAt"] = home_changed
        away_pl = {"bookmakerOutcomeId": "away", "price": away_price}
        if away_changed is not None:
            away_pl["changedAt"] = away_changed
        return {
            "bookmakerMarketId": bmid,
            "outcomes": {
                "1": {"players": {"0": home_pl}},
                "2": {"players": {"0": away_pl}},
            },
        }
    out = {
        "bookmakerOdds": {
            "pinnacle": {
                "markets": {
                    "181": market("line/12/284168/1624611971/3466354853/0/moneyline", 1.598, 2.25),
                }
            }
        }
    }
    if updated_at is not None:
        out["updatedAt"] = updated_at
    return out


def test_get_pinnacle_p_ref_changed_at_returns_latest_from_home_away() -> None:
    """When both home and away have changed_at, returns the latest."""
    payload = _payload_with_changed_at(
        home_changed="2026-02-21T16:00:00.000Z",
        away_changed="2026-02-21T16:05:00.000Z",
    )
    result = OddsPapiClient.get_pinnacle_p_ref_changed_at(payload, "match_winner", None)
    assert result is not None
    assert result.tzinfo is not None
    # 16:05 is later
    assert result.hour == 16
    assert result.minute == 5


def test_get_pinnacle_p_ref_changed_at_fallback_to_updated_at() -> None:
    """When Pinnacle outcomes have no changed_at, fallback to payload updatedAt."""
    payload = _payload_with_changed_at(updated_at="2026-02-21T17:30:00+00:00")
    result = OddsPapiClient.get_pinnacle_p_ref_changed_at(payload, "match_winner", None)
    assert result is not None
    assert result.hour == 17
    assert result.minute == 30


def test_get_pinnacle_p_ref_changed_at_returns_none_when_no_timestamps() -> None:
    """When no changed_at and no updatedAt, returns None."""
    payload = _path_style_moneyline_payload()  # no changedAt, no updatedAt
    result = OddsPapiClient.get_pinnacle_p_ref_changed_at(payload, "match_winner", None)
    assert result is None


def test_get_pinnacle_p_ref_changed_at_game_winner_uses_game_market() -> None:
    """game_winner with game_number uses game market when present."""
    payload = {
        "bookmakerOdds": {
            "pinnacle": {
                "markets": {
                    "181": {
                        "bookmakerMarketId": "line/12/foo/0/moneyline",
                        "outcomes": {
                            "1": {"players": {"0": {"bookmakerOutcomeId": "home", "price": 1.5, "changedAt": "2026-02-21T15:00:00Z"}}},
                            "2": {"players": {"0": {"bookmakerOutcomeId": "away", "price": 2.5, "changedAt": "2026-02-21T15:00:00Z"}}},
                        },
                    },
                    "1847": {
                        "bookmakerMarketId": "line/12/foo/1/moneyline",
                        "outcomes": {
                            "1": {"players": {"0": {"bookmakerOutcomeId": "game1/home", "price": 1.9, "changedAt": "2026-02-21T16:10:00Z"}}},
                            "2": {"players": {"0": {"bookmakerOutcomeId": "game1/away", "price": 1.9, "changedAt": "2026-02-21T16:10:00Z"}}},
                        },
                    },
                }
            }
        }
    }
    result = OddsPapiClient.get_pinnacle_p_ref_changed_at(payload, "game_winner", 1)
    assert result is not None
    assert result.hour == 16
    assert result.minute == 10
