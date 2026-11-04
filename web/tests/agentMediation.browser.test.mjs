// Agent mediation's UI (kitty.web_agents, PR #77) in a real browser: Settings → Agents (the
// host's guarantee, the sessions with their journal, Hold/Release and End, the policy with
// Simulate and Apply) and the guarantee badges on Rika's agent cards, the Rika bar's chips
// and the agent-start notice. The pages are the real Settings view, Rika UI and new-agent
// strip; the backend is a scripted stand-in served through Playwright routes, so no agent,
// launcher or confinement ever runs and every record is synthetic.
// Run from web/ with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/agentMediation.browser.test.mjs
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

const iso = (offset = 0) => new Date(Date.now() + offset).toISOString().replace(/\.\d+Z$/, "+00:00");
const SOURCE = "# Demo policy (version 1)\nmode: enforce\ndefault: deny\nrules:\n  - decision: allow\n    purposes: [notes, \"notes.*\"]\n";
const HOST = { kernel: "Linux 7.2.5", abi: 10, guarantee: "full", closed: ["files", "ptrace", "tcp", "abstract-unix-sockets", "signals", "unix-sockets", "tiocsti"], open: [] };
const FULL = "Full: every route to rika!'s data from this tab goes through rika!'s API, and every access is journaled.";
const session = (id, extra) => ({
  id, kind: "omp", name: id, host: "herdr", pane: "w1:p2", cwd: "/work/parser", state: "confined", guarantee: "full", abi: 10, pid: null,
  message: FULL, policy_version: 1, created_at: iso(-2_520_000), confined_at: iso(-2_518_000), ended_at: null, report: null, token: true, ...extra,
});
const SESSIONS = [
  { ...session("omp-3f2a91c4"), accesses: 230, held: false },
  { ...session("codex-7be0d215", { kind: "codex", host: "tern", pane: "5", state: "unconfined", guarantee: "attribution", abi: null, token: false,
    message: "Attribution only: the launcher could not confine this tab (no Landlock). The agent runs without a token; its kitty notes writes carry this session.",
    confined_at: null }), accesses: 0, held: false },
  { ...session("claude-c41e9a07", { kind: "claude", state: "refused", guarantee: "none", token: false, confined_at: null, ended_at: iso(-90_000_000),
    message: "rika! could not verify this launch, so it issued no token: the reporting process is not in the Agent tab" }), accesses: 0, held: false },
];
const TOOLS = ["get_page", "read", "search_blocks", "read", "append_blocks", "write", "update_block", "write"];
/** 230 journal rows of the first session, newest first. */
const JOURNAL = Array.from({ length: 230 }, (_, index) => {
  const id = 230 - index;
  const pick = (id % 4) * 2;
  return { id, tool: TOOLS[pick], op: TOOLS[pick + 1], purpose: "notes", block_id: `b${String(id).padStart(7, "0")}-0000-4000-8000-000000000000`,
    seq: id, decision: "allow", mode: "enforce", policy_version: 1, at: iso((id - 231) * 10_000) };
});
const PURPOSES = { notes: "Note pages and their blocks", "notes.quick": "Quick notes", "notes.tags": "Tag names, keywords and counts" };
const AGENT_TOOLS = { get_page: { op: "read", scope: "notes.read" }, append_blocks: { op: "write", scope: "notes.write" } };
const FLIPS = [3, 7].map((id) => ({ id, session: "omp-3f2a91c4", agent: "omp", name: "omp-3f2a91c4", tool: "append_blocks", op: "write", purpose: "notes",
  block_id: JOURNAL[230 - id].block_id, seq: id, at: JOURNAL[230 - id].at, mode: "enforce", policy_version: 1, recorded: "allow", draft: "deny" }));
const RIKA_AGENTS = [
  { id: "herdr:w1:p2", kind: "omp", name: "omp · parser", status: "working", task: "Refactor the parser", line: "tests 14/32", since: Date.now() - 60_000, where: { herdr_pane: "w1:p2" }, now: null, guarantee: "full" },
  { id: "tern:5", kind: "codex", name: "codex · cards", status: "idle", task: "Import the deck", line: "", since: Date.now() - 90_000, where: { tern_pane: "5" }, now: null, guarantee: "attribution" },
  { id: "tern:6", kind: "claude", name: "claude · notes", status: "blocked", task: "Summarise", line: "Allow?", since: Date.now() - 30_000, where: { tern_pane: "6" }, now: null, guarantee: "none" },
];

/** Polls a condition on the stand-in backend (requests land after the click resolves). */
async function until(condition, label) {
  for (let tries = 0; tries < 150; tries += 1) {
    if (condition()) return;
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  assert.fail(`timed out waiting for ${label}`);
}

/** A scripted agent-mediation backend for one browser context: a fake for these harnesses only. */
async function fakeBackend(context, { host = HOST, agents = RIKA_AGENTS } = {}) {
  const posts = [];
  const gets = [];
  const state = { sessions: structuredClone(SESSIONS), held: new Set(), version: 1, source: SOURCE };
  const overview = () => ({
    host, sessions: state.sessions.map((item) => ({ ...item, held: state.held.has(item.id) })),
    policy: { version: state.version, mode: "enforce", source: state.source },
    versions: [...Array.from({ length: state.version }, (_, index) => ({ version: state.version - index, created_at: iso(-172_800_000), mode: "enforce" })),
      { version: 0, created_at: null, mode: "enforce" }],
    retention: { floor: 1, horizon_seq: 0, reasons: { replay_window: 1, user_hold: state.held.size }, cap: 200000, over_cap: [], journal_rows: 230 },
    purposes: PURPOSES, tools: AGENT_TOOLS,
  });
  const detail = (id, before) => {
    const found = state.sessions.find((item) => item.id === id);
    const { accesses: _accesses, held: _held, ...described } = found;
    const rows = id === "omp-3f2a91c4" ? JOURNAL.filter((row) => before === undefined || row.id < before) : [];
    return { session: described, journal: rows.slice(0, 200), next: rows.length > 200 ? rows[199].id : null, held: state.held.has(id),
      planted: id === "omp-3f2a91c4" ? [{ path: "/work/parser/.envrc", change: "created" }] : [] };
  };
  await context.routeWebSocket("**/api/rika/stream", () => {});
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
    if (path === "/api/rika/agents") return json({ agents });
    if (path === "/api/rika/brief") return json({ brief: null });
    if (path === "/api/rika/alerts") return json({ alerts: [] });
    if (path === "/api/agents") return json(overview());
    if (path === "/api/agents/session") {
      const before = url.searchParams.get("before");
      return json(detail(url.searchParams.get("id"), before === null ? undefined : Number(before)));
    }
    if (path === "/api/agents/sessions/hold") {
      if (body.hold) state.held.add(body.id);
      else state.held.delete(body.id);
      return json(detail(body.id));
    }
    if (path === "/api/agents/sessions/end") {
      state.sessions = state.sessions.map((item) => (item.id === body.id ? { ...item, state: "ended", ended_at: iso(), message: "Ended in Settings → Agents; its token was revoked" } : item));
      return json(detail(body.id));
    }
    if (path === "/api/agents/policy/simulate") {
      if (body.source.includes("mode: loud")) return json({ error: "The policy has problems", problems: ["mode is enforce or observe"] }, 400);
      const denies = body.source.includes("ops: [write]");
      return json({ draft: {}, replayed: 256, flips: denies ? FLIPS : [], counts: { allow_to_deny: denies ? 2 : 0, deny_to_allow: 0 },
        total: denies ? 2 : 0, truncated: false, sessions: denies ? ["omp-3f2a91c4"] : [], enforced: true });
    }
    if (path === "/api/agents/policy/apply") {
      state.version += 1;
      state.source = body.source;
      return json({ version: state.version, policy: { mode: "enforce" }, ...overview() });
    }
    if (path === "/api/terminal/agents" && request.method() === "GET")
      return json({ binary: "/opt/kitty-herdr", session: "kitty", cwd: "/work", default: "omp", kinds: ["omp", "claude", "codex"] });
    if (path === "/api/terminal/agents") {
      const guarantee = body.kind === "omp" ? "full" : "attribution";
      return json({ tab: "w1:t3", pane: "w1:p3", name: `${body.kind}-1`, kind: body.kind,
        mediation: { session: "s1", state: guarantee === "full" ? "confined" : "unconfined", guarantee,
          message: guarantee === "full" ? FULL : "Attribution only: API accesses are journaled, but rika! can be traced." } });
    }
    if (path === "/api/terminal/agent-skills") return json({ can_open: false, kinds: [] });
    return json({ error: `No stand-in for ${path}` }, 404);
  });
  return { posts, gets, state };
}

async function open(hash, { theme, ...options } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const backend = await fakeBackend(context, options);
  if (theme) await context.addInitScript((id) => { window.__harnessBuild = { theme: { base: id } }; }, theme);
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`tests/rika-settings-harness.html#${hash}`, base).href);
  await page.waitForFunction(() => !!window.rikaSettingsHarness);
  return { page, backend, problems, context };
}
const agentsPage = async (page) => {
  const agents = page.getByRole("region", { name: "Agents", exact: true });
  await agents.getByRole("table").first().waitFor();
  return agents;
};
const badge = (scope, label) => scope.getByRole("img", { name: `Guarantee: ${label}`, exact: true });

test("Settings → Agents: an index page with the host's guarantee, the sessions and the policy in force", async () => {
  const { page, problems, context } = await open("settings/agents");
  const agents = await agentsPage(page);
  const index = page.getByRole("navigation", { name: "Settings pages" });
  assert.equal((await index.locator('[aria-current="page"]').textContent()).trim(), "Agents");
  const connections = index.locator("h2", { hasText: "Connections" }).locator("+ ul a");
  assert.deepEqual((await connections.allTextContents()).map((text) => text.trim()), ["Connected apps", "Agents", "Keys", "Devices"]);

  const host = agents.getByRole("region", { name: "This computer" });
  await host.getByText("Linux 7.2.5").waitFor();
  await host.getByText("ABI 10").waitFor();
  await badge(host, "Full").waitFor();
  assert.match(await host.innerText(), /Still open\s+Nothing/);

  const rows = agents.getByRole("region", { name: "Sessions" }).locator("tbody tr");
  assert.equal(await rows.count(), 3);
  assert.match(await rows.nth(0).innerText(), /omp-3f2a91c4[\s\S]*Confined[\s\S]*230/);
  await badge(rows.nth(0), "Full").waitFor();
  await badge(rows.nth(1), "Attribution only").waitFor();
  await badge(rows.nth(2), "Not mediated").waitFor();
  assert.match(await rows.nth(2).innerText(), /Refused/);

  const policy = agents.getByRole("region", { name: "Policy" });
  assert.match(await policy.innerText(), /Version 1[\s\S]*Enforce[\s\S]*Applied/);
  const editor = policy.getByRole("textbox", { name: "Policy source (YAML)" });
  assert.equal(await editor.inputValue(), SOURCE);
  assert.match(await editor.evaluate((node) => getComputedStyle(node).fontFamily), /JetBrains Mono/);
  assert.ok((await editor.boundingBox()).width > 500, "the editor does not span the page");
  assert.equal(await policy.getByRole("img", { name: /Draft differs/ }).count(), 0);
  await editor.fill(`${SOURCE}# edited\n`);
  await policy.getByRole("img", { name: "Draft differs from version 1" }).waitFor();

  await agents.getByRole("region", { name: "Retention" }).getByText("230").waitFor();
  await agents.getByRole("region", { name: "Purposes" }).getByText("Quick notes").waitFor();
  await agents.getByRole("region", { name: "Tools" }).getByText("append_blocks").waitFor();
  // Q4: labels are Inter semibold sentence case; controls sit on the 28px line.
  const label = await agents.locator(".set-label").first().evaluate((node) => getComputedStyle(node).fontFamily);
  assert.match(label, /^"?Inter/);
  for (const name of ["Simulate", "Apply"]) assert.equal((await policy.getByRole("button", { name, exact: true }).boundingBox()).height, 28);
  assert.deepEqual(problems, []);
  await context.close();
});

test("an attribution-only host lists what stays open", async () => {
  const open_ = ["rika! can be traced", "input can be pushed into the terminal (TIOCSTI/TIOCLINUX)"];
  const { page, problems, context } = await open("settings/agents", { host: { ...HOST, abi: 6, guarantee: "attribution", open: open_ } });
  const host = (await agentsPage(page)).getByRole("region", { name: "This computer" });
  await badge(host, "Attribution only").waitFor();
  assert.deepEqual(await host.getByRole("list", { name: "Still open" }).getByRole("listitem").allInnerTexts(), open_);
  assert.deepEqual(problems, []);
  await context.close();
});

test("opening a session shows its journal (who, what, when, why), pages with Older and lists planted files", async () => {
  const { page, backend, problems, context } = await open("settings/agents");
  const agents = await agentsPage(page);
  await agents.getByRole("button", { name: "omp-3f2a91c4", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "omp-3f2a91c4" });
  const journal = drawer.getByRole("list", { name: "Journal" });
  await journal.waitFor();
  assert.equal(await journal.getByRole("listitem").count(), 200);
  const newest = journal.getByRole("listitem").first();
  assert.match(await newest.innerText(), /\d\d:\d\d:\d\d[\s\S]*Wrote notes b0000230[\s\S]*allow[\s\S]*append_blocks/);
  assert.equal(await newest.getAttribute("title"), "Policy version 1 · enforce · seq 230");
  await badge(drawer, "Full").waitFor();
  assert.match(await drawer.innerText(), /Policy\s+Version 1/);
  const planted = drawer.getByRole("list", { name: "Planted files" }).getByRole("listitem");
  assert.deepEqual(await planted.allInnerTexts(), [".envrc\ncreated"]);
  assert.equal(await planted.locator("code").getAttribute("title"), "/work/parser/.envrc");

  await drawer.getByRole("button", { name: "Older" }).click();
  await until(() => backend.gets.includes("/api/agents/session?id=omp-3f2a91c4&before=31"), "the older page");
  await page.waitForFunction(() => document.querySelectorAll('[aria-label="Journal"] li').length === 230);
  assert.equal(await drawer.getByRole("button", { name: "Older" }).count(), 0);
  assert.match(await journal.getByRole("listitem").last().innerText(), /b0000001/);

  // Another session: no token, so nothing was journaled; Escape in the drawer closes it.
  await agents.getByRole("button", { name: "codex-7be0d215", exact: true }).click();
  const codex = page.getByRole("dialog", { name: "codex-7be0d215" });
  await codex.getByText("No token, so nothing was journaled.").waitFor();
  await codex.getByText(/could not confine this tab/).waitFor();
  await codex.getByRole("button", { name: "Hold", exact: true }).focus();
  await page.keyboard.press("Escape");
  await codex.waitFor({ state: "detached" });
  assert.deepEqual(problems, []);
  await context.close();
});

test("Hold toggles to Release and back, and the list follows", async () => {
  const { page, backend, problems, context } = await open("settings/agents");
  const agents = await agentsPage(page);
  await agents.getByRole("button", { name: "omp-3f2a91c4", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "omp-3f2a91c4" });
  await drawer.getByRole("list", { name: "Journal" }).waitFor();
  await drawer.getByRole("button", { name: "Hold", exact: true }).click();
  await drawer.getByRole("button", { name: "Release", exact: true }).waitFor();
  assert.deepEqual(backend.posts.at(-1), { path: "/api/agents/sessions/hold", body: { id: "omp-3f2a91c4", hold: true } });
  await drawer.locator(".held").waitFor();
  await agents.locator("tbody tr").first().locator(".held").waitFor();
  await drawer.getByRole("button", { name: "Release", exact: true }).click();
  await drawer.getByRole("button", { name: "Hold", exact: true }).waitFor();
  assert.deepEqual(backend.posts.at(-1), { path: "/api/agents/sessions/hold", body: { id: "omp-3f2a91c4", hold: false } });
  await agents.locator("tbody tr").first().locator(".held").waitFor({ state: "detached" });
  assert.deepEqual(problems, []);
  await context.close();
});

test("End asks first: Cancel keeps the session, End session revokes it", async () => {
  const { page, backend, problems, context } = await open("settings/agents");
  const agents = await agentsPage(page);
  await agents.getByRole("button", { name: "omp-3f2a91c4", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "omp-3f2a91c4" });
  await drawer.getByRole("list", { name: "Journal" }).waitFor();
  await drawer.getByRole("button", { name: "End", exact: true }).click();
  const confirm = page.getByRole("dialog", { name: "End omp-3f2a91c4?" });
  await confirm.getByText("Revokes its token at once. The journal stays.").waitFor();
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await confirm.waitFor({ state: "detached" });
  assert.equal(backend.posts.filter((post) => post.path === "/api/agents/sessions/end").length, 0);

  await drawer.getByRole("button", { name: "End", exact: true }).click();
  await confirm.getByRole("button", { name: "End session" }).click();
  await until(() => backend.posts.some((post) => post.path === "/api/agents/sessions/end"), "the end request");
  assert.deepEqual(backend.posts.at(-1), { path: "/api/agents/sessions/end", body: { id: "omp-3f2a91c4" } });
  await drawer.locator(".state", { hasText: "Ended" }).waitFor();
  assert.equal(await drawer.getByRole("button", { name: "End", exact: true }).count(), 0, "an ended session still offers End");
  assert.match(await agents.locator("tbody tr").first().innerText(), /Ended/);
  // Refused sessions never offered End.
  await agents.getByRole("button", { name: "claude-c41e9a07", exact: true }).click();
  const refused = page.getByRole("dialog", { name: "claude-c41e9a07" });
  await refused.getByRole("button", { name: "Hold", exact: true }).waitFor();
  assert.equal(await refused.getByRole("button", { name: "End", exact: true }).count(), 0);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Simulate lists the flips; Apply is enabled only after simulating that exact source, and confirms", async () => {
  const { page, backend, problems, context } = await open("settings/agents");
  const policy = (await agentsPage(page)).getByRole("region", { name: "Policy" });
  const editor = policy.getByRole("textbox", { name: "Policy source (YAML)" });
  const apply = policy.getByRole("button", { name: "Apply", exact: true });
  const simulate = policy.getByRole("button", { name: "Simulate", exact: true });
  assert.equal(await apply.isDisabled(), true, "Apply before any simulation");
  const draft = SOURCE.replace("rules:\n", "rules:\n  - decision: deny\n    ops: [write]\n");
  await editor.fill(draft);
  assert.equal(await apply.isDisabled(), true, "Apply for an edited source that was never simulated");

  await simulate.click();
  const result = page.getByRole("region", { name: "Simulation" });
  await result.getByText("256 replayed · 2 allow → deny").waitFor();
  assert.deepEqual(backend.posts.at(-1), { path: "/api/agents/policy/simulate", body: { source: draft } });
  const flips = result.getByRole("table", { name: "Flips" }).locator("tbody tr");
  const [summary, table] = [await result.locator(".summary").boundingBox(), await result.getByRole("table").boundingBox()];
  assert.ok(summary.y < table.y && Math.abs(summary.x - table.x) < 2, "the counts are not above the flips");
  assert.equal(await flips.count(), 2);
  assert.match(await flips.first().innerText(), /omp-3f2a91c4[\s\S]*Write notes append_blocks[\s\S]*allow → deny/);
  assert.equal(await apply.isDisabled(), false);

  // An edit needs a new simulation (the old one dims); the exact simulated text is applicable again.
  const edited = `${draft}# and more\n`;
  await editor.fill(edited);
  assert.equal(await apply.isDisabled(), true, "Apply stayed on after the source changed");
  assert.match(await result.getAttribute("class"), /stale/);
  await editor.fill(draft);
  assert.equal(await apply.isDisabled(), false, "the simulated source is not applicable");
  await editor.fill(edited);
  await simulate.click();
  await until(() => backend.posts.at(-1)?.body?.source === edited, "the second simulation");
  await policy.getByRole("button", { name: "Apply", exact: true, disabled: false }).waitFor();
  assert.doesNotMatch(await result.getAttribute("class"), /stale/);

  await apply.click();
  const confirm = page.getByRole("dialog", { name: "Apply this policy?" });
  await confirm.getByText("Apply as version 2?").waitFor();
  await confirm.getByText("Agents get these rules on their next access.").waitFor();
  assert.equal(backend.posts.filter((post) => post.path === "/api/agents/policy/apply").length, 0, "applied before confirming");
  await confirm.getByRole("button", { name: "Apply policy" }).click();
  await policy.getByRole("status").filter({ hasText: "Version 2 applied." }).waitFor();
  assert.deepEqual(backend.posts.at(-1), { path: "/api/agents/policy/apply", body: { source: edited } });
  assert.match(await policy.innerText(), /Version 2/);
  assert.equal(await apply.isDisabled(), true, "Apply stays on after applying");
  assert.equal(await policy.getByRole("img", { name: /Draft differs/ }).count(), 0);

  // A source with problems lists them and never enables Apply.
  await editor.fill("mode: loud\n");
  await simulate.click();
  await policy.getByRole("list", { name: "Policy problems" }).getByText("mode is enforce or observe").waitFor();
  assert.equal(await page.getByRole("region", { name: "Simulation" }).count(), 0);
  assert.equal(await apply.isDisabled(), true);
  assert.deepEqual(problems, []);
  await context.close();
});

test("guarantee badges show per level on Rika's agent cards and bar chips, in every theme's roles", async () => {
  const { page, problems, context } = await open("settings/agents");
  await agentsPage(page);
  const chips = page.getByRole("group", { name: "Running agents" }).getByRole("button");
  await chips.first().waitFor();
  assert.deepEqual(await chips.evaluateAll((nodes) => nodes.map((node) => node.dataset.guarantee)), ["full", "attribution", "none"]);
  await page.getByRole("button", { name: "omp · parser, working, Full: open its window" }).waitFor();
  await page.getByRole("button", { name: "codex · cards, idle, Attribution only: open its window" }).waitFor();
  await page.getByRole("button", { name: "claude · notes, blocked, Not mediated: open its window" }).waitFor();
  assert.equal(await chips.locator(".guarantee").count(), 3);
  assert.match(await chips.first().getAttribute("title"), /Guarantee: Full/);

  await page.getByRole("button", { name: "Rika history", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Rika history" });
  await drawer.getByRole("button", { name: /^Agents/ }).click();
  for (const [name, label] of [["omp · parser · working", "Full"], ["codex · cards · idle", "Attribution only"], ["claude · notes · blocked", "Not mediated"]]) {
    const card = drawer.getByRole("article", { name });
    await badge(card, label).waitFor();
    assert.equal(await card.locator(".guarantee").innerText(), label);
  }

  // Each theme resolves the three levels to its own success, warning and error roles,
  // readable on its background (light themes included); each level also has its own mark,
  // so a one-accent theme (red) still tells them apart.
  const themes = await page.evaluate(async () => (await import("/src/lib/themes/catalog.ts")).THEMES.map((theme) => theme.id));
  assert.ok(themes.includes("speed") && themes.includes("bloodluxe"));
  for (const theme of themes) {
    await page.evaluate((id) => window.rikaSettingsHarness.setTheme(id), theme);
    const tones = await drawer.locator(".guarantee").evaluateAll((nodes) => {
      const canvas = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
      const rgb = (color) => {
        canvas.clearRect(0, 0, 1, 1);
        canvas.fillStyle = "#000";
        canvas.fillStyle = color;
        canvas.fillRect(0, 0, 1, 1);
        return [...canvas.getImageData(0, 0, 1, 1).data.slice(0, 3)];
      };
      const luminance = ([r, g, b]) => [r, g, b].map((v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; })
        .reduce((sum, c, i) => sum + c * [0.2126, 0.7152, 0.0722][i], 0);
      const ground = rgb(getComputedStyle(document.documentElement).getPropertyValue("--br-void").trim());
      return nodes.map((node) => {
        const text = rgb(getComputedStyle(node).color);
        const [hi, lo] = [luminance(text), luminance(ground)].sort((a, b) => b - a);
        return { level: node.dataset.guarantee, color: text.join(","), mark: node.querySelector("path").getAttribute("d"), contrast: (hi + 0.05) / (lo + 0.05) };
      });
    });
    assert.deepEqual(tones.map((tone) => tone.level), ["full", "attribution", "none"], theme);
    assert.equal(new Set(tones.map((tone) => tone.mark)).size, 3, `${theme}: two levels share a mark`);
    if (!["red"].includes(theme)) assert.equal(new Set(tones.map((tone) => tone.color)).size, 3, `${theme}: the three levels share a colour`);
    for (const tone of tones) assert.ok(tone.contrast >= 3, `${theme}: ${tone.level} badge contrast ${tone.contrast.toFixed(2)}`);
  }
  assert.deepEqual(problems, []);
  await context.close();
});

test("the agent-start notice shows the launch's guarantee and, short of Full, why", async () => {
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const backend = await fakeBackend(context, { agents: [] });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/agent-skills-harness.html", base).href);
  await page.waitForFunction(() => !!window.skillsHarness);
  const strip = page.locator(".agent-tools");
  await strip.getByRole("button", { name: "New Agent tab" }).click();
  await strip.getByRole("button", { name: "Start agent" }).click();
  const status = strip.getByRole("status").filter({ hasText: "Agent tab opened (w1:t3, w1:p3)." });
  await status.waitFor();
  assert.deepEqual(backend.posts.at(-1), { path: "/api/terminal/agents", body: { kind: "omp" } });
  const full = badge(status, "Full");
  await full.waitFor();
  assert.equal(await full.getAttribute("title"), FULL);
  assert.doesNotMatch(await status.innerText(), /every route/, "Full's explanation is printed instead of a tooltip");

  await strip.getByRole("button", { name: "New Agent tab" }).click();
  await strip.getByRole("button", { name: "codex", exact: true }).click();
  await strip.getByRole("button", { name: "Start agent" }).click();
  await badge(status, "Attribution only").waitFor();
  await status.getByText("Attribution only: API accesses are journaled, but rika! can be traced.").waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});
