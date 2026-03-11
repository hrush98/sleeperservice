from shared.oddspapi_client import OddsPapiClient


def _payload_with_totals(*, market_id: str = "line/totals", line: str = "3.5") -> dict:
    return {
        "bookmakerOdds": {
            "pinnacle": {
                "markets": {
                    market_id: {
                        "bookmakerMarketId": market_id,
                        "outcomes": {
                            "1": {
                                "players": {
                                    "0": {
                                        "bookmakerOutcomeId": f"{line}/over",
                                        "price": 1.91,
                                        "changedAt": "2026-02-13T10:00:00Z",
                                        "playerId": 1,
                                    }
                                }
                            },
                            "2": {
                                "players": {
                                    "0": {
                                        "bookmakerOutcomeId": f"{line}/under",
                                        "price": 1.91,
                                        "changedAt": "2026-02-13T10:00:01Z",
                                        "playerId": 2,
                                    }
                                }
                            },
                        },
                    }
                }
            }
        }
    }


def test_extract_pinnacle_totals_returns_over_under_pair() -> None:
    payload = _payload_with_totals()
    parsed = OddsPapiClient.extract_pinnacle_totals(payload, line_value=3.5)
    assert parsed.get("line_value") == 3.5
    assert parsed.get("over", {}).get("price") == 1.91
    assert parsed.get("under", {}).get("price") == 1.91


def test_extract_pinnacle_totals_respects_game_filter() -> None:
    payload = _payload_with_totals(market_id="line/map2/totals")
    parsed_wrong = OddsPapiClient.extract_pinnacle_totals(payload, line_value=3.5, game_number=1)
    parsed_right = OddsPapiClient.extract_pinnacle_totals(payload, line_value=3.5, game_number=2)
    assert parsed_wrong == {}
    assert parsed_right.get("line_value") == 3.5


def test_extract_pinnacle_totals_returns_empty_for_missing_line() -> None:
    payload = _payload_with_totals(line="4.5")
    parsed = OddsPapiClient.extract_pinnacle_totals(payload, line_value=3.5)
    assert parsed == {}
