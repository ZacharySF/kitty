/**
 * Adding a flashcard (Q66): the one validation and duplicate check shared by the
 * card browser's inline add row and the quick-add popover (N / Ctrl K "New card").
 * Kept free of component and fetch imports so it runs under `node --test`.
 */
export type DeckOption = { id: number; name: string; archived: number };
export type TypeOption = { id: number; kind: string; fields: string[] };
export type CardDraft = { front: string; back: string };
export type CardCheck = { problem: string; field: "front" | "back" | null; duplicate: boolean };
export type Call = <T>(path: string, body?: unknown) => Promise<T>;
type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

export const DUPLICATE_WARNING = "A card with this front is already in this deck. Press Enter again to add it anyway.";
const LAST_DECK_KEY = "kitty.flashcards.last-deck";
const ENTITIES: Record<string, string> = { nbsp: " ", amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" };

/** The comparable form of a front: no markup, entities decoded, spacing collapsed, case folded. */
export function normalizeFront(text: string): string {
  return text
    .replace(/<(?:br|\/?(?:p|div|li))\b[^>]*>/gi, " ")
    .replace(/<[^>]*>/g, "")
    .replace(/&(nbsp|amp|lt|gt|quot|#39);/g, (_, name: string) => ENTITIES[name])
    .normalize("NFC")
    .replace(/\s+/g, " ")
    .trim()
    .toLocaleLowerCase();
}

/** Empty faces are problems that block saving; a front already in the deck is only flagged. */
export function checkCard(draft: CardDraft, fronts: Iterable<string>): CardCheck {
  const key = normalizeFront(draft.front);
  let duplicate = false;
  if (key) for (const front of fronts) if (normalizeFront(front) === key) { duplicate = true; break; }
  if (!draft.front.trim()) return { problem: "Write the front (question) first.", field: "front", duplicate };
  if (!draft.back.trim()) return { problem: "Write the back (answer) too.", field: "back", duplicate };
  return { problem: "", field: null, duplicate };
}

/** The Basic note type with Front and Back fields that both entry points save into. */
export function basicType<T extends TypeOption>(types: readonly T[]): T | undefined {
  return types.find((type) => type.kind === "basic" && type.fields.includes("Front") && type.fields.includes("Back"));
}

/** The deck a new card goes to: the current deck, else the last used, else Default, else any open deck. */
export function preselectDeck(decks: readonly DeckOption[], current?: number | null, remembered?: number | null): number {
  const open = decks.filter((deck) => !deck.archived);
  const pick = (id?: number | null) => (id ? open.find((deck) => deck.id === id)?.id : undefined);
  return pick(current) ?? pick(remembered) ?? open.find((deck) => deck.name === "Default")?.id ?? open[0]?.id ?? 0;
}

function storage(): Storage | undefined {
  try { return globalThis.localStorage; } catch { return undefined; }
}
export function rememberDeck(id: number, store: Storage | undefined = storage()) {
  try { store?.setItem(LAST_DECK_KEY, String(id)); } catch { /* The deck is simply not remembered. */ }
}
export function rememberedDeck(store: Storage | undefined = storage()): number | null {
  try {
    const value = Number(store?.getItem(LAST_DECK_KEY));
    return Number.isInteger(value) && value > 0 ? value : null;
  } catch { return null; }
}

/** Every note's first field in exactly this deck, for the duplicate check. */
export async function loadFronts(call: Call, deckId: number): Promise<string[]> {
  if (!deckId) return [];
  return (await call<{ fronts: string[] }>(`/flashcards/fronts?deck_id=${deckId}`)).fronts;
}

/**
 * Checks and saves one card. Returns `saved: null` with the check when it is invalid,
 * or a duplicate the caller has not yet confirmed with `allowDuplicate`.
 */
export async function addCard(
  call: Call,
  options: { deckId: number; types: readonly TypeOption[]; draft: CardDraft; fronts: Iterable<string>; allowDuplicate?: boolean; storage?: Storage },
): Promise<{ check: CardCheck; saved: { id: number } | null }> {
  const { deckId, types, draft, fronts, allowDuplicate = false } = options;
  const check = checkCard(draft, fronts);
  if (check.problem || (check.duplicate && !allowDuplicate)) return { check, saved: null };
  if (!deckId) throw new Error("Choose a deck for this card.");
  const type = basicType(types);
  if (!type) throw new Error("Create a Basic note type with Front and Back fields to add cards here.");
  const fields = Object.fromEntries(type.fields.map((field) => [field, field === "Front" ? draft.front.trim() : field === "Back" ? draft.back.trim() : ""]));
  const { note } = await call<{ note: { id: number } }>("/flashcards/note", { deck_id: deckId, type_id: type.id, fields, tags: [] });
  rememberDeck(deckId, options.storage ?? storage());
  return { check, saved: note };
}
