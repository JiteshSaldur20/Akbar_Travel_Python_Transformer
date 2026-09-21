# Provider Transformer - Sabre BFM / NDC Connector

High-performance FastAPI service running on Python 3.12 that adapts Java ORBiS search requests into Sabre Bargain Finder Max (BFM v5 REST) shop requests and parses ITA shopping responses into the canonical unified format.

JSON is handled with extreme efficiency via **msgspec** and connection pooling via **httpx**.

---

## 1. Project Structure

```
Transformer/
├── Dockerfile                 # Python 3.12-slim container
├── docker-compose.yml         # Standalone compose file
├── .dockerignore
├── .env.example
├── requirements.txt
├── app/
│   ├── main.py                # FastAPI entry point & connection pool management
│   ├── api/
│   │   ├── search.py          # POST /api/flights/search (Java ORBiS -> Sabre BFM)
│   │   └── transform.py       # Standalone transform endpoints & GET /health
│   ├── connectors/
│   │   ├── sabre_client.py    # Sabre BFM HTTP client with connection pooling
│   │   └── java_auth_client.py# Client to fetch cached bearer tokens & PCC from Java DB
│   ├── transformers/
│   │   ├── sabre.py           # Sabre TransformerPair
│   │   ├── request_transformer.py
│   │   └── response_transformer.py
│   ├── schemas/               # Msgspec schemas
│   └── core/                  # Configuration & exception handlers
└── tests/
```

---

## 2. Running with Docker

```bash
# Build and run container
docker compose up -d --build

# Check logs
docker compose logs -f

# Check health
curl http://localhost:8090/health
```

---

## 3. Running Locally (Virtual Environment)

```bash
# Create venv and install dependencies
python -m venv env
env/Scripts/pip install -r requirements.txt      # Windows
# source env/bin/activate && pip install -r requirements.txt  # Linux/macOS

# Run with uvicorn on port 8090
env/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8090 --reload
```

---

## 4. Configuration

Copy `.env.example` to `.env` or set environment variables:

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `JAVA_INTERNAL_URL` | `http://localhost:8080` | Java backend URL for token & PCC lookup |
| `SABRE_BASE_URL` | `https://api.cert.platform.sabre.com` | Sabre API endpoint |
| `SABRE_SEARCH_PATH` | `v5/offers/shop` | BFM REST endpoint path |
| `SEARCH_ENABLE_ATPCO`| `true` | Enable ATPCO public/private fares |
| `SEARCH_ENABLE_NDC`  | `false`| Enable NDC airline content |
| `SEARCH_NUMBER_OF_TRIPS` | `10` | BFM number of trip options |
| `SEARCH_REQUEST_TYPE`| `50ITINS` | Sabre BFM IntelliSell bucket |
| `SEARCH_TIMING_LOG`  | `true` | Log latency stage breakdowns |

---

## 5. Key Endpoints

- `POST /api/flights/search`: Primary flight search endpoint called by Java ORBiS.
- `GET /health`: Healthcheck endpoint reporting status and registered providers.
- `GET /docs`: Interactive Swagger API documentation.