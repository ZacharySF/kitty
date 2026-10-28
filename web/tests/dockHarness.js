// Browser fixture for tests/dock.browser.test.mjs. Mounts the real dock with a
// stand-in kitty! API and WebSocket so the page never reaches a herdr session.
import { mount } from "svelte";
import "/src/styles.css";
import { applySavedTheme } from "/src/lib/themes.ts";
import { installPanelFullscreen } from "/src/lib/panelFullscreen.ts";
import Dock from "/src/lib/Dock.svelte";

const ACTIVITY = {
  running: true,
  workspaces: [
    { id: "w1", label: "api", status: "working", focused: true, agents: [
      { pane: "w1:p1", name: "omp", status: "working" },
      { pane: "w1:p2", name: "Codex", status: "blocked" },
    ] },
    { id: "w2", label: "docs", status: "idle", focused: false, agents: [] },
  ],
};

const calls = [];
const sockets = [];
const events = [];
let tokens = 0;

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  constructor(url) {
    this.url = url;
    this.readyState = FakeSocket.CONNECTING;
    this.binaryType = "blob";
    this.sent = [];
    sockets.push(this);
    setTimeout(() => {
      this.readyState = FakeSocket.OPEN;
      this.onopen?.(new Event("open"));
    }, 0);
  }
  send(data) {
    this.sent.push(typeof data === "string" ? { text: data } : { binary: new TextDecoder().decode(data) });
  }
  close() {
    if (this.readyState === FakeSocket.CLOSED) return;
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.(new CloseEvent("close", { code: 1000 }));
  }
}
window.WebSocket = FakeSocket;

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
window.fetch = async (url, init = {}) => {
  calls.push({ url: String(url), method: init.method ?? "GET", headers: init.headers ?? {} });
  if (url === "/api/terminal/session") return json({ token: `token-${++tokens}`, socket: "/api/terminal/socket" });
  if (url === "/api/terminal/activity") return json(ACTIVITY);
  return json({ error: "not in harness" }, 404);
};

applySavedTheme();
const fullscreen = installPanelFullscreen(window, document);
const content = document.getElementById("content");
mount(Dock, {
  target: document.getElementById("dock"),
  props: {
    measure: () => content.getBoundingClientRect().bottom,
    onherdr: () => events.push("open-herdr-view"),
  },
});

window.dockHarness = {
  calls, sockets, events,
  fullscreen: () => fullscreen.panels.active()?.getAttribute("data-kitty-panel") ?? null,
  /** Server output arriving on the newest terminal socket. */
  emit(text) {
    const socket = sockets.at(-1);
    socket.onmessage?.(new MessageEvent("message", { data: new TextEncoder().encode(text).buffer }));
  },
  sent: () => sockets.at(-1)?.sent ?? [],
};
