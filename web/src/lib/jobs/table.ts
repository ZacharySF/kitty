/**
 * Find's job table (Career look, Q7 C / Q15 B / Q16): its columns, the column sort and the fit
 * cell. A heading sorts the loaded batch; which batch loads stays the "Sort jobs" order.
 */

/** One posting skill (a config/resume_terms.yaml term) and what covers it. */
export interface FitSkill {
  term: string;
  covered: "resume" | "fact" | null;
  fact_ids: number[];
}
/** `GET /api/jobs/fit` for one job (kitty.core.workshop.ats.fit plus kitty.core.pay). */
export interface JobFit {
  job_id: number;
  skills: FitSkill[];
  covered: number;
  total: number;
  document: { id: string; title: string; kind: string } | null;
  compared_with: string | null;
  error?: string;
  pay: { text: string; hourly: number | null } | null;
}
/** The job fields the table reads (a subset of api.ts `Job`). */
export interface TableJob {
  id: number;
  title: string;
  company: string;
  location: string | null;
  posted_at: string | null;
  deadline_at: string | null;
  queue_state: string;
  status: string | null;
}

export type ColumnKey = "role" | "place" | "fit" | "pay" | "dates" | "queue";
export type SortKey = "title" | "company" | "location" | "fit" | "pay" | "posted" | "deadline" | "queue";
export interface Sort {
  key: SortKey | null;
  descending: boolean;
}

/** In table order. A heading may sort by more than one thing (role, then company). */
export const COLUMNS: { key: ColumnKey; label: string; sorts: { key: SortKey; label: string }[] }[] = [
  { key: "role", label: "Role & company", sorts: [{ key: "title", label: "Role" }, { key: "company", label: "Company" }] },
  { key: "place", label: "Location & remote", sorts: [{ key: "location", label: "Location" }] },
  { key: "fit", label: "Fit", sorts: [{ key: "fit", label: "Fit" }] },
  { key: "pay", label: "Pay", sorts: [{ key: "pay", label: "Pay" }] },
  { key: "dates", label: "Posted & deadline", sorts: [{ key: "posted", label: "Posted" }, { key: "deadline", label: "Deadline" }] },
  { key: "queue", label: "Queue state", sorts: [{ key: "queue", label: "Queue" }] },
];
export const DEFAULT_COLUMNS: ColumnKey[] = ["role", "place", "fit", "pay", "dates"];
export const COLUMNS_KEY = "kitty.jobs.columns";
const ORDER = COLUMNS.map((column) => column.key);
/** Sorted high to low on the first press: the most covered, best paid, newest first. */
const DESCENDING_FIRST: Partial<Record<SortKey, true>> = { fit: true, pay: true, posted: true };
const QUEUE_ORDER = ["queued", "new", "later", "applied", "skipped", "not_eligible"];

type Store = Pick<Storage, "getItem">;

/** The columns this browser shows: always the role, in table order. */
export function readColumns(storage: Store): ColumnKey[] {
  try {
    const saved = storage.getItem(COLUMNS_KEY);
    if (saved === null) return [...DEFAULT_COLUMNS];
    const value: unknown = JSON.parse(saved);
    if (!Array.isArray(value)) return [...DEFAULT_COLUMNS];
    return ORDER.filter((key) => key === "role" || value.includes(key));
  } catch {
    return [...DEFAULT_COLUMNS];
  }
}

export function writeColumns(storage: Pick<Storage, "setItem">, columns: ColumnKey[]): void {
  try {
    storage.setItem(COLUMNS_KEY, JSON.stringify(ORDER.filter((key) => key === "role" || columns.includes(key))));
  } catch {
    /* Storage can be unavailable; the choice still applies until the page closes. */
  }
}

export function fitLabel(fit: JobFit | undefined): string {
  if (!fit) return "";
  if (!fit.total) return "No skills listed";
  return `${fit.covered} of ${fit.total} skill${fit.total === 1 ? "" : "s"}`;
}

function value(job: TableJob, fits: Record<number, JobFit>, key: SortKey): string | number | null {
  const fit = fits[job.id];
  switch (key) {
    case "title": return job.title.toLowerCase();
    case "company": return job.company.toLowerCase();
    case "location": return job.location?.toLowerCase() || null;
    // Skills covered first; between equal counts, the larger share of what the posting asks.
    case "fit": return fit?.total ? fit.covered * 1000 + Math.round((fit.covered / fit.total) * 999) : null;
    case "pay": return fit?.pay?.hourly ?? null;
    case "posted": return job.posted_at ? Date.parse(job.posted_at) || null : null;
    case "deadline": return job.deadline_at ? Date.parse(job.deadline_at) || null : null;
    case "queue": return job.status ? QUEUE_ORDER.length : Math.max(0, QUEUE_ORDER.indexOf(job.queue_state));
  }
}

/** `jobs` sorted by `key` (stable; jobs without a value last either way), or as given for null. */
export function sortJobs<T extends TableJob>(jobs: T[], fits: Record<number, JobFit>, key: SortKey | null, descending: boolean): T[] {
  if (!key) return jobs;
  const keyed = jobs.map((job, index) => ({ job, index, value: value(job, fits, key) }));
  keyed.sort((a, b) => {
    if (a.value === null || b.value === null) return a.value === b.value ? a.index - b.index : a.value === null ? 1 : -1;
    const order = typeof a.value === "number" ? a.value - (b.value as number) : a.value.localeCompare(b.value as string);
    return (descending ? -order : order) || a.index - b.index;
  });
  return keyed.map((item) => item.job);
}

/** The sort after pressing `key`'s heading. */
export function nextSort(current: Sort, key: SortKey): Sort {
  return current.key === key ? { key, descending: !current.descending } : { key, descending: DESCENDING_FIRST[key] === true };
}
