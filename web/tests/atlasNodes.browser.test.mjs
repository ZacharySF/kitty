// Q13 atlas node choice, exercised through the real 2D home atlas.
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/atlasNodes.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("..", import.meta.url));
const HIDDEN_KEY = "kitty.atlas.hidden";
let server;
let browser;
let base;

before(async () => {
  server = await createServer({ root, logLevel: "error", server: { host: "127.0.0.1", port: 0 } });
  await server.listen();
  base = server.resolvedUrls.local[0];
  browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
});
after(async () => {
  await browser?.close();
  await server?.close();
});

async function harness() {
  const page = await browser.newPage({ viewport: { width: 1100, height: 1160 }, reducedMotion: "reduce" });
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  // Never reach a real kitty backend: every API call answers with synthetic data.
  await page.route("**/api/**", (route) =>
    new URL(route.request().url()).pathname === "/api/atlas"
      ? route.fulfill({ json: { sections: [{ kind: "notes", label: "Notes", view: "notes", count: 3 }] } })
      : route.fulfill({ status: 503, json: { error: "synthetic harness" } }),
  );
  await page.goto(new URL("tests/atlas-harness.html", base).href);
  await page.waitForFunction(() => !!window.atlasHarness);
  return { page, problems };
}
async function open(page, enabled, hidden) {
  await page.evaluate(([key, hidden]) => {
    if (hidden) localStorage.setItem(key, JSON.stringify(hidden));
    else localStorage.removeItem(key);
  }, [HIDDEN_KEY, hidden]);
  await page.evaluate((enabled) => window.atlasHarness.mount({ enabled }), enabled);
  for (const tab of [0, 1]) {
    await page.waitForFunction((tab) => !!window.atlasHarness.point(tab, "view:settings"), tab);
    await settle(page, tab);
  }
}
const point = (page, tab, id) => page.evaluate(([tab, id]) => window.atlasHarness.point(tab, id), [tab, id]);
const settle = (page, tab) => page.evaluate((tab) => window.atlasHarness.settled(tab, ["view:settings", "view:notes", "view:calendar", "view:flashcards"]), tab);
/** The tab's Sections control in the stage corner; its popover renders inside it. */
const sectionsButton = (page, tab) => page.locator(`.tab-${tab} .stage`).getByRole("button", { name: /^Sections \d/ });
const popover = (page, tab) => page.locator(`.tab-${tab}`).getByRole("dialog", { name: "Sections" });
/** Opens the tab's Sections popover (if closed) and returns its row for `label`. */
async function row(page, tab, label) {
  if (!(await popover(page, tab).count())) await sectionsButton(page, tab).click();
  return popover(page, tab).getByRole("listitem").filter({ has: page.getByRole("link", { name: new RegExp(`^${label}(\\s|$)`) }) });
}
const dimmed = async (locator) => Number(await locator.evaluate((element) => getComputedStyle(element).opacity)) < 1;
const stored = (page) => page.evaluate((key) => JSON.parse(localStorage.getItem(key) || "[]"), HIDDEN_KEY);
/** Runs a row's ⋯ menu item by mouse. */
async function choose(page, tab, label, item) {
  await (await row(page, tab, label)).getByRole("button", { name: `${label} options` }).click();
  await page.getByRole("menuitem", { name: item }).click();
}

/** Centres a node through its row's "Show on the map", then returns its screen point. */
async function reachable(page, tab, label, id) {
  await choose(page, tab, label, "Show on the map");
  await page.keyboard.press("Escape");
  await popover(page, tab).waitFor({ state: "detached" });
  await settle(page, tab);
  return point(page, tab, id);
}
/** Every drawn section node lies inside the stage, whose 2D canvas fills it. */
async function assertFits(page, tab, label) {
  await settle(page, tab);
  const result = await page.evaluate((tab) => {
    const stage = document.querySelector(`.tab-${tab} .stage`).getBoundingClientRect();
    const canvas = document.querySelector(`.tab-${tab} .stage canvas[data-graph-look="2d"]`).getBoundingClientRect();
    const ids = ["view:settings", "view:notes", "view:calendar", "view:flashcards", "view:study"];
    const outside = ids.filter((id) => {
      const at = window.atlasHarness.point(tab, id);
      return at && !(at.x >= stage.left && at.x <= stage.right && at.y >= stage.top && at.y <= stage.bottom);
    });
    return { stage: stage.width, canvas: canvas.width, height: [stage.height, canvas.height], outside };
  }, tab);
  assert.ok(Math.abs(result.stage - result.canvas) <= 2 && Math.abs(result.height[0] - result.height[1]) <= 2, `${label}: the 2D canvas does not fill its stage: ${JSON.stringify(result)}`);
  assert.deepEqual(result.outside, [], `${label}: nodes are drawn outside the stage`);
  return result.stage;
}

describe("2D home atlas", () => {
  test("dragging a node onto the Sections control hides it in every tab; dragging its dimmed row onto the graph shows it", async () => {
    const { page, problems } = await harness();
    await open(page, ["workspace", "applyai"]);
    assert.ok(!(await dimmed(await row(page, 0, "Calendar"))));

    const start = await reachable(page, 0, "Calendar", "view:calendar");
    const target = await sectionsButton(page, 0).boundingBox();
    await page.mouse.move(start.x, start.y, { steps: 3 });
    await page.mouse.down();
    await page.mouse.move(target.x + target.width / 2, target.y + target.height / 2, { steps: 20 });
    await page.locator(".tab-0").getByText("Drop to hide Calendar").waitFor();
    await page.mouse.up();

    for (const tab of [0, 1])
      await page.waitForFunction((tab) => window.atlasHarness.point(tab, "view:calendar") === null, tab);
    assert.ok(await dimmed(await row(page, 0, "Calendar")), "hidden row is not dimmed");
    assert.ok(await dimmed(await row(page, 1, "Calendar")), "the other tab's row is not dimmed");
    assert.deepEqual(await stored(page), ["calendar"]);
    assert.ok(await point(page, 0, "view:notes"), "other sections stay on the atlas");
    assert.deepEqual(await page.evaluate(() => window.atlasHarness.opened), [], "a drag never opens a section");

    // The popover covers the stage's top-left; the row drops on the open graph below it.
    const stage = await page.locator(".tab-0 .stage").boundingBox();
    await (await row(page, 0, "Calendar")).getByRole("link", { name: "Calendar", exact: true })
      .dragTo(page.locator(".tab-0 .stage"), { targetPosition: { x: stage.width - 60, y: stage.height - 60 } });
    for (const tab of [0, 1])
      await page.waitForFunction((tab) => !!window.atlasHarness.point(tab, "view:calendar"), tab);
    assert.ok(!(await dimmed(await row(page, 0, "Calendar"))));
    assert.ok(!(await dimmed(await row(page, 1, "Calendar"))));
    assert.deepEqual(await stored(page), []);
    assert.deepEqual(problems, []);
    await page.close();
  });

  test("dropping a node anywhere else on the graph only moves it", async () => {
    const { page } = await harness();
    await open(page, ["workspace", "applyai"]);
    const start = await reachable(page, 0, "Flashcards", "view:flashcards");
    await page.mouse.move(start.x, start.y, { steps: 3 });
    await page.mouse.down();
    await page.mouse.move(start.x + 60, start.y + 40, { steps: 10 });
    await page.mouse.up();
    await page.waitForTimeout(200);
    assert.ok(await point(page, 0, "view:flashcards"));
    assert.deepEqual(await stored(page), []);
    await page.close();
  });

  test("each row's menu hides and shows its section from the keyboard", async () => {
    const { page, problems } = await harness();
    await open(page, ["workspace", "applyai"]);
    const flashcards = await row(page, 0, "Flashcards");
    const menuButton = flashcards.getByRole("button", { name: "Flashcards options" });
    await menuButton.focus();
    await page.keyboard.press("Enter");
    const hide = page.getByRole("menuitem", { name: "Hide from the atlas" });
    await hide.waitFor();
    while (!(await hide.evaluate((element) => element === document.activeElement))) await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => window.atlasHarness.point(0, "view:flashcards") === null);
    assert.ok(await dimmed(flashcards));
    assert.equal(await page.getByRole("menu").count(), 0, "menu closes after choosing");
    assert.ok(await menuButton.evaluate((element) => element === document.activeElement), "focus returns to the row's menu button");
    assert.ok(await popover(page, 0).isVisible(), "choosing closed the Sections popover");

    await page.keyboard.press("Enter");
    const show = page.getByRole("menuitem", { name: "Show on the atlas" });
    await show.waitFor();
    while (!(await show.evaluate((element) => element === document.activeElement))) await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => !!window.atlasHarness.point(0, "view:flashcards"));
    assert.ok(!(await dimmed(flashcards)));

    await page.keyboard.press("Enter");
    await page.getByRole("menu").waitFor();
    await page.keyboard.press("Escape");
    assert.equal(await page.getByRole("menu").count(), 0, "Escape closes the menu");
    assert.ok(await popover(page, 0).isVisible(), "Escape in the menu closed the Sections popover");
    await page.keyboard.press("Escape");
    await popover(page, 0).waitFor({ state: "detached" });
    assert.ok(await sectionsButton(page, 0).evaluate((element) => element === document.activeElement), "focus returns to Sections");
    assert.deepEqual(problems, []);
    await page.close();
  });

  test("the 2D atlas stays fitted to its stage with the side rail expanded, collapsed and expanded again", async () => {
    const { page, problems } = await harness();
    await open(page, ["workspace", "applyai"]);
    const expanded = await assertFits(page, 0, "expanded");
    await page.locator(".tab-0").getByRole("button", { name: "Side widgets" }).click();
    await page.locator(".tab-0 .side.collapsed").waitFor();
    // Every tab's home follows the one remembered choice.
    await page.locator(".tab-1 .side.collapsed").waitFor();
    const collapsed = await assertFits(page, 0, "collapsed");
    assert.ok(collapsed - expanded >= 150, `the stage only widened ${collapsed - expanded}px`);
    assert.equal(await page.evaluate(() => localStorage.getItem("kitty.home.rail")), "collapsed");
    await page.locator(".tab-1").getByRole("button", { name: "Side widgets" }).click();
    await page.locator(".tab-0 .side:not(.collapsed)").waitFor();
    const again = await assertFits(page, 0, "expanded again");
    assert.ok(Math.abs(again - expanded) <= 1, "the stage did not return to its width");
    assert.deepEqual(problems, []);
    await page.close();
  });

  test("persisted hidden choices for a disabled module neither resurrect nor crash the atlas", async () => {
    const { page, problems } = await harness();
    await open(page, ["workspace"], ["jobs", "calendar", "no-such-section"]);
    assert.equal(await point(page, 0, "view:jobs"), null);
    assert.equal(await point(page, 0, "view:calendar"), null);
    assert.ok(await point(page, 0, "view:notes"));
    assert.equal(await (await row(page, 0, "Find jobs")).count(), 0, "a disabled module's section is not listed");
    assert.ok(await dimmed(await row(page, 0, "Calendar")));

    // Hiding something else keeps the disabled module's choice.
    await choose(page, 0, "Flashcards", "Hide from the atlas");
    await page.waitForFunction(() => window.atlasHarness.point(0, "view:flashcards") === null);
    assert.deepEqual((await stored(page)).sort(), ["calendar", "flashcards", "jobs", "no-such-section"]);

    // Turning the module back on keeps Find jobs hidden rather than resurrecting it.
    await open(page, ["workspace", "applyai"], await stored(page));
    assert.equal(await point(page, 0, "view:jobs"), null);
    assert.ok(await dimmed(await row(page, 0, "Find jobs")));
    assert.ok(await point(page, 0, "view:reminders"));
    assert.deepEqual(problems, []);
    assert.deepEqual(await page.evaluate(() => window.atlasHarness.errors), []);
    await page.close();
  });
});
