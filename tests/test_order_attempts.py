from datetime import datetime, timedelta, timezone

# pylint: disable=import-error
from cli.trader import _is_order_not_found_timeout
from shared.config import settings


def test_order_not_found_timeout() -> None:
    now = datetime.now(tz=timezone.utc)
    submitted_recent = now - timedelta(seconds=5)
    submitted_old = now - timedelta(seconds=15)

    assert not _is_order_not_found_timeout(submitted_recent, now, 10.0)
    assert _is_order_not_found_timeout(submitted_old, now, 10.0)


def test_exit_order_timeout_setting() -> None:
    now = datetime.now(tz=timezone.utc)
    submitted_old = now - timedelta(seconds=settings.exit_order_not_found_seconds + 1)
    assert _is_order_not_found_timeout(
        submitted_old,
        now,
        settings.exit_order_not_found_seconds,
    )
