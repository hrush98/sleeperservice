"""Tests for thesis-death and hard-stop exit guards."""

# pylint: disable=import-error
from shared.edge import check_hard_stop, check_thesis_death


# ── thesis death ──────────────────────────────────────────────

def test_thesis_death_fires_when_p_ref_below_entry() -> None:
    assert check_thesis_death(p_ref=0.25, entry_price=0.27) is True


def test_thesis_death_does_not_fire_when_p_ref_above_entry() -> None:
    assert check_thesis_death(p_ref=0.30, entry_price=0.27) is False


def test_thesis_death_does_not_fire_when_p_ref_equals_entry() -> None:
    assert check_thesis_death(p_ref=0.27, entry_price=0.27) is False


def test_thesis_death_safe_on_none_p_ref() -> None:
    assert check_thesis_death(p_ref=None, entry_price=0.27) is False


# ── hard stop ─────────────────────────────────────────────────

def test_hard_stop_fires_at_exact_threshold() -> None:
    # entry 0.27, 20% stop → threshold = 0.216
    assert check_hard_stop(bid=0.216, entry_price=0.27, stop_pct=0.20) is True


def test_hard_stop_fires_below_threshold() -> None:
    assert check_hard_stop(bid=0.16, entry_price=0.27, stop_pct=0.20) is True


def test_hard_stop_does_not_fire_above_threshold() -> None:
    assert check_hard_stop(bid=0.25, entry_price=0.27, stop_pct=0.20) is False


def test_hard_stop_safe_on_none_bid() -> None:
    assert check_hard_stop(bid=None, entry_price=0.27, stop_pct=0.20) is False


# ── priority: thesis death should fire before hard stop ───────

def test_thesis_death_fires_even_when_hard_stop_also_true() -> None:
    """Both conditions met; thesis death is stricter and should be checked first."""
    entry = 0.27
    p_ref = 0.15   # below entry → thesis death
    bid = 0.15     # below 0.216 → hard stop
    assert check_thesis_death(p_ref, entry) is True
    assert check_hard_stop(bid, entry, 0.20) is True
    # In trader.py the elif chain evaluates thesis_death first.
