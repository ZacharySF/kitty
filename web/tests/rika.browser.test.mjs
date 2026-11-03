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
  browser = await chromium.launch();
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

/**
 * A scripted Rika backend for one browser context: a fake for this test harness only.
 * "add a task …" applies a reversible action; "start an agent" waits for an approval.
 * `hold` keeps a turn streaming until `release()` so the breathing ring can be seen.
 */
async function fakeRika(context, { agents = AGENTS, history = [] } = {}) {
  const posts = [];
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
    } else if (/^move my study block/i.test(text)) {
      // Q13: a deterministic reply runs its tool with no model.
      send({ type: "action", action: { id: 11, message_id: id, tool: "move_event", summary: "Moved “Study: React deck” to 14:00", reversible: true, status: "done", created_at: now() } });
      send({ type: "message", message: { id, role: "rika", reply_to: userId, text: "Moved “Study: React deck” to 14:00.", tier: "fast", model: "deterministic", cost_usd: null, status: "done", created_at: now(),
        usage: { tokens_in: 0, tokens_cached: 0, tokens_out: 0, path: ["move_event"], signature: "move-event", remembered: false, shortcut: false } } });
    } else {
      send({ type: "message", message: { id, role: "rika", reply_to: userId, text: "I can't do that in this harness.", tier: "fast", status: "done", created_at: now() } });
    }
  }
  await context.routeWebSocket("**/api/rika/stream", (ws) => { socket = ws; });
  await context.route("**/api/rika/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.postData() ? JSON.parse(request.postData()) : undefined;
    if (request.method() === "POST") posts.push({ path, body, header: request.headers()["x-kitty-request"] });
    if (request.method() === "GET" && path === "/api/rika/history") return route.fulfill({ json: { items: history, has_more: false, next_before: null } });
    if (request.method() === "GET" && path === "/api/rika/agents") return route.fulfill({ json: { agents } });
    if (path === "/api/rika/messages") {
      const userId = ++ids;
      await route.fulfill({ json: { conversation_id: 1, message_id: userId } });
      send({ type: "message", message: { id: userId, role: "user", text: body.text, created_at: now() } });
      setTimeout(() => reply(userId, body.text), 30);
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
    // Q16: Send holds the draft for 30 s; Undo-send cancels it until then; Discard drops it.
    const mail = path.match(/^\/api\/rika\/mail\/drafts\/([0-9a-z]+)\/(send|undo|discard)$/);
    if (mail) {
      const current = history.find((item) => item.kind === "mail" && item.id === mail[1]);
      if (!current) return route.fulfill({ status: 404, json: { error: "Unknown draft" } });
      const status = { send: "scheduled", undo: "cancelled", discard: "discarded" }[mail[2]];
      Object.assign(current, { status, send_after: status === "scheduled" ? new Date(Date.now() + 30_000).toISOString() : null, updated_at: now() });
      await route.fulfill({ json: { draft: current } });
      send({ type: "mail", mail: current });
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
  };
}

async function harness({ reducedMotion, ...options } = {}) {
  const context = await browser.newContext({ viewport: { width: 1200, height: 800 }, reducedMotion });
  const rika = await fakeRika(context, options);
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

test("each reply carries its readout: tier, remembered path, tokens in (cached), out and cost; no model reads Direct", async () => {
  const history = [
    { kind: "message", id: 1, role: "user", text: "Add a card: a closure is a function plus its scope", created_at: "2026-11-02T11:05:00Z" },
    { kind: "message", id: 2, role: "rika", text: "Added to “JavaScript interview”.", tier: "fast", model: "demo/fast", cost_usd: 0.0002, status: "done", reply_to: 1, created_at: "2026-11-02T11:05:02Z",
      usage: { tokens_in: 1180, tokens_cached: 960, tokens_out: 64, path: ["find_deck", "add_card"], signature: "add-card", remembered: true, shortcut: false } },
    { kind: "message", id: 3, role: "user", text: "Add a card: hoisting moves declarations up", created_at: "2026-11-02T11:06:00Z" },
    { kind: "message", id: 4, role: "rika", text: "Added to “JavaScript interview”.", tier: "fast", model: "shortcut", cost_usd: null, status: "done", reply_to: 3, created_at: "2026-11-02T11:06:01Z",
      usage: { tokens_in: 0, tokens_cached: 0, tokens_out: 0, path: ["find_deck", "add_card"], signature: "add-card", remembered: true, shortcut: true } },
  ];
  const { page, problems, context } = await harness({ history });
  await page.keyboard.press("Control+j");
  const replies = drawer(page).locator('[data-entry="message"] .said');
  await replies.first().waitFor();
  const model = replies.nth(0);
  assert.equal((await model.locator(".rika-chip").innerText()).toLowerCase(), "fast");
  await model.getByText("remembered path find_deck → add_card · 1,180 in (960 cached) · 64 out · $0.0002").waitFor();
  const shortcut = replies.nth(1);
  assert.equal(await shortcut.locator(".rika-chip").getAttribute("data-tier"), "direct");
  assert.equal((await shortcut.locator(".rika-chip").innerText()).toLowerCase(), "direct");
  await shortcut.getByText("find_deck → add_card · 0 tokens · $0").waitFor();
  await page.keyboard.press("Control+j");
  // A live deterministic reply: its toast's chip says Direct too.
  await ask(page, "Move my study block to 2");
  const toast = toasts(page).getByRole("listitem").filter({ hasText: "Moved “Study: React deck” to 14:00." });
  await toast.waitFor();
  assert.equal((await toast.locator("[data-tier]").innerText()).toLowerCase(), "direct");
  assert.equal(await toast.locator("[data-tier]").getAttribute("title"), "Direct · move_event · 0 tokens · $0");
  await page.keyboard.press("Control+j");
  await drawer(page).getByText("move_event · 0 tokens · $0").waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("a mail item: who and what, Auto-send · scheduling with an Undo-send countdown, and once sent Gmail can't unsend it", async () => {
  const mail = (id, extra) => ({
    kind: "mail", id: id.padEnd(32, "0"), mail_id: `m-${id}`, message_id: null, thread_id: null, to: "Maya Chen <maya@example.test>", subject: "Re: Onsite interview: pick a time",
    body: "Hi Maya,\n\nTuesday after 1pm or Thursday morning both work.\n\nBest,\nAlex", reply_kind: "scheduling", status: "scheduled", auto: true,
    send_after: new Date(Date.now() + 25_000).toISOString(), sent_at: null, error: null, original: null, created_at: now(), updated_at: now(), ...extra,
  });
  const held = mail("a", {});
  const waiting = mail("b", { to: "Prof. Okafor <okafor@example.test>", subject: "Re: TA cover for Thursday lab?", reply_kind: "question", status: "draft", auto: false, send_after: null, body: "Yes, I can cover Thursday's 3–5 lab." });
  const { page, rika, problems, context } = await harness({ history: [held, waiting] });
  await page.keyboard.press("Control+j");
  const card = drawer(page).getByRole("article", { name: /Reply to Maya Chen/ });
  await card.waitFor();
  const text = await card.innerText();
  assert.match(text, /Maya Chen <maya@example\.test>/);
  assert.match(text, /Re: Onsite interview: pick a time/);
  assert.match(text, /Tuesday after 1pm/);
  assert.match(text, /auto-send · scheduling/i);
  const undo = card.getByRole("button", { name: /^Undo send · 0:2\d$/ });
  await undo.waitFor();
  const first = await undo.innerText();
  await page.waitForFunction((before) => [...document.querySelectorAll("button")].some((node) => /^Undo send · /.test(node.textContent.trim()) && node.textContent.trim() !== before), first);
  await undo.click();
  await card.getByText("Cancelled").waitFor();
  assert.deepEqual(rika.posts.at(-1), { path: `/api/rika/mail/drafts/${"a".padEnd(32, "0")}/undo`, body: {}, header: "1" });
  // A draft waits for one tap; Send holds it with the countdown.
  const okafor = drawer(page).getByRole("article", { name: /Reply to Prof\. Okafor/ });
  assert.match(await okafor.innerText(), /Reply · question/i);
  await okafor.getByRole("button", { name: "Send", exact: true }).click();
  await okafor.getByRole("button", { name: /^Undo send · 0:(2\d|30)$/ }).waitFor();
  assert.deepEqual(rika.posts.at(-1), { path: `/api/rika/mail/drafts/${"b".padEnd(32, "0")}/send`, body: {}, header: "1" });
  // The stream says it went out: no more Undo, and the copy says it can't be unsent.
  rika.send({ type: "mail", mail: { ...held, status: "sent", send_after: null, sent_at: now(), updated_at: now() } });
  await card.getByText("Sent — Gmail can't unsend it").waitFor();
  assert.match(await card.innerText(), /Auto-sent · scheduling/i);
  assert.equal(await card.getByRole("button").count(), 0);
  assert.deepEqual(problems, []);
  await context.close();
});
