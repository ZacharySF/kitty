import { api, type TaskRecord } from './api';
export interface WorkshopDoc { id: string; title: string; format: 'html'|'typst'|'latex'; source: string; assets: Record<string,string>; entrypoint: string; engine: string; revision: number; kind: 'master'|'tailored'; base_doc_id: string|null; base_revision: number|null; job_id: number|null; company: string; role: string; archived: boolean }
export interface Revision { revision: number; author: string; note: string; created_at: string }
export interface Compile { doc_id: string; revision: number; ok: boolean; diagnostics: string[]; pages: number; checks: Record<string,unknown> }
export interface Fact { id: string; text: string; source: string; confirmed: boolean; revision: number }
export interface Settings { enabled: boolean; model_id: string; max_cost_usd: string }
export interface Proposal { id: string; base_revision: number; status: string; summary: string; warnings: string[]; hunks: { id: string; find: string; replace: string; reason: string; fact_ids: string[]; evidence: unknown; flags: string[] }[] }
export interface Preview { id: string; digest: string; maximum_cost_usd: string; model_id: string; redacted_payload: unknown; expires_at: string }
export interface Research { id: string; company: string; role: string; status: string; sources: { url: string; title: string; quote: string; status: string }[]; findings: { text: string; url: string; quote: string }[]; warnings: string[] }
export interface Batch { id: string; master_id: string; master_revision: number; max_cost_usd: string; reserved_usd: string; research: boolean; status: string; items: { position: number; job_id: number; company: string; role: string; status: string; doc_id?: string; proposal_id?: string; error?: string }[] }
export interface OutcomeRow { doc_id: string; revision: number; resume_id: number; title: string; label: string; applications: number; responses: number; advanced: number; outcomes: Record<string, number> }
export interface BatchIndex { batches: Batch[]; jobs: { id: number; title: string; company: string }[]; insights: { resumes: OutcomeRow[]; notice: string } }
export interface WorkshopIndex { documents: WorkshopDoc[]; templates: { id: string; label: string; format: WorkshopDoc['format']; description: string }[]; capabilities: { engines: { id: string; label: string; available: boolean; reason: string }[] } }
export interface AtsFinding { id: string; level: 'pass'|'warn'|'fail'; title: string; detail: string }
export interface AtsReport { contact: { name: string|null; email: string|null; phone: string|null; links: string[] }; sections: { title: string; kind: string; lines: number }[]; findings: AtsFinding[]; counts: { pass: number; warn: number; fail: number }; note: string }
export interface JobTerms { job_id: number; title: string; company: string; found: string[]; missing: string[]; error?: string }
export interface AtsResult { revision: number; report: AtsReport|null; job_terms: JobTerms|null }
export const workshop = {
  index: () => api<WorkshopIndex>('/workshop'),
  get: (id: string) => api<{document: WorkshopDoc; revisions: Revision[]}>(`/workshop/${id}`),
  save: (id: string, body: {source: string; assets: Record<string,string>; title: string; base_revision: number}) => api<{document: WorkshopDoc}>(`/workshop/${id}`, body),
  batches: () => api<BatchIndex>('/workshop-batches'),
  ats: (id: string, revision: number) => api<AtsResult>(`/workshop/${id}/ats?revision=${revision}`),
};
export async function taskResult<T>(response: {task?: TaskRecord} & Record<string,unknown>, signal: AbortSignal): Promise<T> {
  let task = response.task;
  while (task && task.status === 'running') {
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new DOMException('Stopped polling', 'AbortError')); };
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 600);
      if (signal.aborted) abort(); else signal.addEventListener('abort', abort, {once:true});
    });
    if (signal.aborted) throw new DOMException('Stopped polling', 'AbortError');
    task = await api<TaskRecord>(`/tasks/${task.id}`);
  }
  if (signal.aborted) throw new DOMException('Stopped polling', 'AbortError');
  if (task && task.status !== 'completed') throw new Error(task.error || 'Task failed');
  return (task ? task.result : response) as T;
}
export async function fileBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}
export const encodeText = (text: string) => { const bytes = new TextEncoder().encode(text); let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte); return btoa(binary); };
export const decodeText = (value: string) => new TextDecoder('utf-8', {fatal:true}).decode(Uint8Array.from(atob(value), c => c.charCodeAt(0)));
