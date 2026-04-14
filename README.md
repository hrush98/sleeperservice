# SleeperService

Polymarket trading and analysis platform for esports-focused strategies.

The repo is in the middle of the platform restructure documented in:

- `docs/platform/implementation-roadmap.md`
- `docs/platform/target-architecture.md`
- `docs/platform/engineering-improvements.md`

Phase 0 is focused on making the repo repeatable to install, run, and test.
The current active implementation phase is `Phase 0.5 - historical research foundation`.

## Current runtimes

- `services.cli`: operational CLI for discovery, live monitoring, and strategy tools
- `services.api`: FastAPI service for health, ops, and the first read-only `v0` analysis endpoints
- `services.coherence`: market-family scanner for coherence analysis
- `services.tools`: one-off utilities, including Phase 0.5 historical-research entrypoints

## Quick start

### 1. Create a local env file

```bash
cp .env.example .env
```

Fill in the keys you actually need:

- `DATABASE_URL` is required
- `ODDS_API_KEY` is required for OddsPapi-backed discovery and live odds
- `GOALSERVE_FEED_KEY` is required for Goalserve-backed features
- `POLY_API_KEY` and the Polymarket signing settings are only needed for authenticated Polymarket operations

### 2. Install the project

```bash
python -m pip install -e .
```

### 3. Start Postgres

```bash
docker compose -f infra/docker-compose.yml up -d postgres
```

### 4. Apply migrations

```bash
alembic -c alembic.ini upgrade head
```

## Canonical commands

### CLI

```bash
python -m services.cli discover --days 7
python -m services.cli live
python -m services.cli status
```

After editable install, the console script is also available:

```bash
sleeperservice discover --days 7
```

### API

```bash
uvicorn services.api.main:app --host 0.0.0.0 --port 8000
```

Current read-only analysis routes:

```bash
curl http://127.0.0.1:8000/health
curl -H "Authorization: Bearer change-me" http://127.0.0.1:8000/v0/analysis/opportunities
curl -H "Authorization: Bearer change-me" "http://127.0.0.1:8000/v0/analysis/opportunities?categories=politics&include_trace=true"
curl -H "Authorization: Bearer change-me" "http://127.0.0.1:8000/v0/markets/<market_id>/analysis?include_trace=true"
```

Current `v0` behavior:

- ranked opportunities are driven by promoted historical priors over active Polymarket markets
- single-market analysis combines one live market snapshot with promoted historical contexts
- the first slice is read-only and does not expose execution or signal-history endpoints

Current public-beta plumbing:

- `API_AUTH_MODE=api_key` enables bearer API-key auth for `/v0/*`
- `API_KEY_RECORDS` configures comma-separated `key_id:secret[:tier]` credentials
- `API_DEFAULT_RATE_LIMIT_PER_MINUTE` applies single-instance in-memory throttling per caller
- `API_MAINTENANCE_MODE=true` blocks `/v0/*` while keeping `/health` available
- responses include `X-Request-ID`
- errors use a deterministic envelope:

```json
{
  "error": {
    "code": "rate_limited",
    "message": "Rate limit exceeded for this caller. Retry after the indicated cooldown.",
    "retryable": true,
    "details": {
      "retry_after_seconds": 30
    }
  },
  "request_id": "3d5d7d30780d4fa5a1ef4b1f3c4f6ce0"
}
```

### Coherence scanner

```bash
python -m services.coherence scan --cache-only
```

### Historical research

```bash
python -m services.tools.profile_historical_dataset --help
HISTORICAL_DATASET_ROOT=/absolute/path/to/prediction-markets-data python -m services.tools.profile_historical_dataset
python -m services.tools.materialize_historical_research --help
HISTORICAL_DATASET_ROOT=/absolute/path/to/prediction-markets-data python -m services.tools.materialize_historical_research
HISTORICAL_DATASET_ROOT=/absolute/path/to/prediction-markets-data python -m services.tools.materialize_historical_research --skip-view-row-counts
HISTORICAL_DATASET_ROOT=/absolute/path/to/prediction-markets-data python -m services.tools.materialize_historical_research --skip-view-row-counts --skip-bucket-stats
python -m services.tools.run_historical_studies --help
python -m services.tools.run_historical_studies
python -m services.tools.run_historical_studies --study calibration --venue polymarket
python -m services.tools.run_historical_studies --study longshot_favorite_bias --study sizing_priors --venue polymarket
```

## Testing

Run the full suite from the repo root:

```bash
python -m pytest
```

If your local Python environment injects unrelated global pytest plugins, use:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest
```

## Environment notes

The tracked template is `.env.example`. Local secrets should stay in ignored env files such as:

- `.env`
- `.env.local`
- `.env.dev`

Default secret-path settings now point at:

- `~/.sleeperservice/keys/polymarket.key.age`
- `~/.sleeperservice/keys/STOP_TRADING`

Phase 0.5 historical-research tooling uses:

- `HISTORICAL_DATASET_ROOT` for the external parquet dataset root
- `HISTORICAL_RESEARCH_OUTPUT_ROOT` for generated manifests and summaries
- `duckdb` in the `sleeperservice` environment for normalized research materialization into persisted DuckDB tables
- `--skip-view-row-counts` on the materializer when the target dataset is large enough that final view counts are not practical to compute during the build
- `--skip-bucket-stats` on the materializer when a study-ready build is enough and `historical_bucket_stats` is not needed
- optional bucket sync for promoted artifacts at API startup:
  - `ARTIFACT_BUCKET_ENABLED=true`
  - `ARTIFACT_BUCKET_NAME` and optional `ARTIFACT_BUCKET_PREFIX`
  - `ARTIFACT_BUCKET_ENDPOINT_URL` for S3-compatible providers such as Railway Buckets
  - `ARTIFACT_BUCKET_ACCESS_KEY_ID` and `ARTIFACT_BUCKET_SECRET_ACCESS_KEY`
  - `ARTIFACT_BUCKET_REGION` when your provider requires an explicit region

For Railway Buckets, the intended deploy shape is:

- deploy the API from GitHub
- upload only promoted study outputs such as `studies/<run_id>/...` to the bucket
- set `HISTORICAL_RESEARCH_OUTPUT_ROOT` to a writable local path in the container
- enable the bucket sync env vars so the API downloads the promoted bundle during startup before serving requests

Public beta API runtime also uses:

- `API_PUBLIC_BETA_ENABLED` to expose or disable the public beta surface
- `API_MAINTENANCE_MODE` and `API_MAINTENANCE_MESSAGE` for manual maintenance mode
- `API_AUTH_MODE` with `disabled` for local-only smoke use or `api_key` for public beta
- `API_KEY_RECORDS` for bearer API keys in `key_id:secret[:tier]` format
- `API_DEFAULT_RATE_LIMIT_PER_MINUTE` for per-caller single-instance rate limiting
- `API_REQUEST_LOGGING_ENABLED` for structured request logging with request IDs

## Docker

Compose currently provides:

- `postgres`
- `migrate`
- `api`

The stale worker service was removed during Phase 0 cleanup.

## Status

The codebase still contains MVP-era modules and docs, but the package/import model is now standardized on `services.*`. Continue Phase 0.5 work from `docs/platform/implementation-roadmap.md`.
