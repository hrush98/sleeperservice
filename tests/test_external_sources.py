from services.shared.oddspapi_client import OddsPapiClient
from services.shared.polymarket_client import PolymarketClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, path, params=None, headers=None):
        self.calls.append({"path": path, "params": params, "headers": headers})
        if not self._responses:
            return FakeResponse([])
        return self._responses.pop(0)


def test_get_odds_by_tournaments_uses_api_key(monkeypatch):
    responses = [FakeResponse([{"fixtureId": "1"}])]
    client = FakeClient(responses)
    monkeypatch.setattr("services.shared.oddspapi_client.httpx.Client", lambda **_: client)

    oddspapi = OddsPapiClient(api_key="k", base_url="https://x")
    oddspapi._cooldown.wait = lambda *_: None  # avoid sleeping in tests
    payload = oddspapi.get_odds_by_tournaments([17])

    assert payload == [{"fixtureId": "1"}]
    assert client.calls == [
        {
            "path": "https://x/v4/odds-by-tournaments",
            "params": {
                "tournamentIds": "17",
                "bookmaker": "pinnacle",
                "oddsFormat": "decimal",
                "verbosity": 3,
                "apiKey": "k",
            },
            "headers": None,
        }
    ]


def test_polymarket_get_markets_paginates(monkeypatch):
    client = PolymarketClient()
    calls = []

    def fake_gamma(endpoint, params):
        calls.append((endpoint, dict(params)))
        if params.get("offset") == 0:
            return [{"id": "m1"}, {"id": "m2"}]
        return [{"id": "m3"}]

    monkeypatch.setattr(client, "_gamma_request", fake_gamma)

    markets = client.get_markets(limit=2)

    assert markets == [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]
    assert calls == [
        ("/markets", {"limit": 2, "active": "true", "offset": 0}),
        ("/markets", {"limit": 2, "active": "true", "offset": 2}),
    ]
