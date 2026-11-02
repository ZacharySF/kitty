// The background's mirroring logic, with the browser and kitty! behind small interfaces so
// node tests drive it with fakes (tests/controller.test.mjs).
//
// A tab is mirrored only while the person has switched it on, it is an http(s) page outside
// the deny list, in a normal (not private) window, without a password field. Each frame
// carries the URL, title, readable text, selection and, when the tab is the visible one in
// its window, a JPEG screenshot. Switching off, closing the tab, or reaching a denied page
// stops it and tells kitty! to drop the frame at once.
import { checkPage, passwordField, type Verdict } from "./denylist.ts";
import { buildFrame, buildSnapshot, fitsImage, LIMITS, type Extraction } from "./frame.ts";
import { type Kitty, KittyError } from "./kitty.ts";
import type { SendResult, TabStatus } from "./messages.ts";
import { CaptureGate, HEARTBEAT_MS, Pacer } from "./pace.ts";

export type TabInfo = { id: number; windowId: number; url?: string; title?: string; active: boolean; incognito: boolean };

export interface Browser {
  now(): number;
  setTimer(run: () => void, ms: number): unknown;
  clearTimer(handle: unknown): void;
  tab(tabId: number): Promise<TabInfo | null>;
  /** Read the page (injecting the content script when needed). */
  extract(tabId: number): Promise<Extraction>;
  /** A JPEG of the visible tab in `windowId` at `quality` (0–100), as a data URL. */
  capture(windowId: number, quality: number): Promise<string>;
}

export interface Connection {
  /** kitty! with this extension's token, or null while not paired. */
  kitty(): Promise<Kitty | null>;
  /** kitty! refused the token (401): forget it, so the panel pairs again. */
  unpaired(): Promise<void>;
  rules(): Promise<string[]>;
}

export interface Persist {
  /** The tabs being mirrored, kept across background restarts (MV3 suspends it). */
  save(tabs: number[]): Promise<void>;
  load(): Promise<number[]>;
  /** Something a panel shows changed. */
  changed(tabId: number | null): void;
}

type Mirroring = { pacer: Pacer; timer: unknown; lastFrame: number | null; notice: string };

/** JPEG qualities tried in turn until a screenshot fits kitty!'s limit. */
export const QUALITIES = [70, 45];

export class Controller {
  private readonly tabs = new Map<number, Mirroring>();
  /** Why a tab stopped mirroring by itself, shown until the person switches it on again. */
  private readonly stopped = new Map<number, string>();
  private readonly gate = new CaptureGate();
  private readonly browser: Browser;
  private readonly connection: Connection;
  private readonly persist: Persist;

  constructor(browser: Browser, connection: Connection, persist: Persist) {
    this.browser = browser;
    this.connection = connection;
    this.persist = persist;
  }

  /** After a background restart: the tabs that were mirrored carry on. */
  async restore(): Promise<void> {
    for (const tabId of await this.persist.load()) {
      if (!this.tabs.has(tabId) && (await this.browser.tab(tabId))) this.tabs.set(tabId, this.fresh());
    }
  }

  private fresh(): Mirroring {
    return { pacer: new Pacer(), timer: undefined, lastFrame: null, notice: "" };
  }

  mirrored(): number[] {
    return [...this.tabs.keys()];
  }

  async verdict(tab: TabInfo): Promise<Verdict> {
    return checkPage(tab.url, { incognito: tab.incognito, rules: await this.connection.rules() });
  }

  async status(tabId: number): Promise<TabStatus | null> {
    const tab = await this.browser.tab(tabId);
    if (!tab) return null;
    const state = this.tabs.get(tabId);
    return {
      tabId, url: tab.url ?? "", title: tab.title ?? "", on: !!state, verdict: await this.verdict(tab),
      paired: (await this.connection.kitty()) !== null, lastFrame: state?.lastFrame ?? null,
      notice: state?.notice || this.stopped.get(tabId) || "",
    };
  }

  /** The person switched mirroring on or off for a tab. */
  async set(tabId: number, on: boolean): Promise<TabStatus | null> {
    if (!on) {
      await this.stop(tabId, "");
      return this.status(tabId);
    }
    const tab = await this.browser.tab(tabId);
    if (!tab) return null;
    const verdict = await this.verdict(tab);
    this.stopped.delete(tabId);
    if (!verdict.ok) {
      this.stopped.set(tabId, verdict.reason);
      return this.status(tabId);
    }
    if (!this.tabs.has(tabId)) {
      this.tabs.set(tabId, this.fresh());
      await this.persist.save(this.mirrored());
    }
    this.schedule(tabId);
    return this.status(tabId);
  }

  /** Stop mirroring `tabId`; kitty! drops its frame at once. `reason` is shown in the panel. */
  async stop(tabId: number, reason: string): Promise<void> {
    const state = this.tabs.get(tabId);
    if (reason) this.stopped.set(tabId, reason);
    else this.stopped.delete(tabId);
    if (!state) return;
    this.browser.clearTimer(state.timer);
    state.pacer.cancel();
    this.tabs.delete(tabId);
    await this.persist.save(this.mirrored());
    this.persist.changed(tabId);
    const kitty = await this.connection.kitty();
    try {
      await kitty?.stop(tabId);
    } catch {
      // kitty! is gone or the token was revoked: it holds no frame of this tab after either.
    }
  }

  /** The page changed, scrolled or was activated: send a frame within the pacing gap. */
  schedule(tabId: number): void {
    const state = this.tabs.get(tabId);
    if (!state) return;
    const delay = state.pacer.schedule(this.browser.now());
    if (delay === null) return;
    state.timer = this.browser.setTimer(() => void this.frame(tabId), delay);
  }

  /** Every minute: a still page sends a frame anyway, so kitty! keeps the mirror. */
  heartbeat(): void {
    for (const [tabId, state] of this.tabs) if (state.pacer.quiet(this.browser.now(), HEARTBEAT_MS)) this.schedule(tabId);
  }

  /** The tab navigated or finished loading. */
  async navigated(tabId: number): Promise<void> {
    if (!this.tabs.has(tabId)) return;
    const tab = await this.browser.tab(tabId);
    if (!tab) return this.stop(tabId, "");
    const verdict = await this.verdict(tab);
    if (!verdict.ok) return this.stop(tabId, `Mirroring stopped: ${verdict.reason}`);
    this.schedule(tabId);
  }

  /** The deny list or the pairing changed: re-check every mirrored tab. */
  async recheck(): Promise<void> {
    for (const tabId of this.mirrored()) await this.navigated(tabId);
  }

  private note(tabId: number, notice: string): void {
    const state = this.tabs.get(tabId);
    if (state && state.notice !== notice) {
      state.notice = notice;
      this.persist.changed(tabId);
    }
  }

  /** Take one frame of `tabId` and send it. */
  async frame(tabId: number): Promise<void> {
    const state = this.tabs.get(tabId);
    if (!state) return;
    state.pacer.ran(this.browser.now());
    const tab = await this.browser.tab(tabId);
    if (!tab) return this.stop(tabId, "");
    let verdict = await this.verdict(tab);
    if (!verdict.ok) return this.stop(tabId, `Mirroring stopped: ${verdict.reason}`);
    const kitty = await this.connection.kitty();
    if (!kitty) return this.note(tabId, "Connect to kitty! to send frames.");
    let page: Extraction;
    try {
      page = await this.browser.extract(tabId);
    } catch {
      return this.note(tabId, "This page cannot be read yet; trying again when it changes.");
    }
    if (page.passwordField) return this.stop(tabId, `Mirroring stopped: ${passwordField().reason}`);
    verdict = page.url === tab.url ? verdict : await this.verdict({ ...tab, url: page.url });
    if (!verdict.ok) return this.stop(tabId, `Mirroring stopped: ${verdict.reason}`);
    if (page.url.length > LIMITS.url) return this.note(tabId, "This page's address is too long to mirror.");
    const image = tab.active ? await this.screenshot(tab, page.url) : null;
    if (!this.tabs.has(tabId)) return;
    try {
      await kitty.frame(buildFrame(tabId, page, image));
      state.lastFrame = this.browser.now();
      this.note(tabId, tab.active ? "" : "Showing text only: the tab is in the background.");
      this.persist.changed(tabId);
    } catch (error) {
      await this.failed(tabId, error);
    }
  }

  /** A screenshot of `tab` if it is still the visible tab on `url` after taking it. */
  private async screenshot(tab: TabInfo, url: string): Promise<string | null> {
    for (const quality of QUALITIES) {
      const wait = this.gate.reserve(this.browser.now());
      if (wait > 0) {
        const { promise, resolve } = Promise.withResolvers<void>();
        this.browser.setTimer(resolve, wait);
        await promise;
      }
      let image: string;
      try {
        image = await this.browser.capture(tab.windowId, quality);
      } catch {
        return null;
      }
      // The person may have switched tabs while it was taken: then it shows another page.
      const after = await this.browser.tab(tab.id);
      if (!after?.active || after.url !== url || after.windowId !== tab.windowId) return null;
      if (fitsImage(image)) return image;
    }
    return null;
  }

  private async failed(tabId: number, error: unknown): Promise<void> {
    if (!(error instanceof KittyError)) return this.note(tabId, "Sending the frame failed.");
    if (error.status === 401) {
      await this.connection.unpaired();
      return this.note(tabId, "kitty! no longer knows this browser: connect again in the panel.");
    }
    if (error.status === 429) {
      const state = this.tabs.get(tabId);
      if (state) state.timer = this.browser.setTimer(() => this.schedule(tabId), (error.retryAfter ?? 1) * 1000);
      return;
    }
    this.note(tabId, error.message);
  }

  /** The person's selection in `tabId`, sent to kitty!'s Inbox (a callout linked to the page). */
  async sendSelection(tabId: number): Promise<SendResult> {
    const tab = await this.browser.tab(tabId);
    if (!tab) return { ok: false, error: "That tab is gone." };
    const verdict = await this.verdict(tab);
    if (!verdict.ok) return { ok: false, error: verdict.reason };
    const kitty = await this.connection.kitty();
    if (!kitty) return { ok: false, error: "Connect to kitty! first." };
    let page: Extraction;
    try {
      page = await this.browser.extract(tabId);
    } catch {
      return { ok: false, error: "This page cannot be read." };
    }
    if (page.passwordField) return { ok: false, error: passwordField().reason };
    const after = page.url === tab.url ? verdict : await this.verdict({ ...tab, url: page.url });
    if (!after.ok) return { ok: false, error: after.reason };
    if (!page.selection.trim()) return { ok: false, error: "Select some text on the page first." };
    try {
      const saved = await kitty.snapshot(buildSnapshot(page));
      return { ok: true, page: saved.page };
    } catch (error) {
      if (error instanceof KittyError && error.status === 401) {
        await this.connection.unpaired();
        return { ok: false, error: "kitty! no longer knows this browser: connect again." };
      }
      return { ok: false, error: error instanceof Error ? error.message : "Sending failed." };
    }
  }
}
