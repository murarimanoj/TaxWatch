import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Authority, Document, DocumentDetail, DocumentPage, Overview, request, safeUrl } from './api';
import './styles.css';

const names: Record<Authority, string> = { rbi: 'Reserve Bank of India', cbdt: 'Central Board of Direct Taxes', sebi: 'Securities and Exchange Board of India', gst: 'GST Council' };
const date = (value: string | null) => value ? new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(value)) : 'Date not available';
type View = { kind: 'overview' } | { kind: 'list'; source: Authority } | { kind: 'detail'; document: Document };

function App() {
  const [view, setView] = useState<View>({ kind: 'overview' });
  const [overview, setOverview] = useState<Overview | null>(null);
  const [listing, setListing] = useState<DocumentPage | null>(null);
  const [detail, setDetail] = useState<DocumentDetail | null>(null);
  const [query, setQuery] = useState('');
  const [draft, setDraft] = useState('');
  const [year, setYear] = useState('');
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    const load = async () => {
      try {
        if (view.kind === 'overview') setOverview(await request<Overview>('/overview', controller.signal));
        if (view.kind === 'list') {
          const params = new URLSearchParams({ source: view.source, q: query, page: String(page), page_size: '20' });
          if (year) params.set('year', year);
          setListing(await request<DocumentPage>(`/documents?${params}`, controller.signal));
        }
        if (view.kind === 'detail') setDetail(await request<DocumentDetail>(`/documents/${view.document.source}/${encodeURIComponent(view.document.document_hash)}`, controller.signal));
      } catch (err) {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load publications.');
      } finally { if (!controller.signal.aborted) setLoading(false); }
    };
    void load();
    return () => controller.abort();
  }, [view, query, year, page, refresh]);

  function openSource(source: Authority) {
    setQuery(''); setDraft(''); setYear(''); setPage(1); setView({ kind: 'list', source });
  }
  const groups = new Map<string, Document[]>();
  for (const item of listing?.items || []) {
    const key = item.published_date?.slice(0, 4) || 'Undated';
    groups.set(key, [...(groups.get(key) || []), item]);
  }

  return <main>
    <header><div><div className="brand">Tax Watch</div><h1>Notification Dashboard</h1></div>
      <nav aria-label="Main navigation"><button className={view.kind === 'overview' ? 'active' : ''} onClick={() => setView({ kind: 'overview' })}>Overview</button><button disabled={loading} onClick={() => setRefresh(value => value + 1)}>↻ Refresh</button></nav>
    </header>
    {view.kind !== 'overview' && <button className="back" onClick={() => view.kind === 'detail' ? setView({ kind: 'list', source: view.document.source }) : setView({ kind: 'overview' })}>← Back to {view.kind === 'detail' ? 'publications' : 'overview'}</button>}
    {view.kind === 'overview' && <div className="section-heading"><h2>Regulatory sources</h2><span>{!loading && !error && overview ? `${overview.total.toLocaleString()} publications across ${overview.authorities.length} authorities` : 'RBI · CBDT · SEBI · GST'}</span></div>}
    {view.kind === 'list' && <><h2 className="source-title">{names[view.source]}</h2><form className="filters" onSubmit={event => { event.preventDefault(); setPage(1); setQuery(draft.trim()); const data = new FormData(event.currentTarget); setYear(String(data.get('year') || '')); }}>
      <label>Search publications<input value={draft} onChange={event => setDraft(event.target.value)} placeholder="Search by title" maxLength={200} /></label>
      <label>Publication year<input name="year" type="number" min="1900" max="9998" placeholder="All years" defaultValue={year} /></label>
      <button className="primary" type="submit">Search</button>
    </form></>}
    {loading && <div className="message" role="status">Loading publications…</div>}
    {!loading && error && <div className="message error" role="alert"><h2>Couldn’t load the dashboard</h2><p>{error}</p><button onClick={() => setRefresh(value => value + 1)}>Try again</button></div>}
    {!loading && !error && view.kind === 'overview' && overview && <div className="source-grid">{overview.authorities.map(item => <button className="source-card" key={item.source} onClick={() => openSource(item.source)}>
      <div className="card-top"><span className="monogram">{item.source.toUpperCase()}</span><span aria-hidden="true">↗</span></div>
      <h2>{names[item.source]}</h2><div className="counts"><span className="badge">{item.latest_year ? `Latest ${item.latest_year}` : 'No dated publications'}</span><span>{item.total.toLocaleString()} total</span></div>
      <div className="previews">{item.preview.length ? item.preview.map(doc => <div key={doc.document_hash}><time>{date(doc.published_date)}</time><p>{doc.title}</p></div>) : <p>No publications ingested yet.</p>}</div>
      <div className={`run-status ${item.last_run_status === 'failed' ? 'failed' : ''}`}>{item.last_checked_at ? `${item.last_run_status === 'failed' ? 'Last ingestion had failures' : 'Last checked'} · ${date(item.last_checked_at)}` : 'No ingestion runs recorded'}</div>
    </button>)}</div>}
    {!loading && !error && view.kind === 'list' && listing && <>
      <p className="results-count" role="status">{listing.total.toLocaleString()} matching publications</p>
      {listing.items.length === 0 && <div className="message"><h2>No publications found</h2><p>Try another title or year, or check back after the next ingestion run.</p></div>}
      {[...groups].map(([group, docs]) => <section key={group} className="year-group"><h3>{group}</h3>{docs.map(doc => <button className="document-card" key={doc.document_hash} onClick={() => setView({ kind: 'detail', document: doc })}><div className="document-meta"><span>{doc.document_type.replaceAll('_', ' ')}</span><time>{date(doc.published_date)}</time></div><h4>{doc.title}</h4><p>{doc.excerpt || 'Open to read this publication.'}</p><span className="read">Read publication →</span></button>)}</section>)}
      {listing.total > 0 && <nav className="pagination" aria-label="Publication pages"><button disabled={page === 1} onClick={() => setPage(value => value - 1)}>← Previous</button><span>Page {page} of {Math.ceil(listing.total / listing.page_size)}</span><button disabled={page * listing.page_size >= listing.total} onClick={() => setPage(value => value + 1)}>Next →</button></nav>}
    </>}
    {!loading && !error && view.kind === 'detail' && detail && <article className="detail"><div className="document-meta"><span>{names[detail.source]} · {detail.document_type.replaceAll('_', ' ')}</span><time>{date(detail.published_date)}</time></div><h2>{detail.title}</h2><div className="links">{safeUrl(detail.detail_url) && <a href={safeUrl(detail.detail_url)} target="_blank" rel="noopener noreferrer">View official publication ↗</a>}{detail.attachment_url && safeUrl(detail.attachment_url) && <a href={safeUrl(detail.attachment_url)} target="_blank" rel="noopener noreferrer">Open attachment ↗</a>}</div><div className="content">{detail.content || 'No extracted text is available. Open the official publication to read the original.'}</div><footer>Retrieved {date(detail.fetched_at)}</footer></article>}
    <footer className="page-footer">TaxWatch <span>Regulatory publication monitor</span></footer>
  </main>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
