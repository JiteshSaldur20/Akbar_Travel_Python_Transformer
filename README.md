# Provider Transformer - Sabre Connector

A FastAPI service that receives the Java ORBiS `FlightSearchRequest`, adapts it to
the canonical **home** payload, transforms it into the Sabre Bargain Finder Max
(`OTA_AirLowFareSearchRQ`) request, calls Sabre itself (Bearer token from Java
ORBiS), and returns the **raw Sabre response** to Java. JSON is handled entirely
by **msgspec**; outbound HTTP by a pooled **httpx** client.

This connector is **Sabre-only** (provider code `SABRE`, api type `sabre_ndc`).
Credentials (username/password/PCC) are **not** stored here — Python gets the
token from Java ORBiS `/internal/providers/SABRE/auth`, and Java owns credentials.

## Flow

```text
Java ORBiS
     |  POST /api/flights/search (common FlightSearchRequest)
     v
Sabre Connector
     |  GET /internal/providers/SABRE/auth?apiType=sabre_ndc  (token via Java)
     v
Sabre REST BFM (v5/shop/flights)  --Bearer token-->  OTA_AirLowFareSearchRQ
     |
     v  raw OTA_AirLowFareSearchRS passed through byte-for-byte
Java ORBiS
```

## Project structure

```
app/
├── main.py                    # FastAPI app factory (/health, lifespan)
├── api/
│   └── search.py              # POST /api/flights/search (Java ORBiS -> Sabre)
├── connectors/
│   ├── java_auth_client.py    # token retrieval from Java ORBiS (memoized)
│   └── sabre_client.py        # pooled httpx client calling Sabre BFM
├── transformers/
│   ├── base.py                # RequestTransformer/ResponseTransformer ABCs + pair
│   ├── router.py              # provider registry (sabre)
│   ├── sabre.py               # SABRE TransformerPair
│   ├── sabre_request_transformer.py  # home request -> BFM body
│   └── sabre_response_transformer.py # stub (responses are passed through raw)
├── schemas/
│   ├── connector.py           # FlightSearchRequest / Stops / Filters (msgspec)
│   └── home_payload.py        # canonical home msgspec Structs
└── core/                      # config.py, exceptions.py
tests/                         # pytest + TestClient tests
```

## Setup (virtual environment)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env   # then edit as needed
```

## Run

The Java ORBiS mid-system calls this connector on
`http://<host>:8090/api/flights/search` (see the sabre connector block in the Java
`application.properties`), so run it on 8090:

```powershell
.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8090
```

Interactive docs: http://127.0.0.1:8090/docs

### Configuration

| Env var | Default | Effect |
|---------|---------|--------|
| `JAVA_INTERNAL_URL` | `http://localhost:8080` | Java ORBiS token endpoint base |
| `SABRE_BASE_URL` | `https://api.cert.platform.sabre.com` | Sabre REST host |
| `SABRE_SEARCH_PATH` | `v5/offers/shop` | BFM path |
| `SABRE_CONNECT_TIMEOUT_S` / `SABRE_READ_TIMEOUT_S` | `10` / `30` | Sabre connection and read timeouts |
| `HTTP_MAX_CONNECTIONS` | `100` | Outbound pool size |
| `HTTP_MAX_KEEPALIVE_CONNECTIONS` | `20` | Idle keep-alive sockets retained |
| `HTTP_KEEPALIVE_EXPIRY_S` | `30.0` | Idle socket lifetime before close |
| `AUTH_TOKEN_SAFETY_BUFFER_S` | `30.0` | Refresh the cached token this long before expiry |
| `SEARCH_TIMING_LOG` | `true` | Per-stage latency breakdown per search |
| `SEARCH_NUMBER_OF_TRIPS` | `10` | Trips (itineraries) BFM should return - a direct latency lever |
| `SEARCH_REQUEST_TYPE` | `50ITINS` | IntelliSell transaction type - the biggest BFM latency lever, but it must be a value your PCC is provisioned for |
| `SEARCH_MAX_CONNECTIONS` / `SEARCH_FARES_PER_JOURNEY` / `SEARCH_PREFER_NDC_ON_TIE` / `SEARCH_ENABLE_ATPCO` / `SEARCH_ENABLE_NDC` / `SEARCH_MAX_UPSELLS` / `SEARCH_MULTIPLE_BRANDED_FARES` | see `app/core/config.py` | Shape the BFM request (NOT credentials) |

Notes:
- Sabre username/password/PCC are configured in the **Java backend DB**
  (`provider_credential` rows, with `SABRE_*` env fallback), never here. The PCC
  is read back from Java's auth response and sent in the POS.
- Getting a latency number per hop: set `SEARCH_TIMING_LOG=true` here (it prints
  a per-stage breakdown and the Sabre round trip), and read the Java log lines
  `<CODE> connector responded in <n> ms` plus `TOTAL ~<n> ms` - the providers are
  called concurrently, so the total tracks the **slowest** provider.
- Do **not** use `--workers`/`--reload` under load on Windows — each worker gets
  a `SelectorEventLoop` with a 512-socket cap. Run a single process:
  `--no-access-log --http httptools`.

## Endpoint: `POST /api/flights/search`

Input (same common payload Java forwards to every provider connector):

```json
{
  "operation": "FLIGHT_SEARCH",
  "from": "MAA",
  "to": "DEL",
  "dateFrom": "2026-11-04",
  "dateTo": "2026-11-11",
  "tripType": "ROUND_TRIP",
  "ADT": 1,
  "CHD": 1,
  "INF": 0,
  "currency": "INR",
  "cabin": "ECONOMY",
  "refundable": null,
  "filters": { "stops": { "min": 0, "max": 1 }, "airlines": [] }
}
```

Output: **200 with the raw Sabre `OTA_AirLowFareSearchRS` body** (Java contract —
Python does not convert it). Sabre non-2xx responses are forwarded faithfully
(status + body). Controlled failures return `{"error": ...}` without stack
traces:

| Status | error | When |
|--------|-------|------|
| 502 | `provider_api_error` | Cannot reach Java auth or Sabre; auth/token failure |
| 422 | `transformation_failed` | Empty/invalid/missing-required-fields body |

### Mappings

- `from`/`to`/`dateFrom`/`dateTo` -> `OriginDestinationInformation` legs
  (1 leg when no return date, else outbound + inbound).
- `ADT`/`CHD`/`INF` -> `PassengerTypeQuantity` (zero counts omitted; empty ->
  single ADT).
- `NumTrips`, `DataSources` (ATPCO/NDC), `PreferNDCSourceOnTie`, `NDCIndicators`,
  `IntelliSellTransaction.RequestType` from service settings.
- `PseudoCityCode` from the Java ORBiS auth response -> `POS.Source[0]`. It is
  **mandatory**: a sessionless (OAuth) shop call has no session PCC, and Sabre
  answers `400 ... Unable to determine PseudoCityCode` without it. A missing
  PCC fails fast with `422 transformation_failed` instead of being dropped.
- Deliberately **not** mapped (no confirmed Sabre mapping): `cabin`, `currency`,
  `refundable`, `filters.airlines`, and `filters.stops.max`. Sabre's BFM v5 JSON
  schema rejects additional properties, and `MaxConnections` is not defined under
  `TravelPreferences.TPA_Extensions`:
  `JSON_ADAPTER: ... property 'MaxConnections' is not defined in the schema`.
  Do not re-add it without a schema-confirmed replacement.
- Credentials (username/password) never enter the body - auth rides the
  `Authorization` header.

## Tests

```powershell
.venv\Scripts\python -m pytest tests -q
```

## Adding a new provider

1. Subclass `RequestTransformer` / `ResponseTransformer` from
   `app/transformers/base.py`.
2. Create `app/transformers/<provider>.py` with a `TransformerPair`.
3. Register the pair in `PROVIDER_TRANSFORMERS` in `app/transformers/router.py`.
4. Add a `<provider>_client.py` and tests under `tests/`.