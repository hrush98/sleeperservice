from datetime import datetime, timedelta, timezone

from services.shared.fixture_state import FixtureStateManager, TriggerConfig


def test_fixture_state_triggers_primary_after_flicker():
    manager = FixtureStateManager()
    now = datetime(2026, 1, 22, 0, 0, 0, tzinfo=timezone.utc)
    fixture_id = "fx-1"

    manager.update_p_ref(fixture_id, 0.5, 0.5, now=now)
    trigger = manager.update_p_ref(
        fixture_id, 0.53, 0.47, now=now + timedelta(seconds=1)
    )
    assert trigger is None
    trigger = manager.update_p_ref(
        fixture_id, 0.6, 0.4, now=now + timedelta(seconds=2)
    )
    assert trigger is not None
    assert trigger.trigger_type == "burst"


def test_fixture_state_burst_triggers_hot():
    manager = FixtureStateManager()
    now = datetime(2026, 1, 22, 0, 0, 0, tzinfo=timezone.utc)
    fixture_id = "fx-2"

    manager.update_p_ref(fixture_id, 0.5, 0.5, now=now)
    trigger = manager.update_p_ref(
        fixture_id,
        0.5 + TriggerConfig.BURST_THRESHOLD,
        0.5 - TriggerConfig.BURST_THRESHOLD,
        now=now + timedelta(seconds=1),
    )
    assert trigger is not None
    assert trigger.trigger_type == "burst"
    assert manager.is_hot(fixture_id, now=now + timedelta(seconds=2)) is True
