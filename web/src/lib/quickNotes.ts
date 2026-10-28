import { api } from "./api";

export const TINTS = ["rose", "lilac", "sky", "mint", "honey", "frost"] as const;
export type Tint = (typeof TINTS)[number];
export type QuickNote = {
  id: number;
  body: string;
  tint: Tint;
  pinned: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
};
export type QuickNotePatch = Partial<Pick<QuickNote, "body" | "tint" | "pinned">>;

export const listQuickNotes = () => api<{ notes: QuickNote[] }>("/quick-notes").then(data => data.notes);
export const getQuickNote = (id: number) => api<QuickNote>(`/quick-notes/${id}`);
export const createQuickNote = (body: string, tint: Tint) => api<QuickNote>("/quick-notes/create", { body, tint });
export const saveQuickNote = (note: QuickNote, patch: QuickNotePatch) =>
  api<QuickNote>("/quick-notes/save", { id: note.id, revision: note.revision, ...patch });
export const deleteQuickNote = (note: QuickNote) =>
  api<{ ok: true }>("/quick-notes/delete", { id: note.id, revision: note.revision });

export function isConflict(cause: unknown) {
  return (cause as { status?: number } | null)?.status === 409;
}

/** The desktop app turns this into a transparent floating window; browsers open a popup. */
export function popOutQuickNote(id?: number) {
  const route = id ? `quick-note/${id}` : "quick-note";
  window.open(`/#${route}`, `kitty-${route.replace("/", "-")}`, "popup=yes,width=380,height=460");
}

/** First non-empty line, for titles and accessible names. */
export function noteTitle(body: string) {
  return body.split("\n").map(line => line.trim()).find(Boolean)?.slice(0, 80) || "Empty note";
}
