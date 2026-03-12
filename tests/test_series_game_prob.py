import pytest

from services.shared.edge import series_prob_to_game_prob


def _bo3_series_prob(game_prob: float) -> float:
    return (3.0 * game_prob * game_prob) - (2.0 * game_prob * game_prob * game_prob)


def _bo5_series_prob(game_prob: float) -> float:
    return (10.0 * game_prob**3) - (15.0 * game_prob**4) + (6.0 * game_prob**5)


def test_series_prob_to_game_prob_known_bo3_points() -> None:
    assert series_prob_to_game_prob(0.50, "bo3") == pytest.approx(0.50, abs=1e-6)
    assert series_prob_to_game_prob(0.70, "bo3") == pytest.approx(0.636743, abs=1e-5)
    assert series_prob_to_game_prob(0.90, "bo3") == pytest.approx(0.804200, abs=1e-5)


def test_series_prob_to_game_prob_known_bo5_points() -> None:
    assert series_prob_to_game_prob(0.50, "bo5") == pytest.approx(0.50, abs=1e-6)
    assert series_prob_to_game_prob(0.70, "bo5") == pytest.approx(0.610182, abs=1e-5)
    assert series_prob_to_game_prob(0.90, "bo5") == pytest.approx(0.753364, abs=1e-5)


def test_series_prob_to_game_prob_handles_bo1_and_unknown() -> None:
    assert series_prob_to_game_prob(0.65, "bo1") is None
    assert series_prob_to_game_prob(0.65, "bo7") is None
    assert series_prob_to_game_prob(0.65, None) is None


def test_series_prob_to_game_prob_roundtrip_bo3() -> None:
    for target in (0.1, 0.25, 0.5, 0.75, 0.9):
        game_prob = series_prob_to_game_prob(target, "bo3")
        assert game_prob is not None
        assert _bo3_series_prob(game_prob) == pytest.approx(target, abs=1e-6)


def test_series_prob_to_game_prob_roundtrip_bo5() -> None:
    for target in (0.1, 0.25, 0.5, 0.75, 0.9):
        game_prob = series_prob_to_game_prob(target, "bo5")
        assert game_prob is not None
        assert _bo5_series_prob(game_prob) == pytest.approx(target, abs=1e-6)


def test_series_prob_to_game_prob_bounds() -> None:
    assert series_prob_to_game_prob(0.0, "bo3") == pytest.approx(0.0, abs=1e-6)
    assert series_prob_to_game_prob(1.0, "bo3") == pytest.approx(1.0, abs=1e-6)
    assert series_prob_to_game_prob(0.0, "bo5") == pytest.approx(0.0, abs=1e-6)
    assert series_prob_to_game_prob(1.0, "bo5") == pytest.approx(1.0, abs=1e-6)
