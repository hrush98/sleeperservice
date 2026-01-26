## Polymarket Gamma API (Markets) — Reference (mirrors OddsPapi taxonomy + fixtures + odds)

Base URL: `https://gamma-api.polymarket.com` :contentReference[oaicite:0]{index=0}

This section documents the Gamma endpoints shown in the Polymarket docs sidebar (Gamma Status / Sports / Tags / Events / Markets). All endpoint paths + example response shapes below are taken directly from the docs.

---

## Concept mapping (OddsPapi → Gamma)

- **OddsPapi sport/tournament/participant/fixture/odds** becomes roughly:
  - **Sports metadata / teams** (taxonomy + labels) → `/sports` + `/teams` :contentReference[oaicite:1]{index=1}
  - **Fixture discovery** (what’s coming up) → `/events` (events contain markets) :contentReference[oaicite:2]{index=2}
  - **Odds / outcomes** → `/markets` (markets have outcomes + prices) :contentReference[oaicite:3]{index=3}
  - **Category filtering / grouping** → `/tags` + event/market tags :contentReference[oaicite:4]{index=4}

> Note: Gamma “events” are containers (often with multiple markets). Gamma “markets” are the tradeable objects (with outcome prices) you’ll align against external “sharp” sources.

---

# Gamma Status

## GET Gamma API Health check
> This endpoint appears in the “Gamma Status” section of the docs sidebar, but I did not capture its path/response in the sources retrieved in this run. If you want it included in this markdown, paste the health-check page contents (or the curl snippet shown there) and I’ll append it exactly.

---

# Sports

## GET List teams
Docs page: “List teams” under Sports. :contentReference[oaicite:5]{index=5}  
> Use to build/refresh a **team-id → team name** cache for sports-style markets.

*(I did not capture the parameter table/response example for this endpoint in the sources retrieved in this run. If you paste the endpoint section, I’ll add it verbatim.)*

## GET Get sports metadata information
Docs page: “Get sports metadata information” under Sports. :contentReference[oaicite:6]{index=6}  
> Use to understand which sports metadata Gamma exposes (useful for building the static mappings layer).

*(I did not capture the parameter table/response example for this endpoint in the sources retrieved in this run. If you paste the endpoint section, I’ll add it verbatim.)*

## GET /sports/market-types — Get valid sports market types
Get a list of all valid sports market types. Docs explicitly note these values are used when filtering markets by the `sportsMarketTypes` parameter. :contentReference[oaicite:7]{index=7}

**Endpoint**
- `GET /sports/market-types` :contentReference[oaicite:8]{index=8}

**Response (200)**
- Object with:
  - `marketTypes: string[]` :contentReference[oaicite:9]{index=9}

**Example response**
```json
{
  "marketTypes": ["<string>"]
}
``` :contentReference[oaicite:10]{index=10}

---

# Tags

## GET /tags — List tags
**Endpoint**
- `GET /tags` :contentReference[oaicite:11]{index=11}

**Example request**
```bash
curl --request GET \
  --url https://gamma-api.polymarket.com/tags
``` :contentReference[oaicite:12]{index=12}

*(I did not capture the query-parameter table or full response schema text for List tags in the retrieved sources; only the endpoint + curl snippet is present.)* :contentReference[oaicite:13]{index=13}

## GET Get tag by id
Docs page exists under Tags. :contentReference[oaicite:14]{index=14}  
*(Path/params/response schema not captured in retrieved sources for this run.)*

## GET Get tag by slug
Docs page exists under Tags. :contentReference[oaicite:15]{index=15}  
*(Path/params/response schema not captured in retrieved sources for this run.)*

## GET Get related tags (relationships) by tag id
Docs page exists under Tags. :contentReference[oaicite:16]{index=16}  
*(Path/params/response schema not captured in retrieved sources for this run.)*

## GET Get related tags (relationships) by tag slug
Docs page exists under Tags. :contentReference[oaicite:17]{index=17}  
*(Path/params/response schema not captured in retrieved sources for this run.)*

## GET Get tags related to a tag id
Docs page exists under Tags. :contentReference[oaicite:18]{index=18}  
*(Path/params/response schema not captured in retrieved sources for this run.)*

## GET Get tags related to a tag slug
Docs page exists under Tags. :contentReference[oaicite:19]{index=19}  
*(Path/params/response schema not captured in retrieved sources for this run.)*

> For the 6 tag-detail endpoints above: if you want them fully documented “in the exact form required” (endpoint path + parameter tables + response schemas), paste the body of each endpoint page (or even just the cURL + parameter list sections) and I’ll drop them in precisely.

---

# Events

## GET /events — List events
Docs describe this as “Fetches a list of events with various filtering and sorting options.” :contentReference[oaicite:20]{index=20}

**Endpoint**
- `GET /events` :contentReference[oaicite:21]{index=21}

**Example request**
```bash
curl --request GET \
  --url https://gamma-api.polymarket.com/events
``` :contentReference[oaicite:22]{index=22}

**Response shape (high-level)**
The docs’ example response for events is large; it includes many event fields and nested arrays (notably `markets`, and within markets: tags, pricing/market metadata, etc.). :contentReference[oaicite:23]{index=23}

**Implementation note (this project)**
- We treat the **event** as the parent match fixture and derive its start time from the moneyline market `gameStartTime` when present.
- Match/game markets are stored as children of the event; `gameStartTime` (or `game_start_time`) is preferred for markets when present.

> I didn’t capture the full query-parameter table for `/events` in the retrieved sources, only that it supports filtering/sorting plus the example response portions shown in the docs. :contentReference[oaicite:24]{index=24}

## GET /events/{id} — Get event by id
**Endpoint**
- `GET /events/{id}` :contentReference[oaicite:25]{index=25}

**Example request**
```bash
curl --request GET \
  --url https://gamma-api.polymarket.com/events/{id}
``` :contentReference[oaicite:26]{index=26}

**Example response fields (partial; as shown in docs)**
```json
{
  "id": "<string>",
  "ticker": "<string>",
  "slug": "<string>",
  "title": "<string>",
  "subtitle": "<string>",
  "description": "<string>",
  "resolutionSource": "<string>",
  "startDate": "2023-11-07T05:31:56Z",
  "creationDate": "2023-11-07T05:31:56Z",
  "endDate": "2023-11-07T05:31:56Z",
  "image": "<string>",
  "icon": "<string>",
  "active": true,
  "closed": true,
  "archived": true,
  "new": true,
  "featured": true,
  "restricted": true,
  "liquidity": 123,
  "volume": 123,
  "openInterest": 123,
  "commentsEnabled": true,
  "competitive": 123
}
``` :contentReference[oaicite:27]{index=27}

## GET /events/slug/{slug} — Get event by slug
**Endpoint**
- `GET /events/slug/{slug}` :contentReference[oaicite:28]{index=28}

**Example request**
```bash
curl --request GET \
  --url https://gamma-api.polymarket.com/events/slug/{slug}
``` :contentReference[oaicite:29]{index=29}

**Response**
Same event object shape as “Get event by id” (docs show the same top-level fields in the example). :contentReference[oaicite:30]{index=30}

## GET /events/{id}/tags — Get event tags
**Endpoint**
- `GET /events/{id}/tags` :contentReference[oaicite:31]{index=31}

**Example request**
```bash
curl --request GET \
  --url https://gamma-api.polymarket.com/events/{id}/tags
``` :contentReference[oaicite:32]{index=32}

**Response (200)**
Array of tag objects:
- `id, label, slug`
- `forceShow, forceHide, isCarousel`
- `publishedAt, createdBy, updatedBy, createdAt, updatedAt` :contentReference[oaicite:33]{index=33}

**Example response**
```json
[
  {
    "id": "<string>",
    "label": "<string>",
    "slug": "<string>",
    "forceShow": true,
    "publishedAt": "<string>",
    "createdBy": 123,
    "updatedBy": 123,
    "createdAt": "2023-11-07T05:31:56Z",
    "updatedAt": "2023-11-07T05:31:56Z",
    "forceHide": true,
    "isCarousel": true
  }
]
``` :contentReference[oaicite:34]{index=34}

---

# Markets

## GET /markets — List markets
**Endpoint**
- `GET /markets` :contentReference[oaicite:35]{index=35}

**Example request**
```bash
curl --request GET \
  --url https://gamma-api.polymarket.com/markets
``` :contentReference[oaicite:36]{index=36}

**Response (200)**
Array of market objects. Docs show many fields; the example includes (partial list):
- Core ids/labels: `id`, `question`, `conditionId`, `slug` :contentReference[oaicite:37]{index=37}
- Dates: `startDate`, `endDate`, plus `startDateIso`, `endDateIso`, etc. :contentReference[oaicite:38]{index=38}
- Market mechanics / pricing fields: `outcomes`, `outcomePrices`, `liquidity`, `fee`, `marketType`, `formatType`, `enableOrderBook`, `orderPriceMinTickSize`, `orderMinSize`, … :contentReference[oaicite:39]{index=39}
- Activity fields: `volume`, `volume24hr`, `volume1wk`, `volume1mo`, `volume1yr`, … :contentReference[oaicite:40]{index=40}
- Nested: `imageOptimized`, `iconOptimized`, and `events` (array of event objects) :contentReference[oaicite:41]{index=41}

> I did not capture the list-markets query-parameter table in the retrieved sources for this run; only the endpoint + large example response excerpt. :contentReference[oaicite:42]{index=42}

## GET /markets/{id} — Get market by id
**Endpoint**
- `GET /markets/{id}` :contentReference[oaicite:43]{index=43}

Docs show a full Market object response and include a response schema table (200 → “Market”) listing fields such as:
- `id, question, conditionId, slug, twitterCardImage, resolutionSource, endDate, category, ammType, liquidity, sponsorName, sponsorImage, startDate, xAxisValue, yAxisValue, denominationToken, ...` :contentReference[oaicite:44]{index=44}

Docs also show nested arrays on the Market object such as:
- `events` (array of event objects)
- `categories` (array)
- `tags` (array of tag objects) :contentReference[oaicite:45]{index=45}

## GET /markets/slug/{slug} — Get market by slug
**Endpoint**
- `GET /markets/slug/{slug}` :contentReference[oaicite:46]{index=46}

Response is the same Market object shape (docs show the same object fields and a similar schema table as “Get market by id”). :contentReference[oaicite:47]{index=47}

## GET /markets/{id}/tags — Get market tags by id
**Endpoint**
- `GET /markets/{id}/tags` :contentReference[oaicite:48]{index=48}

**Path parameters**
- `id` (integer, required) :contentReference[oaicite:49]{index=49}

**Response (200)**
Array of tag objects attached to the market:
- `id: string`
- `label: string | null`
- `slug: string | null`
- `forceShow: boolean | null`
- `publishedAt: string | null`
- `createdBy: integer | null`
- `updatedBy: integer | null`
- `createdAt: string<date-time> | null`
- `updatedAt: string<date-time> | null`
- `forceHide: boolean | null`
- `isCarousel: boolean | null` :contentReference[oaicite:50]{index=50}

**Example response**
```json
[
  {
    "id": "<string>",
    "label": "<string>",
    "slug": "<string>",
    "forceShow": true,
    "publishedAt": "<string>",
    "createdBy": 123,
    "updatedBy": 123,
    "createdAt": "2023-11-07T05:31:56Z",
    "updatedAt": "2023-11-07T05:31:56Z",
    "forceHide": true,
    "isCarousel": true
  }
]
``` :contentReference[oaicite:51]{index=51}

---

## Recommended “OddsPapi-like” call flow for LoL-style markets (Gamma side)

1) **Build static mappings layer**
- Pull tags (`/tags`) and cache tag id/slug ↔ label. :contentReference[oaicite:52]{index=52}
- Pull sports market types (`/sports/market-types`) and cache `marketTypes[]`. :contentReference[oaicite:53]{index=53}
- Use sports metadata + teams (`/sports` + `/teams`) to label/normalize participants (taxonomy layer). :contentReference[oaicite:54]{index=54}

2) **Discover upcoming opportunities**
- Use `/events` to list candidate events (filtering via query params per docs), then inspect the nested markets within each event response. :contentReference[oaicite:55]{index=55}

3) **Poll / snapshot “prices”**
- Use `/markets` for bulk market snapshots, or fetch single markets by id/slug for focused polling. :contentReference[oaicite:56]{index=56}
- Use `/markets/{id}/tags` or `/events/{id}/tags` when you need to verify category/tag alignment for mapping. :contentReference[oaicite:57]{index=57}
