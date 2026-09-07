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
failure states are supported. Alerts and AI Q&A from the supplied design are
deferred until corresponding backend services exist.

## Production build

```bash
npm run build
```

Serve `dist/` and reverse-proxy `/api` to the API service, or set
`VITE_API_BASE_URL` to the API origin before building and configure the API's
`API_CORS_ORIGINS`. Vite's development proxy is not included in production output.
Never put MongoDB credentials in frontend environment variables.
