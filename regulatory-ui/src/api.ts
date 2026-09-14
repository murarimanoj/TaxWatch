export type Authority = 'rbi' | 'cbdt' | 'sebi' | 'gst' | 'mca';
export interface Document {
  source: Authority;
  document_hash: string;
  title: string;
  published_date: string | null;
  document_type: string;
  detail_url: string;
  attachment_url: string | null;
  excerpt: string;
}
export interface DocumentDetail extends Document { content: string; fetched_at: string | null; metadata: Record<string, string> }
export interface DocumentPage { items: Document[]; total: number; page: number; page_size: number }
export interface Overview { total: number; authorities: { source: Authority; total: number; latest_year: number | null; last_checked_at: string | null; last_run_status: string | null; preview: Document[] }[] }

const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');
export async function request<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${base}/api${path}`, { signal, headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(response.status === 503
    ? 'Publication data is unavailable. Please try again shortly.'
    : `Unable to load publications (${response.status}). Please try again.`);
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Unable to reach the publication service. Please try again shortly.');
  return response.json();
}

export function safeUrl(value: string): string | undefined {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined; }
  catch { return undefined; }
}


export interface ChatCitation { id: string; source: Authority; document_hash: string; title: string; url: string; published_date: string | null; passage: string }
export interface ChatAnswer { answer: string; citations: ChatCitation[]; insufficient_evidence: boolean; source: Authority | null }
export interface ChatMessage { role: 'user' | 'assistant'; content: string }
export async function askQuestion(question: string, source: Authority | null, history: ChatMessage[], signal: AbortSignal, year: number | null = null): Promise<ChatAnswer> {
  const response = await fetch(`${base}/api/chat`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ question, source, year, history }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(typeof data?.detail === 'string' ? data.detail : 'Unable to answer right now. Please try again.');
  }
  return response.json();
}
