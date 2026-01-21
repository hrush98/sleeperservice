# Prediction Market Intelligence Engine (M1)

## Prerequisites

- Docker + Docker Compose
- Internet access for Polymarket API polling

## How to run

```bash
docker compose -f infra/docker-compose.yml up --build
```

This starts:
- Postgres on `localhost:5432`
- API on `http://localhost:8000`
- Worker polling Polymarket on a fixed interval

## Migrations

Migrations run automatically via the `migrate` service on `docker compose up`.

To run manually:

```bash
export DATABASE_URL="postgresql+psycopg2://polymarket:polymarket@localhost:5432/polymarket"
alembic -c alembic.ini upgrade head
```

## API examples

1) Health check

```bash
curl -s http://localhost:8000/health | jq
```

Expected shape:

```json
{"status":"ok"}
```

2) List markets

```bash
curl -s "http://localhost:8000/markets?limit=2&offset=0" | jq
```

Expected shape:

```json
{
  "items": [
    {
      "id": "uuid",
      "platform": "polymarket",
      "platform_market_id": "...",
      "title": "...",
      "status": "open",
      "open_time": "2026-01-18T00:00:00Z",
      "close_time": "2026-02-01T00:00:00Z"
    }
  ],
  "limit": 2,
  "offset": 0
}
```

3) Market detail + quotes

```bash
curl -s "http://localhost:8000/markets/<market_id>" | jq
curl -s "http://localhost:8000/markets/<market_id>/quotes?limit=5" | jq
```

Expected shapes:

```json
{
  "id": "uuid",
  "platform": "polymarket",
  "platform_market_id": "...",
  "title": "...",
  "description": null,
  "url": null,
  "status": "open",
  "open_time": "2026-01-18T00:00:00Z",
  "close_time": "2026-02-01T00:00:00Z",
  "raw_json": {},
  "outcomes": [
    {
      "id": "uuid",
      "outcome_name": "Yes",
      "platform_outcome_id": null,
      "raw_json": {}
    }
  ],
  "latest_settlement_spec": {
    "id": "uuid",
    "source": null,
    "resolution_time": null,
    "criteria_text": "...",
    "spec_version_hash": "...",
    "raw_json": {},
    "created_at": "2026-01-18T00:00:00Z"
  }
}
```

```json
{
  "items": [
    {
      "id": "uuid",
      "outcome_id": "uuid",
      "ts": "2026-01-18T00:00:00Z",
      "price": 0.52,
      "volume_24h": null,
      "liquidity": null,
      "raw_json": {}
    }
  ],
  "limit": 5
}
```

## Troubleshooting

- Missing env vars: `DATABASE_URL` is required for local Alembic commands.
- Database not reachable: ensure Postgres is healthy (`docker compose ps`) and port `5432` is not in use.
- Polymarket API errors: the worker logs `HTTPStatusError` and retries on next interval; verify `POLYMARKET_BASE_URL`.
