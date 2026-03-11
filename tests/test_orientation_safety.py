from types import SimpleNamespace

from services.cli.poller import (
    _extract_p_refs_from_odds,
    _orientation_swap_from_game_markets,
    _resolve_token_ids_from_mapping,
)


def _make_fixture(team_a: str, team_b: str) -> SimpleNamespace:
    return SimpleNamespace(source_id="fixture-1", team_a_name=team_a, team_b_name=team_b)


def _odds_payload(
    *,
    participant1_id: str,
    participant2_id: str,
    participant1_name: str,
    participant2_name: str,
    home_player_id: str,
    away_player_id: str,
    home_price: float = 2.2,
    away_price: float = 1.7,
) -> dict:
    return {
        "participant1Id": participant1_id,
        "participant2Id": participant2_id,
        "participant1Name": participant1_name,
        "participant2Name": participant2_name,
        "bookmakerOdds": {
            "pinnacle": {
                "markets": {
                    "match": {
                        "outcomes": {
                            "0": {
                                "players": {
                                    "0": {
                                        "bookmakerOutcomeId": "home",
                                        "price": home_price,
                                        "playerId": home_player_id,
                                    }
                                }
                            },
                            "1": {
                                "players": {
                                    "0": {
                                        "bookmakerOutcomeId": "away",
                                        "price": away_price,
                                        "playerId": away_player_id,
                                    }
                                }
                            },
                        }
                    }
                }
            }
        },
    }


def test_extract_p_refs_uses_name_fallback_when_ids_present_but_ambiguous() -> None:
    fixture = _make_fixture("Lille Esport", "TLN Pirates")
    payload = _odds_payload(
        participant1_id="p1",
        participant2_id="p2",
        participant1_name="TLN Pirates",
        participant2_name="Lille Esport",
        home_player_id="home-x",
        away_player_id="away-y",
    )
    p_ref_a, p_ref_b, odds_a, odds_b, orientation = _extract_p_refs_from_odds(
        payload,
        market_type="match_winner",
        game_number=None,
        line_value=None,
        op_fixture=fixture,
    )
    assert orientation["status"] == "name_swapped"
    assert odds_a == 1.7
    assert odds_b == 2.2
    assert p_ref_a is not None and p_ref_b is not None


def test_extract_p_refs_blocks_unresolved_abbrev_orientation() -> None:
    fixture = _make_fixture("French Flair", "Joblife")
    payload = _odds_payload(
        participant1_id="p1",
        participant2_id="p2",
        participant1_name="FF1",
        participant2_name="JL",
        home_player_id="home-x",
        away_player_id="away-y",
    )
    p_ref_a, p_ref_b, _, _, orientation = _extract_p_refs_from_odds(
        payload,
        market_type="match_winner",
        game_number=None,
        line_value=None,
        op_fixture=fixture,
    )
    assert orientation["status"] == "unresolved"
    assert p_ref_a is None
    assert p_ref_b is None


def test_market_side_map_takes_precedence_over_teams_swapped_fallback() -> None:
    token_id_a, token_id_b, source, _stored_market = _resolve_token_ids_from_mapping(
        match_details={
            "teams_swapped": True,
            "market_side_map": {
                "match_winner": {
                    "token_id_a": "tok-a",
                    "token_id_b": "tok-b",
                    "pm_team_for_a": "Team A",
                    "pm_team_for_b": "Team B",
                }
            },
        },
        mapping_key="match_winner",
        outcome_pairs=[("Left", "left-token"), ("Right", "right-token")],
        market_type="match_winner",
    )
    assert source == "stored_market"
    assert token_id_a == "tok-a"
    assert token_id_b == "tok-b"


def test_extract_p_refs_uses_locked_orientation_when_present() -> None:
    fixture = _make_fixture("French Flair", "BK Rog eSports")
    payload = _odds_payload(
        participant1_id="p1",
        participant2_id="p2",
        participant1_name="French Flair",
        participant2_name="BK Rog eSports",
        home_player_id="home-x",
        away_player_id="away-y",
        home_price=1.70,
        away_price=2.20,
    )
    p_ref_a, p_ref_b, odds_a, odds_b, orientation = _extract_p_refs_from_odds(
        payload,
        market_type="match_winner",
        game_number=None,
        line_value=None,
        op_fixture=fixture,
        mapping_details={
            "orientation_locked": True,
            "team_a_is_home": True,
            "orientation_anchor_source": "goalserve_pre",
        },
    )
    assert orientation["status"] == "locked_home"
    assert orientation["locked"] is True
    assert orientation["source"] == "goalserve_pre"
    assert odds_a == 1.70
    assert odds_b == 2.20
    assert p_ref_a is not None and p_ref_b is not None


def test_match_orientation_from_game_player_id_when_match_has_no_player_id() -> None:
    """Match and game use same ordering; use game market player_id for match when match has none."""
    fixture = _make_fixture("Fnatic", "Team Vitality")
    # Match market: home/away, no playerId. Game 1 market: game1/home, game1/away with playerId.
    payload = {
        "participant1Id": 100,
        "participant2Id": 200,
        "participant1Name": "Fnatic",
        "participant2Name": "Team Vitality",
        "bookmakerOdds": {
            "pinnacle": {
                "markets": {
                    "181": {
                        "bookmakerMarketId": "line/12/211390/1624476859/3466195363/0/moneyline",
                        "outcomes": {
                            "181a": {
                                "players": {
                                    "0": {
                                        "bookmakerOutcomeId": "home",
                                        "price": 1.636,
                                        "playerId": None,
                                    }
                                }
                            },
                            "181b": {
                                "players": {
                                    "0": {
                                        "bookmakerOutcomeId": "away",
                                        "price": 2.28,
                                        "playerId": None,
                                    }
                                }
                            },
                        },
                    },
                    "g1": {
                        "bookmakerMarketId": "line/12/211390/1624476859/3466195363/1/moneyline",
                        "outcomes": {
                            "g1h": {
                                "players": {
                                    "0": {
                                        "bookmakerOutcomeId": "game1/home",
                                        "price": 1.65,
                                        "playerId": 100,
                                    }
                                }
                            },
                            "g1a": {
                                "players": {
                                    "0": {
                                        "bookmakerOutcomeId": "game1/away",
                                        "price": 2.25,
                                        "playerId": 200,
                                    }
                                }
                            },
                        },
                    },
                }
            }
        },
    }
    p_ref_a, p_ref_b, odds_a, odds_b, orientation = _extract_p_refs_from_odds(
        payload,
        market_type="match_winner",
        game_number=None,
        line_value=None,
        op_fixture=fixture,
    )
    assert orientation["status"] == "game_id_fallback"
    assert orientation.get("source") == "game_1_player_id"
    # Fnatic = team_a, participant1 = 100 = home in game1 -> no swap; match home -> team_a
    assert odds_a == 1.636
    assert odds_b == 2.28
    assert p_ref_a is not None and p_ref_b is not None


def test_orientation_swap_from_game_markets_returns_swap_true_when_game_away_is_p1() -> None:
    """When game has home=p2, away=p1 we get swap_order True."""
    payload = {
        "participant1Id": 100,
        "participant2Id": 200,
        "bookmakerOdds": {
            "pinnacle": {
                "markets": {
                    "g1": {
                        "bookmakerMarketId": "line/1/moneyline",
                        "outcomes": {
                            "h": {
                                "players": {
                                    "0": {
                                        "bookmakerOutcomeId": "game1/home",
                                        "price": 1.9,
                                        "playerId": 200,
                                    }
                                }
                            },
                            "a": {
                                "players": {
                                    "0": {
                                        "bookmakerOutcomeId": "game1/away",
                                        "price": 1.95,
                                        "playerId": 100,
                                    }
                                }
                            },
                        },
                    },
                }
            }
        },
    }
    swap_order, game_used = _orientation_swap_from_game_markets(payload)
    assert swap_order is True
    assert game_used == 1


def test_extract_p_refs_blocks_when_locked_orientation_is_invalid() -> None:
    fixture = _make_fixture("French Flair", "BK Rog eSports")
    payload = _odds_payload(
        participant1_id="p1",
        participant2_id="p2",
        participant1_name="French Flair",
        participant2_name="BK Rog eSports",
        home_player_id="home-x",
        away_player_id="away-y",
        home_price=1.70,
        away_price=2.20,
    )
    p_ref_a, p_ref_b, odds_a, odds_b, orientation = _extract_p_refs_from_odds(
        payload,
        market_type="match_winner",
        game_number=None,
        line_value=None,
        op_fixture=fixture,
        mapping_details={
            "orientation_locked": True,
            "team_a_is_home": None,
            "orientation_anchor_source": "goalserve_pre",
        },
    )
    assert orientation["status"] == "locked_invalid"
    assert p_ref_a is None and p_ref_b is None
    assert odds_a is None and odds_b is None
