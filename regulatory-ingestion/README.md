# TaxWatch regulatory ingestion

Batch ingestion service for regulatory publications from RBI, CBDT, SEBI, the GST
Council, and MCA. It discovers publications, downloads HTML or PDF content, extracts
text, calculates document and content hashes, and stores normalized records in
MongoDB.

Each regulator can run independently. Jobs can be scheduled through cron,
Kubernetes CronJobs, or Airflow without changing the ingestion pipeline.

## TaxWatch workspace

TaxWatch consists of three sibling projects, each with its own dependencies and
run commands:

```text
TaxWatch/
├── regulatory-ingestion/
├── regulatory-api/
└── regulatory-ui/
```

| Project | Responsibility | Setup |
|---|---|---|
| `regulatory-ingestion` | Discover, extract, and store publications; maintain indexes and ingestion history | This README |
| `regulatory-api` | Read ingested data from MongoDB and expose HTTP endpoints | [API README](../regulatory-api/README.md) |
| `regulatory-ui` | Display regulator summaries and searchable publications | [UI README](../regulatory-ui/README.md) |

```text
regulatory-ingestion → MongoDB → regulatory-api → regulatory-ui
```

Ingestion runs independently of the API and UI. Configure ingestion and the API
to use the same `MONGODB_URI` and `MONGODB_DATABASE`. Each Python project has its
own `.venv` and loads its own `.env` from its working directory. The UI accesses
data through the API.

## Architecture

```text
Manual command / future scheduler
              |
              v
       Source configuration
       config/sources.toml
              |
              v
      RBI | CBDT | SEBI | GST | MCA adapter
        discover publications
              |
              v
        Ingestion pipeline
 download -> extract -> hash -> normalize
              |
              v
       MongoDB repository
 sources | documents | run history
```

Main responsibilities:

- `adapters/`: regulator-specific discovery and HTML parsing.
- `pipeline.py`: source-independent orchestration and per-document error isolation.
- `extraction.py`: PDF and HTML text extraction.
- `repository.py`: MongoDB indexes, source synchronization, and idempotent writes.
- `jobs.py`: independent regulator jobs for future schedulers.
- `config/sources.toml`: source URLs and document categories.

## Project structure

```text
regulatory-ingestion/
├── config/sources.toml
├── src/regulatory_ingestion/
│   ├── adapters/{base,cbdt,gst,rbi,sebi}.py
│   ├── browser.py
│   ├── cli.py
│   ├── config.py
│   ├── domain.py
│   ├── extraction.py
│   ├── hashing.py
│   ├── http.py
│   ├── jobs.py
│   ├── pipeline.py
│   ├── ports.py
│   ├── registry.py
│   └── repository.py
├── tests/
├── .env.example
└── pyproject.toml
```

## Requirements and installation

- Python 3.11 or newer
- MongoDB Atlas or a compatible MongoDB deployment
- Network access to the configured regulator websites

Run the following from the `TaxWatch` workspace. If `.venv` already exists, reuse
it and skip the environment creation command:

```bash
cd regulatory-ingestion
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

All remaining commands in this README run from `regulatory-ingestion/` with its
`.venv` activated. The API and UI dependencies are installed in their own projects.

## Environment configuration

Create a local configuration file if you do not already have one:

```bash
cp .env.example .env
```

Replace the example MongoDB connection URI with your connection details. Settings
load automatically from `.env`; exported environment variables take precedence.

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `MONGODB_URI` | For database writes | None | MongoDB connection URI |
| `MONGODB_DATABASE` | No | `taxwatch` | Target database |
| `SOURCE_CONFIG_PATH` | No | `config/sources.toml` | Source catalog path |
| `HTTP_TIMEOUT_SECONDS` | No | `30` | Request timeout |
| `HTTP_USER_AGENT` | No | TaxWatch agent name | HTTP user-agent header |
| `MAX_ITEMS_PER_RUN` | No | `25` | Default discovery limit |
| `PLAYWRIGHT_HEADLESS` | No | `false` | CBDT blocks headless execution; headed Chrome is the working default |
| `PLAYWRIGHT_CHANNEL` | No | `chrome` | Installed browser channel used by Playwright |
| `PLAYWRIGHT_POPUP_TIMEOUT_SECONDS` | No | `90` | Maximum wait for a browser-driven document link to open |
| `PLAYWRIGHT_USER_AGENT` | No | Chrome-compatible value | User-agent used by browser-backed sources |

Alternatively, load credentials from an external file (replace the path below):

```bash
set -a
source /path/to/atlas-credentials.env
set +a
export MONGODB_DATABASE=taxwatch
```

The local `.env` file is excluded by `.gitignore`. Keep other credential files
outside the repository.

## Source configuration

Runtime URLs are defined in `config/sources.toml`, not in the adapters:

```toml
[sources.cbdt]
adapter = "cbdt"

[[sources.cbdt.pages]]
url = "https://www.incometaxindia.gov.in/notifications"
document_type = "notification"
transport = "playwright"

[[sources.cbdt.pages]]
url = "https://www.incometaxindia.gov.in/circulars"
document_type = "circular"
transport = "playwright"
```

Add another page for a supported regulator with a `[[sources.<name>.pages]]`
entry. `transport` defaults to `http`; set it to `playwright` for a JavaScript or
WAF-sensitive listing. Use `timeout_seconds` when a specific page needs a longer
timeout than `HTTP_TIMEOUT_SECONDS`. No pipeline change is required.

## Database initialization

Create indexes and synchronize configured sources without scraping:

```bash
reg-ingest init-db
```

This upserts one `regulatory_sources` record for every configured page. Entries
removed from `sources.toml` are preserved for audit history and marked
`enabled: false`.

## Running ingestion

Run one regulator independently:

```bash
reg-ingest run rbi --limit 10
reg-ingest run cbdt --limit 10
reg-ingest run sebi --limit 10
reg-ingest run gst --limit 10
reg-ingest run mca --limit 10
```

When a source has multiple configured pages, omit `--document-type` to process all
of them. The limit applies separately to each page. Select one configured document
type with `--document-type` (or `-t`):

```bash
reg-ingest run cbdt --document-type circular --limit 10
reg-ingest run cbdt -t notification --limit 10
```

Run all regulators sequentially:

```bash
reg-ingest run-all --limit 10
```

Exercise discovery and extraction without MongoDB writes:

```bash
reg-ingest run rbi --limit 3 --dry-run
```

List supported identifiers:

```bash
reg-ingest sources
```

Commands return JSON containing `discovered`, `inserted`, `updated`, `unchanged`,
`failed`, and per-item errors. A run exits with status `1` when discovery or a
document operation fails.

## MongoDB collections

The current MVP uses three collections:

| Collection | Purpose | Creation behavior |
|---|---|---|
| `regulatory_sources` | Authorities, categories, adapters, URLs, and latest run state | Created by `init-db` or a real run |
| `regulatory_documents` | Normalized metadata and extracted content | Index created by `init-db`; records written during ingestion |
| `ingestion_runs` | Run totals and errors | Collection and indexes created by `init-db`; records written after real runs |

Example `regulatory_sources` record:

```json
{
  "source_id": "source_cbdt_notification",
  "authority": "cbdt",
  "category": "notification",
  "adapter": "cbdt",
  "discovery_url": "https://www.incometaxindia.gov.in/notifications",
  "enabled": true,
  "created_at": "...",
  "updated_at": "...",
  "last_checked_at": "...",
  "last_run_status": "succeeded"
}
```

Example `regulatory_documents` record:

```json
{
  "source": "rbi",
  "title": "Publication title",
  "published_date": "...",
  "document_type": "notification",
  "detail_url": "https://...",
  "attachment_url": "https://...",
  "content": "Extracted text...",
  "content_type": "application/pdf",
  "document_hash": "sha256-of-source-date-title...",
  "content_hash": "sha256...",
  "fetched_at": "...",
  "created_at": "...",
  "updated_at": "..."
}
```

Documents use a unique `(source, document_hash)` index. `document_hash` is calculated
from the source, published date, and normalized title. The separate `content_hash`
records the SHA-256 hash of the downloaded bytes for future content-change
detection. Re-running a source inserts, updates, or leaves a record unchanged instead
of creating duplicates.

## Testing and linting

Ingestion tests use local HTML fixtures and mocked collaborators; they do not
require live regulator sites or MongoDB:

```bash
.venv/bin/pytest tests
.venv/bin/ruff check src tests
```

API tests and UI build instructions are maintained in their respective READMEs.

## CBDT browser transport

The CBDT `/notifications` and `/circulars` endpoints reject direct HTTP clients and
headless Chrome with HTTP 403. Both pages therefore use a reusable, headed installed
Chrome session with `playwright-stealth`. RBI and SEBI continue to use the
lower-overhead HTTP transport.

Install the matching Chromium runtime after installing Python dependencies:

```bash
playwright install chromium
```

CBDT ingestion therefore requires a graphical desktop session. Stealth reduces basic
automation signals but cannot guarantee access if CBDT changes its security policy.
Browser failures remain visible in the ingestion-run error summary.

## Planned extensions

- Store original PDFs in S3 or Azure Blob Storage and add `document_artifacts`.
- Add OCR for scanned PDFs behind `ContentExtractor`.
- Add chunking and Atlas Vector Search in `document_chunks`.
- Add `regulatory_provisions` and `document_relationships`.
- Add tax-intelligence, relationship-analysis, and processing-run stages.
- Add timed scheduling around the existing independent source commands.
