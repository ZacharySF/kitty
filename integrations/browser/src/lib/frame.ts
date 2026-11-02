// What goes to kitty!: a mirror frame (POST /api/ingress/mirror/frame) or the selection
// sent to the Inbox (POST /api/ingress/mirror), cut to kitty!'s limits
// (src/kitty/core/ingress.py and src/kitty/core/mirror.py) so a long page never fails.

export const LIMITS = { url: 2048, title: 300, text: 200_000, selection: 20_000, imageBytes: 2 * 1024 * 1024 } as const;

/** What the content script reads from the page (content.ts). */
export type Extraction = { url: string; title: string; text: string; selection: string; passwordField: boolean };
export type FrameBody = { tab: string; url: string; title: string; text: string; selection: string; image?: string };
export type SnapshotBody = { url: string; title: string; selection: string };

export function isExtraction(value: unknown): value is Extraction {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return ["url", "title", "text", "selection"].every((key) => typeof item[key] === "string") && typeof item.passwordField === "boolean";
}

/** `text` cut to `max` UTF-16 units without splitting a surrogate pair. */
export function cut(text: string, max: number): string {
  if (text.length <= max) return text;
  const end = /[\uD800-\uDBFF]/.test(text[max - 1]) ? max - 1 : max;
  return text.slice(0, end);
}

/** The decoded size of a base64 data URL. */
export function dataUrlBytes(dataUrl: string): number {
  const comma = dataUrl.indexOf(",");
  const payload = comma < 0 ? dataUrl : dataUrl.slice(comma + 1);
  const padding = payload.endsWith("==") ? 2 : payload.endsWith("=") ? 1 : 0;
  return Math.floor((payload.length * 3) / 4) - padding;
}

/** True for a JPEG, PNG or WebP data URL within kitty!'s screenshot limit. */
export function fitsImage(dataUrl: string | null | undefined): dataUrl is string {
  return !!dataUrl && /^data:image\/(jpeg|png|webp);base64,/.test(dataUrl) && dataUrlBytes(dataUrl) <= LIMITS.imageBytes;
}

const title = (value: string, url: string) => cut(value.replace(/\s+/g, " ").trim(), LIMITS.title) || cut(url, LIMITS.title);

/** The frame body for tab `tab` (kitty! keys mirrors by it); an image that does not fit is left out. */
export function buildFrame(tab: number | string, page: Extraction, image: string | null): FrameBody {
  const body: FrameBody = {
    tab: String(tab),
    url: page.url,
    title: title(page.title, page.url),
    text: cut(page.text.trim(), LIMITS.text),
    selection: cut(page.selection.trim(), LIMITS.selection),
  };
  if (fitsImage(image)) body.image = image;
  return body;
}

/** The selection sent to kitty!'s Inbox: a callout with the linked title holding the selection as a quote. */
export function buildSnapshot(page: Extraction): SnapshotBody {
  return { url: page.url, title: title(page.title, page.url), selection: cut(page.selection.trim(), LIMITS.selection) };
}
