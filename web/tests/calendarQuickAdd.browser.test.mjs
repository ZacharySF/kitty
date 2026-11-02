// Calendar adding (Q20 A) in a real browser against tests/calendarHarness.js (synthetic data):
// one + on the range pill opens QuickAdd with a typed phrase; a slot click prefills it; the
// drawer only edits existing items; no native selects; helper text kept to the Q9 budget.
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/calendarQuickAdd.browser.test.mjs
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

async function calendar(theme = "kitty") {
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 } });
  await context.addInitScript((id) => localStorage.setItem("kitty.theme", id), theme);
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/calendar-harness.html", base).href, { timeout: 60000 });
  await page.waitForFunction(() => !!window.calendarHarness, null, { timeout: 60000 });
  await page.getByRole("group", { name: "Optics lecture", exact: true }).waitFor({ timeout: 60000 });
  return { page, problems, context };
}
const posted = (page, path) => page.evaluate((p) => window.calendarHarness.posted(p), path);
const pill = (page) => page.getByRole("toolbar", { name: "Calendar range" });
const plus = (page) => pill(page).getByRole("button", { name: "Add event or task" });
const quick = (page) => page.getByRole("dialog", { name: "Add event or task" });
const phrase = (page) => quick(page).getByRole("textbox", { name: "Add event or task" });
async function until(check, what) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.fail(`timed out waiting until ${what}`);
}
const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
/** The parser's "tue": the next Tuesday after today. */
function nextTuesday() {
  const d = new Date();
  d.setHours(12, 0, 0, 0);
  d.setDate(d.getDate() + (((2 - d.getDay() + 7) % 7) || 7));
  return d;
}

test("adding lives in one + on the range pill; the three Add task / Add event spots are gone", async () => {
  const { page, problems, context } = await calendar();
  assert.equal(await page.getByRole("button", { name: /^(Add task|Add event|＋ Task)$/ }).count(), 0, "a duplicate add button remains");
  assert.equal(await plus(page).count(), 1);
  const size = await plus(page).evaluate((node) => {
    const style = getComputedStyle(node);
    return { w: node.offsetWidth, h: node.offsetHeight, radius: style.borderRadius };
  });
  const letter = await pill(page).getByRole("button", { name: "Week", exact: true }).evaluate((node) => node.offsetHeight);
  assert.equal(size.h, letter, "the + is not the pill's control height");
  assert.equal(size.w, size.h, "the + is not round");
  assert.equal(await plus(page).getAttribute("aria-keyshortcuts"), "N");
  assert.deepEqual(problems, []);
  await context.close();
});

test("'gym tue 6pm 1h' + Enter creates an event without opening the drawer", async () => {
  const { page, problems, context } = await calendar();
  await plus(page).click();
  await phrase(page).fill("gym tue 6pm 1h");
  const tue = nextTuesday();
  await quick(page).getByText(new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" }).format(tue)).waitFor();
  await quick(page).getByText("18:00–19:00").waitFor();
  await phrase(page).press("Enter");
  await until(async () => (await posted(page, "/calendar/events")).length === 1, "the event is created");
  const [body] = await posted(page, "/calendar/events");
  assert.equal(body.title, "Gym");
  const start = new Date(body.start);
  assert.equal(iso(start), iso(tue));
  assert.equal(start.getHours(), 18);
  assert.equal(Date.parse(body.end) - Date.parse(body.start), 3600000);
  await quick(page).waitFor({ state: "detached" });
  assert.equal(await page.getByRole("dialog", { name: /event/i }).count(), 0, "a drawer opened for a create");
  assert.deepEqual(problems, []);
  await context.close();
});

test("the Task segment turns the same phrase into a task due that day and time", async () => {
  const { page, problems, context } = await calendar();
  await plus(page).click();
  await quick(page).getByRole("radio", { name: "Task" }).or(quick(page).getByRole("button", { name: "Task", exact: true })).click();
  await phrase(page).fill("pay rent tue 6pm");
  await phrase(page).press("Enter");
  await until(async () => (await posted(page, "/calendar/tasks")).length === 1, "the task is created");
  const [body] = await posted(page, "/calendar/tasks");
  assert.equal(body.title, "Pay rent");
  assert.equal(iso(new Date(body.due)), iso(nextTuesday()));
  assert.equal(new Date(body.due).getHours(), 18);
  assert.deepEqual(problems, []);
  await context.close();
});

test("clicking an empty slot prefills QuickAdd with that time", async () => {
  const { page, problems, context } = await calendar();
  const day = iso(new Date());
  await page.getByRole("button", { name: `Create event ${day} 14:00`, exact: true }).click();
  await quick(page).waitFor();
  await quick(page).getByText("14:00–15:00").waitFor();
  await phrase(page).fill("standup");
  await phrase(page).press("Enter");
  await until(async () => (await posted(page, "/calendar/events")).length === 1, "the event is created");
  const [body] = await posted(page, "/calendar/events");
  assert.equal(body.title, "Standup");
  assert.equal(iso(new Date(body.start)), day);
  assert.equal(new Date(body.start).getHours(), 14);
  assert.equal(await page.getByRole("dialog", { name: /event/i }).count(), 0);
  assert.deepEqual(problems, []);
  await context.close();
});

test("the drawer only edits: an existing event and an existing task open it, and it has no native selects", async () => {
  const { page, problems, context } = await calendar();
  await page.getByRole("button", { name: /^Edit Lunch with Mira,/ }).click();
  const drawer = page.getByRole("dialog", { name: "Event details" });
  await drawer.waitFor();
  for (const fold of await drawer.locator("details").all()) await fold.evaluate((node) => (node.open = true));
  assert.equal(await page.locator("select").count(), 0, "a native select remains in the event drawer");
  await drawer.getByRole("button", { name: "Close event editor" }).click();

  await page.getByRole("button", { name: "Read chapter 3" }).first().click();
  const task = page.getByRole("dialog", { name: "Task details" });
  await task.waitFor();
  assert.notEqual(await task.getAttribute("aria-modal"), "true", "the task editor is not the side drawer");
  for (const fold of await task.locator("details").all()) await fold.evaluate((node) => (node.open = true));
  assert.equal(await page.locator("select").count(), 0, "a native select remains in the task drawer");
  assert.deepEqual(problems, []);
  await context.close();
});

test("no native selects on any calendar section, and helper text stays within the Q9 budget", async () => {
  const { page, problems, context } = await calendar();
  const words = () => page.locator(".calendar-workspace .hint:visible").evaluateAll((nodes) =>
    nodes.reduce((sum, node) => sum + node.textContent.trim().split(/\s+/).filter(Boolean).length, 0));
  assert.ok(await words() <= 12, `calendar view carries ${await words()} words of helper text`);
  assert.equal(await page.getByText("Shortcuts", { exact: true }).count(), 0, "the Shortcuts fold-out remains");
  for (const name of ["Tasks", "Activity", "Availability", "Imports & Google"]) {
    await page.getByRole("navigation", { name: "Calendar sections" }).getByRole("button", { name, exact: true }).click();
    for (const fold of await page.locator(".calendar-workspace details").all()) await fold.evaluate((node) => (node.open = true));
    assert.equal(await page.locator("select").count(), 0, `a native select remains on ${name}`);
  }
  assert.deepEqual(problems, []);
  await context.close();
});
