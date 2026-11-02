// The background (a Chrome service worker, a Firefox event page): mirrors the tabs the
// person switched on (lib/controller.ts) and is one of the two places that talk to kitty!
// (the other is the side panel/sidebar). Content scripts never do.
import browser, { type Runtime } from "webextension-polyfill";
import { Controller, type TabInfo } from "./lib/controller.ts";
import { isExtraction } from "./lib/frame.ts";
import { parseMessage, type ChangedEvent } from "./lib/messages.ts";
import { connected, loadSettings, saveSettings, SETTINGS_KEY } from "./lib/settings.ts";

declare const __TARGET__: "chromium" | "firefox";
/** Chrome's side panel API (not in webextension-polyfill). */
declare const chrome: { sidePanel?: { setPanelBehavior(options: { openPanelOnActionClick: boolean }): Promise<void> } } | undefined;

const MIRRORED = "mirrored";
const HEARTBEAT = "kitty-heartbeat";

async function tab(tabId: number): Promise<TabInfo | null> {
  try {
    const found = await browser.tabs.get(tabId);
    if (found.id === undefined) return null;
    return { id: found.id, windowId: found.windowId ?? -1, url: found.url, title: found.title, active: found.active, incognito: found.incognito };
  } catch {
    return null;
  }
}

const controller = new Controller(
  {
    now: () => Date.now(),
    setTimer: (run, ms) => setTimeout(run, ms),
    clearTimer: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
    tab,
    async extract(tabId) {
      const request = { kind: "extract" };
      let page: unknown;
      try {
        page = await browser.tabs.sendMessage(tabId, request);
      } catch {
        // Not injected yet (mirroring just started, or the tab loaded a new page).
        await browser.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
        page = await browser.tabs.sendMessage(tabId, request);
      }
      if (!isExtraction(page)) throw new Error("The page did not answer");
      return page;
    },
    capture: (windowId, quality) => browser.tabs.captureVisibleTab(windowId, { format: "jpeg", quality }),
  },
  {
    kitty: async () => connected(await loadSettings()),
    unpaired: async () => { await saveSettings({ token: null, client: null, needsPairing: true }); },
    rules: async () => (await loadSettings()).rules,
  },
  {
    // storage.session outlives a suspended service worker or event page, not the browser.
    save: (tabs) => browser.storage.session.set({ [MIRRORED]: tabs }),
    load: async () => {
      const value = (await browser.storage.session.get(MIRRORED))[MIRRORED];
      return Array.isArray(value) ? value.filter((item): item is number => Number.isInteger(item)) : [];
    },
    changed: (tabId) => {
      const event: ChangedEvent = { kind: "changed", tabId };
      void browser.runtime.sendMessage(event).catch(() => {});
    },
  },
);
const ready = controller.restore();

browser.runtime.onMessage.addListener((message: unknown, sender: Runtime.MessageSender) => {
  const parsed = parseMessage(message, sender, browser.runtime.id, browser.runtime.getURL(""));
  if (!parsed) return undefined;
  if (parsed.from === "content") {
    void ready.then(() => controller.schedule(parsed.tabId));
    return undefined;
  }
  const request = parsed.message;
  return ready.then(async () => {
    switch (request.kind) {
      case "status": return controller.status(request.tabId);
      case "mirror": return controller.set(request.tabId, request.on);
      case "send-selection": return controller.sendSelection(request.tabId);
      case "settings-changed": await controller.recheck(); return null;
    }
  });
});

browser.tabs.onUpdated.addListener((tabId, change) => {
  if (change.url !== undefined || change.status === "complete") void ready.then(() => controller.navigated(tabId));
});
browser.tabs.onActivated.addListener(({ tabId }) => void ready.then(() => controller.schedule(tabId)));
browser.tabs.onRemoved.addListener((tabId) => void ready.then(() => controller.stop(tabId, "")));

browser.alarms.create(HEARTBEAT, { periodInMinutes: 1 });
browser.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === HEARTBEAT) void ready.then(() => controller.heartbeat());
});

// A new token, a forgotten one or new deny-list rules: re-check every mirrored tab.
browser.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && SETTINGS_KEY in changes) void ready.then(() => controller.recheck());
});

// The toolbar button opens the panel: Chrome's side panel, Firefox's sidebar.
if (__TARGET__ === "chromium") {
  if (typeof chrome !== "undefined") void chrome.sidePanel?.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
} else {
  browser.action.onClicked.addListener(() => void browser.sidebarAction.toggle());
}
