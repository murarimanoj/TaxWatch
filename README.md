# TaxWatch

TaxWatch collects regulatory publications from Indian authorities and presents
them through a searchable web dashboard. The workspace is split into three
independent projects connected through MongoDB and an HTTP API.

## Architecture

```text
Regulatory websites
        |
        v
regulatory-ingestion ---> MongoDB ---> regulatory-api ---> regulatory-ui
  discover, download,       store       query and serve      search and read
  extract, normalize                    JSON over HTTP        in the browser
```

| Project | Technology | Responsibility |
|---|---|---|
| [`regulatory-ingestion`](regulatory-ingestion/) | Python | Discovers RBI, CBDT, SEBI, GST Council, and MCA publications; extracts content and stores normalized records |
| [`regulatory-api`](regulatory-api/) | FastAPI | Provides read-only overview, search, pagination, and document-detail endpoints backed by MongoDB |
| [`regulatory-ui`](regulatory-ui/) | React, TypeScript, Vite | Displays regulator summaries, searchable document lists, and extracted publication content |

Each project owns its dependencies and runtime configuration. Ingestion and the
API must use the same MongoDB database. The browser UI communicates only with the
API and must never receive MongoDB credentials.

## Data flow

1. Ingestion reads regulator pages from
   `regulatory-ingestion/config/sources.toml`.
2. Source-specific adapters discover publications and their official document
   links.
3. The pipeline downloads HTML or PDF content, extracts text, and calculates:
   - `document_hash` from source, publication date, and normalized title;
   - `content_hash` from the downloaded bytes.
4. MongoDB stores source configuration, normalized documents, and ingestion-run
   history.
5. The API reads those collections and exposes them under `/api`.
6. The UI queries the API and renders searchable publication data.

## Prerequisites

- Python 3.11 or newer
- Node.js 22.12 or newer
- MongoDB Atlas or another compatible MongoDB deployment
- Google Chrome for Playwright-backed sources such as CBDT and MCA

## Local setup

Run commands from the `TaxWatch` workspace directory. Each Python service uses
its own `.venv`.

### 1. Install ingestion

```bash
cd regulatory-ingestion
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
```

Set `MONGODB_URI` and `MONGODB_DATABASE` in
`regulatory-ingestion/.env`, then initialize MongoDB and optionally ingest a
small sample:

```bash
.venv/bin/reg-ingest init-db
.venv/bin/reg-ingest run-all --limit 5
cd ..
```

Individual sources can be run independently, for example:

```bash
cd regulatory-ingestion
.venv/bin/reg-ingest run rbi --limit 10
.venv/bin/reg-ingest run cbdt --document-type circular --limit 10
cd ..
```

### 2. Start the API

```bash
cd regulatory-api
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
.venv/bin/uvicorn regulatory_api.app:create_app \
  --factory --reload --host 127.0.0.1 --port 8000
```

Configure `regulatory-api/.env` with the same `MONGODB_URI` and
`MONGODB_DATABASE` used by ingestion. API documentation is available at
<http://127.0.0.1:8000/docs>.

### 3. Start the UI

In another terminal:

```bash
cd regulatory-ui
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. During development, Vite proxies `/api` requests to
`http://127.0.0.1:8000` by default.

## Common operations

Initialize indexes and synchronize configured regulatory sources:

```bash
cd regulatory-ingestion
.venv/bin/reg-ingest init-db
```

Run all configured sources:

```bash
.venv/bin/reg-ingest run-all --limit 25
```

Build the production UI:

```bash
cd ../regulatory-ui
npm run build
```

## Validation

```bash
cd regulatory-ingestion
.venv/bin/ruff check src tests
.venv/bin/pytest

cd ../regulatory-api
.venv/bin/ruff check src tests
.venv/bin/black --check src tests
.venv/bin/pytest

cd ../regulatory-ui
npm run build
```

## Configuration and security

- Keep `.env` files local and out of version control.
- Never expose `MONGODB_URI` through frontend variables.
- Use the same database name for ingestion and the API.
- Configure `API_CORS_ORIGINS` when the UI and API use different origins.
- Set `VITE_API_BASE_URL` before a production UI build when `/api` is not served
  through a same-origin reverse proxy.
- Run `reg-ingest init-db` during initial deployment and whenever source
  configuration changes.
- Add API or gateway authentication before exposing non-public data.

## Detailed documentation

- [Ingestion setup, sources, and CLI](regulatory-ingestion/README.md)
- [API setup and endpoints](regulatory-api/README.md)
- [UI setup and production build](regulatory-ui/README.md)

