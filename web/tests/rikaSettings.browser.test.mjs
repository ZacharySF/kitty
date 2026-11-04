// Settings → Rika (docs/rika-design.md Q13 A, Q16 B, Q18 A) in a real browser: Routing
// memory, Email and Memory. The page is the real Settings view with the real Rika UI; the
// backend is a scripted stand-in served through Playwright routes and a routed WebSocket,
// so nothing reaches OpenRouter or Gmail and every record is synthetic.
// Run from web/ with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/rikaSettings.browser.test.mjs
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

const iso = (offset = 0) => new Date(Date.now() + offset).toISOString();
const ROUTES = [
  { signature: "move-event", label: "Move a calendar block", tier: "direct", path: ["move_event"], uses: 41, escalations: 0, avg_tokens: 0, avg_cost_usd: 0, shortcut: { template: "move {event} to {time}", uses: 12, active: true }, example: "Move my study block to 2", updated_at: iso() },
  { signature: "add-card", label: "Add a flashcard", tier: "fast", path: ["find_deck", "add_card"], uses: 128, escalations: 0, avg_tokens: 1180, avg_cost_usd: 0.0002, shortcut: null, example: "Add a card: a closure is a function plus its scope", updated_at: iso() },
  { signature: "plan-day", label: "Plan a day or week", tier: "standard", path: ["list_events", "plan_day"], uses: 17, escalations: 2, avg_tokens: 6100, avg_cost_usd: 0.004, shortcut: null, example: "Plan Thursday around the interview", updated_at: iso() },
];
const TREND = [6.1, 5.8, 5.9, 5.1, 4.6, 4.4, 3.9, 3.4, 3.1, 2.9, 2.6, 2.3, 2.1, 1.9].map((cost, index) => ({ day: `2026-10-${String(21 + index).padStart(2, "0")}`, requests: 8, avg_cost_usd: cost / 1000 }));
const SUMMARY = { today_requests: 64, today_tokens: 18_200, today_cost_usd: 0.031, cached_share: 0.78, trend: TREND };

const draft = (id, extra = {}) => ({
  kind: "mail", id: id.padEnd(32, "0"), mail_id: `m-${id}`, message_id: null, thread_id: `t-${id}`, to: "Maya Chen <maya@example.test>", subject: "Re: Onsite interview: pick a time",
  body: "Hi Maya,\n\nTuesday after 1pm or Thursday morning both work.\n\nBest,\nAlex", reply_kind: "scheduling", status: "draft", auto: false, send_after: null, sent_at: null, error: null,
  original: { sender: "Maya Chen", sender_email: "maya@example.test", subject: "Onsite interview: pick a time", received_at: iso(-3_600_000), snippet: "Could you share a few windows next Tuesday or Thursday?" },
  created_at: iso(-60_000), updated_at: iso(-60_000), ...extra,
});
const KINDS = [
  { kind: "scheduling", label: "Scheduling", hint: "Proposing or accepting times", auto: false, sent: 11 },
  { kind: "confirmation", label: "Confirmations", hint: "Appointments, RSVPs, receipts", auto: false, sent: 6 },
  { kind: "thanks", label: "Thanks", hint: "Short thanks and acknowledgements", auto: false, sent: 4 },
];
const MAILBOX = { connected: true, demo: true, account: null, setup: null, can_delay: false, hold_seconds: 30, hold_choices: [10, 30, 60, 120, 300], last_check: iso(-300_000) };

const PREFS = [
  { id: 1, text: "Keeps evenings after 9pm free of study blocks", source: "stated", created_at: "2026-10-28T09:00:00Z", updated_at: "2026-10-28T09:00:00Z" },
  { id: 2, text: "Signs off emails “Best, Alex”", source: "added", created_at: "2026-10-29T09:00:00Z", updated_at: "2026-10-29T09:00:00Z" },
];
const SOURCES = [
  { source: "page", label: "Pages", items: 412, refreshed_at: iso(-120_000) },
  { source: "block", label: "Blocks", items: 4812, refreshed_at: iso(-120_000) },
  { source: "event", label: "Calendar", items: 1240, refreshed_at: iso(-600_000) },
  { source: "mail", label: "Email", items: 38, refreshed_at: iso(-600_000) },
];
const HITS = [
  { source: "event", ref: "e1", title: "Fri 2–4pm · Study block (control theory)", snippet: "Study block", view: "calendar" },
  { source: "page", ref: "p9", title: "Lecture 9 · PID controllers", snippet: "", view: "notes" },
];

/** Polls a condition on the stand-in backend (requests land after the click resolves). */
async function until(condition, label) {
  for (let tries = 0; tries < 150; tries += 1) {
    if (condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  assert.fail(`timed out waiting for ${label}`);
}

/** A scripted backend for one browser context: a fake for this harness only. */
async function fakeBackend(context, { routes = ROUTES, mailbox = MAILBOX, drafts = [draft("a")], notesShared = false, sources = SOURCES } = {}) {
  const posts = [];
  const gets = [];
  let socket;
  const state = {
    routes: structuredClone(routes),
    mail: { mailbox: structuredClone(mailbox), kinds: structuredClone(KINDS), drafts: structuredClone(drafts) },
    prefs: structuredClone(PREFS),
    nextPref: 10,
    task: 0,
  };
  let index = sources;
  const memory = (results = []) => ({ preferences: state.prefs, index: { sources: index, total: index.reduce((sum, source) => sum + source.items, 0) }, results, notes_shared: notesShared });
  const send = (event) => socket.send(JSON.stringify(event));
  await context.routeWebSocket("**/api/rika/stream", (ws) => { socket = ws; });
  await context.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const body = request.postData() ? JSON.parse(request.postData()) : undefined;
    const json = (data, status = 200) => route.fulfill({ status, json: data });
    if (request.method() === "POST") posts.push({ path, body });
    else gets.push(path + url.search);
    if (path === "/api/modules") return json({ available: [], configured: [], enabled: [], restart_required: false });
    if (path === "/api/rika/history") return json({ items: [], has_more: false, next_before: null });
    if (path === "/api/rika/agents") return json({ agents: [] });
    // The bar's morning brief and the notifier's alerts (Q15) have nothing to say here.
    if (path === "/api/rika/brief") return json({ brief: null });
    if (path === "/api/rika/alerts") return json({ alerts: [] });
    if (path === "/api/rika/routes") return json({ routes: state.routes, summary: SUMMARY });
    if (path === "/api/rika/routes/forget") {
      if (!state.routes.some((item) => item.signature === body.signature)) return json({ error: "Unknown route" }, 404);
      state.routes = state.routes.filter((item) => item.signature !== body.signature);
      return json({ routes: state.routes, summary: SUMMARY });
    }
    if (path === "/api/rika/mail") return json(state.mail);
    if (path === "/api/rika/mail/settings") {
      if (body.hold_seconds) state.mail.mailbox.hold_seconds = body.hold_seconds;
      else {
        if (body.auto && !body.confirm) return json({ error: "Confirm turning on auto-send" }, 409);
        state.mail.kinds = state.mail.kinds.map((kind) => (kind.kind === body.kind ? { ...kind, auto: body.auto } : kind));
      }
      return json(state.mail);
    }
    if (path === "/api/rika/mail/check") {
      const fresh = draft("c", { to: "Brightside Dental <desk@example.test>", subject: "Re: Please confirm: cleaning Thu", reply_kind: "confirmation", body: "Confirmed, see you Thursday.", created_at: iso(), updated_at: iso() });
      state.mail.drafts = [fresh, ...state.mail.drafts];
      return json({ new: 2, drafts: [fresh] });
    }
    if (path === "/api/rika/mail/connect") {
      if (state.mail.mailbox.demo) return json({ error: "Not in the demo" }, 409);
      return json({ task_id: "t1" });
    }
    if (path === "/api/tasks/t1") {
      state.task += 1;
      if (state.task < 2) return json({ id: "t1", kind: "gmail", status: "running", started_at: iso(), finished_at: null, result: null, error: null });
      state.mail.mailbox = { ...state.mail.mailbox, connected: true, account: "alex@example.test", setup: null };
      return json({ id: "t1", kind: "gmail", status: "completed", started_at: iso(), finished_at: iso(), result: null, error: null });
    }
    const mail = path.match(/^\/api\/rika\/mail\/drafts\/([0-9a-z]+)(?:\/(send|undo|discard))?$/);
    if (mail) {
      const current = state.mail.drafts.find((item) => item.id === mail[1]);
      if (!current) return json({ error: "Unknown draft" }, 404);
      const next = { ...current, updated_at: iso() };
      if (!mail[2]) next.body = body.body;
      else if (mail[2] === "send") Object.assign(next, { status: "scheduled", send_after: iso(state.mail.mailbox.hold_seconds * 1000) });
      else if (mail[2] === "undo") {
        if (current.status === "sent") return json({ error: "Already sent: Gmail can't unsend mail" }, 409);
        Object.assign(next, { status: "cancelled", send_after: null });
      } else next.status = "discarded";
      state.mail.drafts = state.mail.drafts.map((item) => (item.id === next.id ? next : item));
      return json({ draft: next });
    }
    if (path === "/api/rika/memory") {
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      return json(memory(q ? HITS.filter((hit) => hit.title.toLowerCase().includes(q)) : []));
    }
    if (path === "/api/rika/preferences") {
      const preference = { id: state.nextPref++, text: body.text, source: "added", created_at: iso(), updated_at: iso() };
      state.prefs = [...state.prefs, preference];
      return json({ preference });
    }
    const pref = path.match(/^\/api\/rika\/preferences\/(\d+)(\/delete)?$/);
    if (pref) {
      const id = Number(pref[1]);
      if (pref[2]) {
        state.prefs = state.prefs.filter((item) => item.id !== id);
        return json({ deleted: id });
      }
      const preference = { ...state.prefs.find((item) => item.id === id), text: body.text, updated_at: iso() };
      state.prefs = state.prefs.map((item) => (item.id === id ? preference : item));
      return json({ preference });
    }
    if (path === "/api/rika/memory/rebuild") {
      index = SOURCES;
      return json({ index: memory().index });
    }
    return json({ error: `No stand-in for ${path}` }, 404);
  });
  return { posts, gets, state, send, connected: () => Boolean(socket) };
}

async function open(hash, { reducedMotion, theme, ...options } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion });
  const backend = await fakeBackend(context, options);
  // The theme is a build preference: the harness's stand-in build starts on it.
  if (theme) await context.addInitScript((id) => { window.__harnessBuild = { theme: { base: id } }; }, theme);
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`tests/rika-settings-harness.html#${hash}`, base).href);
  await page.waitForFunction(() => !!window.rikaSettingsHarness);
  await page.waitForFunction(() => window.rikaSettingsHarness.connected());
  return { page, backend, problems, context };
}
const index = (page) => page.getByRole("navigation", { name: "Settings pages" });
const region = (page, name) => page.getByRole("region", { name, exact: true });

test("Settings has a Rika group: Routing memory, Email and Memory, each deep-linked and found by keyword", async () => {
  const { page, problems, context } = await open("settings/rika-routing");
  await region(page, "Routing memory").waitFor();
  assert.equal(await page.evaluate(() => location.hash), "#settings/rika-routing");
  const groups = await index(page).locator("h2").allTextContents();
  assert.equal(groups.at(-1), "Rika");
  const rika = index(page).locator("h2", { hasText: "Rika" }).locator("+ ul a");
  assert.deepEqual((await rika.allTextContents()).map((text) => text.trim()), ["Routing memory", "Email", "Memory"]);
  assert.equal((await index(page).locator('[aria-current="page"]').textContent()).trim(), "Routing memory");
  await index(page).getByRole("link", { name: "Email", exact: true }).click();
  await region(page, "Email").waitFor();
  assert.equal(await page.evaluate(() => location.hash), "#settings/rika-email");
  await index(page).getByRole("link", { name: "Memory", exact: true }).click();
  await region(page, "Memory").waitFor();
  assert.equal(await page.evaluate(() => location.hash), "#settings/rika-memory");
  const find = page.getByRole("searchbox", { name: "Find a setting" });
  const names = () => index(page).locator("a, button").allTextContents().then((texts) => texts.map((text) => text.trim()));
  await find.fill("gmail");
  assert.deepEqual(await names(), ["Email"]);
  await find.fill("cache");
  assert.deepEqual(await names(), ["Routing memory"]);
  await find.fill("preferences");
  assert.deepEqual(await names(), ["Memory"]);
  await find.fill("rika");
  assert.deepEqual(await names(), ["Routing memory", "Email", "Memory"]);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Routing memory: the 14-day cost trend, today's totals, one row per kind of request, and Forget", async () => {
  const { page, backend, problems, context } = await open("settings/rika-routing");
  const memory = region(page, "Routing memory");
  await memory.getByRole("table").waitFor();
  await memory.getByText("Avg cost per request · 14 days").waitFor();
  assert.match(await memory.getByRole("img").getAttribute("aria-label"), /from \$0\.0061 to \$0\.0019 over 14 days/);
  assert.equal(await memory.locator("path.spark").count(), 1);
  await memory.getByText("$0.0061 → $0.0019").waitFor();
  await memory.getByText("64 requests · 18k tokens · $0.031 · cache hits 78%").waitFor();
  assert.deepEqual((await memory.locator("thead th").allInnerTexts()).map((text) => text.trim()), ["Kind of request", "Tier", "Tool path", "Uses", "Avg tokens", "Avg cost", "Forget"]);
  const rows = memory.locator("tbody tr");
  assert.equal(await rows.count(), 3);
  const card = rows.filter({ hasText: "Add a flashcard" });
  assert.deepEqual((await card.locator("td").allInnerTexts()).slice(0, 6).map((text) => text.trim().toLowerCase()), ["add a flashcard", "fast", "find_deck → add_card", "128", "1.2k", "$0.0002"]);
  const move = rows.filter({ hasText: "Move a calendar block" });
  assert.equal(await move.locator('[data-tier="direct"]', { hasText: /shortcut/i }).count(), 1, "the shortcut has no badge");
  assert.match(await move.locator(".shortcut").getAttribute("title"), /move \{event\} to \{time\} · used 12 times/);
  assert.match(await move.innerText(), /\$0\b/);
  assert.match(await rows.filter({ hasText: "Plan a day" }).innerText(), /escalated 2×/);
  // Forget appears on hover or focus and drops the row.
  const forget = card.getByRole("button", { name: "Forget Add a flashcard" });
  await card.hover();
  await forget.click();
  await until(() => backend.posts.length === 1, "the forget request");
  assert.deepEqual(backend.posts[0], { path: "/api/rika/routes/forget", body: { signature: "add-card" } });
  await rows.filter({ hasText: "Add a flashcard" }).waitFor({ state: "detached" });
  assert.equal(await rows.count(), 2);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Routing memory with nothing learned yet shows its empty state", async () => {
  const { page, problems, context } = await open("settings/rika-routing", { routes: [] });
  const memory = region(page, "Routing memory");
  await memory.getByRole("heading", { name: "Nothing learned yet" }).waitFor();
  assert.equal(await memory.getByRole("table").count(), 0);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Email: the demo mailbox, auto-send off for every kind until you confirm one, and the hold time", async () => {
  const { page, backend, problems, context } = await open("settings/rika-email");
  const email = region(page, "Email");
  await email.getByText("Demo mailbox — nothing is really sent").waitFor();
  assert.equal(await email.getByRole("button", { name: "Connect" }).count(), 0);
  for (const name of ["Scheduling", "Confirmations", "Thanks"])
    assert.equal(await email.getByRole("switch", { name: `Auto-send ${name}` }).getAttribute("aria-checked"), "false");
  const scheduling = email.getByRole("switch", { name: "Auto-send Scheduling" });
  // Turning one on asks first; Cancel leaves it off and sends nothing.
  await scheduling.click();
  const ask = page.getByRole("dialog", { name: "Auto-send scheduling?" });
  await ask.waitFor();
  assert.match(await ask.innerText(), /without asking you first/);
  await ask.getByRole("button", { name: "Cancel" }).click();
  await ask.waitFor({ state: "hidden" });
  assert.equal(await scheduling.getAttribute("aria-checked"), "false");
  assert.deepEqual(backend.posts, []);
  await scheduling.click();
  await ask.getByRole("button", { name: "Turn on" }).click();
  await until(() => backend.posts.length === 1, "the auto-send setting");
  assert.deepEqual(backend.posts[0], { path: "/api/rika/mail/settings", body: { kind: "scheduling", auto: true, confirm: true } });
  await page.waitForFunction(() => document.querySelector('[aria-label="Auto-send Scheduling"]')?.getAttribute("aria-checked") === "true");
  // Turning it off needs no confirmation.
  await scheduling.click();
  await until(() => backend.posts.length === 2, "turning auto-send off");
  assert.deepEqual(backend.posts[1].body, { kind: "scheduling", auto: false });
  await page.waitForFunction(() => document.querySelector('[aria-label="Auto-send Scheduling"]')?.getAttribute("aria-checked") === "false");
  // The hold: honest copy, and the select changes it.
  await email.getByText("Gmail's API can't delay or undo a send, so rika! holds each send on this machine for 30 s; after that it can't be unsent. If rika! closes during the hold, the mail stays a draft.").waitFor();
  await email.getByRole("combobox", { name: "Hold each send" }).click();
  await page.getByRole("option", { name: "60 s" }).click();
  await until(() => backend.posts.length === 3, "the hold setting");
  assert.deepEqual(backend.posts[2], { path: "/api/rika/mail/settings", body: { hold_seconds: 60 } });
  await email.getByText(/holds each send on this machine for 60 s/).waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("Email drafts: the original, an editable reply, Send with an Undo-send countdown, Discard, and Check mail now", async () => {
  const sent = draft("b", { to: "Jordan Lee <jordan@example.test>", subject: "Re: Ch. 7 problem set", reply_kind: "thanks", status: "sent", auto: true, sent_at: iso(-30_000) });
  const { page, backend, problems, context } = await open("settings/rika-email", { drafts: [draft("a"), sent] });
  const email = region(page, "Email");
  const list = email.getByRole("list", { name: "Drafts" });
  const maya = list.getByRole("listitem", { name: /Reply to Maya Chen/ });
  await maya.waitFor();
  assert.match(await maya.innerText(), /Could you share a few windows next Tuesday or Thursday\?/);
  assert.match(await maya.innerText(), /Maya Chen <maya@example\.test>/);
  const jordan = list.getByRole("listitem", { name: /Reply to Jordan Lee/ });
  assert.match(await jordan.innerText(), /Gmail can't unsend it/);
  assert.equal(await jordan.getByRole("textbox").count(), 0, "a sent reply is still editable");
  // Edit, then leave the field: the reply saves.
  const reply = maya.getByRole("textbox", { name: "Reply to Maya Chen <maya@example.test>" });
  await reply.fill("Hi Maya,\n\nTuesday at 2pm works.\n\nBest,\nAlex");
  await reply.blur();
  await until(() => backend.posts.length === 1, "the draft save");
  assert.deepEqual(backend.posts[0], { path: `/api/rika/mail/drafts/${"a".padEnd(32, "0")}`, body: { body: "Hi Maya,\n\nTuesday at 2pm works.\n\nBest,\nAlex" } });
  // Send is one tap; the send is held and counts down with Undo.
  await maya.getByRole("button", { name: "Send", exact: true }).click();
  const undo = maya.getByRole("button", { name: /^Undo send · 0:(2\d|30)$/ });
  await undo.waitFor();
  assert.equal(backend.posts[1].path, `/api/rika/mail/drafts/${"a".padEnd(32, "0")}/send`);
  const first = await undo.innerText();
  await page.waitForFunction((before) => [...document.querySelectorAll("button")].some((node) => /^Undo send · /.test(node.textContent.trim()) && node.textContent.trim() !== before), first);
  await undo.click();
  await maya.getByText("Send cancelled; still a draft").waitFor();
  assert.equal(backend.posts[2].path, `/api/rika/mail/drafts/${"a".padEnd(32, "0")}/undo`);
  assert.equal(await maya.getByRole("button", { name: "Send", exact: true }).count(), 1);
  // Check mail now writes new drafts and says so.
  await email.getByRole("button", { name: "Check mail now" }).click();
  await email.getByText("2 new messages · 1 draft written").waitFor();
  await list.getByRole("listitem", { name: /Reply to Brightside Dental/ }).waitFor();
  // Discard removes a draft from the list.
  await maya.getByRole("button", { name: "Discard" }).click();
  await maya.waitFor({ state: "detached" });
  assert.equal(backend.posts.at(-1).path, `/api/rika/mail/drafts/${"a".padEnd(32, "0")}/discard`);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Email follows the stream: a held send that goes out turns into Sent — Gmail can't unsend it", async () => {
  const held = draft("d", { status: "scheduled", auto: true, send_after: iso(20_000), updated_at: iso(-1000) });
  const { page, backend, problems, context } = await open("settings/rika-email", { drafts: [held] });
  const item = region(page, "Email").getByRole("listitem", { name: /Reply to Maya Chen/ });
  await item.getByRole("button", { name: /^Undo send · / }).waitFor();
  backend.send({ type: "mail", mail: { ...held, status: "sent", send_after: null, sent_at: iso(), updated_at: iso() } });
  await item.getByText(/Gmail can't unsend it/).waitFor();
  assert.equal(await item.getByRole("button", { name: /Undo send/ }).count(), 0);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Email without Gmail: the setup text and Connect, which waits for the sign-in to finish", async () => {
  const mailbox = { ...MAILBOX, demo: false, connected: false, setup: "Connect Gmail to let Rika draft replies. Only the mail you choose is read." };
  const { page, backend, problems, context } = await open("settings/rika-email", { mailbox, drafts: [] });
  const email = region(page, "Email");
  await email.getByText(mailbox.setup).waitFor();
  assert.equal(await email.getByRole("button", { name: "Check mail now" }).count(), 0);
  await email.getByRole("button", { name: "Connect" }).click();
  await email.getByText("Connected as alex@example.test").waitFor();
  assert.deepEqual(backend.posts, [{ path: "/api/rika/mail/connect", body: {} }]);
  assert.ok(backend.gets.filter((path) => path === "/api/tasks/t1").length >= 2);
  await email.getByRole("button", { name: "Check mail now" }).waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("Memory: a debounced search over the index with source labels, counts per source, and the notes note", async () => {
  const { page, backend, problems, context } = await open("settings/rika-memory");
  const memory = region(page, "Memory");
  const sources = memory.getByRole("list", { name: "Indexed sources" });
  await sources.getByRole("listitem").first().waitFor();
  assert.deepEqual(await sources.getByRole("listitem").allInnerTexts(), ["Pages\n412", "Blocks\n4,812", "Calendar\n1,240", "Email\n38"]);
  await memory.getByText("6,502 items").waitFor();
  await memory.getByText("Note text stays local: Rika only sends note titles to the model.").waitFor();
  const search = memory.getByRole("searchbox", { name: "Search what Rika knows" });
  await search.pressSequentially("study", { delay: 30 });
  const results = memory.getByRole("list", { name: "Search results" });
  await results.getByRole("listitem").filter({ hasText: "Study block" }).waitFor();
  assert.deepEqual(backend.gets.filter((path) => path.startsWith("/api/rika/memory?")), ["/api/rika/memory?q=study"], "the search was not debounced");
  const hit = results.getByRole("listitem").first();
  assert.match(await hit.innerText(), /calendar/i);
  assert.equal(await hit.getByRole("link").getAttribute("href"), "#calendar");
  await search.fill("zzz");
  await results.getByText("Nothing indexed matches “zzz”.").waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("Memory: preferences edit in place on Enter or blur, forget with ×, and add from the field", async () => {
  const { page, backend, problems, context } = await open("settings/rika-memory", { notesShared: true });
  const memory = region(page, "Memory");
  await memory.getByText(/Notes AI sharing is on/).waitFor();
  const prefs = memory.getByRole("list", { name: "Preferences" });
  const first = prefs.getByRole("textbox").first();
  await first.waitFor();
  assert.equal(await first.inputValue(), "Keeps evenings after 9pm free of study blocks");
  assert.match(await prefs.getByRole("listitem").first().innerText(), /You told Rika/);
  await first.fill("Keeps evenings after 10pm free of study blocks");
  await first.press("Enter");
  await until(() => backend.posts.length === 1, "the preference save");
  assert.deepEqual(backend.posts[0], { path: "/api/rika/preferences/1", body: { text: "Keeps evenings after 10pm free of study blocks" } });
  await prefs.getByText(/Edited by you/).waitFor();
  // Leaving an unchanged field sends nothing.
  await prefs.getByRole("textbox").nth(1).focus();
  await prefs.getByRole("textbox").nth(1).blur();
  await page.waitForTimeout(100);
  assert.equal(backend.posts.length, 1);
  await prefs.getByRole("button", { name: "Forget “Signs off emails “Best, Alex””" }).click();
  await until(() => backend.posts.length === 2, "the forget request");
  assert.deepEqual(backend.posts[1], { path: "/api/rika/preferences/2/delete", body: {} });
  await page.waitForFunction(() => document.querySelectorAll('[aria-label="Preferences"] li').length === 1);
  const add = memory.getByRole("textbox", { name: "Tell Rika something to remember" });
  await add.fill("Prefers morning interviews");
  await add.press("Enter");
  await until(() => backend.posts.length === 3, "the new preference");
  assert.deepEqual(backend.posts[2], { path: "/api/rika/preferences", body: { text: "Prefers morning interviews" } });
  await page.waitForFunction(() => [...document.querySelectorAll('[aria-label="Preferences"] input')].some((node) => node.value === "Prefers morning interviews"));
  assert.equal(await add.inputValue(), "");
  assert.deepEqual(problems, []);
  await context.close();
});

test("Memory builds an index that was never built when first opened, so the counts are real", async () => {
  const unbuilt = SOURCES.map((source) => ({ ...source, items: 0, refreshed_at: null }));
  const { page, backend, problems, context } = await open("settings/rika-memory", { sources: unbuilt });
  const memory = region(page, "Memory");
  await memory.getByText("6,502 items").waitFor();
  assert.deepEqual(backend.posts, [{ path: "/api/rika/memory/rebuild", body: {} }]);
  assert.deepEqual(problems, []);
  await context.close();
});

for (const theme of ["kitty", "liquid-glass"]) {
  test(`the three Rika pages draw in the ${theme} theme: one surface, readable text, no nested boxes`, async () => {
    const { page, problems, context } = await open("settings/rika-routing", { theme });
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), theme);
    for (const [link, name] of [["Routing memory", "Routing memory"], ["Email", "Email"], ["Memory", "Memory"]]) {
      await index(page).getByRole("link", { name: link, exact: true }).click();
      const shown = region(page, name);
      await shown.locator(".set-row").first().waitFor();
      const look = await shown.evaluate((node) => {
        const style = getComputedStyle(node);
        const text = getComputedStyle(node.querySelector(".set-desc") ?? node);
        const boxes = [...node.querySelectorAll("*")].filter((child) => {
          if (child.closest("button, input, select, textarea, label, a, [role=switch], [role=img]")) return false;
          const box = child.getBoundingClientRect();
          if (box.width < 120 || box.height < 40) return false;
          const own = getComputedStyle(child);
          const filled = own.backgroundColor !== "rgba(0, 0, 0, 0)" || own.backgroundImage !== "none";
          return filled && ["Top", "Right", "Bottom", "Left"].every((side) => parseFloat(own[`border${side}Width`]) > 0 && own[`border${side}Style`] !== "none");
        });
        return { border: parseFloat(style.borderTopWidth), color: text.color, boxes: boxes.map((child) => child.className) };
      });
      assert.ok(look.border > 0, `${name} is not drawn as a surface`);
      assert.notEqual(look.color, "rgba(0, 0, 0, 0)");
      assert.deepEqual(look.boxes, [], `${name} nests boxes inside its surface`);
    }
    assert.deepEqual(problems, []);
    await context.close();
  });
}

test("under Reduce motion Forget appears without a fade", async () => {
  const { page, problems, context } = await open("settings/rika-routing", { reducedMotion: "reduce" });
  const forget = region(page, "Routing memory").getByRole("button", { name: "Forget Add a flashcard" });
  await forget.waitFor();
  assert.ok(parseFloat(await forget.evaluate((node) => getComputedStyle(node).transitionDuration)) <= 0.001, "Forget still fades");
  assert.deepEqual(problems, []);
  await context.close();
});
