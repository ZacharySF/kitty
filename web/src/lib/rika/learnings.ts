/**
 * Wire types and pure helpers for Rika's career memory (kitty.core.rika.learnings, served
 * by kitty.web_rika_learnings): skills the postings the user acts on keep asking for,
 * patterns in the jobs they choose, their resume review habits and notes they added.
 */
export type LearningKind = "posting_skill" | "posting_pattern" | "resume_edit" | "note";
export type SkillDetail = { group: string; postings: number; against: number; fact: boolean };
export type PatternDetail = { facet: string; postings: number };
export type EditDetail = { accepted: number; rejected: number };
export type Learning = {
  id: number;
  kind: LearningKind;
  key: string;
  text: string;
  weight: number;
  /** Job ids for posting learnings, Workshop proposal ids for resume edits. */
  evidence: (number | string)[];
  detail: Partial<SkillDetail & PatternDetail & EditDetail>;
  pinned: boolean;
  created_at: string;
  updated_at: string;
};
export type EvidenceJob = { title: string; company: string };
export type LearningsState = {
  learnings: Learning[];
  jobs: Record<string, EvidenceJob>;
  refreshed_at: string | null;
  forgotten: number;
};

export const FACETS: Record<string, string> = {
  season: "Season", remote: "Remote", location: "Location", track: "Track", level: "Level", company: "Company",
};

export type Grouped = { skills: Learning[]; patterns: Learning[]; accepted: Learning[]; rejected: Learning[]; notes: Learning[] };

/** The Memory page's groups, in the server's order (pinned first, then heaviest). */
export function grouped(items: Learning[]): Grouped {
  const of = (kind: LearningKind) => items.filter((item) => item.kind === kind);
  const edits = of("resume_edit");
  return {
    skills: of("posting_skill"),
    patterns: of("posting_pattern"),
    accepted: edits.filter((item) => (item.detail.accepted ?? 0) >= (item.detail.rejected ?? 0)),
    rejected: edits.filter((item) => (item.detail.accepted ?? 0) < (item.detail.rejected ?? 0)),
    notes: of("note"),
  };
}

/** A skill chip's size step, 1–4, by its weight against the heaviest skill. */
export function chipSize(weight: number, heaviest: number): 1 | 2 | 3 | 4 {
  if (!(heaviest > 0) || !(weight > 0)) return 1;
  return Math.min(4, Math.max(1, Math.ceil((weight / heaviest) * 4))) as 1 | 2 | 3 | 4;
}

/** The first `count` posting skills, for the Workshop's "Rika remembers" strip. */
export function topSkills(items: Learning[], count = 5): Learning[] {
  return items.filter((item) => item.kind === "posting_skill").slice(0, count);
}

/** "4 postings · confirmed fact" under a skill chip. */
export function skillLine(item: Learning): string {
  const postings = item.detail.postings ?? 0;
  const against = item.detail.against ? ` · ${item.detail.against} skipped` : "";
  return `${postings} posting${postings === 1 ? "" : "s"}${against} · ${item.detail.fact ? "confirmed fact" : "no confirmed fact"}`;
}

/** "Accepted 3 of 4" for a review habit. */
export function editLine(item: Learning): string {
  const accepted = item.detail.accepted ?? 0;
  return `Accepted ${accepted} of ${accepted + (item.detail.rejected ?? 0)}`;
}

/** The evidence jobs of a posting learning that still exist, as "Title · Company". */
export function evidenceJobs(item: Learning, jobs: Record<string, EvidenceJob>): { id: number; label: string }[] {
  if (!item.kind.startsWith("posting_")) return [];
  return item.evidence.flatMap((id) => {
    const job = jobs[String(id)];
    return job ? [{ id: Number(id), label: [job.title, job.company].filter(Boolean).join(" · ") }] : [];
  });
}
