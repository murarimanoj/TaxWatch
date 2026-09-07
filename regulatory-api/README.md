# TaxWatch regulatory API

Read-only FastAPI service for the MongoDB collections populated by the sibling
`../regulatory-ingestion` project. Run these commands from `regulatory-api/`.

## Setup and run

Requires Python 3.11 or newer. Use this project's own virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
```

Set `MONGODB_URI` and `MONGODB_DATABASE` in `.env` to the same database used by
ingestion, or export them in your shell. Credentials are never sent to the UI.
The API reads its own `.env` from the working directory; it does not automatically
load the ingestion project's `.env`.

```bash
.venv/bin/uvicorn regulatory_api.app:create_app --factory --reload --host 127.0.0.1 --port 8000
```

Interactive API documentation: <http://127.0.0.1:8000/docs>.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Process liveness; does not check MongoDB |
| `GET /api/overview` | Authority counts, previews, and latest ingestion status |
| `GET /api/documents` | Paginated listing with `source`, `q` (literal title search), `year`, `page`, `page_size` |
| `GET /api/documents/{source}/{document_hash}` | Extracted text and original URLs |

The API does not run ingestion or modify database indexes. An unavailable or
unconfigured database returns HTTP 503. Populate data using the sibling ingestion
project's commands.

## Validation

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/black --check src tests
```

Tests mock database calls and do not need a live MongoDB connection. For a
cross-origin UI, configure `API_CORS_ORIGINS` as a JSON array of exact UI origins.
Add authentication at the API or gateway before exposing private ingestion data.
