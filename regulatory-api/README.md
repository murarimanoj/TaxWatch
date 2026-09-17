# TaxWatch regulatory API

Read-only FastAPI service for the MongoDB collections populated by the sibling
`../regulatory-ingestion` project. Run these commands from `regulatory-api/`.

## Setup and run

Requires Python 3.11 or newer. Use this project's own virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Create a local `.env` with the settings below; never commit credentials.

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

## Phase 3: relationships, impact and alert drafts

The administrative batch CLI supports one **Relationship Agent** (references,
amendments, clarifications, supersessions and rescissions), followed by a **Tax
Intelligence Agent**. Results are source-quoted drafts for CA review. Document
details display stored intelligence without additional model calls.

```bash
.venv/bin/python -m regulatory_api.regulatory_intelligence.cli init-db
.venv/bin/python -m regulatory_api.regulatory_intelligence.cli analyze-document --source cbdt --document-hash DOCUMENT_HASH
```

Omit the hash to analyze all documents for a source; use `--source` on review for
a bulk decision (instead of `--analysis-id`):

```bash
.venv/bin/python -m regulatory_api.regulatory_intelligence.cli analyze-document --source cbdt
.venv/bin/python -m regulatory_api.regulatory_intelligence.cli review --source cbdt --reviewer CA_IDENTIFIER --decision approved
```

Both batches continue after per-record failures and print a final JSON summary
with completed IDs and failures. Analysis also reports skipped existing runs.
Any failures produce exit code 1. Review updates both impacts and relationships
transactionally; MongoDB Atlas or another transaction-capable replica set/sharded
cluster is required. Bulk review includes pending and same-decision records (to
repair previously pending relationships), but does not reverse opposite decisions.

Failed analysis runs store `error_type`, `error_message`, and `error_trace` in
`processing_runs`. Traces include chained exceptions but not stack-frame locals.
Configured credentials and common secret patterns are redacted; messages/traces
are capped at 8,000/32,000 characters plus a truncation marker. Diagnostics may
still contain source text from validation errors: keep this collection restricted
to operators. Successful retries clear the previous error fields. Failures before
a run is claimed, or while MongoDB is unavailable, cannot use this run-error path.

See the [step-by-step Phase 3 guide](../docs/phase3-design.md) for review, client
import, matching, private alert authentication and MVP limits. The CLI creates
Phase 3 indexes; the HTTP API remains read-only. Chat can now read approved
relationship context through the multi-agent flow described below.
Personalized alerts are stored drafts, not sent messages.

## Validation

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests
.venv/bin/black --check src tests
```

Tests mock database calls and do not need a live MongoDB connection. For a
cross-origin UI, configure `API_CORS_ORIGINS` as a JSON array of exact UI origins.
Add authentication at the API or gateway before exposing private ingestion data.


## Chat and agentic retrieval

### Multi-agent Q&A (default)

`POST /api/chat` now uses a conversation router, controlled retrieval, specialist
agents, independent evidence review and a constrained answer composer. One
Relationship Agent handles all relationship types. Current-position questions run
Relationship Analysis before Tax Intelligence; comparisons use a Comparison Agent.

See [chat architecture and configuration](../docs/chat-architecture.md) for the
step-by-step flow, routes, limits and diagnostics. The existing chat UI and response
format are retained. Optional `as_of: "YYYY-MM-DD"` is a legal-analysis date,
not the publication `year` filter.

```dotenv
CHAT_MULTI_AGENT_ENABLED=true
```

This defaults to true. Set it to false to restore the legacy loop documented below.
Normal requests use four model stages (five for current-position/comparison), with
a shared six-logical-call budget. Transport retries are separate. Chat is read-only
and does not query private client profiles or write analysis records.

### Shared setup and legacy retrieval loop

`POST /api/chat` answers questions against ingested publication text. Configure
these values in **regulatory-api/.env**, then restart the API:

```dotenv
OPENAI_API_KEY=your-api-key
CHAT_MODEL=gpt-4.1
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
VECTOR_INDEX_NAME=document_chunks_vector
# Temporarily enable safe, per-request troubleshooting logs:
CHAT_DIAGNOSTICS_ENABLED=true
```

The model is configurable and must support Responses API function calling. The API
uses LangChain’s `ChatOpenAI` integration with the Responses API; neither the key nor system prompt is sent to the
browser. `langchain-core`, `langchain-openai`, and the OpenAI SDK are runtime dependencies; refresh the environment after updating:

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn regulatory_api.app:create_app --factory --reload --host 127.0.0.1 --port 8000
```

Example request (use `source: null` to search across authorities):

```bash
curl http://127.0.0.1:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"What reporting requirements are described?","source":"mca","history":[]}'
```

The response contains `answer`, `citations`, `insufficient_evidence`, and `source`.
Each citation includes a server-assigned ID, regulator, document hash, title,
original URL, publication date, and retrieved passage. Optional `history` accepts
up to ten user/assistant messages. History helps interpret follow-ups but is not
considered evidence; each answer retrieves its own supporting passages.

Legacy-loop design and shared retrieval limits (`CHAT_MULTI_AGENT_ENABLED=false`):

- The server-owned system prompt is in `src/regulatory_api/chat.py`. It directs
  the model to search, refine queries, cite evidence, and explain uncertainty.
- LangChain binds `search_publications` and `finish` with strict schemas. The model chooses calls through `AIMessage.tool_calls`; results are returned as `ToolMessage` objects. A request
  has at most six model calls; only finishing is available on the final call.
- Retrieval embeds each search query and uses Atlas Vector Search over ingestion's
  `document_chunks`, then includes keyword-matched passages for exact-term coverage.
  Numbered queries such as `notification 116/2026` first look for that exact
  number in document titles, ahead of broader vector and keyword results.
  Source and publication-year filters are enforced in vector and keyword queries.
  Vector results are checked against the current document content hash so stale
  chunks cannot be cited. If query embedding or vector search is unavailable, the
  keyword search remains available. Global searches reserve passages by authority.
- Set `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` to the same values as ingestion.
  Run `reg-ingest embed-stored` for existing documents and
  `reg-ingest init-vector-index` in the ingestion project before expecting semantic
  results. Atlas builds the index asynchronously; until ready, chat uses keywords.
- Each search considers up to eight recent matching documents per authority and
  the first 120,000 characters per document. It returns at most ten passages
  globally or six for a selected authority. Broad historical/comprehensive
  questions can require narrower keywords and years; results are not exhaustive.
- Source scope is enforced in database filters and checked again before passages
  are passed to the model. Tools cannot widen a user-selected authority.
- Citation IDs and inline references must match retrieved evidence. This checks
  provenance, not whether every model interpretation is correct; users can expand
  passages and open originals to verify conclusions.
- Two simultaneous chat requests are allowed per API process. Additional requests
  return 429. Provider errors return 502; missing credentials return 503.
- Questions, recent conversation context, and selected passages are transmitted
  to OpenAI. Responses requests use `store=false`; this is not a guarantee of zero
  provider retention. Chat messages and prompts are not logged by this feature.
- With `CHAT_DIAGNOSTICS_ENABLED=true`, API logs include a short request ID, model
  tool choices, passage counts, citation-rejection categories, and budget exhaustion.
  They do not include question text, document passages, prompts, or credentials.
  Match `chat_trace id=...` lines for one request. `search_complete passages=0`
  means retrieval returned no evidence; `rejected_unknown_inline_citation` means
  the answer used a passage ID not returned by search; and
  `rejected_missing_inline_citations` means a supported answer omitted visible
  `[S1]` markers. A mismatched metadata citation list is reconciled from valid
  inline markers and logged as `citation_list_reconciled`. Turn diagnostics off
  after troubleshooting.
- Conversations stay in browser memory and are lost on refresh. There is no
  server-side chat persistence. Add authentication and per-user rate limits at
  the gateway before exposing this cost-bearing endpoint outside local use.

Tests in `tests/test_chat.py` mock both MongoDB and the LLM, so they require no
credentials and make no paid requests. Live answer quality still requires
validation against your own ingested corpus and configured model.

The tool loop follows the [OpenAI function-calling guide](https://developers.openai.com/api/docs/guides/function-calling).

Chat requests also accept optional `year` (1900–9998). The selected publication year is enforced by the server on every retrieval call. `GET /api/publication-years?source=mca` lists available years in descending order; omit `source` for all authorities.


### LangChain integration

`src/regulatory_api/llm.py` owns model configuration, LangChain `bind_tools` /
`invoke`, and provider error translation. `qa/` owns the default multi-agent flow;
`chat.py` owns shared retrieval and the legacy loop, server-enforced source/year
filters, and legacy citation checks. Legacy conversation context
uses LangChain `SystemMessage`, `HumanMessage`, `AIMessage`, and `ToolMessage`.
The full returned AI message is preserved for subsequent model calls, including
Responses API content blocks. Tool-call IDs link each result to its request.

The bounded loop remains explicit so the application controls when retrieval can
run and validates citations before returning a result. It does not use an
unrestricted AgentExecutor or add LangGraph persistence. The existing `/api/chat`
request/response format, UI, `OPENAI_API_KEY`, and `CHAT_MODEL` settings are unchanged.
Requests disable provider response storage and use a 30-second transport timeout
and 2,500 output-token limit. The transport retries temporary provider failures up
to three times; this is separate from the six-logical-call application budget.
LangSmith tracing is not enabled by this application; enabling it separately can
transmit prompts and retrieved content to a tracing service.

`tests/test_llm.py` runs the actual LangChain/OpenAI request serialization and
response parsing against an in-memory HTTP transport. No paid calls are made.
`tests/test_chat.py` uses LangChain AI messages to cover query refinement, tool
results, history, malformed calls, scope/year restrictions, citation validation,
and bounded fallback behavior. Existing endpoint and repository tests remain.

See [LangChain's ChatOpenAI documentation](https://docs.langchain.com/oss/python/integrations/chat/openai)
for the model integration used here.

`GET /api/overview?year=2026` filters publication counts and previews by calendar publication year. Omit `year` for all years. Last-ingestion status still describes the latest run, independently of the selected publication year.
