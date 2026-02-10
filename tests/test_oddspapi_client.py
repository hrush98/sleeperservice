from datetime import datetime

from services.shared.oddspapi_client import OddsPapiClient
from services.cli.discover import _parse_datetime


def test_parse_datetime_handles_zulu() -> None:
    parsed = _parse_datetime("2024-01-01T12:00:00Z")
    assert parsed == datetime(2024, 1, 1, 12, 0, 0, tzinfo=parsed.tzinfo)


def test_parse_datetime_rejects_invalid() -> None:
    assert _parse_datetime("not-a-date") is None
    assert _parse_datetime(123) is None


def test_auth_params() -> None:
    client = OddsPapiClient(api_key="key")
    assert client._auth_params() == {"apiKey": "key"}
