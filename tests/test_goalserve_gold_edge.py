from cli.gold_edge import _duration_to_seconds, _evaluate_entry_decision, _parse_rules
from shared.goalserve_client import parse_game_stats


def test_parse_game_stats_extracts_gold_diff() -> None:
    match = {
        "games": {
            "game": {
                "@no": "1",
                "@duration": "00:30:00",
                "@started_at": "03:00:00",
                "stats": {
                    "localteam": {
                        "@gold_earned": "65000",
                        "@kills": "20",
                        "@tower_kills": "8",
                        "@dragon_kills": "3",
                        "@nashor_kills": "1",
                        "@inhibitor_kills": "1",
                    },
                    "awayteam": {
                        "@gold_earned": "59000",
                        "@kills": "12",
                        "@tower_kills": "3",
                        "@dragon_kills": "1",
                        "@nashor_kills": "0",
                        "@inhibitor_kills": "0",
                    },
                },
            }
        }
    }
    rows = parse_game_stats(match)
    assert len(rows) == 1
    assert rows[0]["gold_diff"] == 6000.0
    assert rows[0]["barons_diff"] == 1.0


def test_duration_to_seconds_parses_hh_mm_ss() -> None:
    assert _duration_to_seconds("00:31:05") == 1865
    assert _duration_to_seconds("31:05") == 1865
    assert _duration_to_seconds(None) is None


def test_parse_rules_uses_config_format(monkeypatch) -> None:
    monkeypatch.setattr(
        "cli.gold_edge.settings.gold_edge_rules",
        "12,4000,0.70,0;18,5000,0.88,1",
    )
    rules = _parse_rules()
    assert len(rules) == 2
    assert rules[0].minute_min == 12
    assert rules[1].requires_baron is True


def test_entry_decision_requires_edge_and_rule() -> None:
    game_row = {
        "duration": "00:20:00",
        "gold_diff": 8000.0,
        "localteam_barons": 1.0,
        "awayteam_barons": 0.0,
    }
    rules = _parse_rules()
    books = {"a": {"best_ask": 0.7, "best_bid": 0.69}}
    decision = _evaluate_entry_decision(game_row, books, rules)
    assert decision is not None
    assert decision["picked_side"] == "A"
