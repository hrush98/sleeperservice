from services.cli.discover import is_target_league, normalize_name, similarity


def test_normalize_name_strips_suffixes() -> None:
    assert normalize_name("Gen.G Esports") == "gen g"
    assert normalize_name("T1!") == "t1"


def test_is_target_league_matches_patterns() -> None:
    assert is_target_league("LCK Spring") is True
    assert is_target_league("LaLiga") is False


def test_similarity_increases_for_close_names() -> None:
    assert similarity("Team Heretics", "Team Heretics") > 0.95
    assert similarity("Team Heretics", "G2 Esports") < 0.5
