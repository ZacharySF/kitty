// Auto-tags in the block editor (platform design Q13). The server matches keywords on the
// transaction path (src/kitty/core/blocks/tagging.py) and answers each transaction with
// `suggestions` for the blocks it changed; the editor underlines them while the block's
// text is still the text they were computed for. A block's state for one tag is its
// `tag_<tag id without dashes>` property: manual, auto or accepted (attached) or rejected
// (never attached or suggested again). Tab or a click attaches (`accepted`); Esc, Ignore
// or removing an attached tag records `rejected`. Tag states are not edits: they never
// move the page revision.
import type { BlockData, Op } from "./blockDoc.ts";

export type TagState = "manual" | "auto" | "accepted" | "rejected";
export type SuggestionItem = { tag: string; name: string; start: number; end: number };
/** One block's suggestions and the text they were computed for (offsets are UTF-16, as marks). */
export type BlockSuggestions = { text: string; items: SuggestionItem[] };
export type Suggestions = Record<string, BlockSuggestions>;

const KEY = /^tag_[0-9a-f]{32}$/;
const ATTACHED: Record<string, true> = { manual: true, auto: true, accepted: true };

export function isTagKey(key: string): boolean {
  return KEY.test(key);
}

/** The block property that holds a block's state for one tag. */
export function tagKey(tagId: string): string {
  return `tag_${tagId.replaceAll("-", "")}`;
}

export function tagOfKey(key: string): string | null {
  if (!KEY.test(key)) return null;
  const raw = key.slice(4);
  return `${raw.slice(0, 8)}-${raw.slice(8, 12)}-${raw.slice(12, 16)}-${raw.slice(16, 20)}-${raw.slice(20)}`;
}

/** A block's tag states, by tag id. */
export function tagStates(block: Pick<BlockData, "properties">): Record<string, TagState> {
  const states: Record<string, TagState> = {};
  for (const [key, value] of Object.entries(block.properties)) {
    const tag = tagOfKey(key);
    if (tag && typeof value === "string") states[tag] = value as TagState;
  }
  return states;
}

/** Tags attached to a block (by hand, automatically or accepted), ordered by name. */
export function attachedTags(block: Pick<BlockData, "properties">, names: Record<string, string>): { tag: string; name: string; state: TagState }[] {
  return Object.entries(tagStates(block))
    .filter(([tag, state]) => Object.hasOwn(ATTACHED, state) && Object.hasOwn(names, tag))
    .map(([tag, state]) => ({ tag, name: names[tag], state }))
    .sort((a, b) => a.name.localeCompare(b.name) || a.tag.localeCompare(b.tag));
}

/** Tag ids a block carries that have no known name yet. */
export function unnamedTags(blocks: Pick<BlockData, "properties">[], names: Record<string, string>): string[] {
  const missing = new Set<string>();
  for (const block of blocks) for (const tag of Object.keys(tagStates(block))) if (!Object.hasOwn(names, tag)) missing.add(tag);
  return [...missing].sort();
}

/**
 * The suggestions to show on a block now: only while its text is what the server matched
 * (typing on hides them until the next answer), and never for a tag the block already has
 * a state for (attached or rejected in this window, not yet confirmed).
 */
export function liveSuggestions(block: Pick<BlockData, "text" | "properties">, entry: BlockSuggestions | undefined): SuggestionItem[] {
  if (!entry || entry.text !== block.text) return [];
  const states = tagStates(block);
  return entry.items.filter((item) => !Object.hasOwn(states, item.tag) && item.end <= block.text.length);
}

/** Suggestions under a collapsed caret (touching either end of the word counts), first tag first. */
export function suggestionsAt(items: SuggestionItem[], start: number, end = start): SuggestionItem[] {
  if (start !== end) return [];
  const seen = new Set<string>();
  return items.filter((item) => item.start <= start && start <= item.end && !seen.has(item.tag) && seen.add(item.tag));
}

/** A server answer's suggestions merged over what this window had (blocks it re-checked are replaced). */
export function mergeSuggestions(current: Suggestions, incoming: Suggestions | undefined): Suggestions {
  if (!incoming || !Object.keys(incoming).length) return current;
  const next = { ...current };
  for (const [id, entry] of Object.entries(incoming)) {
    if (entry.items.length) next[id] = entry;
    else delete next[id];
  }
  return next;
}

export function acceptOps(blockId: string, tagId: string): Op[] {
  return [{ op: "set", id: blockId, props: { [tagKey(tagId)]: "accepted" } }];
}

/** Ignore a suggestion or remove an attached tag: remembered, so no later edit brings it back. */
export function rejectOps(blockId: string, tagId: string): Op[] {
  return [{ op: "set", id: blockId, props: { [tagKey(tagId)]: "rejected" } }];
}
