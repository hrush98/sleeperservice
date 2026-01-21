# ADR 0001: Worker/API split and polling-first ingestion

## Status

Accepted

## Context

We need a first vertical slice that ingests Polymarket data, stores it in Postgres, and exposes a read-only API. The system must be restart-safe, simple to run locally, and preserve raw payloads for later schema evolution.

## Decision

- Split responsibilities into two services:
  - **Worker**: ingestion + periodic jobs, writes to Postgres.
  - **API**: read-only FastAPI, reads from Postgres.
- Use **polling** against Polymarket's public markets endpoint as the initial ingestion strategy.
- Store raw payloads in `raw_json` columns alongside canonical fields.

## Rationale

- **Worker/API split** keeps ingestion concerns (retries, idempotency, polling) isolated from query serving, reducing operational coupling and making scaling simpler.
- **Polling first** avoids extra infrastructure while providing predictable, debuggable data capture. Real-time streaming can be added later without changing the canonical schema.
- **Postgres + raw_json** supports structured queries while keeping the original payload for reprocessing when schema evolves.

## Consequences

- Two services must run in local orchestration (Docker Compose).
- Ingestion latency is bounded by polling interval.
- Raw payload storage increases DB size but preserves data fidelity.
