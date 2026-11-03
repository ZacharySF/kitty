// Browser fixture for tests/dock.browser.test.mjs. Mounts the real dock with a
// stand-in rika! API and WebSocket so the page never reaches a herdr session.
import { mount } from "svelte";
import "/src/styles.css";
import { loadBuild, reloadBuild } from "/src/lib/build.svelte.ts";
import { createBuildStub } from "./buildStub.js";
import "/src/lib/themes/engine.css";
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

/** Tern is absent unless the page is opened with ?tern=1 (then rika!'s session has panes). */
const withTern = new URLSearchParams(location.search).has("tern");
const ternBlock = (id, extra) => ({ id, title: "", cwd: "/sample/workspace", program: "/bin/bash", cols: 120, rows: 30,
  focused: false, live: true, exited: null, working: false, ...extra });
const tern = withTern ? {
  install_url: "https://stencil.so/tern", installed: true, path: "/sample/bin/tern", source: "path", override: null,
  version: "0.9.8", problem: null, workspace: "/sample/workspace", socket: "/run/user/1000/kitty/tern-0123456789ab/daemon.sock",
  running: true, windows: 0, quick_notes: true,
  activity: [{ event: "pane_exited", pane: 5, status: 3, at: "2026-10-31T20:00:00+00:00" },
             { event: "pane_spawned", pane: 5, status: null, at: "2026-10-31T19:59:00+00:00" }],
  sessions: [{ id: 1, name: "kitty", shown: true, tabs: [
    { id: 2, number: 1, name: null, shown: true, blocks: [ternBlock(4, { title: "cargo test", focused: true, working: true })] },
    { id: 3, number: 2, name: null, shown: false, blocks: [ternBlock(5, { title: "make check", exited: 3 })] },
  ] }],
} : {
  install_url: "https://stencil.so/tern", installed: false, path: null, source: null, override: null, version: null,
  problem: null, workspace: "/sample/workspace", socket: "/run/user/1000/kitty/tern-0123456789ab/daemon.sock",
  running: false, windows: 0, sessions: [], activity: [], quick_notes: true,
};
/** Error answers for the next Tern POSTs, by path. */
const ternErrors = {};
function ternPost(url, body) {
  const action = url.split("/").pop();
  if (ternErrors[url]) return json({ error: ternErrors[url] }, 400);
  const blocks = tern.sessions[0].tabs.flatMap((tab) => tab.blocks);
  if (action === "new") {
    // Like Tern without a window: the new tab's block is focused, the shown tab stays.
    const id = Math.max(...blocks.map((block) => block.id)) + 1;
    tern.sessions[0].tabs.push({ id: id + 100, number: tern.sessions[0].tabs.length + 1, name: null, shown: false, blocks: [ternBlock(id, { focused: true })] });
  } else if (action === "open") {
    tern.windows += 1;
  } else if (action === "run") {
    blocks.find((block) => block.id === body.block).title = body.text;
  } else if (action === "focus") {
    for (const block of blocks) block.focused = block.id === body.block;
    if (!tern.windows) tern.windows = 1;
  } else if (action === "capture") {
    return json({ note_id: 7, block: body.block, truncated: false });
  }
  return json(tern);
}

const calls = [];
const sockets = [];
const events = [];
const opened = [];
let tokens = 0;
window.open = (url, name) => { opened.push({ url, name }); return null; };

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
// tests/dock.browser.test.mjs seeds the build's dock once per tab, before the first load.
const seed = JSON.parse(sessionStorage.getItem("harness.dock-seed") || "null");
const build = createBuildStub(seed ? { ui: { dock: seed } } : {});
window.fetch = async (url, init = {}) => {
  const answer = build.fetch(url, init);
  if (answer) return answer;
  calls.push({ url: String(url), method: init.method ?? "GET", headers: init.headers ?? {}, body: init.body ? JSON.parse(init.body) : undefined });
  if (url === "/api/terminal/tern") return json(tern);
  if (String(url).startsWith("/api/terminal/tern/") && init.method === "POST") return ternPost(String(url), JSON.parse(init.body));
  if (url === "/api/terminal/session") return json({ token: `token-${++tokens}`, socket: "/api/terminal/socket" });
  if (url === "/api/terminal/activity") return json(ACTIVITY);
  if (url === "/api/terminal/agents") return json(init.method === "POST"
    ? { tab: "w1:t2", pane: "w1:p2", kind: JSON.parse(init.body).kind }
    : { binary: "/sample/bin/herdr", session: "kitty-sample", cwd: "/sample/workspace", default: "omp", kinds: ["omp", "claude", "codex"] });
  return json({ error: "not in harness" }, 404);
};

await loadBuild();
const fullscreen = installPanelFullscreen(window, document);
mount(Dock, {
  target: document.getElementById("dock"),
  props: { onherdr: () => events.push("open-herdr-view") },
});

window.dockHarness = {
  calls, sockets, events, opened, ternErrors,
  fullscreen: () => fullscreen.panels.active()?.getAttribute("data-kitty-panel") ?? null,
  /** Server output arriving on the newest terminal socket. */
  emit(text) {
    const socket = sockets.at(-1);
    socket.onmessage?.(new MessageEvent("message", { data: new TextEncoder().encode(text).buffer }));
  },
  sent: () => sockets.at(-1)?.sent ?? [],
  /** The build changed elsewhere (as a live push would deliver it). */
  async changeBuild(patch) {
    build.write(patch);
    await reloadBuild();
  },
};
