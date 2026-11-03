// Agent skills (docs/agent-skills-design.md) in a real browser: the now chip and Skills (N)
// on Rika's agent cards, the bar chips' tooltip, the All agent skills view, Open file and
// Copy path, the per-kind opt-in and the new-agent skill checklist. The page is the real
// UI; the backend is a scripted stand-in served through Playwright routes, so nothing
// reaches herdr, an agent or the user's home folder.
// Run from web/ with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/agentSkills.browser.test.mjs
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

const now = (skill, tool, extra = {}) => ({ skill, tool, intent: null, via: "hook", label: [skill, tool].filter(Boolean).join(" · ") || "—", ...extra });
const AGENTS = [
  { id: "herdr:w1:p1", kind: "omp", name: "omp", status: "working", task: "Draw the skills lab", line: "read skill://lavish", since: Date.now() - 252_000,
    where: { herdr_pane: "w1:p1" }, now: now("lavish", "read", { intent: "Reading the lavish skill", via: "extension" }) },
  { id: "herdr:w1:p2", kind: "claude", name: "claude", status: "blocked", task: "Fix the calendar test", line: "Allow Bash?", since: Date.now() - 41_000,
    where: { herdr_pane: "w1:p2" }, now: null },
  { id: "tern:4", kind: "codex", name: "codex", status: "working", task: "Tighten the bullets", line: "exec cat …", since: Date.now() - 96_000,
    where: { tern_pane: "4" }, now: now(null, null, { via: null }) },
];
const SKILLS = {
  omp: ["grill-me", "lavish", "loopy"].map((name) => ({ name, description: `${name} description`, path: `/home/me/.omp/agent/skills/${name}/SKILL.md` })),
  claude: ["docx", "pdf"].map((name) => ({ name, description: `${name} description`, path: `/home/me/.claude/skills/${name}/SKILL.md` })),
  codex: ["imagegen"].map((name) => ({ name, description: "Images", path: `/home/me/.codex/skills/.system/${name}/SKILL.md` })),
};
const LABELS = { omp: "omp", claude: "Claude Code", codex: "Codex" };

/** A scripted backend for one browser context: a fake for this test harness only. */
async function fakeBackend(context, { enabled = { omp: true, claude: false, codex: true }, canOpen = true } = {}) {
  const posts = [];
  const state = { ...enabled };
  const catalog = () => ({
    can_open: canOpen,
    kinds: ["omp", "claude", "codex"].map((kind) => ({
      kind, label: LABELS[kind], enabled: state[kind],
      skills: state[kind] ? SKILLS[kind] : [],
      mcp: state[kind] && kind === "omp" ? [{ name: "figma", enabled: true }, { name: "linear", enabled: false }] : [],
      plugins: state[kind] && kind === "codex" ? [{ name: "sites", enabled: true }] : [],
    })),
  });
  await context.routeWebSocket("**/api/rika/stream", () => {});
  await context.route("**/api/rika/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/rika/history") return route.fulfill({ json: { items: [], has_more: false, next_before: null } });
    if (path === "/api/rika/agents") return route.fulfill({ json: { agents: AGENTS } });
    return route.fulfill({ json: { ok: true, window: "tern", view: null } });
  });
  await context.route("**/api/terminal/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.postData() ? JSON.parse(request.postData()) : undefined;
    if (request.method() === "POST") posts.push({ path, body });
    if (path === "/api/terminal/agent-skills" && request.method() === "GET") return route.fulfill({ json: catalog() });
    if (path === "/api/terminal/agent-skills/settings") {
      state[body.kind] = body.enabled;
      return route.fulfill({ json: catalog() });
    }
    if (path === "/api/terminal/agent-skills/open") return route.fulfill({ json: { ok: true } });
    if (path === "/api/terminal/agents" && request.method() === "GET")
      return route.fulfill({ json: { binary: "/opt/kitty-herdr", session: "kitty", cwd: "/work", default: "omp", kinds: ["omp", "claude", "codex"] } });
    if (path === "/api/terminal/agents")
      return route.fulfill({ json: { tab: "w1:t3", pane: "w1:p3", name: `${body.kind}-1`, kind: body.kind,
        ...(body.kind === "codex" ? { notice: "Codex runs kitty!'s activity hook only after you trust it in /hooks." } : {}) } });
    return route.fulfill({ status: 404, json: { error: `No stand-in for ${path}` } });
  });
  return { posts, state };
}

async function harness(options) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: base });
  const backend = await fakeBackend(context, options);
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/agent-skills-harness.html", base).href);
  await page.waitForFunction(() => !!window.skillsHarness);
  await page.getByRole("region", { name: "Rika" }).waitFor();
  return { page, backend, problems, context };
}
const openAgents = async (page) => {
  await page.getByRole("button", { name: "Rika history", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "Rika history" });
  await drawer.getByRole("button", { name: /^Agents/ }).click();
  return drawer;
};

test("agent cards carry a now chip and Skills (N); the list opens in the drawer with Open file and Copy path", async () => {
  const { page, backend, problems, context } = await harness();
  const drawer = await openAgents(page);
  const omp = drawer.getByRole("article", { name: "omp · working" });
  const chip = omp.locator("[data-now]");
  assert.equal(await chip.innerText(), "now: lavish · read");
  assert.match(await chip.getAttribute("title"), /Reading the lavish skill · What it is using now, from kitty!'s omp extension/);
  // Off for Claude: no chip and no count; codex is on but nothing answered yet.
  const claude = drawer.getByRole("article", { name: "claude · blocked" });
  assert.equal(await claude.locator("[data-now]").count(), 0);
  assert.equal(await claude.getByRole("button", { name: "claude's agent skills" }).innerText(), "Skills");
  assert.equal(await drawer.getByRole("article", { name: "codex · working" }).locator("[data-now]").innerText(), "now: —");
  const skills = omp.getByRole("button", { name: "omp's agent skills, 3" });
  assert.equal((await skills.innerText()).replace(/\s+/g, ""), "Skills3");

  await skills.click();
  const list = drawer.locator(".skills");
  await list.getByText("omp · agent skills").waitFor();
  assert.equal(await drawer.getByRole("article").count(), 0, "the cards stay up while the list shows");
  const lavish = list.locator('[data-skill="lavish"]');
  assert.match(await lavish.innerText(), /^lavish\s*now/i);
  assert.equal(await list.locator('[data-skill="loopy"] em').count(), 0);
  await lavish.getByRole("button", { name: "Open lavish's SKILL.md" }).click();
  await list.getByText("Opened /home/me/.omp/agent/skills/lavish/SKILL.md").waitFor();
  assert.deepEqual(backend.posts.at(-1), { path: "/api/terminal/agent-skills/open", body: { kind: "omp", path: "/home/me/.omp/agent/skills/lavish/SKILL.md" } });
  await lavish.getByRole("button", { name: "Copy the path of lavish's SKILL.md" }).click();
  await list.getByText("Copied /home/me/.omp/agent/skills/lavish/SKILL.md").waitFor();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), "/home/me/.omp/agent/skills/lavish/SKILL.md");

  await list.getByRole("button", { name: "← Agents" }).click();
  await drawer.getByRole("article", { name: "omp · working" }).waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("turning a kind on from the drawer lists its skills; All agent skills opens the view", async () => {
  const { page, backend, problems, context } = await harness();
  const drawer = await openAgents(page);
  await drawer.getByRole("button", { name: "claude's agent skills" }).click();
  const toggle = drawer.getByRole("switch", { name: "Show Claude Code skills and activity" });
  assert.equal(await toggle.getAttribute("aria-checked"), "false");
  assert.match(await drawer.locator(".skills").innerText(), /opens none of Claude Code's files/);
  await toggle.click();
  await drawer.locator('[data-skill="docx"]').waitFor();
  assert.deepEqual(backend.posts.at(-1), { path: "/api/terminal/agent-skills/settings", body: { kind: "claude", enabled: true } });
  await drawer.getByRole("button", { name: "All agent skills →" }).click();
  await drawer.waitFor({ state: "detached" });
  assert.deepEqual(await page.evaluate(() => window.skillsHarness.navigations), ["agent-skills"]);
  // The view follows the card's agent kind.
  const view = page.getByRole("region", { name: "All agent skills" });
  await view.getByRole("region", { name: "Claude Code agent skills" }).waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("bar chips carry the now state in their tooltip", async () => {
  const { page, problems, context } = await harness();
  const chip = page.getByRole("button", { name: /^omp, working, now: lavish · read: open its window$/ });
  await chip.waitFor();
  assert.equal(await chip.getAttribute("title"), "Draw the skills lab\nnow: lavish · read\nReading the lavish skill");
  assert.equal(await page.getByRole("button", { name: "claude, blocked: open its window" }).getAttribute("title"), "Fix the calendar test");
  assert.deepEqual(problems, []);
  await context.close();
});

test("All agent skills: every kind's skills, MCP tools and plugins, with the per-kind switch", async () => {
  const { page, backend, problems, context } = await harness({ canOpen: false });
  const view = page.getByRole("region", { name: "All agent skills" });
  const running = view.getByRole("list", { name: "Running agents" });
  await running.getByText("now: lavish · read").waitFor();
  const skills = view.getByRole("region", { name: "omp agent skills" });
  await skills.locator('[data-skill="lavish"] em').waitFor();
  assert.equal(await skills.getByRole("listitem").count(), 3);
  // A website has no desktop opener: Copy path only.
  assert.equal(await skills.getByRole("button", { name: /^Open / }).count(), 0);
  assert.equal(await skills.getByRole("button", { name: /^Copy / }).count(), 3);
  await view.getByText("Copy a skill's SKILL.md path; Open file needs the rika! desktop app").waitFor();
  const tools = view.getByRole("region", { name: "omp tools and plugins" });
  assert.match(await tools.innerText(), /Tools \(MCP\)\s*2[\s\S]*figma[\s\S]*linear\s*off[\s\S]*Plugins\s*0[\s\S]*No plugins\./i);
  await view.getByRole("button", { name: /^Codex/ }).click();
  await view.getByRole("region", { name: "Codex tools and plugins" }).getByText("sites").waitFor();
  await view.getByRole("button", { name: /^Claude Code/ }).click();
  const toggle = view.getByRole("switch", { name: "Show Claude Code skills and activity" });
  assert.equal(await toggle.getAttribute("aria-checked"), "false");
  assert.equal(await view.getByRole("region", { name: "Claude Code agent skills" }).count(), 0);
  await toggle.click();
  await view.getByRole("region", { name: "Claude Code agent skills" }).locator('[data-skill="pdf"]').waitFor();
  assert.deepEqual(backend.posts.map((post) => post.path), ["/api/terminal/agent-skills/settings"]);
  assert.deepEqual(problems, []);
  await context.close();
});

test("the new-agent checklist becomes the launch's flags, and Codex's hook trust is told once", async () => {
  const { page, backend, problems, context } = await harness();
  const strip = page.locator(".agent-tools");
  await strip.getByRole("button", { name: "New Agent tab" }).click();
  const checklist = strip.getByRole("group", { name: "Agent skills for this omp launch" });
  await checklist.waitFor();
  assert.match(await checklist.innerText(), /3 of 3/);
  assert.equal(await strip.getByLabel("Launch arguments").innerText(), "omp");
  await checklist.getByRole("checkbox", { name: "lavish" }).uncheck();
  assert.match(await checklist.innerText(), /2 of 3/);
  assert.equal(await strip.getByLabel("Launch arguments").innerText(), "omp --skills 'grill-me,loopy'");
  await strip.getByRole("button", { name: "Start agent" }).click();
  await strip.getByRole("status").filter({ hasText: "Agent tab opened (w1:t3, w1:p3)." }).waitFor();
  assert.deepEqual(backend.posts.at(-1), { path: "/api/terminal/agents", body: { kind: "omp", skills_off: ["lavish"] } });

  // Off for Claude: the switch instead of a checklist, and a plain launch.
  await strip.getByRole("button", { name: "New Agent tab" }).click();
  await strip.getByRole("button", { name: "claude", exact: true }).click();
  await strip.getByRole("switch", { name: "Show Claude Code skills and activity" }).waitFor();
  await strip.getByRole("button", { name: "Start agent" }).click();
  await strip.getByRole("status").filter({ hasText: "Agent tab opened" }).waitFor();
  assert.deepEqual(backend.posts.at(-1).body, { kind: "claude" });

  for (const told of [true, false]) {
    await strip.getByRole("button", { name: "New Agent tab" }).click();
    await strip.getByRole("button", { name: "codex", exact: true }).click();
    await strip.getByRole("group", { name: "Agent skills for this Codex launch" }).getByRole("checkbox", { name: "imagegen" }).uncheck();
    assert.equal(await strip.getByLabel("Launch arguments").innerText(), `codex -c 'skills.config=[{name="imagegen",enabled=false}]'`);
    await strip.getByRole("button", { name: "Start agent" }).click();
    const status = strip.getByRole("status").filter({ hasText: "Agent tab opened" });
    await status.waitFor();
    assert.equal(/trust it in \/hooks/.test(await status.innerText()), told);
  }
  assert.deepEqual(problems, []);
  await context.close();
});
