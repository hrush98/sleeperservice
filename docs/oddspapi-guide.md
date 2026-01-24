# OddsPapi v4 — Core Endpoints Reference

This doc summarizes the OddsPapi endpoints we’re using for **sport → tournament → fixtures → odds** discovery, plus supporting lookups (bookmakers, participants).

---

## Base

- Base path: `/v4`
- All responses are JSON
- Many endpoints have a **cooldown** (minimum delay between calls). Respect these with client-side throttling + jittered backoff.

Status codes:
- `200 OK` success (may return empty arrays/objects)

---

## 1) GET /v4/sports

Retrieve the list of sports available.

**Endpoint**
- `GET /v4/sports`

**Query params**
- `language` (string, optional): language for `sportName`

**Example**
- `GET /v4/sports?language=en`

**Response**
Array of:
- `sportId` (number): use this everywhere else
- `slug` (string)
- `sportName` (string)

**Notes**
- May return `[]` if none available

---

## 2) GET /v4/bookmakers

List bookmakers available in the system.

**Endpoint**
- `GET /v4/bookmakers`

**Response**
Array of:
- `bookmakerName` (string)
- `slug` (string): use this in `odds.bookmakers=...`
- `liveOdds` (boolean | null): may be null
- `cloneOf` (string | null)

**Cooldown**
- 1000ms

**Notes**
- Handle `liveOdds: null` as “unknown/unavailable”

---

## 3) GET /v4/tournaments

List tournaments for a given sport.

**Endpoint**
- `GET /v4/tournaments`

**Query params**
- `sportId` (number, required)
- `language` (a2, optional, default `en`)

**Example**
- `GET /v4/tournaments?sportId=10&language=en`

**Response**
Array of:
- `tournamentId` (number)
- `tournamentSlug` (string)
- `tournamentName` (string)
- `categorySlug` (string)
- `categoryName` (string)
- `futureFixtures` (number): >24h from now
- `upcomingFixtures` (number): <24h from now
- `liveFixtures` (number): in-play

**Cooldown**
- 1000ms

**Notes**
- May return `[]` if no tournaments for the sportId

---

## 4) GET /v4/fixtures

Retrieve fixtures filtered by tournament, sport, participant, time window, and other flags.

**Endpoint**
- `GET /v4/fixtures`

### Query params
- `tournamentId` (number, optional)
- `sportId` (number, optional)
- `participantId` (number, optional)
- `from` (string, optional): ISO 8601 (e.g., `YYYY-MM-DDTHH:MM:SSZ`)
- `to` (string, optional): ISO 8601
- `language` (a2, optional, default `en`)
- `statusId` (number, optional): `0` Not started, `1` Live, `2` Finished, `3` Cancelled
- `hasOdds` (boolean, optional): only fixtures with odds

### Parameter rules / constraints (important)
Allowed “solo” filters:
- `tournamentId` can be the only parameter
- `participantId` can be the only parameter

`sportId` requires at least one of:
- `tournamentId`, OR
- `participantId`, OR
- both `from` and `to` (and **range < 10 days**)

Time window rules:
- If **only** `from` and `to` are provided, they must be **< 48 hours** apart.
- If `tournamentId` or `participantId` provided, `from/to` are optional and it’s acceptable to provide only one.

**Example**
- `GET /v4/fixtures?sportId=10&from=2025-07-14T00:00:00Z&to=2025-07-21T00:00:00Z&hasOdds=true`

**Response**
Array of fixture objects. Common fields:
- `fixtureId` (string)
- `sportId` (number)
- `tournamentId` (number)
- `seasonId` (number, optional)
- `startTime` (string, ISO)
- `statusId` (number)
- `hasOdds` (boolean)
- `updatedAt` (string, ISO)
- `trueStartTime` (string | null)
- `trueEndTime` (string | null)
- `participant1Id` (number)
- `participant2Id` (number)
- `participant1Name` (string)
- `participant2Name` (string)
- `tournamentName` (string)
- `categoryName` (string)
- `externalProviders` (object): mapping to other vendor IDs (may be nulls)

**Cooldown**
- 2000ms

**Notes**
- We should treat `fixtureId` as the primary join key into `/odds`.
- `externalProviders.pinnacleId` is useful for mapping to Pinnacle’s internal fixture id (when present).

---

## 5) GET /v4/participants

List participants (teams/players) for a sport.

**Endpoint**
- `GET /v4/participants`

**Query params**
- `sportId` (number, required)
- `language` (a2, optional): if unavailable falls back to `en`

**Example**
- `GET /v4/participants?sportId=11&language=en`

**Response**
A JSON object map:
- keys: `participantId` (as string keys in JSON)
- values: participant name (string)

Example:
```json
{
  "3409": "Chicago Bulls",
  "3410": "Milwaukee Bucks"
}

### Cooldown
- **1000ms**

### Notes
- Response is an object (dictionary), not an array.
- Store as `participantId -> name` cache per sport.

---

## 6) GET /v4/odds

Retrieve detailed odds + metadata for a given fixture from one or more bookmakers.

### Endpoint
- `GET /v4/odds`

### Query params
- `fixtureId` (string, required)
- `bookmakers` (string, optional): comma-separated bookmaker slugs (e.g., `pinnacle,bet365`)
- `oddsFormat` (string, optional): `fractional | decimal | american`
- `language` (string, optional)
- `verbosity` (number, optional): higher => more verbose response

### Example
- `GET /v4/odds?fixtureId=id1000001761300517&oddsFormat=decimal&verbosity=3`

### Cooldown
- **500ms**

### Response overview

**Top-level metadata (common):**
- `fixtureId` (string)
- `participant1Id` (number), `participant2Id` (number)
- `sportId` (number), `tournamentId` (number), `seasonId` (number | null)
- `statusId` (number)
- `hasOdds` (boolean)
- `startTime` (ISO string)
- `trueStartTime` / `trueEndTime` (ISO | null)
- `updatedAt` (ISO string)

**names + slugs:**
- `participant1Name`, `participant2Name`
- `sportName`
- `tournamentSlug`, `categorySlug`, `categoryName`, `tournamentName`

**external providers:**
- `externalProviders` (object; IDs; many nullable)

### `bookmakerOdds` structure (key piece)

`bookmakerOdds` is an object keyed by bookmaker slug, e.g.:
- `bookmakerOdds.pinnacle`

**Within each bookmaker:**
- `bookmakerIsActive` (boolean)
- `bookmakerFixtureId` (string/number-like)
- `fixturePath` (string URL)
- `markets` (object keyed by market id string, e.g. `"101"`, `"1010"`)

**Market object:**
- `bookmakerMarketId` (string): bookmaker-specific descriptor
- `outcomes` (object keyed by outcome id strings)

**Outcome object:**
- `players` (object keyed by player id string)

Each player entry commonly includes:
- `price` (number): odds in requested format
- `limit` (number)
- `active` (boolean)
- `changedAt` (ISO string)
- `playerId` (number)
- `bookmakerOutcomeId` (string, e.g. `home`, `draw`, `away`, or `2.5/over`)

### Notes / gotchas
- IDs (market/outcome/player) are **strings** in JSON; don’t assume ints.
- Many nested fields can be missing depending on bookmaker + verbosity.
- For price-history / change detection, `changedAt` is the key timestamp per outcome.

---

## Recommended call flow (common usage)

1. `/sports`  
   Pick `sportId` for the domain we’re monitoring.

2. `/bookmakers`  
   Identify target sharp books (e.g., Pinnacle slug `pinnacle`) and confirm if live odds exist.

3. `/tournaments?sportId=...`  
   Filter to relevant `tournamentId`s (league coverage).

4. `/fixtures?...&hasOdds=true`  
   Pull fixture IDs in a time window (or by `tournamentId`). Cache fixture metadata and external mappings.

5. `/odds?fixtureId=...&bookmakers=...&oddsFormat=decimal&verbosity=...`  
   Poll for changes (or snapshot on schedule) for lead/lag signals and/or price movement detection.

---

## Cooldowns summary
- `/bookmakers` — **1000ms**
- `/tournaments` — **1000ms**
- `/participants` — **1000ms**
- `/fixtures` — **2000ms**
- `/odds` — **500ms**

### Implementation notes
- Use a per-endpoint token-bucket or simple timestamp gate.
- Add jitter (e.g., ±100ms) to reduce thundering-herd effects.
- If we parallelize across fixtures, still respect overall QPS and cooldown per endpoint.
