# LoL Lead-Lag Arbitrage Bot (v2)

Detect and exploit short-lived lead-lag inefficiencies between Pinnacle odds (via OddsPapi) and Polymarket LoL CLOB markets.

## Overview

This bot compares prices between:
- **Pinnacle** (via OddsPapi) — fast-moving sharp bookmaker
- **Polymarket** — prediction market CLOB (slower to reprice)

When Pinnacle moves, there's a brief window where Polymarket is stale. We detect that window, measure it, and record shadow orders for later analysis.

## Architecture

Two-mode CLI system:
1. **Discovery** (`cli discover`) — on-demand collection of upcoming matches
2. **Live** (`cli live`) — real-time odds comparison during live matches

Target leagues: LCK, LPL, LEC, LCS/LTA, LCP, CBLOL (top 6 LoL leagues)

## Prerequisites

- Docker + Docker Compose (for Postgres)
- Python 3.11+ with conda environment `poly`
- API keys: `ODDS_API_KEY` (OddsPapi), `POLY_API_KEY` (Polymarket, optional)

## Setup

### 1. Start Postgres

```bash
docker compose -f infra/docker-compose.yml up -d postgres
```

### 2. Activate conda environment

```bash
conda activate poly
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set environment variables

Create a `.env` file in the project root (copy from `.env.example`):

```bash
cp .env.example .env
# Then edit .env and add your API keys
```

The `.env` file will be automatically loaded when you run CLI commands. No need to manually export variables!

### 5. Run migrations

```bash
cd services
alembic -c ../alembic.ini upgrade head
```

## Usage

### Discovery — collect upcoming matches

```bash
cd services
python -m cli discover --days 7
```

Expected output:
```
🔍 Discovering LoL matches for next 7 days...

📋 Fetching OddsPapi tournaments...
   Found 5 target leagues from 42 total

👥 Fetching OddsPapi participants...
   Found 234 teams

🎮 Fetching OddsPapi fixtures...
   LCK: 8 fixtures
   LPL: 12 fixtures
   ...
   Total: 28 OddsPapi fixtures

🔮 Fetching Polymarket markets...
   Found 15 LoL-related markets

🔗 Building mappings...

==================================================
📊 Discovery Summary
==================================================
Leagues:      OddsPapi=5
Teams:        OddsPapi=234
Fixtures:     OddsPapi=28, Polymarket=15
Mappings:     12 created (8 high confidence)

✅ Discovery complete!
```

Options:
- `--days N` — look ahead N days (default: 7)
- `--dry-run` — don't write to database
- `--verbose` / `-v` — enable debug logging

### Live — watch live matches

```bash
cd services
python -m cli live
```
### Live TUI — stationary live view (read-only)

```bash
cd services
python -m cli live --interval 5 --edge-threshold 0.03
```

Notes:
- Uses `clobTokenIds` from Gamma as the CLOB `token_id` (cross-compatible).
- Requires `DATABASE_URL` and `ODDS_API_KEY` to show sharps vs Poly odds.
- Shows **market type** (match winner vs game winner) when available.

Options:
- `--interval N` — poll interval in seconds (default: 5)
- `--edge-threshold N` — net edge threshold for alerts (default: 0.03)
- `--spread-factor N` — spread penalty multiplier (default: 1.0)
- `--min-confidence N` — minimum mapping confidence (default: 0.7)
- `--lookahead-minutes N` — how far ahead to show matches (default: 30)
- `--verbose` / `-v` — enable debug logging

### Status — check system state

```bash
cd services
python -m cli status
```

### API — operational endpoints

Start the API:
```bash
cd services
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Endpoints:
```bash
# Health check
curl http://localhost:8000/health

# System status
curl http://localhost:8000/ops/status

# Live matches + gaps
curl http://localhost:8000/ops/live
```

## Database Schema (6 tables)

| Table | Purpose |
|-------|---------|
| `leagues` | League/tournament metadata |
| `teams` | Team/participant cache |
| `fixtures` | Upcoming/live matches |
| `mappings` | OddsPapi ↔ Polymarket links |
| `odds_snapshots` | Live price time series |
| `shadow_orders` | Paper trade decisions |

## Configuration

Environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | Postgres connection string | required |
| `ODDS_API_KEY` | OddsPapi API key | optional |
| `POLY_API_KEY` | Polymarket API key | optional |
| `TARGET_LEAGUES` | Comma-separated league names | `LCK,LPL,LEC,LCS,LTA,LCP,CBLOL` |
| `SHADOW_GAP_THRESHOLD` | Gap threshold for shadow orders | `0.05` |
| `MONITOR_POLL_INTERVAL_SECONDS` | Monitor poll interval | `5` |

## Troubleshooting

### "No live matches found"
Run discovery first: `python -m cli discover --days 7`

### "No Pinnacle odds available"
The match may not have active odds yet. Wait for closer to match start.

### Database connection errors
Ensure Postgres is running: `docker compose -f infra/docker-compose.yml ps`

### OddsPapi rate limits
The client respects cooldowns (500ms-2000ms per endpoint). If you hit limits, increase cooldown settings.

## Development

### Run tests
```bash
cd services
pytest tests/
```

### Run with Docker Compose (full stack)
```bash
docker compose -f infra/docker-compose.yml up --build
```

## What's NOT included (MVP scope)

- No UI
- No actual order execution (shadow orders only)
- No broad market discovery (only LoL top 6 leagues)
- No continuous background polling
