// The side panel (Chrome, Brave, Edge) or sidebar (Firefox): connection to rika!, the
// mirror switch for the tab you are looking at, "Send selection to rika! Inbox", and the
// deny list. It talks to rika! only to pair; the background sends frames and selections.
import browser from "webextension-polyfill";
import { parseRules } from "./lib/denylist.ts";
import { DEFAULT_ADDRESS, Kitty } from "./lib/kitty.ts";
import type { PanelMessage, SendResult, TabStatus } from "./lib/messages.ts";
import { Pairing, POLL_MS, type PairingView } from "./lib/pairing.ts";
import { loadSettings, saveSettings, SETTINGS_KEY, type Settings } from "./lib/settings.ts";

declare const __TARGET__: "chromium" | "firefox";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const connection = $<HTMLParagraphElement>("connection");
const codeBox = $<HTMLDivElement>("code-box");
const code = $<HTMLParagraphElement>("code");
const address = $<HTMLInputElement>("address");
const forget = $<HTMLButtonElement>("forget");
const tabTitle = $<HTMLParagraphElement>("tab-title");
const mirror = $<HTMLInputElement>("mirror");
const tabNotice = $<HTMLParagraphElement>("tab-notice");
const sendSelection = $<HTMLButtonElement>("send-selection");
const sendResult = $<HTMLParagraphElement>("send-result");
const rules = $<HTMLTextAreaElement>("rules");
const rulesResult = $<HTMLSpanElement>("rules-result");

/** The browser's own name for the pairing request ("rika! for Brave", say). */
function browserName(): string {
  if (__TARGET__ === "firefox") return "Firefox";
  const brands = (navigator as Navigator & { userAgentData?: { brands: { brand: string }[] } }).userAgentData?.brands.map((item) => item.brand) ?? [];
  return ["Brave", "Microsoft Edge", "Opera", "Vivaldi", "Google Chrome"].find((name) => brands.includes(name)) ?? "Chromium";
}

const ask = <T>(message: PanelMessage) => browser.runtime.sendMessage(message) as Promise<T>;
const pairing = new Pairing({
  load: loadSettings,
  save: saveSettings,
  kitty: (origin, token) => new Kitty(origin, token),
  now: () => Date.now(),
  changed: () => void ask({ kind: "settings-changed" }).catch(() => {}),
}, `rika! for ${browserName()}`);

// Opened as a page (panel.html?tab=<id>) the panel follows that tab; as a side panel or
// sidebar it follows the active tab of its window.
const pinned = Number(new URLSearchParams(location.search).get("tab") ?? NaN);
let tabId: number | null = null;
let pollTimer: ReturnType<typeof setTimeout> | undefined;
let hostAccess = true;

function showPairing(view: PairingView): void {
  clearTimeout(pollTimer);
  codeBox.hidden = view.state !== "waiting";
  forget.hidden = view.state !== "paired";
  // The address rika! actually uses (localhost becomes 127.0.0.1, a missing port 8765).
  if (view.state === "paired" || view.state === "waiting") address.value = view.address;
  if (view.state === "paired") connection.textContent = `Connected to rika! at ${view.address} as “${view.name}”.`;
  else if (view.state === "waiting") {
    connection.textContent = `Waiting for you to approve this browser in rika! at ${view.address}…`;
    code.textContent = view.code;
    pollTimer = setTimeout(() => void pairing.poll().then(showPairing), POLL_MS);
  } else connection.textContent = view.message;
  void refreshTab();
}

async function currentTab(): Promise<number | null> {
  if (Number.isInteger(pinned) && pinned >= 0) return pinned;
  const [active] = await browser.tabs.query({ active: true, currentWindow: true });
  return active?.id ?? null;
}

async function refreshTab(): Promise<void> {
  const next = await currentTab();
  if (next !== tabId) sendResult.textContent = "";
  tabId = next;
  const status = tabId === null ? null : await ask<TabStatus | null>({ kind: "status", tabId }).catch(() => null);
  if (!status) {
    tabTitle.textContent = "No tab";
    mirror.checked = false;
    mirror.disabled = sendSelection.disabled = true;
    tabNotice.textContent = "";
    return;
  }
  tabTitle.textContent = status.title || status.url || "Untitled tab";
  mirror.checked = status.on;
  const blocked = !status.verdict.ok;
  mirror.disabled = blocked || (!status.on && !status.paired);
  sendSelection.disabled = blocked || !status.paired;
  const last = status.lastFrame ? ` Last frame ${new Date(status.lastFrame).toLocaleTimeString()}.` : "";
  tabNotice.textContent = !status.verdict.ok ? status.verdict.reason
    : status.notice || (!status.paired ? "Connect to rika! to mirror this tab." : status.on ? `Mirroring.${last}` : "");
}

address.form?.addEventListener("submit", (event) => {
  event.preventDefault();
  connection.textContent = "Connecting…";
  void pairing.connect(address.value || DEFAULT_ADDRESS).then(showPairing);
});
forget.addEventListener("click", () => void pairing.forget().then(showPairing));

mirror.addEventListener("change", () => {
  if (tabId === null) return;
  const on = mirror.checked;
  // Firefox may hold back host access until the person grants it; asking needs this click.
  const granted = on && !hostAccess ? browser.permissions.request({ origins: ["<all_urls>"] }) : Promise.resolve(true);
  void granted.then(async (ok) => {
    hostAccess ||= ok;
    if (!ok) {
      tabNotice.textContent = "rika! needs access to web pages to mirror them.";
      mirror.checked = false;
      return;
    }
    await ask({ kind: "mirror", tabId: tabId!, on });
    await refreshTab();
  });
});

sendSelection.addEventListener("click", () => {
  if (tabId === null) return;
  sendResult.textContent = "Sending…";
  void ask<SendResult>({ kind: "send-selection", tabId }).then((result) => {
    sendResult.textContent = result.ok ? `Sent to rika!'s Inbox (page ${result.page}).` : result.error;
  });
});

$<HTMLButtonElement>("save-rules").addEventListener("click", () => {
  const parsed = parseRules(rules.value);
  void saveSettings({ rules: parsed.rules }).then(() => {
    rulesResult.textContent = parsed.invalid.length ? `Saved. Not rules, so ignored: ${parsed.invalid.join(", ")}` : "Saved.";
    void ask({ kind: "settings-changed" }).catch(() => {});
    void refreshTab();
  });
});

browser.runtime.onMessage.addListener((message: unknown) => {
  if (message && typeof message === "object" && "kind" in message && message.kind === "changed") void refreshTab();
  return undefined;
});
browser.tabs.onActivated.addListener(() => void refreshTab());
// The background met a 401 (this browser was revoked in rika!): pair again at once.
browser.storage.onChanged.addListener((changes, area) => {
  const next = changes[SETTINGS_KEY]?.newValue as Settings | undefined;
  const before = changes[SETTINGS_KEY]?.oldValue as Settings | undefined;
  if (area === "local" && next?.needsPairing && !before?.needsPairing) void pairing.check().then(showPairing);
});
browser.tabs.onUpdated.addListener((updated) => { if (updated === tabId) void refreshTab(); });

void (async () => {
  const settings = await loadSettings();
  address.value = settings.address ?? DEFAULT_ADDRESS;
  rules.value = settings.rules.join("\n");
  hostAccess = await browser.permissions.contains({ origins: ["<all_urls>"] });
  showPairing(await pairing.check());
})();
