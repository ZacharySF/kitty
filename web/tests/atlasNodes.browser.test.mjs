// Q13 atlas node choice, exercised through the real atlas in both renderers.
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/atlasNodes.browser.test.mjs
import { after, before, describe, test } from "node:test";
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
async function open(page, look, enabled, hidden = []) {
  await page.evaluate(([look, enabled, hidden]) => window.atlasHarness.mount({ look, enabled, hidden }), [look, enabled, hidden]);
  for (const tab of [0, 1]) {
    await page.waitForFunction((tab) => !!window.atlasHarness.point(tab, "view:settings"), tab);
    await settle(page, tab);
  }
}
const point = (page, tab, id) => page.evaluate(([tab, id]) => window.atlasHarness.point(tab, id), [tab, id]);
const settle = (page, tab) => page.evaluate((tab) => window.atlasHarness.settled(tab, ["view:settings", "view:notes", "view:calendar", "view:flashcards"]), tab);
const row = (page, tab, label) => page.locator(`.tab-${tab} nav[aria-label="Workspace sections"] li`).filter({ has: page.getByRole("link", { name: label, exact: true }) });
const dimmed = async (locator) => Number(await locator.evaluate((element) => getComputedStyle(element).opacity)) < 1;
/** The hidden sections saved in the build (`ui.atlas.hidden`). */
const stored = (page) => page.evaluate(() => window.atlasHarness.hidden());

/** Brings a node to the front (3D) through the row's locate control, then returns its screen point. */
async function reachable(page, tab, label, id) {
  await row(page, tab, label).getByRole("button", { name: `Show ${label} on the map` }).click();
  await settle(page, tab);
  return point(page, tab, id);
}

for (const look of ["2d", "3d"]) {
  describe(`${look} atlas`, () => {
    test("dragging a node onto the Sections list hides it in every tab; dragging its dimmed row onto the graph shows it", async () => {
      const { page, problems } = await harness();
      await open(page, look, ["workspace", "applyai"]);
      assert.ok(!(await dimmed(row(page, 0, "Calendar"))));

      const start = await reachable(page, 0, "Calendar", "view:calendar");
      const list = await page.locator('.tab-0 nav[aria-label="Workspace sections"]').boundingBox();
      await page.mouse.move(start.x, start.y, { steps: 3 });
      await page.mouse.down();
      await page.mouse.move(list.x + list.width / 2, list.y + 40, { steps: 20 });
      await page.locator(".tab-0").getByText("Drop to hide Calendar").waitFor();
      await page.mouse.up();

      for (const tab of [0, 1])
        await page.waitForFunction((tab) => window.atlasHarness.point(tab, "view:calendar") === null, tab);
      assert.ok(await dimmed(row(page, 0, "Calendar")), "hidden row is not dimmed");
      assert.ok(await dimmed(row(page, 1, "Calendar")), "the other tab's row is not dimmed");
      assert.deepEqual(await stored(page), ["calendar"]);
      assert.ok(await point(page, 0, "view:notes"), "other sections stay on the atlas");
      assert.deepEqual(await page.evaluate(() => window.atlasHarness.opened), [], "a drag never opens a section");

      await row(page, 0, "Calendar").getByRole("link", { name: "Calendar", exact: true }).dragTo(page.locator(".tab-0 .stage"));
      for (const tab of [0, 1])
        await page.waitForFunction((tab) => !!window.atlasHarness.point(tab, "view:calendar"), tab);
      assert.ok(!(await dimmed(row(page, 0, "Calendar"))));
      assert.ok(!(await dimmed(row(page, 1, "Calendar"))));
      assert.deepEqual(await stored(page), []);
      assert.deepEqual(problems, []);
      await page.close();
    });

    test("dropping a node anywhere else on the graph only moves it", async () => {
      const { page } = await harness();
      await open(page, look, ["workspace", "applyai"]);
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
      await open(page, look, ["workspace", "applyai"]);
      const menuButton = row(page, 0, "Flashcards").getByRole("button", { name: "Flashcards options" });
      await menuButton.focus();
      await page.keyboard.press("Enter");
      const hide = page.getByRole("menuitem", { name: "Hide from the atlas" });
      await hide.waitFor();
      while (!(await hide.evaluate((element) => element === document.activeElement))) await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await page.waitForFunction(() => window.atlasHarness.point(0, "view:flashcards") === null);
      assert.ok(await dimmed(row(page, 0, "Flashcards")));
      assert.equal(await page.getByRole("menu").count(), 0, "menu closes after choosing");
      assert.ok(await menuButton.evaluate((element) => element === document.activeElement), "focus returns to the row's menu button");

      await page.keyboard.press("Enter");
      const show = page.getByRole("menuitem", { name: "Show on the atlas" });
      await show.waitFor();
      while (!(await show.evaluate((element) => element === document.activeElement))) await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await page.waitForFunction(() => !!window.atlasHarness.point(0, "view:flashcards"));
      assert.ok(!(await dimmed(row(page, 0, "Flashcards"))));

      await page.keyboard.press("Enter");
      await page.getByRole("menu").waitFor();
      await page.keyboard.press("Escape");
      assert.equal(await page.getByRole("menu").count(), 0, "Escape closes the menu");
      assert.deepEqual(problems, []);
      await page.close();
    });

    test("persisted hidden choices for a disabled module neither resurrect nor crash the atlas", async () => {
      const { page, problems } = await harness();
      await open(page, look, ["workspace"], ["jobs", "calendar", "no-such-section"]);
      assert.equal(await point(page, 0, "view:jobs"), null);
      assert.equal(await point(page, 0, "view:calendar"), null);
      assert.ok(await point(page, 0, "view:notes"));
      assert.equal(await row(page, 0, "Find jobs").count(), 0, "a disabled module's section is not listed");
      assert.ok(await dimmed(row(page, 0, "Calendar")));

      // Hiding something else keeps the disabled module's choice.
      await row(page, 0, "Flashcards").getByRole("button", { name: "Flashcards options" }).click();
      await page.getByRole("menuitem", { name: "Hide from the atlas" }).click();
      await page.waitForFunction(() => window.atlasHarness.point(0, "view:flashcards") === null);
      assert.deepEqual((await stored(page)).sort(), ["calendar", "flashcards", "jobs", "no-such-section"]);

      // Turning the module back on keeps Find jobs hidden rather than resurrecting it.
      await open(page, look, ["workspace", "applyai"], await stored(page));
      assert.equal(await point(page, 0, "view:jobs"), null);
      assert.ok(await dimmed(row(page, 0, "Find jobs")));
      assert.ok(await point(page, 0, "view:reminders"));
      assert.deepEqual(problems, []);
      assert.deepEqual(await page.evaluate(() => window.atlasHarness.errors), []);
      await page.close();
    });
  });
}
