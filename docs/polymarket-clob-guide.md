# Polymarket CLOB API Guide (for Cursor docs)

This guide is the “live odds / orderbook” half of our Polymarket integration.

**Architecture (what we’re mirroring vs OddsPapi):**
- **Gamma API** = discovery + mappings (market metadata, IDs, token ids)
- **CLOB API** = live prices / orderbook + (optionally) order placement + order management

> **Key link:** Gamma `GET /markets` includes `conditionId` (market identifier) and also `clobTokenIds` (token IDs we need for CLOB). :contentReference[oaicite:0]{index=0}

---

## Core identifiers (how Gamma ↔ CLOB connect)

### `conditionId` (Gamma) ↔ `market` (CLOB)
In CLOB messages/docs, `market` is the **condition ID**. :contentReference[oaicite:1]{index=1}

### `clobTokenIds` (Gamma) ↔ `token_id` / `asset_id` (CLOB)
Gamma market payloads include `clobTokenIds`. :contentReference[oaicite:2]{index=2}  
CLOB uses `token_id` as the query param for orderbook/pricing endpoints, and orderbook responses include `asset_id`. :contentReference[oaicite:3]{index=3}

**Practical mapping table we store:**

| Concept | Gamma field | CLOB field |
|---|---|---|
| Market identifier | `conditionId` | `market` |
| Outcome token id(s) | `clobTokenIds` | `token_id` (query) / `asset_id` (responses) |

---

## Base URLs

- **CLOB REST API base:** `https://clob.polymarket.com` :contentReference[oaicite:4]{index=4}
- **Gamma REST API base:** `https://gamma-api.polymarket.com` :contentReference[oaicite:5]{index=5}

---

## Public market data (no auth required)

### 1) Orderbook

#### GET `/book` — Get order book summary (single token)
- **Endpoint:** `GET https://clob.polymarket.com/book` :contentReference[oaicite:6]{index=6}
- **Query params:**
  - `token_id` (string, required) :contentReference[oaicite:7]{index=7}
- **Response (high level):**
  - `market` (string) — market identifier :contentReference[oaicite:8]{index=8}
  - `asset_id` (string) — token id :contentReference[oaicite:9]{index=9}
  - `bids[]`, `asks[]` with `{ price: string, size: string }` :contentReference[oaicite:10]{index=10}
  - `min_order_size`, `tick_size`, `neg_risk` :contentReference[oaicite:11]{index=11}

#### POST `/books` — Get multiple order book summaries by request
- **Endpoint:** `POST https://clob.polymarket.com/books` :contentReference[oaicite:12]{index=12}
- **Body:** array of `{ token_id: string }` :contentReference[oaicite:13]{index=13}
- **Response:** array of orderbook summary objects (same shape as `/book`) :contentReference[oaicite:14]{index=14}

---

### 2) Pricing

#### GET `/price` — Get market price (single token + side)
- **Endpoint:** `GET https://clob.polymarket.com/price` :contentReference[oaicite:15]{index=15}
- **Query params:**
  - `token_id` (string, required) :contentReference[oaicite:16]{index=16}
  - `side` (`BUY` | `SELL`, required) :contentReference[oaicite:17]{index=17}
- **Response:** `{ "price": "..." }` (string for precision) :contentReference[oaicite:18]{index=18}

#### GET `/prices` — Get multiple market prices
- **Endpoint:** `GET https://clob.polymarket.com/prices` :contentReference[oaicite:19]{index=19}
- **Docs show response as:** map of `token_id -> { BUY: string, SELL: string }` :contentReference[oaicite:20]{index=20}
- **Note:** the docs page does not show query parameters for selecting which tokens (it only shows the endpoint + response shape). :contentReference[oaicite:21]{index=21}  
  - **Workaround:** use the POST form below where you explicitly specify token ids + sides.

#### POST `/prices` — Get multiple market prices by request (explicit token ids + sides)
- **Endpoint:** `POST https://clob.polymarket.com/prices` :contentReference[oaicite:22]{index=22}
- **Headers:** `Content-Type: application/json` :contentReference[oaicite:23]{index=23}
- **Body:** array (max length `500`) of:
  - `token_id` (string, required)
  - `side` (`BUY` | `SELL`, required) :contentReference[oaicite:24]{index=24}
- **Response:** map of `token_id -> { BUY|SELL: string }` (prices as strings) :contentReference[oaicite:25]{index=25}

#### GET `/midpoint` — Get midpoint price
- **Endpoint:** `GET https://clob.polymarket.com/midpoint` :contentReference[oaicite:26]{index=26}
- **Query params:** `token_id` (string, required) :contentReference[oaicite:27]{index=27}
- **Response:** `{ "mid": "..." }` (string for precision) :contentReference[oaicite:28]{index=28}

---

### 3) Spreads

#### POST `/spreads` — Get bid-ask spreads (multiple tokens)
- **Endpoint:** `POST https://clob.polymarket.com/spreads` :contentReference[oaicite:29]{index=29}
- **Headers:** `Content-Type: application/json` :contentReference[oaicite:30]{index=30}
- **Body:** array of `{ token_id: string }` :contentReference[oaicite:31]{index=31}
- **Response:** (see docs for exact response shape; endpoint is explicitly documented as returning bid-ask spreads for multiple tokens) :contentReference[oaicite:32]{index=32}

---

### 4) Historical timeseries

#### GET `/prices-history` — Historical Timeseries Data
- **Endpoint:** `GET https://clob.polymarket.com/prices-history` :contentReference[oaicite:33]{index=33}
- **Response example:** `{ "history": [ { "t": <unix_seconds>, "p": <price_number> } ] }` :contentReference[oaicite:34]{index=34}

---

## Authentication (required for private endpoints like orders)

Polymarket docs distinguish **L1** vs **L2** auth headers.

### L1 Headers
Fields shown in docs:
- `POLY_ADDRESS`
- `POLY_SIGNATURE`
- `POLY_TIMESTAMP`
- `POLY_NONCE` :contentReference[oaicite:35]{index=35}

### L2 Headers
Fields shown in docs:
- `POLY_ADDRESS`
- `POLY_SIGNATURE`
- `POLY_TIMESTAMP`
- `POLY_NONCE`
- `POLY_API_KEY`
- `POLY_PASSPHRASE` :contentReference[oaicite:36]{index=36}

> Many order-management endpoints explicitly state they “require a L2 Header.” :contentReference[oaicite:37]{index=37}

---

## Order Management (private; requires L2 header)

### Get Order
- **HTTP request:** `GET /<clob-endpoint>/data/order/<order_hash>` :contentReference[oaicite:38]{index=38}
- Docs note: “This endpoint requires a L2 Header.” :contentReference[oaicite:39]{index=39}

### Cancel Orders (by order ids)
From docs:
- **HTTP request:** `DELETE /<clob-endpoint>/orders` :contentReference[oaicite:40]{index=40}
- **Body parameters:**
  - `orderIDs` (string[], required): order IDs to cancel :contentReference[oaicite:41]{index=41}
- Docs note: requires L2 header. :contentReference[oaicite:42]{index=42}

### Cancel all orders
From docs:
- **HTTP request:** `DELETE /<clob-endpoint>/cancel-all` :contentReference[oaicite:43]{index=43}
- Docs note: requires L2 header. :contentReference[oaicite:44]{index=44}

> The Polymarket docs page shown above is the authoritative source for the remaining order-management endpoints (placing single orders, batching, active orders, onchain order info, trade history, etc.). If you want, paste those specific sections here and I’ll fold them into this file without guessing any missing fields.

---

## Recommended workflow (mirroring OddsPapi pattern)

### A) Build mappings (Gamma; not polled frequently)
1. **Gamma `GET /markets`** filtered to your domain (e.g., LoL / esports).
2. Store per market:
   - `conditionId`
   - `clobTokenIds`
   - any team/event metadata you care about :contentReference[oaicite:45]{index=45}

### B) Live pricing loop (CLOB; polled or streamed)
For each tracked token:
- Use `GET /book?token_id=...` for full best levels (bids/asks) :contentReference[oaicite:46]{index=46}
- Or use `GET /price?token_id=...&side=...` for single-sided price :contentReference[oaicite:47]{index=47}
- Or use `POST /prices` to fetch many token prices in one call (recommended) :contentReference[oaicite:48]{index=48}
- Optionally compute midpoint via `GET /midpoint?token_id=...` :contentReference[oaicite:49]{index=49}
- Optionally request spreads via `POST /spreads` :contentReference[oaicite:50]{index=50}

---

## FAQ

### Does Gamma provide the tokenID?
Yes — Gamma `GET /markets` includes a `clobTokenIds` field. :contentReference[oaicite:51]{index=51}

### How do we know what `market` means on the CLOB side?
Docs for the CLOB market websocket message specify `market` is the **condition ID**. :contentReference[oaicite:52]{index=52}
