// Messages inside the extension. The side panel/sidebar asks the background to mirror a
// tab, for a tab's status and to send the selection; the content script only reports that
// its page changed and answers `extract`. Every message is checked here: one from a web
// page's tab can only ever be `dirty`, and only for that same tab.
import type { Verdict } from "./denylist.ts";

export type PanelMessage =
  | { kind: "status"; tabId: number }
  | { kind: "mirror"; tabId: number; on: boolean }
  | { kind: "send-selection"; tabId: number }
  | { kind: "settings-changed" };
export type ContentMessage = { kind: "dirty" };
/** From the background to the content script. */
export type ExtractRequest = { kind: "extract" };
/** From the background to open panels. */
export type ChangedEvent = { kind: "changed"; tabId: number | null };

/** What the panel shows for one tab. */
export type TabStatus = {
  tabId: number;
  url: string;
  title: string;
  /** Mirroring is switched on for this tab. */
  on: boolean;
  /** Whether this page may be mirrored or sent at all. */
  verdict: Verdict;
  paired: boolean;
  /** When the last frame reached rika! (ms since the epoch). */
  lastFrame: number | null;
  /** Why mirroring was switched off, or what is going wrong now. */
  notice: string;
};
export type SendResult = { ok: true; page: number } | { ok: false; error: string };

type Sender = { id?: string; tab?: { id?: number }; url?: string };

const isTab = (value: unknown): value is number => Number.isInteger(value) && (value as number) >= 0;

/**
 * The message as a typed panel or content message, or null to ignore it. `extensionUrl`
 * is this extension's base URL (runtime.getURL("")).
 */
export function parseMessage(
  message: unknown, sender: Sender, runtimeId: string, extensionUrl: string,
): { from: "panel"; message: PanelMessage } | { from: "content"; tabId: number; message: ContentMessage } | null {
  if (sender.id !== runtimeId || !message || typeof message !== "object") return null;
  const item = message as Record<string, unknown>;
  if (!sender.url?.startsWith(extensionUrl)) {
    // A content script, running in a web page's tab: it may only say its own page changed.
    return sender.tab && item.kind === "dirty" && Object.keys(item).length === 1 && isTab(sender.tab.id)
      ? { from: "content", tabId: sender.tab.id, message: { kind: "dirty" } } : null;
  }
  // One of this extension's pages: the side panel or sidebar (or panel.html opened in a tab).
  if (item.kind === "settings-changed") return { from: "panel", message: { kind: "settings-changed" } };
  if (!isTab(item.tabId)) return null;
  if (item.kind === "status" || item.kind === "send-selection") return { from: "panel", message: { kind: item.kind, tabId: item.tabId } };
  if (item.kind === "mirror" && typeof item.on === "boolean") return { from: "panel", message: { kind: "mirror", tabId: item.tabId, on: item.on } };
  return null;
}
