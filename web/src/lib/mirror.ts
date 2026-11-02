// The live page mirror (platform design Q11): the browser tab the kitty extension mirrors,
// shown in the Mirror pane beside a note. Frames arrive over the live channel
// (`mirror`, `mirror_end`; src/kitty/web_mirror.py) and live only in kitty!'s memory.
// Quoting a passage writes a callout (the page title linked to its URL, and the time)
// holding a quote, through the block editor's own transaction.
import { appendKeys, pasteAfter, type Edit } from "./blockEdits.ts";
import { isTextType, type Op, type PageDoc } from "./blockDoc.ts";
import { safeHref, type Spec } from "./blockText.ts";

export type MirrorImage = { frame: number; type: string; width: number; height: number; bytes: number };
/** One live-channel `mirror` message; `text` is present only when it changed. */
export type MirrorFrame = {
  type: "mirror";
  id: string;
  client: { id: string; name: string };
  tab: string;
  url: string;
  title: string;
  selection: string;
  frame: number;
  text_rev: number;
  text?: string;
  at: string;
  started_at: string;
  image: MirrorImage | null;
  expires_in: number;
};
export type Mirror = Omit<MirrorFrame, "text"> & { text: string };
export type Retention = {
  memory_only: boolean;
  frames_kept: number;
  expiry_seconds: number;
  max_image_bytes: number;
  max_mirrors: number;
  max_per_client: number;
};
/** GET /api/mirror */
export type MirrorListing = { mirrors: MirrorFrame[]; retention: Retention };

/** The quote limit: a passage longer than this is cut (the editor's blocks hold far more). */
export const MAX_QUOTE = 20_000;

/**
 * The mirrors after one `mirror` message, most recently updated first. `stale` means the
 * message carried no text for a mirror this window has never seen (it missed the first
 * frame), so the window should reload the list.
 */
export function applyFrame(list: Mirror[], message: MirrorFrame): { list: Mirror[]; stale: boolean } {
  const known = list.find((item) => item.id === message.id);
  const { text, ...rest } = message;
  const stale = text === undefined && (!known || known.text_rev !== message.text_rev);
  const next: Mirror = { ...rest, text: text ?? known?.text ?? "" };
  return { list: [next, ...list.filter((item) => item.id !== message.id)], stale };
}

export function endMirror(list: Mirror[], id: string): Mirror[] {
  return list.filter((item) => item.id !== id);
}

export function fromListing(listing: MirrorListing): Mirror[] {
  return listing.mirrors.map((item) => ({ ...item, text: item.text ?? "" }));
}

/** The URL of the mirror's current screenshot (served from memory while it is the latest). */
export function imageUrl(mirror: Mirror): string | null {
  return mirror.image ? `/api/mirror/${mirror.id}/image?frame=${mirror.image.frame}` : null;
}

/** The readable text as paragraphs (blank lines separate them). */
export function paragraphs(text: string): string[] {
  return text.split(/\n\s*\n/).map((part) => part.trim()).filter(Boolean);
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** Local date and time to the minute, as the quote's heading shows it. */
export function stamp(at: Date): string {
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

/** "just now", "12 s ago", "3 min ago", "2 h ago". */
export function age(at: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - Date.parse(at)) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  return `${Math.floor(seconds / 3600)} h ago`;
}

/** Whitespace tidied (runs of blank lines become one) and cut to MAX_QUOTE. */
export function cleanPassage(passage: string): string {
  const text = passage.replace(/\r\n?/g, "\n").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  if (text.length <= MAX_QUOTE) return text;
  // Never cut a surrogate pair in half.
  const end = /[\uD800-\uDBFF]/.test(text[MAX_QUOTE - 1]) ? MAX_QUOTE - 1 : MAX_QUOTE;
  return `${text.slice(0, end)}…`;
}

/**
 * The blocks one quote becomes: a callout headed by the page title (linked to the page)
 * and the time, with the passage as a quote inside it. `source_url` and `quoted_at` keep
 * the link and time as properties too, as the ingress's page snapshots do.
 */
export function quoteSpecs(quote: { passage: string; title: string; url: string; at: Date }): Spec[] {
  const title = quote.title.trim() || quote.url;
  const href = safeHref(quote.url);
  const head = `${title} · ${stamp(quote.at)}`;
  const props = {
    ...(href ? { source_url: href, marks: [{ s: 0, e: title.length, t: "a", href }] } : {}),
    quoted_at: quote.at.toISOString(),
  };
  return [
    { type: "callout", text: head, props, depth: 0 },
    { type: "quote", text: cleanPassage(quote.passage), props: {}, depth: 1 },
  ];
}

/**
 * Where a quote goes: after the top-level block holding `anchor` (the block the caret was
 * last in, or the previous quote) when it is still on the page, otherwise at the end of
 * the page.
 */
export function quoteEdit(doc: PageDoc, anchor: string | null, specs: Spec[], newId: () => string): Edit {
  let top = anchor && anchor !== doc.pageId && doc.has(anchor) ? doc.get(anchor) : null;
  while (top && top.parent_id !== doc.pageId) top = top.parent_id && doc.has(top.parent_id) ? doc.get(top.parent_id) : null;
  if (top && isTextType(top.type)) return pasteAfter(doc, top.id, specs, newId);
  const children = doc.children(doc.pageId);
  const last = children[children.length - 1];
  if (last && isTextType(last.type)) return pasteAfter(doc, last.id, specs, newId);
  // An empty page (or one whose last child is a canvas element): append after every child.
  const ops: Op[] = [];
  const chain: string[] = [];
  for (const spec of specs) {
    const id = newId();
    const parent = spec.depth === 0 ? doc.pageId : chain[spec.depth - 1];
    const key = spec.depth === 0 ? appendKeys(children.map((block) => block.order_key), 1)[0] : appendKeys([], 1)[0];
    chain.length = spec.depth;
    chain.push(id);
    ops.push({ op: "insert", id, type: spec.type, parent, key, props: spec.props, text: spec.text });
  }
  const end = ops[ops.length - 1] as Extract<Op, { op: "insert" }>;
  return { ops, focus: { id: end.id, offset: specs[specs.length - 1].text.length } };
}
