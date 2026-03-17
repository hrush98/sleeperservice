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
- `services.api`: FastAPI service for health and ops endpoints
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
python -m services.tools.run_historical_studies --help
python -m services.tools.run_historical_studies
python -m services.tools.run_historical_studies --study calibration --venue polymarket
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
- `duckdb` in the `sleeperservice` environment for normalized research materialization
- `--skip-view-row-counts` on the materializer when the target dataset is large enough that final view counts are not practical to compute during the build

## Docker

Compose currently provides:

- `postgres`
- `migrate`
- `api`

The stale worker service was removed during Phase 0 cleanup.

## Status

The codebase still contains MVP-era modules and docs, but the package/import model is now standardized on `services.*`. Continue Phase 0.5 work from `docs/platform/implementation-roadmap.md`.
