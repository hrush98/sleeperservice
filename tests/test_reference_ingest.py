from services.shared.edge import (
    compute_alpha_entry,
    compute_avg_fill_price,
    compute_entry_edge,
    compute_exit_signal,
)


def test_compute_alpha_entry() -> None:
    assert compute_alpha_entry(0.01) == 0.03
    assert compute_alpha_entry(0.02) == 0.04


def test_compute_avg_fill_price() -> None:
    asks = [(0.5, 100), (0.51, 50)]
    assert compute_avg_fill_price(asks, 50) == 0.5
    assert compute_avg_fill_price(asks, 120) == (0.5 * 100 + 0.51 * 20) / 120


def test_compute_entry_edge() -> None:
    asks = [(0.5, 100), (0.52, 50)]
    result = compute_entry_edge(0.6, asks, 80, 0.03)
    assert result["actionable"] is True
    assert result["limit_price"] == 0.57


def test_compute_exit_signal() -> None:
    assert compute_exit_signal(0.6, 0.591, epsilon=0.01) is True
    assert compute_exit_signal(0.6, 0.58, epsilon=0.01) is False
