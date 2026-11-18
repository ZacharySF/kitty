// Home's widget board in a real browser against the disposable synthetic backend
// (`kitty-web --demo`); no private workspace is read. Colour-coded widgets are tiles of one
// size on one grid beside Rika, pressing a tile opens its page while its own controls keep
// working, and a widget with little to show says so. Calendar, pipeline and timer answers
// are synthetic and fixed here; the goals are the demo seed's two.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/home.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { routeBuild } from "./buildRoutes.mjs";
import { startHarness, startKitty } from "./kittyServer.js";

const DESKTOP = { width: 1568, height: 942 };
const HOME = {
  columns: [["goals", "week", "focus"], ["pipeline", "clock"]],
  hidden: ["agents", "session", "agenda", "note", "next"],
  template_offered: true,
  widths: [1, 1],
  rika: 0,
  tones: ["goals:c3", "week:c3", "focus:c2", "pipeline:c3", "clock:c3"],
  heights: [],
};

let kitty;
let server;
let browser;
let base;

before(async () => {
  kitty = await startKitty();
  server = await startHarness(kitty.origin);
  base = server.resolvedUrls.local[0];
  browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
});
after(async () => {
  await browser?.close();
  await server?.close();
  await kitty?.stop();
});

const card = {};
const PIPELINE = { columns: { applied: [card, card, card, card, card, card], oa: [card, card, card], interview: [card, card], offer: [card] } };
const timer = (status, focusSeconds = 1500) => ({
  timer: { revision: 1, status, phase: "focus", phase_id: null, session_id: null, completed_at: null, completed_phase_id: null, completed_phase: null, deadline_at: null, remaining_seconds: focusSeconds, duration_seconds: focusSeconds },
  server_now: new Date().toISOString(),
  settings: { sound: false, notifications: false, focus_seconds: focusSeconds, short_break_seconds: 300 },
});
/** A synthetic timed event `days` from today at `hour`. */
function event(title, days, hour) {
  const start = new Date();
  start.setDate(start.getDate() + days);
  start.setHours(hour, 0, 0, 0);
  return { id: title, title, start: start.toISOString(), end: new Date(start.getTime() + 3_600_000).toISOString(), all_day: false, calendar_id: "c", location: "" };
}

/** A returning user's home with `home` as the build's arrangement; `routes` answer API GETs with fixed JSON; `icons` is the Icons module. */
async function open({ home = HOME, viewport = DESKTOP, routes = {}, icons = false } = {}) {
  const context = await browser.newContext({ viewport });
  await kitty.authorize(context);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  await routeBuild(context, { theme: { base: "kitty" }, ui: { home, icons: { on: icons } } });
  const fixed = { "/pipeline": PIPELINE, "/study/timer": timer("idle"), "/calendar?*": { events: [], calendars: [] }, ...routes };
  for (const [path, json] of Object.entries(fixed))
    await context.route(`**/api${path}`, (route) => (route.request().method() === "GET" ? route.fulfill({ json }) : route.continue()));
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("#today", base).href);
  await widget(page, "Applications").locator("dd, .note").first().waitFor();
  return { page, problems, context };
}

const widget = (page, name) => page.getByRole("region", { name: "Home widgets" }).getByRole("region", { name, exact: true });
/** Each column's tiles, top to bottom, as rounded boxes. */
const tiles = (page) =>
  page.evaluate(() =>
    [...document.querySelectorAll(".board .column")].map((column) =>
      [...column.querySelectorAll("[data-widget]")].map((slot) => {
        const box = slot.getBoundingClientRect();
        return { id: slot.dataset.widget, top: Math.round(box.top), bottom: Math.round(box.bottom), left: Math.round(box.left), right: Math.round(box.right), height: Math.round(box.height) };
      }),
    ),
  );

describe("colour-coded widgets are tiles on one grid", () => {
  test("every tile is one size, rows line up across columns with one gutter, and the top edge is Rika's", async () => {
    const { page, problems, context } = await open();
    const [left, right] = await tiles(page);
    assert.deepEqual(left.map((tile) => tile.id), ["goals", "week", "focus"]);
    const heights = [...left, ...right].map((tile) => tile.height);
    assert.ok(Math.max(...heights) - Math.min(...heights) <= 1, `tile heights: ${heights}`);
    for (const [index, tile] of right.entries()) assert.equal(tile.top, left[index].top, `row ${index + 1}`);
    const across = right[0].left - left[0].right;
    assert.equal(left[1].top - left[0].bottom, across, "the gutter between rows is the gutter between columns");
    assert.ok(across >= 16 && across <= 24, `the gutter is ${across}px`);
    const rika = await page.locator('[data-tutorial="rika"]').evaluate((node) => Math.round(node.getBoundingClientRect().top));
    assert.ok(Math.abs(left[0].top - rika) <= 1, `tiles start at ${left[0].top}, Rika at ${rika}`);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("a widget given a height by hand keeps it", async () => {
    const { page, context } = await open({ home: { ...HOME, heights: ["clock:320"] } });
    const height = await page.locator('[data-widget="clock"] .home-widget').evaluate((node) => Math.round(node.getBoundingClientRect().height));
    assert.equal(height, 320);
    await context.close();
  });

  test("a phone stacks the tiles, each holding all it draws, with nothing to scroll sideways", async () => {
    const { page, context } = await open({ viewport: { width: 390, height: 844 } });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    const [left, right] = await tiles(page);
    assert.ok(right[0].top >= left.at(-1).bottom, "the second column sits under the first");
    const spill = await page.evaluate(() =>
      [...document.querySelectorAll(".board [data-widget]")].flatMap((slot) => {
        const tile = slot.getBoundingClientRect();
        return [...slot.querySelectorAll("svg, dl, ol, ul, p")].filter((node) => node.getBoundingClientRect().bottom > tile.bottom + 1).map(() => slot.dataset.widget);
      }),
    );
    assert.deepEqual(spill, [], "content below its tile's edge");
    await context.close();
  });
});

describe("pressing a tile opens its page", () => {
  test("anywhere on the tile, or Enter on its title", async () => {
    const { page, context } = await open();
    const box = await page.locator('[data-widget="week"]').boundingBox();
    await page.mouse.click(box.x + box.width - 8, box.y + box.height / 2);
    await page.waitForURL(/#calendar/);
    await page.goBack();
    const title = widget(page, "Applications").getByRole("button", { name: "Applications" });
    await title.focus();
    await page.keyboard.press("Enter");
    await page.waitForURL(/#pipeline/);
    await context.close();
  });

  test("a control inside a tile still does its own job", async () => {
    const { page, context } = await open();
    await page.route("**/api/study/timer", (route) => (route.request().method() === "POST" ? route.fulfill({ json: timer("running") }) : route.fallback()));
    const [request] = await Promise.all([
      page.waitForRequest((sent) => sent.method() === "POST" && sent.url().endsWith("/api/study/timer")),
      widget(page, "Focus").getByRole("button", { name: "Start" }).click(),
    ]);
    assert.equal(request.postDataJSON().action, "start");
    assert.match(page.url(), /#today$/);
    await context.close();
  });
});

describe("what each tile shows", () => {
  test("Applications: the active total, and a bar per stage as tall as its share of the busiest", async () => {
    const { page, context } = await open();
    const applications = widget(page, "Applications");
    assert.equal((await applications.locator(".total strong").textContent()).trim(), "12");
    assert.deepEqual(await applications.locator("dd").allTextContents(), ["6", "3", "2", "1"]);
    const heights = await applications.locator("[data-stage]").evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().height / node.parentElement.getBoundingClientRect().height));
    for (const [index, share] of [1, 3 / 6, 2 / 6, 1 / 6].entries())
      assert.ok(Math.abs(heights[index] - share) < 0.03, `stage ${index} is ${heights[index].toFixed(2)} of the tallest`);
    await context.close();
  });

  test("Applications with none offers Find jobs instead of zeros", async () => {
    const { page, context } = await open({ routes: { "/pipeline": { columns: { applied: [], oa: [], interview: [], offer: [] } } } });
    const applications = widget(page, "Applications");
    await applications.getByText("No applications yet.").waitFor();
    assert.equal(await applications.locator("dd").count(), 0);
    await applications.getByRole("button", { name: "Find jobs" }).click();
    await page.waitForURL(/#jobs/);
    await context.close();
  });

  test("This week marks today and lists the next three events, soonest first", async () => {
    const events = [event("Fourth", 4, 9), event("Second", 2, 9), event("First", 1, 9), event("Third", 3, 9)];
    const { page, context } = await open({ routes: { "/calendar?*": { events, calendars: [] } } });
    const week = widget(page, "This week");
    await week.getByText("First").waitFor();
    assert.deepEqual(await week.locator(".next .title").allTextContents(), ["First", "Second", "Third"]);
    const today = week.locator('li[aria-current="date"]');
    assert.equal(await today.count(), 1);
    assert.equal((await today.locator("strong").textContent()).trim(), String(new Date().getDate()));
    await context.close();
  });

  test("This week with no events says so", async () => {
    const { page, context } = await open();
    await widget(page, "This week").getByText("Nothing scheduled.").waitFor();
    await context.close();
  });

  test("Focus: a session's length and a full bezel before it starts; its ticks go out from the top as the phase runs", async () => {
    const idle = await open({ routes: { "/study/timer": timer("idle", 3000) } });
    const focus = widget(idle.page, "Focus");
    await focus.getByText("50:00").waitFor();
    assert.equal(await focus.locator("svg line.on").count(), await focus.locator("svg line").count(), "an idle bezel is fully lit");
    await idle.context.close();

    const halfway = timer("running");
    halfway.timer.deadline_at = new Date(Date.now() + 750_000).toISOString();
    halfway.timer.remaining_seconds = 750;
    const running = await open({ routes: { "/study/timer": halfway } });
    await widget(running.page, "Focus").getByRole("timer").waitFor();
    const bezel = widget(running.page, "Focus").locator("svg");
    const [ticks, lit] = [await bezel.locator("line").count(), await bezel.locator("line.on").count()];
    assert.ok(Math.abs(lit / ticks - 0.5) <= 0.04, `${lit} of ${ticks} ticks lit at half time`);
    assert.deepEqual(await bezel.locator("line").evaluateAll((lines) => [lines[0].classList.contains("on"), lines.at(-1).classList.contains("on")]), [false, true]);
    await running.context.close();
  });

  test("Goals draws a gauge per goal, filled as far as the goal is along", async () => {
    const { page, context } = await open();
    const goals = widget(page, "Goals");
    await goals.locator("li").first().waitFor();
    const percents = (await goals.locator("li b").allTextContents()).map((text) => parseInt(text, 10));
    assert.ok(percents.length >= 2, "the demo seed's goals");
    // A gauge's track and its filled part are dashes of the same circle: their ratio is the goal's progress.
    const shares = await goals.locator("svg g:has(.track)").evaluateAll((groups) =>
      groups.map((group) => parseFloat(group.querySelector(".value")?.getAttribute("stroke-dasharray") ?? "0") / parseFloat(group.querySelector(".track").getAttribute("stroke-dasharray"))),
    );
    assert.equal(shares.length, percents.length);
    for (const [index, share] of shares.entries()) assert.ok(Math.abs(share * 100 - percents[index]) <= 1, `gauge ${index} is ${Math.round(share * 100)}% full at ${percents[index]}%`);
    await context.close();
  });

  test("on the Black theme a tile draws in colour on a wash of its own colour with a coloured label, while its figure stays white", async () => {
    const { page, context } = await open();
    const goals = widget(page, "Goals");
    await goals.locator("svg .value").first().waitFor();
    /** How far a computed colour is from grey (0 to 1): the spread of its red, green and blue. */
    const colourful = (locator, property) =>
      locator.evaluate((node, name) => {
        const value = getComputedStyle(node)[name];
        const scale = value.startsWith("color(") ? 1 : 255;
        const [r, g, b] = value.match(/[\d.]+/g).map((number) => Number(number) / scale);
        return Math.max(r, g, b) - Math.min(r, g, b);
      }, property);
    assert.ok((await colourful(goals.locator("svg .value").first(), "stroke")) > 0.25, "a goal's gauge is grey");
    assert.ok((await colourful(widget(page, "Applications").locator("[data-stage]").first(), "backgroundColor")) > 0.25, "a stage's bar is grey");
    assert.ok((await colourful(goals.locator("header"), "color")) > 0.2, "the tile's label is grey");
    assert.ok((await colourful(widget(page, "Applications").locator(".total strong"), "color")) < 0.05, "the figure is tinted");
    assert.match(await goals.evaluate((node) => getComputedStyle(node, "::before").backgroundImage), /gradient/, "the tile has no wash");
    await context.close();
  });

  test("a tile's icon sits on a badge, and only while the Icons module is on", async () => {
    const off = await open();
    assert.equal(await widget(off.page, "Goals").locator(".module-icon").evaluate((node) => node.getBoundingClientRect().width), 0);
    await off.context.close();
    const on = await open({ icons: true });
    const badge = widget(on.page, "Goals").locator(".module-icon");
    assert.deepEqual(await badge.evaluate((node) => [node.getBoundingClientRect().width, node.getBoundingClientRect().height]), [28, 28]);
    assert.notEqual(await badge.evaluate((node) => getComputedStyle(node).backgroundColor), "rgba(0, 0, 0, 0)");
    await on.context.close();
  });
});
