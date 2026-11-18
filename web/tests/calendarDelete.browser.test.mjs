// Deleting calendar events in a real browser against a real rika! server (`kitty-web --demo`:
// a disposable synthetic workspace; tests/kittyServer.js). An x in an event's top-right corner
// on hover or keyboard focus; a click selects the event (and still opens its drawer) so Delete
// or Backspace removes it; the keys are the text field's while typing; a read-only calendar's
// events offer neither; a single event goes at once with an Undo toast, a repeating one asks.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/calendarDelete.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { chromium } from "playwright-core";
import { routeBuild } from "./buildRoutes.mjs";
import { startHarness, startKitty } from "./kittyServer.js";

let kitty;
let vite;
let browser;
let base;
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

before(async () => {
  kitty = await startKitty();
  vite = await startHarness(kitty.origin);
  base = vite.resolvedUrls.local[0];
  browser = await chromium.launch();
});
after(async () => {
  await browser?.close();
  await vite?.close();
  await kitty?.stop();
});

/** Today at `hour`:`minute` local time, as an ISO instant. */
function at(hour, minute = 0, dayOffset = 0) {
  const d = new Date();
  d.setDate(d.getDate() + dayOffset);
  d.setHours(hour, minute, 0, 0);
  return d.toISOString();
}
/** Creates an event through the real API, early in the day where the demo has none. */
const addEvent = (title, hour, extra = {}) =>
  kitty.request("/calendar/events", { calendar_id: "personal", timezone: zone, title, start: at(hour), end: at(hour + 1), ...extra });
/** The server's events of the weeks around today with this title. */
async function saved(title) {
  const query = new URLSearchParams({ start: at(0, 0, -8), end: at(0, 0, 40), timezone: zone, hidden: "1" });
  return (await kitty.request(`/calendar?${query}`)).events.filter((event) => event.title === title);
}
/** Removes every event, for a test that counts what a day shows. */
async function clearEvents() {
  const query = new URLSearchParams({ start: at(0, 0, -8), end: at(0, 0, 40), timezone: zone, hidden: "1" });
  const events = (await kitty.request(`/calendar?${query}`)).events;
  for (const id of new Set(events.map((event) => event.id))) {
    const event = await kitty.request(`/calendar/events/${id}`);
    await kitty.request(`/calendar/events/${id}`, { revision: event.revision, scope: "all", changes: {}, delete: true });
  }
}
async function until(check, what) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.fail(`timed out waiting until ${what}`);
}

/** The calendar of a returning user, on this week. `setup(context)` adds routes before the first load. */
async function calendar(setup) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 } });
  await kitty.authorize(context);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue());
  await routeBuild(context);
  // The demo's urgent Rika match would toast over the bottom-right corner.
  await context.route("**/api/rika/alerts", (route) => route.fulfill({ json: { alerts: [] } }));
  await setup?.(context);
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("#calendar", base).href, { timeout: 90000 });
  await week(page).waitFor({ timeout: 60000 });
  return { page, context, problems };
}
const week = (page) => page.getByRole("region", { name: "Week calendar" });
const block = (page, title) => week(page).getByRole("group", { name: title, exact: true });
const edit = (page, title) => block(page, title).getByRole("button", { name: new RegExp(`^Edit ${title},`) });
const remove = (scope, title) => scope.getByRole("button", { name: `Delete ${title}`, exact: true });
const drawer = (page) => page.getByRole("dialog", { name: "Event details" });
const view = (page, name) => page.getByRole("navigation", { name: "Calendar view" }).getByRole("button", { name, exact: true }).click();
const shown = (locator) => locator.evaluate((node) => getComputedStyle(node).opacity === "1" && getComputedStyle(node).pointerEvents !== "none");
/** Whether `inner` lies in the top-right quarter of `outer`, to half a pixel. */
function inTopRight(inner, outer) {
  return inner.x >= outer.x + outer.width / 2 - 0.5 && inner.x + inner.width <= outer.x + outer.width + 0.5
    && inner.y >= outer.y - 0.5 && inner.y + inner.height <= outer.y + outer.height / 2 + 0.5;
}

test("hovering an event shows Delete in its top-right corner; pressing it removes the event without opening the drawer", async () => {
  await addEvent("Hover target", 1, { end: at(3) });
  const { page, context, problems } = await calendar();
  const event = block(page, "Hover target");
  await event.scrollIntoViewIfNeeded();
  const x = remove(event, "Hover target");
  assert.equal(await shown(x), false, "the x shows before the pointer is over the event");
  await event.hover({ position: { x: 12, y: 40 } });
  await until(() => shown(x), "the x shows on hover");
  const [corner, whole] = [await x.boundingBox(), await event.boundingBox()];
  assert.ok(corner.width >= 24 && corner.height >= 24, `the x is a ${corner.width}x${corner.height} target`);
  assert.ok(inTopRight(corner, whole), `the x ${JSON.stringify(corner)} is not in the top-right quarter of ${JSON.stringify(whole)}`);
  assert.ok(await x.locator("svg").count(), "the x is the app's close icon");

  // The drawer must never open, not even for a moment.
  await page.evaluate(() => {
    window.drawerSeen = false;
    new MutationObserver(() => { window.drawerSeen ||= !!document.querySelector(".edit-drawer"); }).observe(document.body, { childList: true, subtree: true });
  });
  await x.click();
  await event.waitFor({ state: "detached" });
  await until(async () => (await saved("Hover target")).length === 0, "the event is gone from GET /api/calendar");
  assert.equal(await page.evaluate(() => window.drawerSeen), false, "the x opened the event drawer");
  assert.equal(await drawer(page).count(), 0);
  assert.equal(await page.getByRole("status").filter({ hasText: /^Drop to/ }).count(), 0, "the x started a drag");
  assert.deepEqual(problems, []);
  await context.close();
});

test("clicking an event selects it and still opens its drawer; Delete then removes it", async () => {
  await addEvent("Press Delete", 3);
  const { page, context, problems } = await calendar();
  await edit(page, "Press Delete").click();
  await drawer(page).waitFor();
  assert.equal(await drawer(page).getByRole("textbox", { name: "Title" }).inputValue(), "Press Delete");
  assert.equal(await edit(page, "Press Delete").getAttribute("aria-current"), "true", "the clicked event is not marked selected");
  const ring = await block(page, "Press Delete").evaluate((node) => getComputedStyle(node).outlineStyle);
  assert.notEqual(ring, "none", "the selected event has no visible ring");
  await page.mouse.move(0, 0);
  await until(() => shown(remove(block(page, "Press Delete"), "Press Delete")), "a selected event shows its x away from the pointer (touch has no hover)");
  await page.keyboard.press("Delete");
  await block(page, "Press Delete").waitFor({ state: "detached" });
  await drawer(page).waitFor({ state: "detached" });
  await until(async () => (await saved("Press Delete")).length === 0, "the event is gone from GET /api/calendar");
  assert.deepEqual(problems, []);
  await context.close();
});

test("Backspace removes the clicked event too, and the page stays where it is", async () => {
  await addEvent("Press Backspace", 4);
  const { page, context, problems } = await calendar();
  const address = page.url();
  await edit(page, "Press Backspace").click();
  await drawer(page).waitFor();
  await page.keyboard.press("Backspace");
  await block(page, "Press Backspace").waitFor({ state: "detached" });
  await until(async () => (await saved("Press Backspace")).length === 0, "the event is gone from GET /api/calendar");
  assert.equal(page.url(), address, "Backspace navigated");
  await week(page).waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("from the keyboard: a focused event shows its x, and Delete removes it without a click", async () => {
  await addEvent("Keyboard only", 5);
  const { page, context, problems } = await calendar();
  await edit(page, "Keyboard only").focus();
  await until(() => shown(remove(block(page, "Keyboard only"), "Keyboard only")), "the x shows while the event has focus");
  await page.keyboard.press("Tab");
  assert.equal(await remove(block(page, "Keyboard only"), "Keyboard only").evaluate((node) => node === document.activeElement), true, "Tab does not reach the x");
  await edit(page, "Keyboard only").focus();
  await page.keyboard.press("Delete");
  await block(page, "Keyboard only").waitFor({ state: "detached" });
  await until(async () => (await saved("Keyboard only")).length === 0, "the event is gone from GET /api/calendar");
  assert.equal(await drawer(page).count(), 0);
  // Focus stays in the calendar, so its single keys keep working (M is Month).
  await page.keyboard.press("m");
  await page.getByRole("grid", { name: "Month calendar" }).waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("while typing, Backspace and Delete edit the text and no event", async () => {
  await addEvent("Stays put", 6);
  const { page, context, problems } = await calendar();
  await edit(page, "Stays put").click();
  await drawer(page).waitFor();
  // Focus stays on the clicked event, yet typing still lands in the drawer's title, as it did
  // when a click focused that field: no view key (Y is Year) and no delete.
  const title = drawer(page).getByRole("textbox", { name: "Title" });
  assert.equal(await edit(page, "Stays put").evaluate((node) => node === document.activeElement), true, "the click did not leave focus on the event");
  await page.keyboard.type("xy");
  assert.equal(await title.evaluate((node) => node === document.activeElement), true, "typing did not move to the title");
  await week(page).waitFor();
  await page.keyboard.press("End");
  await page.keyboard.press("Backspace");
  assert.equal(await title.inputValue(), "Stays putx");
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("Delete");
  assert.equal(await title.inputValue(), "Stays put");
  // The quick add, opened from the keyboard so the event stays selected.
  await page.getByRole("toolbar", { name: "Calendar range" }).getByRole("button", { name: "New event", exact: true }).focus();
  await page.keyboard.press("Enter");
  const phrase = page.getByRole("dialog", { name: "New event" }).getByRole("textbox", { name: "New event" });
  await phrase.waitFor();
  assert.equal(await edit(page, "Stays put").getAttribute("aria-current"), "true", "the event is no longer selected");
  await page.keyboard.type("gym");
  await page.keyboard.press("Backspace");
  assert.equal(await phrase.inputValue(), "gy");
  await page.waitForTimeout(300);
  assert.equal((await saved("Stays put")).length, 1, "typing deleted the selected event");
  assert.equal(await block(page, "Stays put").count(), 1);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Escape clears the selection, so Delete then removes nothing", async () => {
  await addEvent("Let go", 7);
  const { page, context, problems } = await calendar();
  await edit(page, "Let go").click();
  await drawer(page).waitFor();
  await page.keyboard.press("Escape");
  await drawer(page).waitFor({ state: "detached" });
  assert.equal(await edit(page, "Let go").getAttribute("aria-current"), null, "Escape left the event selected");
  await page.evaluate(() => document.activeElement?.blur());
  await page.keyboard.press("Delete");
  await page.keyboard.press("Backspace");
  await page.waitForTimeout(300);
  assert.equal((await saved("Let go")).length, 1);
  assert.deepEqual(problems, []);
  await context.close();
});

test("an event of a read-only calendar has no x and survives the keys", async () => {
  const outlook = await kitty.request("/calendar/calendars", { name: "Campus (Outlook)" });
  await addEvent("Design review", 10, { calendar_id: outlook.id });
  await addEvent("Mine nearby", 12);
  // The server marks a calendar imported from Outlook (`calendar_remote`, tests/test_calendar.py);
  // the demo has none, so this window's snapshot marks the one made above. The server would
  // still delete its event if the page asked, which is what this test would then catch.
  const { page, context, problems } = await calendar((context) => context.route(/\/api\/calendar\?/, async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    for (const cal of body.calendars) if (cal.id === outlook.id) cal.read_only = 1;
    await route.fulfill({ response, json: body });
  }));
  await block(page, "Mine nearby").waitFor();
  await block(page, "Design review").hover();
  assert.equal(await remove(page, "Design review").count(), 0, "a read-only event offers Delete");
  assert.equal(await remove(block(page, "Mine nearby"), "Mine nearby").count(), 1, "an ordinary event beside it lost its x");
  await edit(page, "Design review").click();
  await drawer(page).waitFor();
  await page.keyboard.press("Delete");
  await page.keyboard.press("Backspace");
  await edit(page, "Design review").focus();
  await page.keyboard.press("Delete");
  await page.keyboard.press("Backspace");
  await page.waitForTimeout(400);
  assert.equal((await saved("Design review")).length, 1, "the keys deleted a read-only event");
  assert.equal(await block(page, "Design review").count(), 1);
  assert.deepEqual(problems, []);
  await context.close();
});

test("a single event goes at once with an Undo toast; Undo brings it back", async () => {
  await addEvent("Oops", 0);
  const { page, context, problems } = await calendar();
  page.on("dialog", () => assert.fail("a single event asked before deleting"));
  await block(page, "Oops").hover();
  await remove(block(page, "Oops"), "Oops").click();
  await block(page, "Oops").waitFor({ state: "detached" });
  const toast = page.getByRole("list", { name: "Calendar changes" }).getByRole("listitem").filter({ hasText: "Oops" });
  await toast.waitFor();
  assert.match(await toast.innerText(), /Event deleted/i);
  await toast.getByRole("button", { name: "Undo", exact: true }).click();
  await block(page, "Oops").waitFor();
  await toast.waitFor({ state: "detached" });
  assert.equal((await saved("Oops")).length, 1, "Undo did not restore the event");
  assert.deepEqual(problems, []);
  await context.close();
});

test("a repeating event asks first and loses only the chosen occurrence", async () => {
  // Today and the two days after: at least today's occurrence is in this week.
  await addEvent("Standup", 22, { rrule: "FREQ=DAILY;COUNT=3" });
  const { page, context, problems } = await calendar();
  assert.equal((await saved("Standup")).length, 3);
  const asked = [];
  let answer = false;
  page.on("dialog", (dialog) => { asked.push(dialog.message()); void (answer ? dialog.accept() : dialog.dismiss()); });
  const first = block(page, "Standup").first();
  await first.getByRole("button", { name: /^Edit Standup,/ }).click();
  await drawer(page).waitFor();
  await page.keyboard.press("Delete");
  await until(() => asked.length === 1, "the repeating event asks");
  assert.match(asked[0], /this occurrence of .Standup./);
  await page.waitForTimeout(300);
  assert.equal((await saved("Standup")).length, 3, "declining still deleted");
  answer = true;
  await first.getByRole("button", { name: /^Edit Standup,/ }).click();
  await page.keyboard.press("Delete");
  await until(async () => (await saved("Standup")).length === 2, "one occurrence is gone");
  assert.equal(asked.length, 2);
  assert.deepEqual(problems, []);
  await context.close();
});

test("month chips, agenda rows, the day plan and the selected-day list carry the same x", async () => {
  await clearEvents();
  await addEvent("Everywhere", 9);
  const { page, context, problems } = await calendar();
  const places = [
    ["Month", page.getByRole("grid", { name: "Month calendar" })],
    ["Agenda", page.getByRole("region", { name: "Agenda" })],
    ["Plan", page.getByRole("region", { name: "Day plan" })],
    ["Day", page.getByRole("complementary", { name: "Selected date plan" })],
  ];
  for (const [name, place] of places) {
    await view(page, name);
    const x = remove(place, "Everywhere");
    await x.waitFor({ state: "attached" });
    const row = place.locator("[data-event]").filter({ has: remove(page, "Everywhere") });
    assert.equal(await shown(x), false, `${name}: the x shows without hover`);
    await row.hover({ position: { x: 6, y: 6 } });
    await until(() => shown(x), `${name}: the x shows on hover`);
    const [corner, whole] = [await x.boundingBox(), await row.boundingBox()];
    assert.ok(corner.width >= 24 && corner.height >= 24, `${name}: the x is a ${corner.width}x${corner.height} target`);
    assert.ok(corner.x >= whole.x + whole.width / 2 && Math.abs(corner.x + corner.width - (whole.x + whole.width)) <= 1,
      `${name}: the x ${JSON.stringify(corner)} is not at the right end of ${JSON.stringify(whole)}`);
    assert.ok(corner.y <= whole.y + 0.5, `${name}: the x is not at the top of its event`);
  }
  // From the list beside the grid it deletes as anywhere else.
  await remove(places[3][1], "Everywhere").click();
  await until(async () => (await saved("Everywhere")).length === 0, "the event is gone from GET /api/calendar");
  assert.deepEqual(problems, []);
  await context.close();
});

test("the second press of a double-click does not delete the event that moved under the pointer", async () => {
  await clearEvents();
  await addEvent("First of two", 13);
  await addEvent("Second of two", 14);
  const { page, context, problems } = await calendar();
  const list = page.getByRole("complementary", { name: "Selected date plan" });
  const first = remove(list, "First of two");
  await list.locator("[data-event]").filter({ has: remove(page, "First of two") }).hover();
  await until(() => shown(first), "the x shows on hover");
  await first.hover();
  const spot = await first.boundingBox();
  const [x, y] = [spot.x + spot.width / 2, spot.y + spot.height / 2];
  await page.mouse.down();
  await page.mouse.up();
  await first.waitFor({ state: "detached" });
  // The next row now lies under the still pointer, its x where the first one was.
  const second = remove(list, "Second of two");
  await until(() => shown(second), "the next row's x is under the pointer");
  const moved = await second.boundingBox();
  assert.ok(x > moved.x && x < moved.x + moved.width && y > moved.y && y < moved.y + moved.height, "the next x did not move under the pointer");
  await page.mouse.down({ clickCount: 2 });
  await page.mouse.up({ clickCount: 2 });
  await page.waitForTimeout(400);
  assert.equal((await saved("Second of two")).length, 1, "a double-click deleted two events");
  assert.equal((await saved("First of two")).length, 0);
  assert.deepEqual(problems, []);
  await context.close();
});
