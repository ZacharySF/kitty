// Tags (platform design Q13): the /api/tags client (src/kitty/web_tags.py) and the small
// helpers the Tags settings and tag pages share. A tag's name and aliases match like
// keywords; terms shorter than three characters match only when the tag opts in.
import { api } from "./api.ts";

export type TagMode = "suggest" | "always";
export type TagRecord = {
  id: string;
  name: string;
  keywords: string[];
  aliases: string[];
  /** The tag's own mode, or null to follow the global setting. */
  mode: TagMode | null;
  short: boolean;
  effective_mode: TagMode;
  count: number;
  revision: number;
};
export type TagsState = { mode: TagMode; revision: number; tags: TagRecord[] };
/** `inline`: tagged by a #name written in the text (remove the #name to untag it). */
export type TaggedBlock = {
  id: string; text: string; source: "manual" | "auto" | "accepted"; page: string; page_type: string; number: number | null; title: string; inline: boolean;
};
export type Mention = { id: string; page: string; number: number | null; title: string; text: string };
export type TagPageData = {
  tag: TagRecord;
  blocks: TaggedBlock[];
  count: number;
  next_offset: number | null;
  unlinked: Mention[];
  unlinked_count: number;
};
export type TagFields = { name?: string; keywords?: string[]; aliases?: string[]; mode?: TagMode | null; short?: boolean };

export const MODE_LABELS: Record<TagMode, string> = { suggest: "Suggest", always: "Always attach" };
export const SOURCE_LABELS: Record<TaggedBlock["source"], string> = { manual: "by hand", auto: "automatically", accepted: "from a suggestion" };

export const listTags = () => api<TagsState>("/tags");
export const setTagMode = (mode: TagMode, revision: number) => api<{ mode: TagMode; revision: number }>("/tags/settings", { mode, revision });
export const createTag = (fields: TagFields & { name: string }) => api<TagRecord>("/tags/create", fields);
export const updateTag = (id: string, fields: TagFields) => api<TagRecord>("/tags/update", { id, ...fields });
export const deleteTag = (id: string) => api<{ deleted: string }>("/tags/delete", { id });
export const findTag = (name: string) => api<{ id: string | null }>(`/tags/find?name=${encodeURIComponent(name)}`);
export const linkAll = (id: string) => api<{ linked: number }>("/tags/link-all", { id });
export const attachTag = (block: string, tag: string) => api("/tags/attach", { block, tag });
export const removeTag = (block: string, tag: string) => api("/tags/remove", { block, tag });
export function tagPage(id: string, offset = 0): Promise<TagPageData> {
  return api<TagPageData>(`/tags/page?id=${encodeURIComponent(id)}${offset ? `&offset=${offset}` : ""}`);
}

/** Comma- or line-separated terms as typed into a field, trimmed, blanks and repeats (ignoring case) dropped. */
export function parseTerms(value: string): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const raw of value.split(/[,\n]/)) {
    const term = raw.trim().replace(/\s+/g, " ");
    const key = term.toLowerCase();
    if (term && !seen.has(key)) {
      seen.add(key);
      result.push(term);
    }
  }
  return result;
}

export function termsText(terms: string[]): string {
  return terms.join(", ");
}

/** Terms that will not match: shorter than three characters while the tag has not opted in. */
export function shortTerms(terms: string[], short: boolean): string[] {
  return short ? [] : terms.filter((term) => [...term.trim()].length < 3);
}
