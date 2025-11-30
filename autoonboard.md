# Auto‑Onboarding: Import Sessions with External Scraper Service

This document describes a safe, real‑time import flow that lets an external scraper/AI agent (today: the Reko Item Scraper service) scrape an existing online store and auto-create the seller’s items, locations, and profile records in an inactive/unavailable state.

Goals
- “One‑click” onboarding: user pastes any store URL, clicks “Set up my storefront for me.”
- Real‑time UX: items appear in the account as the agent scrapes.
- Safety: agent can only create/update inactive/unavailable resources in a short‑lived, tightly scoped import session.
- Alignment with our architecture: Location/ItemAvailability only; no legacy SaleWindow.

Non‑goals
- No automatic public publishing. All imported resources remain inactive/unavailable until the seller chooses to enable them. In addition, auto‑onboarded items and profiles are flagged NOT_SUBMITTED for seller review (this is a seller‑facing review flag, not a public publish state).

Scope and constraints
- Sources: any URL with best‑effort scraping by the scraper service.
- Limits: max 300 items per import session; backend enforces caps and rate limits.
- Created resources are inactive/unavailable; items are not attached to any location by default.
- Availability OFF by default; locations inactive by default.
- Auto-onboarding pre-fills the seller profile but does NOT submit it; sellers must review and submit their profile in-app. Locations are optional.
- Stripe‑only world; no legacy payment flows touched.

## End‑to‑end flow (simplified)
1) User action (frontend)
   - User pastes store URL and clicks “Set up my storefront for me.”
2) Start agent session (backend)
   - POST /v4/auto-onboard/start returns a short‑lived Agent Token (30‑minute TTL) scoped to the current seller.
   - If a scraper URL is configured, backend calls it with `{ URL: storeUrl, agentToken, apiBaseUrl }` to kick off scraping.
3) Scrape + normalize (scraper service)
   - The scraper crawls the URL, extracts items (name, price, image URLs, description, etc.) and pickup addresses.
   - The scraper normalizes data to our minimal schemas. Idempotency keys are optional.
4) Stream data (scraper → backend)
   - The scraper calls the auto‑onboarding endpoints in small batches (locations, profile, items) or single‑item calls, using `agentToken` and `apiBaseUrl`.
   - Backend validates and writes records (202 Accepted), forcing items to isAvailable=false and locations to active=false; no ItemAvailability rows are created.
5) Real‑time UX (frontend)
   - Frontend can poll the seller’s lists or refetch periodically to show newly created items/locations appearing.
6) Review
   - Seller reviews items/locations and submits their seller profile in-app; after submitting, the normal approval workflow applies (PENDING_APPROVAL → APPROVED). They can then enable availability and/or link items to locations.

## Security and authorization
Agent Token (short‑lived):
- Scope: only auto‑onboarding write endpoints.
- Claims include: sub=userId, sellerId, scope=auto_onboard.write, exp.
- Expiration: 30 minutes (configurable).
- Optional IP allowlist for n8n. Webhook is signed.

Backend enforcement:
- Only allow /v4/auto-onboard/* with a valid Agent Token.
- Force items to isAvailable=false and locations to active=false regardless of payload; do not create ItemAvailability rows during auto-onboarding.
- Recommended cap: ≤300 items per session; can be enforced via in‑memory/Redis counters keyed by token; no DB rows required.
- Idempotency keys are optional; when omitted, duplicates may occur. If you can send keys, the server will best‑effort upsert.

## Minimal state & environment flags
- No persistent "imports" or status records. The system mints a short‑lived Agent Token and writes records directly.
- Inactive/unavailable enforcement: items are created with isAvailable=false and notSubmitted=true; locations are created with active=false; items are not attached to any location by default.
- Optional cap of ≤300 items per session can be tracked with in‑memory/Redis counters keyed by token.
- Environment flags:
	  - AUTO_ONBOARDING_ENABLED=true|false (backend disables the start/agent endpoints when false; frontend hides the feature)
	  - ONBOARD_AGENT_WEBHOOK_URL=https://... (optional; scraper URL for production; if set, backend calls it on start)
	  - ONBOARD_AGENT_WEBHOOK_URL_TEST=https://... (optional; scraper URL for non‑production; used when x-env is not production and/or NODE_ENV is not production)
	  - AUTO_ONBOARD_AGENT_TOKEN_TTL_MINUTES=30 (optional; TTL for agent token in minutes; defaults to 30)
	  - AUTO_ONBOARD_API_BASE_URL=https://... (optional; explicit base URL the scraper should use when calling back into this API, e.g. `https://staging.rekohub.com/api` or `http://localhost:8000/api`. If unset, backend derives it from API_BASE_URI.)

## Seller profile states with auto‑onboarding

- Auto‑onboarded profiles are marked NOT_SUBMITTED in Feature Parity responses until the seller explicitly submits in‑app.
- Mapping rules returned by Feature Parity endpoints:
  - NOT_SUBMITTED when autoOnboardNotSubmitted=true
  - APPROVED when autoOnboardNotSubmitted=false and pendingApproval=false
  - CHANGE_REQUESTED when pendingApproval=true and rejectionReason is present
  - PENDING_APPROVAL when pendingApproval=true and no rejectionReason
- Side‑effects:
  - POST /v4/auto-onboard/start sets pendingApproval=true and autoOnboardNotSubmitted=true and clears prior approved/rejected dates.
  - POST /v4/auto-onboard/profile also forces pendingApproval=true and autoOnboardNotSubmitted=true.
  - When the seller submits their profile in‑app (Feature Parity submit), autoOnboardNotSubmitted becomes false. If moderation is required, the state is PENDING_APPROVAL; once approved, it becomes APPROVED.
- Rationale: scraped data may be incomplete or inaccurate; we never assume the profile is ready for sale without an explicit seller submit.

## Item states with auto‑onboarding

- Auto‑onboarded items are created with notSubmitted=true and isAvailable=false.
- Frontend receives notSubmitted via V4 item endpoints and can surface a "Not submitted" badge/group.
- Side‑effects:
  - POST /v4/auto-onboard/items and /v4/auto-onboard/items-batch set notSubmitted=true on every created item.
  - When a seller edits/saves an item via the V4 item update endpoint, notSubmitted becomes false.
  - Changes to moderated fields (name, description, photos) still follow the normal pendingApproval path; this is orthogonal to notSubmitted.

## API surface (V4, minimal)

Start an agent session (user session auth)
- POST /v4/auto-onboard/start
  - Body: { storeUrl: string }
  - Returns: { agentToken: string, expiresAt: ISO8601 }
  - Side-effect: if a scraper URL is configured, backend calls it with `{ URL: storeUrl, agentToken, apiBaseUrl }`.

### Config endpoint (new)

To let the frontend gate the UI before starting a session, the backend exposes a lightweight config endpoint:

- GET /v4/auto-onboard/config (auth: api_key)
- Returns:
  - enabled: boolean — whether AUTO_ONBOARDING_ENABLED is on (server-side)
  - ttlMinutes: number — current agent token TTL (defaults to 30 if unset/invalid)
  - webhookTarget: 'prod' | 'test' | 'none' — which webhook URL will be used by the backend when /start is called, based on the request environment

The webhook target selection logic matches /start:
- If request header x-env is 'production' (or missing and NODE_ENV=production) → use ONBOARD_AGENT_WEBHOOK_URL (fallback to TEST if PROD missing)
- Otherwise (staging/dev) → use ONBOARD_AGENT_WEBHOOK_URL_TEST (fallback to PROD if TEST missing)

Example:

````bash
curl -H "Authorization: <token>" \
     -H "x-env: staging" \
     "http://localhost:8000/api/v4/auto-onboard/config"
# => { "enabled": true, "ttlMinutes": 30, "webhookTarget": "test" }
````

Frontend usage:
- Call once when entering seller mode or opening the Items screen
- If enabled=false, hide the CTA/empty-state card for auto-onboarding
- Use ttlMinutes to tune client timers if needed
- No need to branch on webhookTarget in the client; it is informational for debugging


Agent-scoped endpoints (require Agent Token)
- POST /v4/auto-onboard/profile
  - Body: { profile: { displayName?, bio?, contact? } }
- POST /v4/auto-onboard/locations
  - Body: { locations: [ { label, address1, address2?, city, state, postalCode, country? } ] }
- POST /v4/auto-onboard/items (single item)
  - Body: {
      sourceItemId?, idempotencyKey?,
      name, description?,
      // Price can be flexible format (string) or numeric (cents)
      price?: number | string, // e.g., 899 (cents), "$3.00", "3.00/lb", "3 per pound", "3 ea"
      imageUrl?: string,
      isApproximate?: boolean, // optional; auto-detected from price format if unit present
      // Legacy fields still supported:
      exactPrice?: { amount: number },
      approximatePricing?: { isApproximate: boolean, unit?: "lb"|"oz"|"kg"|"g"|"each", pricePerUnit?: { amount: number } },
      imageUrls?: string[]
    }
  - Returns: 202 Accepted. Items are created with isAvailable=false and notSubmitted=true; locations are created with active=false; items are not attached to any location by default.
- POST /v4/auto-onboard/items-batch
  - Body: { items: [ same object as items-draft ] }
  - Returns: 202 Accepted. Batch size: 10–25 recommended. Backend may enforce ≤300 total items.
  - All items are created with isAvailable=false and notSubmitted=true; global availability OFF. Locations are created with active=false.
	- POST /v4/auto-onboard/progress (optional, recommended)
	  - Purpose: send lightweight scraping/import progress updates that stream in real time to the seller’s app via SSE.
	  - Auth: `Authorization: Bearer <agentToken>`
	  - Body (all fields optional; send what you know):
	    - `discoveredCount?: number` — how many candidate items you’ve discovered so far while scraping.
	    - `sentCount?: number` — how many items you’ve already POSTed to `/items` or `/items-batch`.
	    - `createdCount?: number` — (optional) how many items the backend has confirmed; can usually be the same as `sentCount`.
	    - `totalCount?: number` — best-effort estimate of total items you expect to import (e.g., total products on the source site).
	    - `phase?: "discovery" | "scraping" | "importing" | "complete" | "error"` — high-level phase tag.
	    - `message?: string` — human-readable status such as `"10/86 items found"`; if omitted, the server will construct a basic message from the counts.
	  - Response: HTTP 202 with `{ "status": "accepted" }`. No persistence; events are fanned out to any active SSE listeners for this seller.

Monitoring (frontend)
	- There is no persistent "status" resource. The optional `/v4/auto-onboard/progress` endpoint lets the agent stream ephemeral progress over SSE; the frontend can also poll/refetch to show newly created items/locations appearing during the 30-minute window.

## Realtime updates via Server‑Sent Events (SSE)

To deliver realtime UX during auto‑onboarding without polling, expose a single SSE event that tells the app “something changed—refresh.” The client then invalidates/refetches the seller‑items list (and may optionally navigate to the Item Listings screen on the first event). This integrates cleanly with `~/lc-app` on web (native `EventSource`) and React Native (`react-native-event-source`).

- Endpoint: `GET /api/v4/auto-onboard/events?agentToken=...`
  - Auth: `agentToken` returned by `POST /v4/auto-onboard/start` (short‑lived, scoped to a seller)
  - Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `Connection: keep-alive`, `X-Accel-Buffering: no`
  - Scope: only changes for the token’s `sellerId`
  - Lifetime: closes when token expires or client disconnects; send a heartbeat comment every ~10–15s
  - Delivery: at‑least‑once. Include `id:` (monotonic per-connection) to aid debugging and reconnect flows; no server‑side replay needed—clients refetch on reconnect.
  - One connection per agentToken: If a new connection arrives with the same `agentToken`, close the previous stream to prevent duplicate refreshes.
- Event types:
  - `refresh` → `{}` (no payload; clients refetch the list)
	  - `progress` → `{ discoveredCount?, sentCount?, createdCount?, totalCount?, phase?, message? }` (optional structured progress payload used to drive "10/86 items found"-style UI; no persistence)

Backend sketch (Express):
```ts
// GET /api/v4/auto-onboard/events?agentToken=...
app.get('/api/v4/auto-onboard/events', (req, res) => {
  const token = String(req.query.agentToken || '');
  const claims = agentTokenStore.get(token); // { sellerId, exp }
  if (!claims) return res.status(401).end();

  // Enforce one connection per agent token
  const prior = activeStreams.get(token);
  if (prior) { try { prior.end(); } catch {} }
  activeStreams.set(token, res);

  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    'Connection': 'keep-alive',
    'X-Accel-Buffering': 'no',
  });

  let seq = 0;
  const send = () => {
    res.write(`id: ${++seq}\n`);
    res.write(`event: refresh\n`);
    res.write(`data: {}\n\n`);
  };

  const heartbeat = setInterval(() => res.write(`:keep-alive ${Date.now()}\n\n`), 15000);

  const onEvent = (e: { sellerId: number }) => {
    if (e.sellerId === claims.sellerId) send();
  };
  autoOnboardEventBus.on('refresh', onEvent);

  const stop = () => {
    clearInterval(heartbeat);
    autoOnboardEventBus.off('refresh', onEvent);
    activeStreams.delete(token);
    try { res.end(); } catch {}
  };

  req.on('close', stop);
  setTimeout(stop, Math.max(0, claims.exp * 1000 - Date.now()));
});

// Emit from controllers when writes complete (examples)
autoOnboardEventBus.emit('refresh', { sellerId: agent.sellerId });
```

Scaling: Redis is not required for initial deployment. If horizontally scaling later, you may forward a single `refresh` signal via Redis Pub/Sub per seller (e.g., channel `auto_onboard:<sellerId>`), and have subscribers call `send()` for connected streams.

Frontend (`~/lc-app`) focus‑bound usage

### Frontend integration checklist
- Only connect the SSE stream while the app/screen is foregrounded; disconnect on background or unmount.
- On `refresh`, debounce and invalidate the seller-items query (e.g., 300–500ms) instead of manual list mutation.
- Optional: On the first `refresh`, navigate to the Item Listings screen if not already there.
- Handle `onerror` by closing the stream; the browser/EventSource will attempt reconnects automatically. If the token has expired, the server will respond 401 and the connection will not resume—fallback to polling.
- One connection per agentToken: If you open a new connection with the same token (e.g., second tab), the server will close the prior connection.
- Token expiry: when the 30‑minute window ends, the stream will be closed by the server. The UI should silently stop auto‑refreshing and continue normal behavior.
- Dev: ensure your dev proxy or CORS allows `/api/v4/auto-onboard/events`. In web dev, prefer relative paths (`/api/...`) so same-origin cookies and headers apply.

### Reconnection and expiry behavior
- Heartbeats are sent every ~15s to keep intermediaries from closing idle connections.
- Event IDs are per-connection and for debugging; no server replay is provided because the client refetches the list on reconnect.
- If you want to surface a subtle UI hint (e.g., "Live import connected"), you can flip a small state flag on `open` and clear it on `error/close`.

### Dev/test tips
- You can test the stream in a browser: navigate to `/api/v4/auto-onboard/events?agentToken=...` and watch the event frames stream.
- Trigger writes (POST profile/items/items-batch/locations) in another tab; you should see `event: refresh` frames appear in the stream.

- Maintain the SSE connection only while the app/screen is foregrounded.
- On each `refresh`, debounce and invalidate the seller‑items list query; optionally navigate to the Listings screen on the first event.
- Web: native `EventSource`. RN: `react-native-event-source`.

Web example:
```ts
import { debounce } from 'lodash';
let es: EventSource | null = null;
let navigated = false;

export function connectAutoOnboardStream(agentToken: string) {
  es = new EventSource(`/api/v4/auto-onboard/events?agentToken=${encodeURIComponent(agentToken)}`);
  const refresh = debounce(() => {
    queryClient.invalidateQueries(['seller-items']);
    if (!navigated && !isOnListingsScreen()) { navigateToListings(); navigated = true; }
  }, 300);
  es.addEventListener('refresh', () => refresh());
  es.onerror = () => { es?.close(); es = null; };
}

export function disconnectAutoOnboardStream() { es?.close(); es = null; }

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') disconnectAutoOnboardStream();
});
```

React Native example (conceptual):
```ts
import EventSource from 'react-native-event-source';
import { AppState } from 'react-native';
let es: EventSource | null = null;

function connect(token: string) {
  es = new EventSource(`${API_BASE}/v4/auto-onboard/events?agentToken=${encodeURIComponent(token)}`);
  const refresh = debounce(() => queryClient.invalidateQueries(['seller-items']), 300);
  es.addEventListener('refresh', () => refresh());
  es.onerror = () => { es?.close(); es = null; };
}

function disconnect() { es?.close(); es = null; }

AppState.addEventListener('change', (state) => {
  if (state === 'active') connect(agentToken); else disconnect();
});
```

Notes
- Keep event payloads small; the client can refetch full entities.
- Ensure dev proxy or CORS allows `/api/v4/auto-onboard/events` from the app origin.
- Handle duplicate events idempotently on the client.

## Validation, idempotency, and limits
- Idempotency keys are optional. If provided, we best‑effort upsert by key; otherwise duplicates may be created if the agent re‑sends the same item.
- Backend may enforce ≤300 items/session and per‑request rate limits (e.g., 1–3 RPS). Write endpoints return 202 Accepted; ingestion runs async.
- Minimal sent item field: name. If no price information is provided, the server sets price=0 (USD), forces isAvailable=false, and does not attach items to any location by default.
- Currency: USD is assumed everywhere; omit currency fields in all payloads.
- Modification name is optional/nullable in schemas.
- Image ingestion from remote URLs happens asynchronously; thumbnails update as assets arrive.

## Image handling
- The external scraper/agent provides remote imageUrls for items.
- Backend worker downloads, scans (if applicable), uploads to our storage/CDN, and relinks image URLs.
- Failures are recorded as per‑item warnings; a placeholder remains until fixed.

## Error handling and retries
- Batch endpoints may return partial successes with per‑item errors. You may re‑send the same items; without idempotency, duplicates may be created.
- At‑least‑once sending is acceptable; retries are optional and do not require special keys.
- On 429 responses: backoff is recommended.

---

# Testing & Agent Token

## Getting an agent token for local testing

To test the auto-onboard endpoints locally, you first need to mint an agent token:

```bash
# 1. Start the session and get an agent token
curl -X POST "http://localhost:8000/api/v4/auto-onboard/start" \
  -H "Authorization: reko-admin-test-token" \
  -H "Content-Type: application/json" \
  -d '{ "storeUrl": "https://example.com" }'

# Response:
# {
#   "agentToken": "76dbbec28f2a93ec5f89854244435e1ee219c8cfa679d3b46391dea2aaa2992d",
#   "expiresAt": "2025-11-26T23:47:10.000Z"
# }

# 2. Export the token for use in subsequent requests
export AGENT_TOKEN="76dbbec28f2a93ec5f89854244435e1ee219c8cfa679d3b46391dea2aaa2992d"

# 3. Now you can test the item endpoints
curl -X POST "http://localhost:8000/api/v4/auto-onboard/items" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Pallets",
    "description": "Wood pallets. Most in good shape, some are good for firewood.",
    "price": "$3.00 ea",
    "imageUrl": "https://example.com/pallets.jpg"
  }'
```

**Important:** Use `http://localhost:8000` for local testing, not the ngrok URL. The ngrok URL may be fronting the frontend web server instead of the backend API.

---

# Scraper / Agent Instructions

This section is for the external scraper workflow (e.g., the Reko Item Scraper service) that performs scraping and calls our API.

Inputs provided by backend when a user starts an import:
- apiBaseUrl (e.g., https://staging.rekohub.com/api)
- agentToken (Bearer token scoped to this session)
- storeUrl / URL (the URL to scrape)

General guidance (works even for a "dumb" automation)
- Respect the 300-item cap; stop early if you reach it.
- Sending locations first is optional.
- Send a single profile draft update once.
- You may send items one-by-one or in batches of 10–25.
- Idempotency keys are optional.
- Progress calls to `/v4/auto-onboard/progress` are optional (but recommended). There is currently no `/complete` endpoint; the seller completes review in-app.
- Calls return 202 Accepted; ingestion runs asynchronously.
- Back off on HTTP 429 when possible.

## Example: POST profile draft
```bash
curl -X POST "https://tina-nonperceivable-rabidly.ngrok-free.dev/api/v4/auto-onboard/profile" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "profile": {
      "displayName": "Farm Fresh Co",
      "tagline": "Local dairy & eggs",
      "description": "We produce fresh, high-quality dairy products from our family farm.",
      "email": "owner@farmfresh.co",
      "phone": "+1-555-123-4567",
      "pictureUrl": "https://external-content.duckduckgo.com/iu/?u=https%3A%2F%2Ftse4.mm.bing.net%2Fth%2Fid%2FOIP.v7MEsULiJf-tO5TRch0ZdAAAAA%3Fcb%3D12%26pid%3DApi&f=1&ipt=755c43318406e20c239aab59766206b2afb6c9e74d10f0c76e670755c535f4cb&ipo=images",
      "businessAddress": {
        "streetAddress1": "123 Country Rd",
        "city": "Springfield",
        "region": "IL",
        "postalCode": "62704",
        "country": "US",
        "description": "Farm pickup location"
      }
    }
  }'
```

> Note: Including `profile.businessAddress` creates an inactive self-held pickup Location and links the address to the seller's private market so Feature Parity endpoints (e.g., seller-profiles/me, get-my-address) return it. For multiple pickup addresses, call `/v4/auto-onboard/locations` with an array.


## Example: POST pickup locations (batch)
```bash
curl -X POST "https://tina-nonperceivable-rabidly.ngrok-free.dev/api/v4/auto-onboard/locations" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "locations": [
      {
        "label": "Farm pickup",
        "address1": "123 Country Rd",
        "city": "Springfield",
        "state": "IL",
        "postalCode": "62704"
      }
    ]
  }'
```

## Example: POST a single item with flexible price format

This example shows the recommended simple format with flexible price parsing:

```bash
curl -X POST "http://localhost:8000/api/v4/auto-onboard/items" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Pallets",
    "description": "Wood pallets. Most in good shape, some are good for firewood.",
    "price": "$3.00 ea",
    "imageUrl": "https://example.com/pallets.jpg"
  }'
```

## Example: send progress updates during scraping (optional, recommended)

Use this endpoint to stream human-readable progress to the seller's phone/web app while you scrape and import items. The backend fans these updates out over the SSE `progress` event on `/api/v4/auto-onboard/events`.

Typical pattern:
- While scraping pages and discovering items (before you POST them), periodically call `/v4/auto-onboard/progress` with `discoveredCount` and a best-effort `totalCount`.
- As you POST items to `/items` or `/items-batch`, update `sentCount` (and optionally `createdCount`).
- When you are done, send a final call with `phase: "complete"` and a short summary message.

Example ("10/86 items found"):

```bash
curl -X POST "http://localhost:8000/api/v4/auto-onboard/progress" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "discoveredCount": 10,
    "totalCount": 86,
    "phase": "discovery",
    "message": "10/86 items found"
  }'
```

Example (mid-import, after some items have been sent to the backend):

```bash
curl -X POST "http://localhost:8000/api/v4/auto-onboard/progress" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "discoveredCount": 50,
    "sentCount": 40,
    "totalCount": 86,
    "phase": "importing",
    "message": "Imported 40 of 86 items (50 discovered so far)"
  }'
```

### Supported price formats

The `price` field accepts flexible string or numeric formats:

| Format | Example | Result |
|--------|---------|--------|
| Numeric (cents) | `300` | 300 cents, exact price, unit=Order |
| Simple decimal | `"3.00"` or `"$3.00"` | 300 cents, exact price, unit=Order |
| Per-unit (each) | `"$3.00 ea"` or `"3 each"` | 300 cents, approximate, unit=Each |
| Per-pound | `"$3.00/lb"` or `"3 per pound"` | 300 cents, approximate, unit=Pound |
| Per-kilogram | `"$3.00/kg"` or `"3 per kilogram"` | 300 cents, approximate, unit=Kilogram |
| Per-ounce | `"$3.00/oz"` or `"3 per ounce"` | 300 cents, approximate, unit=Ounce |
| Per-gram | `"$3.00/g"` or `"3 per gram"` | 300 cents, approximate, unit=Gram |

**Automatic behavior:**
- `isApproximate` is auto-detected from the price format (true if a unit is present)
- Measurement unit is auto-created if needed (e.g., "Pound", "Kilogram", "Each")
- The `isApproximate` flag can be overridden explicitly if needed

## Example: POST items (batch) with flexible price formats

```bash
curl -X POST "http://localhost:8000/api/v4/auto-onboard/items-batch" \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "name": "Raw Milk",
        "description": "Fresh local raw milk",
        "price": "$3.00 per pound",
        "imageUrl": "https://cdn.ext/123.jpg"
      },
      {
        "name": "Dozen Eggs",
        "description": "Farm fresh eggs",
        "price": "$5.99 ea",
        "imageUrl": "https://cdn.ext/456.jpg"
      },
      {
        "name": "Honey",
        "description": "Raw honey",
        "price": "12.50/kg",
        "imageUrl": "https://cdn.ext/789.jpg"
      }
    ]
  }'
```

(No separate "status" or "complete" resource is required. The optional `/v4/auto-onboard/progress` endpoint is available for real-time scraping updates; if you don't call it, the app will still update as items are created during the token's 30-minute lifetime.)

Notes for idempotency (optional)
- If you can, include an Idempotency-Key header for batch requests and/or an item-level idempotencyKey. Otherwise, omit them.

Rate limits and backoff
- Expected call rate: ~1–3 RPS with bursts tolerated for small batches.
- On 429, back off with exponential delays if possible.

Error handling
- Batch responses may include per‑item errors. You may re-send the same items; duplicates may be created if no idempotency is provided.

---

Implementation notes and rollout
- Phase 1: endpoints, session token scope, batch/single ingestion, limits, optional idempotency, feature flag.
- Phase 2: optional UI polish for real‑time (polling) and background image ingestion worker.
- Phase 3: optional dedupe improvements and validations.
