// The Rika bar, its toasts and the history drawer (docs/rika-design.md Q2 D, Q4 C,
// Q19, Q20) in a real browser. The page is the real Rika UI; the backend is a scripted
// stand-in served through Playwright routes and a routed WebSocket, so nothing reaches
// OpenRouter, herdr or Tern.
// Run from web/ with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/rika.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("..", import.meta.url));
let server;
let browser;
let base;

before(async () => {
  server = await createServer({ root, logLevel: "error", server: { host: "127.0.0.1", port: 0 } });
  await server.listen();
  base = server.resolvedUrls.local[0];
  // A fake microphone (a steady tone) that needs no permission prompt.
  browser = await chromium.launch({ args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"] });
});
after(async () => {
  await browser?.close();
  await server?.close();
});

const AGENTS = [
  { id: "herdr:w1:p1", kind: "omp", name: "omp", status: "working", task: "Wire the Rika bar", line: "edit web/src/App.svelte", since: Date.now() - 401_000, where: { herdr_pane: "w1:p1" } },
  { id: "herdr:w1:p2", kind: "claude", name: "claude", status: "blocked", task: "Tailor the CV for Lumen", line: "Allow writing resumes/lumen.pdf? [y/n]", since: Date.now() - 123_000, where: { herdr_pane: "w1:p2" } },
  { id: "tern:4", kind: "codex", name: "codex", status: "done", task: "Import CUDA lecture 7", line: "Imported 38 cards", since: Date.now() - 30_000, where: { tern_pane: "4" } },
];
const MORE = [
  ...AGENTS,
  { id: "herdr:w2:p1", kind: "omp", name: "omp tests", status: "idle", task: "Run the suite", line: "$", since: Date.now() - 60_000, where: { herdr_pane: "w2:p1" } },
  { id: "herdr:w2:p2", kind: "codex", name: "codex docs", status: "working", task: "Update GUIDE.md", line: "writing docs/GUIDE.md", since: Date.now() - 5_000, where: { herdr_pane: "w2:p2" } },
];
const now = () => new Date().toISOString();
/** Polls a condition on the stand-in backend (requests land after the click resolves). */
async function until(condition, label) {
  for (let tries = 0; tries < 100; tries += 1) {
    if (condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  assert.fail(`timed out waiting for ${label}`);
}

/** A brain dump's plan as the backend sends it (kitty.core.rika.dumps.public_items). */
const planItem = (id, area, title, extra = {}) => ({ id, area, title, detail: "", source: "", count: 1, outward: false, question: null, choice: null, picked: null, result: null, ...extra });
const PLAN = {
  id: 77, duration: 9, note: { page_id: 5, title: "Brain dump · Tue 3 Nov, 21:14" }, status: "pending", decided_at: null,
  items: [
    planItem("i1", "notes", "CS 220 · Final project idea", { source: "Random idea for the CS 220 project" }),
    planItem("i2", "calendar", "Orgo midterm", { detail: "Fri 6 Nov · 10:00", source: "Orgo midterm is Friday at ten." }),
    planItem("i3", "calendar", "Study group", { detail: "19:00 · Library 3B", question: { text: "Which Tuesday?", choices: ["This Tue 3 Nov", "Next Tue 10 Nov"] }, choice: 0 }),
    planItem("i4", "cards", "Orgo: SN1 and SN2", { count: 2, source: "Make me cards on SN1 and SN2." }),
    planItem("i5", "agents", "omp on the habit tracker", { outward: true }),
  ],
};
const LINKS = { i1: ["[[CS 220 · Final project idea]]"], i2: ["[Orgo midterm (Fri 6 Nov, 10:00)](#calendar)"],
  i3: ["[Study group (Tue 10 Nov, 19:00)](#calendar)"], i4: ["[SN1 or SN2?](#flashcards)", "[E1 or E2?](#flashcards)"], i5: [] };

/**
 * A scripted Rika backend for one browser context: a fake for this test harness only.
 * "add a task …" applies a reversible action; "start an agent" waits for an approval.
 * `hold` keeps a turn streaming until `release()` so the breathing ring can be seen.
 */
async function fakeRika(context, { agents = AGENTS, history = [] } = {}) {
  const posts = [];
  const gets = [];
  let plan = null;
  let socket;
  let ids = 100;
  let hold = false;
  let held = null;
  const send = (event) => socket.send(JSON.stringify(event));
  const turns = new Map();
  async function reply(userId, text) {
    const id = ++ids;
    send({ type: "tier", message_id: id, reply_to: userId, tier: "fast", model: "demo/fast" });
    if (/^add a task/i.test(text)) {
      const task = text.replace(/^add a task\s*/i, "");
      send({ type: "token", message_id: id, reply_to: userId, text: "Added " });
      const finish = () => {
        send({ type: "action", action: { id: 7, message_id: id, tool: "add_task", summary: `Added the task “${task}”`, reversible: true, status: "done", created_at: now() } });
        send({ type: "token", message_id: id, text: `“${task}” to your tasks.` });
        send({ type: "message", message: { id, role: "rika", reply_to: userId, text: `Added “${task}” to your tasks.`, tier: "fast", model: "demo/fast", status: "done", created_at: now() } });
      };
      if (hold) held = finish;
      else finish();
    } else if (/^start an agent/i.test(text)) {
      send({ type: "token", message_id: id, reply_to: userId, text: "Starting omp needs your approval. " });
      turns.set(9, id);
      send({ type: "approval", approval: { id: 9, message_id: id, tool: "start_agent", summary: "Start omp in a herdr pane", detail: "omp runs shell commands in ~/projects/kitty and spends tokens.", cost_usd: 0.42, status: "pending", created_at: now() } });
    } else {
      send({ type: "message", message: { id, role: "rika", reply_to: userId, text: "I can't do that in this harness.", tier: "fast", status: "done", created_at: now() } });
    }
  }
  // Voice (Q3, Q5): the page streams PCM after voice.start; the stand-in "hears" a script,
  // one more word per 0.25 s of audio, and one dump line per second.
  const voice = { controls: [], bytes: 0, session: null, mode: null, said: 0 };
  const COMMAND = "Add a task buy milk";
  const DUMP = ["Orgo midterm is Friday at ten.", "Make me cards on SN1 and SN2.", "Ask Priya for the export today."];
  const spokenSoFar = () => COMMAND.split(" ").slice(0, Math.floor(voice.bytes / 8000)).join(" ");
  const finalOf = () => voice.mode === "dump"
    ? { text: DUMP.join(" "), segments: DUMP.map((text, index) => ({ start: index * 3, end: index * 3 + 3, text })), duration: 9 }
    : { text: COMMAND, segments: [{ start: 0, end: 2, text: COMMAND }], duration: 2 };
  const voiced = (event) => send({ ...event, session: voice.session, mode: voice.mode });
  await context.routeWebSocket("**/api/rika/stream", (ws) => {
    socket = ws;
    ws.onMessage((message) => {
      if (typeof message !== "string") {
        if (!voice.session) return;
        voice.bytes += message.length;
        if (voice.mode === "dump") {
          while (voice.said < Math.min(DUMP.length, Math.floor(voice.bytes / 32000))) {
            voiced({ type: "voice.segment", index: voice.said, start: voice.said * 3, end: voice.said * 3 + 3, text: DUMP[voice.said] });
            voice.said += 1;
          }
          voiced({ type: "voice.partial", text: "and um" });
        } else voiced({ type: "voice.partial", text: spokenSoFar() });
        return;
      }
      const control = JSON.parse(message);
      voice.controls.push(control);
      if (control.type === "voice.start") {
        Object.assign(voice, { session: control.session, mode: control.mode, bytes: 0, said: 0 });
        voiced({ type: "voice.state", state: "loading" });
        voiced({ type: "voice.state", state: "listening" });
      } else if (control.type === "voice.mode") {
        voice.mode = control.mode;
        voiced({ type: "voice.state", state: "listening" });
      } else if (control.type === "voice.stop") {
        voiced({ type: "voice.state", state: "finishing" });
        voiced({ type: "voice.final", ...finalOf() });
        voice.session = null;
      } else if (control.type === "voice.cancel") {
        voiced({ type: "voice.state", state: "cancelled" });
        voice.session = null;
      }
    });
  });
  // The Agents cards read the agent skills catalogue (every kind off here; agentSkills.browser.test.mjs covers it).
  await context.route("**/api/terminal/agent-skills", (route) => route.fulfill({ json: { can_open: false, kinds: ["omp", "claude", "codex"].map((kind) =>
    ({ kind, label: kind, enabled: false, skills: [], mcp: [], plugins: [] })) } }));
  await context.route("**/api/rika/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.postData() ? JSON.parse(request.postData()) : undefined;
    if (request.method() === "POST") posts.push({ path, body, header: request.headers()["x-kitty-request"] });
    else gets.push(path);
    if (request.method() === "GET" && path === "/api/rika/history") return route.fulfill({ json: { items: history, has_more: false, next_before: null } });
    if (request.method() === "GET" && path === "/api/rika/agents") return route.fulfill({ json: { agents } });
    if (path === "/api/rika/messages") {
      const userId = ++ids;
      await route.fulfill({ json: { conversation_id: 1, message_id: userId } });
      send({ type: "message", message: { id: userId, role: "user", text: body.text, source: body.spoken ? "voice" : "typed", created_at: now() } });
      setTimeout(() => reply(userId, body.text), 30);
      return;
    }
    const spoken = path.match(/^\/api\/rika\/speech\/(\d+)$/);
    if (spoken) return route.fulfill({ json: { text: "Added “buy milk” to your tasks.", audio: "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=" } });
    if (path === "/api/rika/dumps") {
      const userId = ++ids;
      await route.fulfill({ json: { conversation_id: 1, message_id: userId } });
      send({ type: "message", message: { id: userId, role: "user", text: body.transcript, source: "dump", created_at: now() } });
      setTimeout(() => {
        const replyId = ++ids;
        plan = { ...PLAN, message_id: replyId, request_id: userId, created_at: now() };
        send({ type: "tier", message_id: replyId, reply_to: userId, tier: "standard", model: "demo" });
        send({ type: "plan", plan });
        send({ type: "message", message: { id: replyId, role: "rika", reply_to: userId, text: "One plan from your brain dump: 5 items in notes, calendar, cards and agents.", tier: "standard", status: "done", created_at: now() } });
      }, 30);
      return;
    }
    const decided = path.match(/^\/api\/rika\/plans\/(\d+)$/);
    if (decided) {
      const picked = new Set(body.items ?? []);
      const running = { ...plan, status: body.decision === "approve" ? "running" : "dismissed", items: plan.items.map((item) => ({ ...item, picked: picked.has(item.id) })) };
      await route.fulfill({ json: { plan: running } });
      if (body.decision !== "approve") return;
      setTimeout(() => {
        const items = running.items.map((item) => ({ ...item, choice: body.choices?.[item.id] ?? item.choice,
          result: item.picked ? { status: "done", summary: item.title, links: LINKS[item.id] } : { status: "skipped", summary: "Not ticked", links: [] } }));
        send({ type: "plan", plan: { ...running, status: "applied", items } });
      }, 30);
      return;
    }
    const approval = path.match(/^\/api\/rika\/approvals\/(\d+)$/);
    if (approval) {
      const status = body.decision === "approve" ? "approved" : "rejected";
      const decided = { id: Number(approval[1]), message_id: turns.get(Number(approval[1])), tool: "start_agent", summary: "Start omp in a herdr pane", detail: "omp runs shell commands in ~/projects/kitty and spends tokens.", cost_usd: 0.42, status, created_at: now() };
      await route.fulfill({ json: { approval: decided } });
      send({ type: "approval", approval: decided });
      const id = decided.message_id;
      setTimeout(() => {
        if (status === "approved") {
          send({ type: "action", action: { id: 8, message_id: id, tool: "start_agent", summary: "Started omp in herdr pane w1:p3", reversible: false, status: "done", created_at: now() } });
          send({ type: "token", message_id: id, text: "omp is running." });
        }
        const text = status === "approved" ? "Starting omp needs your approval. omp is running." : "Starting omp needs your approval. I did not start it.";
        send({ type: "message", message: { id, role: "rika", text, tier: "fast", status: "done", created_at: now() } });
      }, 30);
      return;
    }
    const undo = path.match(/^\/api\/rika\/actions\/(\d+)\/undo$/);
    if (undo) {
      const action = { id: Number(undo[1]), tool: "add_task", summary: "Added the task “buy milk”", reversible: true, status: "undone" };
      await route.fulfill({ json: { action } });
      send({ type: "action", action });
      return;
    }
    // Without Tern, opening a herdr agent answers the view that shows it instead.
    if (/^\/api\/rika\/agents\/[^/]+\/open$/.test(path))
      return route.fulfill({ json: { ok: true, window: path.includes("w1%3Ap1") ? null : 4, view: path.includes("w1%3Ap1") ? "herdr" : null } });
    return route.fulfill({ status: 404, json: { error: `No stand-in for ${path}` } });
  });
  return {
    posts,
    send,
    holdTurns: () => (hold = true),
    release: () => { hold = false; held?.(); held = null; },
    connected: () => Boolean(socket),
    voice,
    gets,
  };
}

async function harness({ reducedMotion, ...options } = {}) {
  const context = await browser.newContext({ viewport: { width: 1200, height: 800 }, reducedMotion });
  const rika = await fakeRika(context, options);
  // Spoken replies are recorded instead of played.
  await context.addInitScript(() => {
    window.__played = [];
    HTMLMediaElement.prototype.play = function play() { window.__played.push(this.src); return Promise.resolve(); };
  });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/rika-harness.html", base).href);
  await page.waitForFunction(() => !!window.rikaHarness);
  const bar = page.getByRole("region", { name: "Rika" });
  await bar.waitFor();
  await page.waitForFunction(() => window.rikaHarness.connected());
  return { page, bar, rika, problems, context };
}
const ask = async (page, text) => {
  const field = page.getByRole("textbox", { name: "Ask Rika" });
  await field.fill(text);
  await field.press("Enter");
};
const drawer = (page) => page.getByRole("dialog", { name: "Rika history" });
const toasts = (page) => page.getByRole("list", { name: "Rika replies" });

test("a slim bar at the bottom: Ask Rika…, no Space keycap or hint, and the page ends above it", async () => {
  const { page, bar, problems, context } = await harness();
  const box = await bar.boundingBox();
  assert.equal(Math.round(box.y + box.height), 800, "the bar is not on the bottom edge");
  assert.equal(box.width, 1200);
  assert.ok(box.height <= 60, `the bar is not slim: ${box.height}px`);
  assert.equal(await page.getByRole("textbox", { name: "Ask Rika" }).getAttribute("placeholder"), "Ask Rika…");
  assert.equal(await bar.locator("kbd").count(), 0);
  assert.doesNotMatch(await bar.innerText(), /space|hold/i);
  const space = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--rika-space").trim());
  assert.equal(space, `${Math.round(box.height)}px`);
  const view = await page.locator("#view").boundingBox();
  assert.equal(Math.round(view.y + view.height), Math.round(box.y), "the page does not end at the bar");
  assert.deepEqual(problems, []);
  await context.close();
});

test("Enter sends; the reply lands as a toast with its tier, and the drawer holds the exchange", async () => {
  const { page, rika, problems, context } = await harness();
  await ask(page, "Add a task buy milk");
  const toast = toasts(page).getByRole("listitem").filter({ hasText: "Added “buy milk” to your tasks." });
  await toast.waitFor();
  assert.deepEqual(rika.posts.map(({ path, body, header }) => [path, body, header]), [["/api/rika/messages", { text: "Add a task buy milk" }, "1"]]);
  assert.equal(await page.getByRole("textbox", { name: "Ask Rika" }).inputValue(), "");
  assert.equal((await toast.locator("[data-tier]").innerText()).toLowerCase(), "fast");
  assert.ok(await toast.getByRole("button", { name: "Undo" }).isVisible());
  // The toast sits above the bar, in the notification stack's corner, once it has risen into place.
  await toast.evaluate((node) => Promise.all(node.getAnimations().map((animation) => animation.finished)));
  const [card, bar] = await Promise.all([toast.boundingBox(), page.getByRole("region", { name: "Rika" }).boundingBox()]);
  assert.ok(card.y + card.height <= bar.y, "the toast overlaps the bar");
  assert.ok(card.x + card.width > 1100, "the toast is not on the right");
  await page.getByRole("button", { name: "Rika history", exact: true }).click();
  const history = drawer(page);
  await history.waitFor();
  await history.getByText("Add a task buy milk").waitFor();
  await history.getByText("Added “buy milk” to your tasks.").waitFor();
  const action = history.getByRole("article").filter({ hasText: "Added the task “buy milk”" });
  assert.ok(await action.getByRole("button", { name: "Undo" }).isVisible());
  assert.deepEqual(problems, []);
  await context.close();
});

test("Undo reverts the action, from the toast or the drawer", async () => {
  const { page, rika, problems, context } = await harness();
  await ask(page, "Add a task buy milk");
  const toast = toasts(page).getByRole("listitem").filter({ hasText: "buy milk" });
  await toast.getByRole("button", { name: "Undo" }).click();
  await toast.getByText("Undone").waitFor();
  assert.deepEqual(rika.posts.at(-1), { path: "/api/rika/actions/7/undo", body: {}, header: "1" });
  assert.equal(await toast.getByRole("button", { name: "Undo" }).count(), 0);
  await page.keyboard.press("Control+j");
  const action = drawer(page).getByRole("article").filter({ hasText: "Added the task “buy milk”" });
  await action.getByText("Undone").waitFor();
  assert.equal(await action.getByRole("button", { name: "Undo" }).count(), 0);
  assert.deepEqual(problems, []);
  await context.close();
});

test("an approval opens the drawer by itself; Approve continues the turn and the drawer closes", async () => {
  const { page, rika, problems, context } = await harness();
  await page.getByRole("textbox", { name: "Ask Rika" }).focus();
  await ask(page, "Start an agent");
  const history = drawer(page);
  await history.waitFor();
  const card = history.getByRole("article").filter({ hasText: "Start omp in a herdr pane" });
  await card.waitFor();
  assert.match(await card.innerText(), /omp runs shell commands in ~\/projects\/kitty and spends tokens\./);
  assert.match(await card.innerText(), /\$0\.42/);
  assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("aria-label")), "Ask Rika", "opening by itself stole focus from the bar");
  await card.getByRole("button", { name: "Approve" }).click();
  await history.waitFor({ state: "detached" });
  assert.deepEqual(rika.posts.at(-1), { path: "/api/rika/approvals/9", body: { decision: "approve" }, header: "1" });
  await toasts(page).getByText("omp is running.").waitFor();
  await page.keyboard.press("Control+j");
  await drawer(page).getByRole("article").filter({ hasText: "Start omp in a herdr pane" }).getByText("Approved").waitFor();
  await drawer(page).getByText("Started omp in herdr pane w1:p3").waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("a drawer you opened yourself stays open after Reject, which the turn reports", async () => {
  const { page, rika, problems, context } = await harness();
  await page.getByRole("button", { name: "Rika history", exact: true }).click();
  await ask(page, "Start an agent");
  const card = drawer(page).getByRole("article").filter({ hasText: "Start omp in a herdr pane" });
  await card.getByRole("button", { name: "Reject" }).click();
  await card.getByText("Rejected").waitFor();
  assert.deepEqual(rika.posts.at(-1).body, { decision: "reject" });
  await drawer(page).getByText("I did not start it.").waitFor();
  assert.ok(await drawer(page).isVisible(), "a drawer opened by hand closed by itself");
  assert.deepEqual(problems, []);
  await context.close();
});

test("agent chips: state dot and name each, clicking opens its Tern window; past four they collapse to a count", async () => {
  const { page, bar, rika, problems, context } = await harness();
  const chips = bar.getByRole("group", { name: "Running agents" });
  await chips.getByRole("button").first().waitFor();
  assert.deepEqual(await chips.getByRole("button").allInnerTexts(), ["omp", "claude", "codex"]);
  assert.deepEqual(await chips.getByRole("button").evaluateAll((nodes) => nodes.map((node) => node.dataset.state)), ["working", "blocked", "done"]);
  await chips.getByRole("button", { name: /claude/ }).click();
  await until(() => rika.posts.length === 1, "the open request");
  assert.deepEqual(rika.posts.at(-1), { path: "/api/rika/agents/herdr%3Aw1%3Ap2/open", body: {}, header: "1" });
  assert.deepEqual(await page.evaluate(() => window.rikaHarness.navigations), [], "a focused Tern window also navigated");
  await chips.getByRole("button", { name: /^omp/ }).click();
  await page.waitForFunction(() => window.rikaHarness.navigations.length === 1);
  assert.deepEqual(await page.evaluate(() => window.rikaHarness.navigations), ["herdr"]);
  rika.send({ type: "agents", agents: MORE });
  const count = chips.getByRole("button", { name: /5 agents/ });
  await count.waitFor();
  assert.equal(await chips.getByRole("button").count(), 1);
  assert.equal(await count.getAttribute("data-state"), "blocked");
  await count.click();
  const section = drawer(page).getByRole("region", { name: "Agents" });
  await section.waitFor();
  assert.equal(await section.getByRole("article").count(), 5);
  const blocked = section.getByRole("article").filter({ hasText: "Tailor the CV for Lumen" });
  assert.match(await blocked.innerText(), /blocked/i);
  assert.match(await blocked.innerText(), /Allow writing resumes\/lumen\.pdf\? \[y\/n\]/);
  assert.match(await blocked.innerText(), /\d+:\d\d/);
  await blocked.getByRole("button", { name: "Open window" }).click();
  await until(() => rika.posts.length === 3, "the open request from the Agents card");
  assert.equal(rika.posts.at(-1).path, "/api/rika/agents/herdr%3Aw1%3Ap2/open");
  assert.deepEqual(problems, []);
  await context.close();
});

const ring = (bar) => bar.evaluate((node) => {
  const glow = getComputedStyle(node, "::after");
  return { streaming: node.dataset.streaming === "true", animation: glow.animationName, opacity: Number(glow.opacity) };
});

test("the bar breathes with a soft ring of light while Rika streams, then rests", async () => {
  const { page, bar, rika, problems, context } = await harness();
  assert.deepEqual(await ring(bar), { streaming: false, animation: "none", opacity: 0 });
  rika.holdTurns();
  await ask(page, "Add a task buy milk");
  await page.waitForFunction(() => document.querySelector('[aria-label="Rika"]')?.dataset.streaming === "true");
  const lit = await ring(bar);
  assert.notEqual(lit.animation, "none");
  rika.release();
  await toasts(page).getByText("Added “buy milk” to your tasks.").waitFor();
  await page.waitForFunction(() => document.querySelector('[aria-label="Rika"]')?.dataset.streaming === "false");
  assert.equal((await ring(bar)).animation, "none");
  assert.deepEqual(problems, []);
  await context.close();
});

test("under Reduce motion the streaming ring is lit but static", async () => {
  const { page, bar, rika, problems, context } = await harness({ reducedMotion: "reduce" });
  rika.holdTurns();
  await ask(page, "Add a task buy milk");
  await page.waitForFunction(() => document.querySelector('[aria-label="Rika"]')?.dataset.streaming === "true");
  const lit = await ring(bar);
  assert.equal(lit.animation, "none");
  assert.ok(lit.opacity > 0.3, "the static ring is not lit");
  rika.release();
  assert.deepEqual(problems, []);
  await context.close();
});

test("Rika opening a view navigates; the shortcut toggles the drawer and Escape closes it", async () => {
  const { page, rika, problems, context } = await harness();
  rika.send({ type: "navigate", view: "calendar" });
  await page.waitForFunction(() => window.rikaHarness.navigations.length === 1);
  assert.deepEqual(await page.evaluate(() => window.rikaHarness.navigations), ["calendar"]);
  await page.keyboard.press("Control+j");
  await drawer(page).waitFor();
  assert.equal(await page.getByRole("button", { name: "Rika history", exact: true }).getAttribute("aria-pressed"), "true");
  await page.keyboard.press("Control+j");
  await drawer(page).waitFor({ state: "detached" });
  await page.keyboard.press("Control+j");
  await drawer(page).getByRole("button", { name: "Close Rika history" }).focus();
  await page.keyboard.press("Escape");
  await drawer(page).waitFor({ state: "detached" });
  assert.deepEqual(problems, []);
  await context.close();
});

test("earlier history loads into the drawer, oldest first", async () => {
  const history = [
    { kind: "message", id: 1, role: "user", text: "What's due before Lumen's interview?", created_at: "2026-11-02T11:05:00Z" },
    { kind: "message", id: 2, role: "rika", text: "3 flashcard decks and the CUDA take-home.", tier: "standard", status: "done", reply_to: 1, created_at: "2026-11-02T11:05:04Z" },
  ];
  const { page, problems, context } = await harness({ history });
  await page.keyboard.press("Control+j");
  const texts = await drawer(page).locator("[data-entry]").allInnerTexts();
  assert.match(texts[0], /What's due before Lumen's interview\?/);
  assert.match(texts[1], /3 flashcard decks and the CUDA take-home\./);
  assert.match(texts[1], /standard/i);
  assert.equal(await toasts(page).count(), 0, "history raised toasts");
  assert.deepEqual(problems, []);
  await context.close();
});

// Voice and brain dumps (Q3 A, Q4 C, Q5 A, Q6 C, Q7 A, Q8 A, Q20 A) ------------------

const field = (page) => page.getByRole("textbox", { name: "Ask Rika" });
const listeningMode = (page) => page.evaluate(() => document.querySelector('[aria-label="Rika"]')?.dataset.listening ?? null);

test("a Space tap types a space; a hold past 0.3 s listens, the bar breathes, and letting go sends it spoken", async () => {
  const { page, bar, rika, problems, context } = await harness();
  await field(page).fill("hi");
  await field(page).press("Space");
  assert.equal(await field(page).inputValue(), "hi ", "a tap did not type its space");
  assert.equal(await listeningMode(page), null);
  await field(page).fill("");
  await page.keyboard.down(" ");
  await page.waitForTimeout(200);
  assert.equal(await listeningMode(page), null, "listening began before 0.3 s");
  await page.waitForFunction(() => document.querySelector('[aria-label="Rika"]')?.dataset.listening === "command");
  assert.notEqual((await ring(bar)).animation, "none", "the bar does not breathe while listening");
  await until(() => rika.voice.bytes > 16000, "microphone audio on the socket");
  assert.deepEqual(rika.voice.controls[0], { type: "voice.start", session: rika.voice.controls[0].session, mode: "command" });
  await page.waitForFunction(() => document.querySelector('[aria-label="Ask Rika"]').placeholder.startsWith("Add a task"));
  // Q5: no keycap or hint, even while listening.
  assert.equal(await bar.locator("kbd").count(), 0);
  assert.doesNotMatch(await bar.innerText(), /space|hold/i);
  assert.equal(await field(page).inputValue(), "", "holding typed spaces");
  await page.keyboard.up(" ");
  await until(() => rika.posts.some((post) => post.path === "/api/rika/messages"), "the spoken request");
  assert.deepEqual(rika.posts.at(-1).body, { text: "Add a task buy milk", spoken: true });
  assert.ok(rika.voice.controls.some((control) => control.type === "voice.stop"));
  await toasts(page).getByText("Added “buy milk” to your tasks.").waitFor();
  assert.equal(await listeningMode(page), null);
  // Q6 C: the reply to a spoken request is spoken back.
  await page.waitForFunction(() => window.__played.length === 1);
  assert.ok(rika.gets.some((path) => /^\/api\/rika\/speech\/\d+$/.test(path)));
  assert.match(await page.evaluate(() => window.__played[0]), /^data:audio\/wav;base64,/);
  await page.keyboard.press("Control+j");
  await drawer(page).getByText(/· spoken/).waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("a typed request is never spoken; Escape drops what is being said", async () => {
  const { page, rika, problems, context } = await harness();
  await ask(page, "Add a task buy milk");
  await toasts(page).getByText("Added “buy milk” to your tasks.").waitFor();
  await field(page).fill("remind me");
  await page.keyboard.down(" ");
  await page.waitForFunction(() => document.querySelector('[aria-label="Rika"]')?.dataset.listening === "command");
  await until(() => rika.voice.controls.some((control) => control.type === "voice.start"), "voice.start");
  await page.keyboard.press("Escape");
  await page.keyboard.up(" ");
  await until(() => rika.voice.controls.some((control) => control.type === "voice.cancel"), "voice.cancel");
  assert.equal(await listeningMode(page), null);
  assert.equal(await field(page).inputValue(), "remind me", "Escape lost the typed words");
  await page.waitForTimeout(150);
  assert.equal(rika.posts.filter((post) => post.path === "/api/rika/messages").length, 1);
  assert.equal(rika.gets.filter((path) => path.startsWith("/api/rika/speech/")).length, 0);
  assert.deepEqual(await page.evaluate(() => window.__played), []);
  assert.deepEqual(problems, []);
  await context.close();
});

test("a long hold becomes a brain dump: the drawer opens by itself with the live transcript, the plan arrives grouped by area", async () => {
  const { page, rika, problems, context } = await harness();
  await field(page).focus();
  await page.keyboard.down(" ");
  await page.waitForFunction(() => document.querySelector('[aria-label="Rika"]')?.dataset.listening === "dump", null, { timeout: 12_000 });
  await page.keyboard.up(" ");
  assert.ok(rika.voice.controls.some((control) => control.type === "voice.mode" && control.mode === "dump"));
  const history = drawer(page);
  const transcript = history.getByRole("region", { name: "Brain dump transcript" });
  await transcript.getByText("Make me cards on SN1 and SN2.").waitFor({ timeout: 10_000 });
  assert.equal(await listeningMode(page), "dump", "letting go ended the dump");
  await field(page).press("Space"); // the next Space ends it
  await until(() => rika.posts.some((post) => post.path === "/api/rika/dumps"), "the dump");
  const dump = rika.posts.find((post) => post.path === "/api/rika/dumps").body;
  assert.equal(dump.transcript, "Orgo midterm is Friday at ten. Make me cards on SN1 and SN2. Ask Priya for the export today.");
  assert.equal(dump.segments.length, 3);
  assert.equal(dump.duration, 9);
  const plan = history.getByRole("article", { name: "Brain dump plan" });
  await plan.waitFor();
  assert.deepEqual(await plan.getByRole("region").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("aria-label"))),
    ["Notes", "Calendar", "Cards", "Agents"]);
  assert.equal(await plan.getByRole("checkbox", { checked: true }).count(), 9);
  assert.ok(await plan.getByRole("button", { name: "Approve all 5" }).isVisible());
  assert.match(await plan.innerText(), /Rika asks|Which Tuesday\?/);
  assert.match(await plan.innerText(), /Approving starts it/);
  // Untick the worker and choose next Tuesday, then approve the rest.
  await plan.getByRole("checkbox", { name: "omp on the habit tracker" }).uncheck();
  await plan.getByRole("button", { name: "Next Tue 10 Nov" }).click();
  await plan.getByRole("button", { name: "Approve 4 of 5" }).click();
  assert.deepEqual(rika.posts.at(-1), { path: "/api/rika/plans/77", body: { decision: "approve", items: ["i1", "i2", "i3", "i4"], choices: { i3: 1 } }, header: "1" });
  // Q20 A: the drawer that opened by itself closes once the plan is decided.
  await history.waitFor({ state: "detached" });
  await page.keyboard.press("Control+j");
  const done = drawer(page).getByRole("article", { name: "Brain dump plan" });
  await done.getByText("Done", { exact: true }).waitFor();
  assert.match(await done.innerText(), /Study group \(Tue 10 Nov, 19:00\)/);
  assert.match(await done.innerText(), /SN1 or SN2\? · E1 or E2\?/);
  assert.match(await done.innerText(), /Dump note: Brain dump · Tue 3 Nov, 21:14/);
  await done.getByRole("button", { name: "Orgo midterm (Fri 6 Nov, 10:00)" }).click();
  await page.waitForFunction(() => window.rikaHarness.navigations.includes("calendar"));
  assert.deepEqual(problems, []);
  await context.close();
});

test("Brain dump mode from the drawer keeps listening until Done; ticking an area off and Dismiss make nothing", async () => {
  const { page, rika, problems, context } = await harness();
  await page.keyboard.press("Control+j");
  const history = drawer(page);
  await history.getByRole("button", { name: "Brain dump" }).click();
  await page.waitForFunction(() => document.querySelector('[aria-label="Rika"]')?.dataset.listening === "dump");
  await until(() => rika.voice.controls.length > 0, "voice.start");
  assert.equal(rika.voice.controls[0].mode, "dump");
  await history.getByRole("region", { name: "Brain dump transcript" }).getByText("Orgo midterm is Friday at ten.").waitFor({ timeout: 10_000 });
  await history.getByRole("button", { name: "Done · make plan" }).click();
  const plan = history.getByRole("article", { name: "Brain dump plan" });
  await plan.waitFor();
  await plan.getByRole("checkbox", { name: "All of Calendar" }).uncheck();
  assert.ok(await plan.getByRole("button", { name: "Approve 3 of 5" }).isVisible());
  await plan.getByRole("button", { name: "Dismiss" }).click();
  assert.deepEqual(rika.posts.at(-1).body, { decision: "dismiss" });
  await plan.getByText("Dismissed").waitFor();
  assert.ok(await history.isVisible(), "a drawer opened by hand closed by itself");
  assert.deepEqual(problems, []);
  await context.close();
});
