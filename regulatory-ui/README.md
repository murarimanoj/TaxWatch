# TaxWatch regulatory dashboard

React + TypeScript dashboard using the sibling `../regulatory-api` service.
Run these commands from `regulatory-ui/` with Node.js 22.12 or newer:

```bash
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. Start the API separately using
[its setup instructions](../regulatory-api/README.md). Development requests to
`/api` are proxied to `http://127.0.0.1:8000`; override `API_PROXY_TARGET` in
`.env.local` if needed.

The dashboard includes authority cards, title search, year filtering, pagination,
extracted document text, and official publication links. Loading, empty, and API
failure states are supported. Alerts remain deferred. The Q&A panel uses the API chat service described below.

## Production build

```bash
npm run build
```

Serve `dist/` and reverse-proxy `/api` to the API service, or set
`VITE_API_BASE_URL` to the API origin before building and configure the API's
`API_CORS_ORIGINS`. Vite's development proxy is not included in production output.
Never put MongoDB credentials in frontend environment variables.


## Ask TaxWatch

Click **Ask a question** to open the right-hand Q&A panel. From the overview it
starts with **All sources**; from a regulator or publication it starts with that
regulator. Use the **Search within** buttons to choose CBDT, GST, MCA, RBI, SEBI, or all sources.
The **Publication year** buttons list years available for the selected source, plus **All years**. Each source/year combination has a separate conversation; switching filters does not mix histories.

Press Enter to send, or Shift+Enter for a new line. Answers include passage
references and expandable source excerpts with official publication links. The
panel supports loading, errors, retrying the retained question, clearing the
current conversation, and closing with Escape. On narrow screens it fills the
screen. Chat state stays in memory and resets when the page refreshes.

Configure `OPENAI_API_KEY` and optionally `CHAT_MODEL` on the **API server** using
[the API instructions](../regulatory-api/README.md#chat-and-agentic-retrieval).
No frontend LLM credentials are needed. The existing development proxy forwards
`POST /api/chat`; production deployments must proxy it as well. Allow sufficient
proxy time for a multi-step answer (up to approximately three minutes).

The interface renders answer text without raw HTML; citations only link to
HTTP(S) URLs returned by the API. The non-modal dialog supports Escape and restores focus to its launcher
when closed from within the panel, and messages have an accessible live log.


### Dashboard filters and contextual Q&A

The overview's upper-right publication-year buttons filter both source counts and
previews. These are calendar publication years, not financial years. Selecting a
source carries the year into its publication list. The tile's bottom-right **Ask
Q&A** button opens chat directly for that regulator and year; it appears on hover
or keyboard focus and remains visible on touch devices. Source lists and document
details also provide **Ask within this source**. All entry points preserve the
selected year when opening chat.

Typography uses locally bundled Inter (`@fontsource-variable/inter`), matching the
[design reference](https://tax-notification-dashboard.architect.space/). Source and
year controls in chat use compact wrapping buttons. The font needs no third-party
runtime request.


Chat now overlays exactly the rightmost tile column on desktop, with width derived
from the dashboard content container and the same column count/gap as the tile
grid. The other tiles stay undimmed and interactive. On mobile, chat remains
full-width. Tile notification text matches the reference's Inter regular (400),
13px size, and 1.45 line height.
