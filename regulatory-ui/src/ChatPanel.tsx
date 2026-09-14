import { useEffect, useRef, useState } from 'react';
import { Authority, ChatAnswer, ChatMessage, askQuestion, safeUrl, request } from './api';
import './chat.css';

type Turn = { question: string; result: ChatAnswer };
type Props = { open: boolean; initialSource: Authority | null; initialYear: number | null; names: Record<Authority, string>; onClose: () => void };

export function ChatPanel({ open, initialSource, initialYear, names, onClose }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const launcher = useRef<HTMLElement | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const active = useRef<AbortController | null>(null);
  const [source, setSource] = useState<Authority | null>(initialSource);
  const [conversations, setConversations] = useState<Record<string, Turn[]>>({});
  const [draft, setDraft] = useState('');
  const [pending, setPending] = useState('');
  const [error, setError] = useState('');
  const [year, setYear] = useState<number | null>(null);
  const [years, setYears] = useState<number[]>([]);
  const [yearsLoading, setYearsLoading] = useState(false);
  const [yearsError, setYearsError] = useState(false);
  const [yearsRetry, setYearsRetry] = useState(0);
  const key = `${source || 'all'}-${year || 'all'}`;
  const turns = conversations[key] || [];

  useEffect(() => {
    if (open) {
      setSource(initialSource); setYear(initialYear); setError('');
      if (!dialog.current?.open) {
        launcher.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        dialog.current?.show();
      }
    } else {
      active.current?.abort(); active.current = null; setPending('');
      if (dialog.current?.open) {
        const focusWasInPanel = dialog.current.contains(document.activeElement);
        dialog.current.close();
        if (focusWasInPanel && launcher.current?.isConnected) launcher.current.focus();
      }
    }
  }, [open, initialSource, initialYear]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setYears([]); setYearsLoading(true); setYearsError(false);
    request<number[]>(`/publication-years${source ? `?source=${source}` : ''}`, controller.signal)
      .then(value => { if (!controller.signal.aborted) setYears(value); })
      .catch(() => { if (!controller.signal.aborted) setYearsError(true); })
      .finally(() => { if (!controller.signal.aborted) setYearsLoading(false); });
    return () => controller.abort();
  }, [open, source, yearsRetry]);
  useEffect(() => () => active.current?.abort(), []);
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }); }, [turns.length, pending, error]);

  async function send() {
    const question = draft.trim();
    if (!question || active.current) return;
    const controller = new AbortController();
    active.current = controller;
    setPending(question); setError('');
    const timeout = window.setTimeout(() => controller.abort(), 210000);
    const history: ChatMessage[] = turns.slice(-5).flatMap(turn => [
      { role: 'user' as const, content: turn.question },
      { role: 'assistant' as const, content: turn.result.answer },
    ]);
    try {
      const result = await askQuestion(question, source, history, controller.signal, year);
      if (controller.signal.aborted) return;
      setConversations(value => ({ ...value, [key]: [...(value[key] || []), { question, result }] }));
      setDraft('');
    } catch (err) {
      if (active.current === controller) setError(controller.signal.aborted
        ? 'The request timed out. Try a narrower question.'
        : err instanceof Error ? err.message : 'Unable to answer. Please try again.');
    } finally {
      window.clearTimeout(timeout);
      if (active.current === controller) { active.current = null; setPending(''); }
    }
  }

  function close() {
    active.current?.abort(); active.current = null; setPending(''); onClose();
  }

  return <dialog ref={dialog} className="chat-panel" aria-labelledby="chat-title" aria-modal="false" onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); close(); } }} onCancel={event => { event.preventDefault(); close(); }} onClick={event => { if (event.target === dialog.current) { const rect = dialog.current.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right) close(); } }}>
    <div className="chat-shell">
      <div className="chat-heading"><div><div className="brand">Q&amp;A Agent</div><h2 id="chat-title">Ask TaxWatch</h2></div><button aria-label="Close chat" onClick={close}>×</button></div>
      <div className="chat-scope">
        <fieldset className="chat-filter" disabled={!!pending}><legend>Search within</legend><div className="chat-choices">
          <button type="button" aria-pressed={source === null} onClick={() => { setSource(null); setYear(null); setError(''); setDraft(''); }}>All sources</button>
          {Object.entries(names).sort(([a], [b]) => a.localeCompare(b)).map(([id, name]) => <button type="button" key={id} title={name} aria-label={name} aria-pressed={source === id} onClick={() => { setSource(id as Authority); setYear(null); setError(''); setDraft(''); }}>{id.toUpperCase()}</button>)}
        </div></fieldset>
        <fieldset className="chat-filter" disabled={!!pending}><legend>Publication year</legend><div className="chat-choices">
          <button type="button" aria-pressed={year === null} onClick={() => { setYear(null); setError(''); setDraft(''); }}>All years</button>
          {[...new Set([...years, ...(year ? [year] : [])])].sort((a, b) => b - a).map(value => <button type="button" key={value} aria-pressed={year === value} onClick={() => { setYear(value); setError(''); setDraft(''); }}>{value}</button>)}
        </div>{yearsLoading && <small role="status">Loading years…</small>}{yearsError && <small role="status">Couldn’t load years. <button type="button" onClick={() => setYearsRetry(value => value + 1)}>Retry</button></small>}{!yearsLoading && !yearsError && !years.length && <small>No dated publications available.</small>}</fieldset>
        <button className="back" disabled={!!pending || !turns.length} onClick={() => { setConversations(value => ({ ...value, [key]: [] })); setError(''); }}>Clear conversation</button>
      </div>
      <div className="chat-messages" role="log" aria-label="Chat conversation" aria-live="polite">
        {!turns.length && !pending && <div className="chat-welcome"><span aria-hidden="true">✦</span><h3>Explore your publications</h3><p>Ask about {source ? names[source] : 'any tracked regulator'}{year ? ` in ${year}` : ''}. Answers link to the ingested publications they use.</p><button onClick={() => setDraft('Summarize the most recent publications and cite the sources.')}>Summarize recent publications</button><button onClick={() => setDraft('What reporting requirements are described in the available publications?')}>Find reporting requirements</button></div>}
        {turns.map((turn, index) => <div className="chat-turn" key={`${key}-${index}`}><div className="chat-bubble user"><span className="sr-only">You: </span>{turn.question}</div><div className="chat-bubble assistant"><span className="chat-author">TaxWatch</span><div className="chat-answer">{turn.result.answer.split(/(\[S\d+\])/g).map((part, i) => {
          const citation = turn.result.citations.find(item => `[${item.id}]` === part);
          return citation ? <a key={i} href={`#citation-${key}-${index}-${citation.id}`}>{part}</a> : part;
        })}</div>{turn.result.insufficient_evidence && <p className="chat-note">Evidence is limited for this question.</p>}
        {!!turn.result.citations.length && <div className="chat-citations"><h4>Sources</h4>{turn.result.citations.map(citation => <details id={`citation-${key}-${index}-${citation.id}`} key={citation.id}><summary>[{citation.id}] {citation.source.toUpperCase()} · {citation.title}</summary><blockquote>{citation.passage}</blockquote>{safeUrl(citation.url) && <a href={safeUrl(citation.url)} target="_blank" rel="noopener noreferrer">Open official publication ↗</a>}</details>)}</div>}</div></div>)}
        {!!pending && <><div className="chat-bubble user">{pending}</div><p role="status" className="chat-working">Searching publications and preparing an answer…</p></>}
        {!!error && <div role="alert" className="chat-error">{error} Your question is kept below so you can retry.</div>}
        <div ref={end} />
      </div>
      <form className="chat-composer" onSubmit={event => { event.preventDefault(); void send(); }}><label className="sr-only" htmlFor="chat-question">Your question</label><textarea id="chat-question" value={draft} onChange={event => setDraft(event.target.value)} placeholder="Ask about a notification…" maxLength={2000} rows={3} disabled={!!pending} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} /><div><small>Based on ingested publications. Verify important conclusions in the originals.</small><button className="primary" type="submit" disabled={!!pending || !draft.trim()}>Send</button></div></form>
    </div>
  </dialog>;
}
