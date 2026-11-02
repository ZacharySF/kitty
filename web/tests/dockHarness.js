// Browser fixture for tests/dock.browser.test.mjs. Mounts the real dock with a
// stand-in kitty! API and WebSocket so the page never reaches a herdr session.
import { mount } from "svelte";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { applySavedTheme } from "/src/lib/themes/index.ts";
import { installPanelFullscreen } from "/src/lib/panelFullscreen.ts";
import Dock from "/src/lib/Dock.svelte";

const since = Date.now() - 90_000;
const ACTIVITY = {
  running: true,
  workspaces: [
    { id: "w1", label: "api", status: "working", focused: true, agents: [
      { pane: "w1:p1", name: "omp", status: "working", task: "Fix the failing tests", line: "running pytest", revision: 12, since },
      { pane: "w1:p2", name: "Codex", status: "blocked", task: "Review the diff", line: "needs permission", revision: 4, since },
    ] },
    { id: "w2", label: "docs", status: "idle", focused: false, agents: [
      { pane: "w2:p1", name: "claude", status: "idle", task: "", line: "", revision: 0, since },
    ] },
    { id: "w3", label: "scratch", status: "idle", focused: false, agents: [] },
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
  if (url === "/api/terminal/agents") return json(init.method === "POST"
    ? { tab: "w1:t2", pane: "w1:p2", kind: JSON.parse(init.body).kind }
    : { binary: "/sample/bin/herdr", session: "kitty-sample", cwd: "/sample/workspace", default: "omp", kinds: ["omp", "claude", "codex"] });
  return json({ error: "not in harness" }, 404);
};

applySavedTheme();
const fullscreen = installPanelFullscreen(window, document);
mount(Dock, {
  target: document.getElementById("dock"),
  props: { onherdr: () => events.push("open-herdr-view") },
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
