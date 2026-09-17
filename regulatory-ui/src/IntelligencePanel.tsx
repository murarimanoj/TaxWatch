import { useEffect, useState } from 'react';
import { Authority, request } from './api';

interface Evidence { document_id: string; quote: string }
interface Intelligence {
  review_status: string;
  impact: { summary: string; what_changed: string[]; ca_actions: string[]; uncertainties: string[]; effective_from: string | null; evidence: Evidence[] };
  relationships: { relationships: { target_document_id: string; relationship_type: string; scope: string; explanation: string; effective_from: string | null; evidence: Evidence[] }[]; unresolved_references: string[]; uncertainties: string[] };
}

export function IntelligencePanel({ source, documentHash }: { source: Authority; documentHash: string }) {
  const [data, setData] = useState<Intelligence | null>(null);
  const [status, setStatus] = useState('Loading AI summary…');
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setStatus('Loading AI summary…');
    request<{ intelligence: Intelligence | null }>(`/documents/${source}/${encodeURIComponent(documentHash)}/intelligence`, controller.signal)
      .then(result => { if (controller.signal.aborted) return; setData(result.intelligence); setStatus('An AI summary is not available for this notification yet.'); })
      .catch(() => { if (!controller.signal.aborted) setStatus('The AI summary is currently unavailable. Please try opening this notification again.'); });
    return () => controller.abort();
  }, [source, documentHash]);
  return <section aria-label="AI Summary" className="intelligence-panel">
    <h3>AI Summary</h3>
    {!data ? <p role="status">{status}</p> : <>
      <p><strong>{data.review_status.replaceAll('_', ' ')}</strong> · Source-grounded analysis; not a determination of the current legal position.</p>
      <p>{data.impact.summary}</p>
      <h4>What changed</h4><ul>{data.impact.what_changed.map((text, i) => <li key={i}>{text}</li>)}</ul>
      <h4>CA actions</h4><ul>{data.impact.ca_actions.map((text, i) => <li key={i}>{text}</li>)}</ul>
      <p>Proposed effective date: {data.impact.effective_from || 'Unknown'}</p>
      <h4>Relationship proposals</h4>
      {!data.relationships.relationships.length && <p>No relationships identified in the bounded search. This does not establish that none exist.</p>}
      {data.relationships.relationships.map((edge, i) => <details key={i}><summary>{edge.relationship_type} · {edge.scope} · {edge.target_document_id}</summary><p>{edge.explanation}</p><p>Effective: {edge.effective_from || 'Unknown'}</p>{edge.evidence.map((e, j) => <blockquote key={j}>{e.quote}<footer>{e.document_id}</footer></blockquote>)}</details>)}
      <h4>Limitations and unresolved references</h4><ul>{[...data.impact.uncertainties, ...data.relationships.uncertainties, ...data.relationships.unresolved_references].map((text, i) => <li key={i}>{text}</li>)}</ul>
      <details><summary>Source excerpts</summary>{data.impact.evidence.map((e, i) => <blockquote key={i}>{e.quote}<footer>{e.document_id}</footer></blockquote>)}</details>
    </>}
  </section>;
}
