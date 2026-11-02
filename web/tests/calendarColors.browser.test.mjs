// Calendar colours (Q54) in a real browser against an in-memory stand-in for the
// calendar API (tests/calendarHarness.js; synthetic data): subject-linked events take
// the study subject's colour and lock it; unlinked ones pick a theme palette slot.
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/calendarColors.browser.test.mjs
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
  // The first load pays Vite's cold transform of the whole Calendar graph; give it time.
  await page.goto(new URL("tests/calendar-harness.html", base).href, { timeout: 60000 });
  await page.waitForFunction(() => !!window.calendarHarness, null, { timeout: 60000 });
  await block(page, "Optics lecture").waitFor({ timeout: 60000 });
  return { page, problems, context };
}
const block = (page, title) => page.getByRole("group", { name: title, exact: true });
const fill = (locator) => locator.evaluate((node) => getComputedStyle(node).backgroundColor);
/** The computed colour a CSS colour (e.g. `var(--c3)`) resolves to on this page's theme. */
const resolved = (page, css) => page.evaluate((value) => {
  const probe = document.createElement("i");
  probe.style.background = value;
  document.body.append(probe);
  const color = getComputedStyle(probe).backgroundColor;
  probe.remove();
  return color;
}, css);
const open = (page, title) => page.getByRole("button", { name: new RegExp(`^Edit ${title},`) }).click();
const drawer = (page) => page.getByRole("dialog", { name: "Event details" });
const colours = (page) => drawer(page).getByRole("radiogroup", { name: "Colour" });
const posted = (page, path) => page.evaluate((p) => window.calendarHarness.posted(p), path);
async function until(check, what) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (await check()) return;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  assert.fail(`timed out waiting until ${what}`);
}

// Kitty is the default look; Gruvbox has the darkest graph palette (dark-text contrast).
for (const theme of ["kitty", "gruvbox"]) {
  test(`subject-linked events take the subject colour; unlinked ones their palette slot (${theme})`, async () => {
    const { page, problems, context } = await calendar(theme);
    assert.equal(await fill(block(page, "Optics lecture")), "rgb(49, 162, 188)", "Physics’ colour from Study");
    assert.equal(await fill(block(page, "Lunch with Mira")), await resolved(page, "var(--c3)"));
    assert.equal(await fill(block(page, "Club night")), "rgb(18, 58, 188)", "an existing hex calendar colour still renders");
    assert.notEqual(await resolved(page, "var(--c3)"), "rgb(109, 93, 252)");
    assert.deepEqual(problems, []);
    await context.close();
  });
}

test("a subject-linked event shows its colour as locked, not a picker", async () => {
  const { page, problems, context } = await calendar();
  await open(page, "Optics lecture");
  await assert.doesNotReject(drawer(page).getByText("Auto · Physics").waitFor());
  assert.equal(await colours(page).count(), 0, "no palette picker while linked");
  assert.equal(await drawer(page).getByRole("combobox", { name: "Study subject" }).textContent(), "Physics");
  assert.equal(await fill(drawer(page).locator(".locked i")), "rgb(49, 162, 188)");
  assert.deepEqual(problems, []);
  await context.close();
});

test("an unlinked event picks a palette slot live and saves the slot, or links a subject", async () => {
  const { page, problems, context } = await calendar();
  await open(page, "Lunch with Mira");
  assert.equal(await colours(page).getByRole("radio", { name: "Theme colour 3" }).getAttribute("aria-checked"), "true");
  await colours(page).getByRole("radio", { name: "Theme colour 5" }).click();
  const c5 = await resolved(page, "var(--c5)");
  await until(async () => (await fill(block(page, "Lunch with Mira"))) === c5, "the block shows the picked slot live");
  await drawer(page).getByRole("button", { name: "Save changes" }).click();
  await until(async () => (await posted(page, "/calendar/events/picked")).length === 1, "the edit is saved");
  assert.equal((await posted(page, "/calendar/events/picked"))[0].changes.color, "c5");
  await until(async () => (await fill(block(page, "Lunch with Mira"))) === c5, "the saved block keeps the slot");

  await open(page, "Lunch with Mira");
  await drawer(page).getByRole("combobox", { name: "Study subject" }).click();
  await page.getByRole("listbox", { name: "Study subject" }).getByRole("option", { name: "Physics" }).click();
  await assert.doesNotReject(drawer(page).getByText("Auto · Physics").waitFor());
  assert.equal(await colours(page).count(), 0);
  await until(async () => (await fill(block(page, "Lunch with Mira"))) === "rgb(49, 162, 188)", "linking recolours live");
  await drawer(page).getByRole("button", { name: "Save changes" }).click();
  await until(async () => (await posted(page, "/calendar/events/picked")).length === 2, "the link is saved");
  assert.equal((await posted(page, "/calendar/events/picked"))[1].changes.subject_id, 7);
  assert.deepEqual(problems, []);
  await context.close();
});

test("a new calendar starts on the next palette slot, never a fixed hex", async () => {
  const { page, problems, context } = await calendar();
  await page.getByText("Calendars & display settings").click();
  await page.getByRole("button", { name: "＋ Calendar" }).click();
  const dialog = page.getByRole("dialog", { name: "Calendar settings" });
  const swatches = dialog.getByRole("radiogroup", { name: "Colour" });
  assert.equal(await swatches.getByRole("radio", { name: "Theme colour 3" }).getAttribute("aria-checked"), "true");
  await dialog.getByLabel("Name").fill("Reading group");
  await swatches.getByRole("radio", { name: "Theme colour 6" }).click();
  await dialog.getByRole("button", { name: "Save calendar" }).click();
  await until(async () => (await posted(page, "/calendar/calendars")).length === 1, "the calendar is saved");
  const [saved] = await posted(page, "/calendar/calendars");
  assert.equal(saved.color, "c6");
  assert.equal(saved.subject_id, null);
  assert.deepEqual(problems, []);
  await context.close();
});
