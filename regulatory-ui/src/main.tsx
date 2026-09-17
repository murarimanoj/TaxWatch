import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Authority, Document, DocumentDetail, DocumentPage, Overview, request, safeUrl } from './api';
import '@fontsource-variable/inter';
import './styles.css';
import { ChatPanel } from './ChatPanel';
import { IntelligencePanel } from './IntelligencePanel';

const names: Record<Authority, string> = { rbi: 'Reserve Bank of India', cbdt: 'Central Board of Direct Taxes', sebi: 'Securities and Exchange Board of India', gst: 'GST Council', mca: 'Ministry of Corporate Affairs' };
const date = (value: string | null) => value ? new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(value)) : 'Date not available';
type View = { kind: 'overview' } | { kind: 'list'; source: Authority } | { kind: 'detail'; document: Document };

function App() {
  const dashboard = useRef<HTMLElement>(null);
  useEffect(() => {
    const element = dashboard.current;
    if (!element) return;
    const measure = () => {
      const rect = element.getBoundingClientRect();
      const css = getComputedStyle(element);
      const columns = Number(css.getPropertyValue('--tile-columns')) || 1;
      const gap = parseFloat(css.getPropertyValue('--tile-gap')) || 20;
      const left = parseFloat(css.paddingLeft);
      const right = parseFloat(css.paddingRight);
      const tileWidth = (rect.width - left - right - (columns - 1) * gap) / columns;
      const edgeSpace = document.documentElement.clientWidth - rect.right + right;
      element.style.setProperty('--chat-tile-width', `${tileWidth + edgeSpace}px`);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    window.addEventListener('resize', measure);
    measure();
    return () => { observer.disconnect(); window.removeEventListener('resize', measure); };
  }, []);
  const [overviewYear, setOverviewYear] = useState<number | null>(null);
  const [availableYears, setAvailableYears] = useState<number[]>([]);
  const [yearError, setYearError] = useState(false);
  const [chatYear, setChatYear] = useState<number | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatSource, setChatSource] = useState<Authority | null>(null);
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
    setYearError(false);
    request<number[]>('/publication-years', controller.signal)
      .then(value => { if (!controller.signal.aborted) setAvailableYears(value); })
      .catch(() => { if (!controller.signal.aborted) setYearError(true); });
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    const load = async () => {
      try {
        if (view.kind === 'overview') setOverview(await request<Overview>(`/overview${overviewYear ? `?year=${overviewYear}` : ''}`, controller.signal));
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
  }, [view, query, year, page, refresh, overviewYear]);

  function openSource(source: Authority) {
    setQuery(''); setDraft(''); setYear(overviewYear ? String(overviewYear) : ''); setPage(1); setView({ kind: 'list', source });
  }
  function openChat(source: Authority | null) {
    setChatSource(source);
    setChatYear(view.kind === 'overview' ? overviewYear : year ? Number(year) : null);
    setChatOpen(true);
  }
  const groups = new Map<string, Document[]>();
  for (const item of listing?.items || []) {
    const key = item.published_date?.slice(0, 4) || 'Undated';
    groups.set(key, [...(groups.get(key) || []), item]);
  }

  return <main ref={dashboard}>
    <header><div><div className="brand">Tax Watch</div><h1>Notification Dashboard</h1></div>
      <nav aria-label="Main navigation"><button className={view.kind === 'overview' ? 'active' : ''} onClick={() => setView({ kind: 'overview' })}>Overview</button><button disabled={loading} onClick={() => setRefresh(value => value + 1)}>↻ Refresh</button><button className="chat-launch" onClick={() => openChat(view.kind === 'overview' ? null : view.kind === 'list' ? view.source : view.document.source)}>✦ Ask a question</button></nav>
    </header>
    {view.kind !== 'overview' && <button className="back" onClick={() => view.kind === 'detail' ? setView({ kind: 'list', source: view.document.source }) : setView({ kind: 'overview' })}>← Back to {view.kind === 'detail' ? 'publications' : 'overview'}</button>}
    {view.kind === 'overview' && <div className="overview-toolbar"><div className="overview-summary"><h2>Regulatory sources</h2><span>{!loading && !error && overview ? `${overview.total.toLocaleString()} publications across ${overview.authorities.length} authorities${overviewYear ? ` · ${overviewYear}` : ''}` : 'RBI · CBDT · SEBI · GST · MCA'}</span></div><fieldset className="home-year-filter"><legend className="sr-only">Publication year</legend><div><button aria-pressed={overviewYear === null} onClick={() => setOverviewYear(null)}>All years</button>{availableYears.map(value => <button key={value} aria-pressed={overviewYear === value} onClick={() => setOverviewYear(value)}>{value}</button>)}</div>{yearError && <small role="status">Years unavailable. <button onClick={() => setRefresh(value => value + 1)}>Retry</button></small>}</fieldset></div>}
    {view.kind === 'list' && <><div className="source-heading"><h2 className="source-title">{names[view.source]}</h2><button className="chat-launch" onClick={() => openChat(view.source)}>✦ Ask within this source</button></div><form className="filters" onSubmit={event => { event.preventDefault(); setPage(1); setQuery(draft.trim()); const data = new FormData(event.currentTarget); setYear(String(data.get('year') || '')); }}>
      <label>Search publications<input value={draft} onChange={event => setDraft(event.target.value)} placeholder="Search by title" maxLength={200} /></label>
      <label>Publication year<input name="year" type="number" min="1900" max="9998" placeholder="All years" defaultValue={year} /></label>
      <button className="primary" type="submit">Search</button>
    </form></>}
    {loading && <div className="message" role="status">Loading publications…</div>}
    {!loading && error && <div className="message error" role="alert"><h2>Couldn’t load the dashboard</h2><p>{error}</p><button onClick={() => setRefresh(value => value + 1)}>Try again</button></div>}
    {!loading && !error && view.kind === 'overview' && overview && <div className="source-grid">{overview.authorities.map(item => <article className="source-card" key={item.source}>
      <div className="tile-heading"><h2><button className="source-card-open" onClick={() => openSource(item.source)}>{names[item.source]}</button></h2><span aria-hidden="true">↗</span></div><div className="counts"><span className="monogram">{item.source.toUpperCase()}</span><span className="badge">{item.latest_year ? `Latest ${item.latest_year}` : 'No dated publications'}</span><span>{item.total.toLocaleString()} total</span></div>
      <div className="previews">{item.preview.length ? item.preview.slice(0, 3).map(doc => <div key={doc.document_hash}><p><time dateTime={doc.published_date || undefined}>{doc.published_date ? doc.published_date.slice(0, 10) : 'Undated'}</time><span className="notification-separator"> — </span>{doc.title}</p><div className="notification-excerpt">{doc.excerpt?.replace(/\s+/g, ' ').trim() || 'Open the publication to read the details.'}</div></div>) : <p>{overviewYear ? `No publications in ${overviewYear}.` : 'No publications ingested yet.'}</p>}</div>
      <div className="source-card-footer"><div className={`run-status ${item.last_run_status === 'failed' ? 'failed' : ''}`}>{item.last_checked_at ? `${item.last_run_status === 'failed' ? 'Last ingestion had failures' : 'Last checked'} · ${date(item.last_checked_at)}` : 'No ingestion runs recorded'}</div><button className="tile-ask" aria-label={`Ask Q&A about ${names[item.source]}`} onClick={() => openChat(item.source)}>✦ Ask Q&amp;A</button></div>
    </article>)}</div>}
    {!loading && !error && view.kind === 'list' && listing && <>
      <p className="results-count" role="status">{listing.total.toLocaleString()} matching publications</p>
      {listing.items.length === 0 && <div className="message"><h2>No publications found</h2><p>Try another title or year, or check back after the next ingestion run.</p></div>}
      {[...groups].map(([group, docs]) => <section key={group} className="year-group"><h3>{group}</h3>{docs.map(doc => <button className="document-card" key={doc.document_hash} onClick={() => setView({ kind: 'detail', document: doc })}><div className="document-meta"><span>{doc.document_type.replaceAll('_', ' ')}</span><time>{date(doc.published_date)}</time></div><h4>{doc.title}</h4><p>{doc.excerpt || 'Open to read this publication.'}</p><span className="read">Read publication →</span></button>)}</section>)}
      {listing.total > 0 && <nav className="pagination" aria-label="Publication pages"><button disabled={page === 1} onClick={() => setPage(value => value - 1)}>← Previous</button><span>Page {page} of {Math.ceil(listing.total / listing.page_size)}</span><button disabled={page * listing.page_size >= listing.total} onClick={() => setPage(value => value + 1)}>Next →</button></nav>}
    </>}
    {!loading && !error && view.kind === 'detail' && detail && <div className="notification-detail-layout"><article className="detail"><div className="document-meta"><span>{names[detail.source]} · {detail.document_type.replaceAll('_', ' ')}</span><time>{date(detail.published_date)}</time></div><h2>{detail.title}</h2><div className="links"><button className="chat-launch" onClick={() => openChat(detail.source)}>✦ Ask within this source</button>{safeUrl(detail.detail_url) && <a href={safeUrl(detail.detail_url)} target="_blank" rel="noopener noreferrer">View official publication ↗</a>}{detail.attachment_url && safeUrl(detail.attachment_url) && <a href={safeUrl(detail.attachment_url)} target="_blank" rel="noopener noreferrer">Open attachment ↗</a>}</div><div className="content">{detail.content || 'No extracted text is available. Open the official publication to read the original.'}</div><footer>Retrieved {date(detail.fetched_at)}</footer></article><IntelligencePanel key={`${detail.source}:${detail.document_hash}`} source={detail.source} documentHash={detail.document_hash} /></div>}
    <ChatPanel open={chatOpen} initialSource={chatSource} initialYear={chatYear} names={names} onClose={() => setChatOpen(false)} />
    <footer className="page-footer">TaxWatch <span>Regulatory publication monitor</span></footer>
  </main>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
