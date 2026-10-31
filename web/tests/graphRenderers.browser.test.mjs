// Behavioural tests shared by the 2D and 3D graph renderers (docs/terminal-dock-design.md Q14–Q16).
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/graphRenderers.browser.test.mjs
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
  const page = await browser.newPage({ viewport: { width: 1100, height: 700 } });
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/graph-harness.html", base).href);
  await page.waitForFunction(() => !!window.graphHarness);
  return { page, problems };
}
const call = (page, name, ...args) => page.evaluate(([name, args]) => window.graphHarness[name](...args), [name, args]);
const events = (page) => page.evaluate(() => window.graphHarness.events.map((event) => [...event]));
const near = (a, b, tolerance) => Math.hypot(a.x - b.x, a.y - b.y) <= tolerance;

for (const look of ["2d", "3d"]) {
  describe(`${look} renderer`, () => {
    test("fits every node and hit-tests each node after keyboard/list focus", async () => {
      const { page, problems } = await harness();
      assert.equal(await call(page, "mount", look), look);
      assert.equal(await call(page, "canvasKind"), look);
      const rect = await call(page, "stageRect");
      for (const id of await call(page, "ids")) {
        const point = await call(page, "point", id);
        assert.ok(point, `${id} has no drawn position`);
        assert.ok(point.x > rect.left && point.x < rect.right && point.y > rect.top && point.y < rect.bottom, `${id} is outside the stage after the initial fit`);
      }
      // In 3D, a node may be behind another. The accessible focus path brings
      // each target to the camera before checking the public drop-target API.
      for (const id of await call(page, "ids")) {
        await call(page, "focus", id);
        await call(page, "settled");
        const point = await call(page, "point", id);
        assert.equal(await call(page, "nodeAt", point.x, point.y), id);
      }
      assert.equal(await call(page, "nodeAt", rect.left + 2, rect.top + 2), null);
      assert.deepEqual(problems, []);
      await page.close();
    });

    test("hovering and clicking a node reports hover then selection", async () => {
      const { page } = await harness();
      await call(page, "mount", look);
      const point = await call(page, "point", "view:notes");
      await page.mouse.move(point.x, point.y, { steps: 4 });
      await page.waitForFunction(() => window.graphHarness.events.some(([kind, id]) => kind === "hover" && id === "view:notes"));
      await page.mouse.click(point.x, point.y);
      await page.waitForFunction(() => window.graphHarness.events.some(([kind]) => kind === "select"));
      assert.deepEqual((await events(page)).filter(([kind]) => kind === "select"), [["select", "view:notes"]]);
      const rect = await call(page, "stageRect");
      await page.mouse.move(rect.left + 3, rect.top + 3, { steps: 4 });
      await page.waitForFunction(() => window.graphHarness.events.at(-1)?.[0] === "hover" && window.graphHarness.events.at(-1)?.[1] === null);
      await page.close();
    });

    test("focus centres the node and focus(null) fits the whole graph", async () => {
      const { page } = await harness();
      await call(page, "mount", look);
      const rect = await call(page, "stageRect");
      const centre = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
      await call(page, "focus", "view:pipeline");
      await call(page, "settled");
      assert.ok(near(await call(page, "point", "view:pipeline"), centre, 40), "focused node is not centred");
      await call(page, "focus", null);
      await call(page, "settled");
      for (const id of await call(page, "ids")) {
        const point = await call(page, "point", id);
        assert.ok(point.x > rect.left && point.x < rect.right && point.y > rect.top && point.y < rect.bottom, `${id} left the view after fitting`);
      }
      await page.close();
    });

    test("an untouched view refits when its stage resizes; a focused view stays on its node", async () => {
      const { page, problems } = await harness();
      await call(page, "mount", look);
      for (const [width, height] of [[800, 220], [1000, 700], [360, 600], [800, 600]]) {
        await call(page, "resize", width, height);
        const rect = await call(page, "stageRect");
        for (const id of await call(page, "ids")) {
          const point = await call(page, "point", id);
          assert.ok(point.x > rect.left + 8 && point.x < rect.right - 8 && point.y > rect.top + 8 && point.y < rect.bottom - 8,
            `${id} at ${Math.round(point.x)},${Math.round(point.y)} is clipped by the ${width}×${height} stage`);
        }
      }
      await call(page, "focus", "view:queue");
      await call(page, "settled");
      await call(page, "resize", 800, 300);
      const rect = await call(page, "stageRect");
      assert.ok(near(await call(page, "point", "view:queue"), { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }, 40), "resizing reset the focused view");
      assert.deepEqual(problems, []);
      await page.close();
    });

    test("right-click on a node opens its context menu; background right-click does not", async () => {
      const { page } = await harness();
      await call(page, "mount", look);
      const rect = await call(page, "stageRect");
      await page.mouse.click(rect.left + 3, rect.top + 3, { button: "right" });
      await page.keyboard.press("Escape");
      await call(page, "focus", "view:queue");
      await call(page, "settled");
      const point = await call(page, "point", "view:queue");
      await page.mouse.move(point.x, point.y, { steps: 3 });
      await page.waitForFunction(() => window.graphHarness.events.some(([kind, id]) => kind === "hover" && id === "view:queue"));
      await page.mouse.click(point.x, point.y, { button: "right" });
      await page.waitForFunction(() => window.graphHarness.events.some(([kind]) => kind === "context"));
      assert.deepEqual((await events(page)).filter(([kind]) => kind === "context" || kind === "select"), [["context", "view:queue"]]);
      await page.close();
    });

    test("deleting a node from its context menu removes it while the rest stays interactive", async () => {
      const { page, problems } = await harness();
      await call(page, "mount", look, { deleteOnContextMenu: true });
      await call(page, "focus", "view:library");
      await call(page, "settled");
      const focused = await call(page, "point", "view:library");
      await page.mouse.move(focused.x, focused.y, { steps: 3 });
      await page.mouse.click(focused.x, focused.y, { button: "right" });
      await page.waitForFunction(() => !window.graphHarness.ids().includes("view:library"));
      await call(page, "settled");
      assert.equal(await call(page, "point", "view:library"), null);
      assert.notEqual(await call(page, "nodeAt", focused.x, focused.y), "view:library");
      await call(page, "focus", "view:jobs");
      await call(page, "settled");
      const other = await call(page, "point", "view:jobs");
      await page.mouse.move(other.x, other.y, { steps: 3 });
      await page.mouse.click(other.x, other.y);
      await page.waitForFunction(() => window.graphHarness.events.some(([kind, id]) => kind === "select" && id === "view:jobs"));
      assert.deepEqual(problems, []);
      await page.close();
    });

    test("dragging a node moves it to the pointer and reports drag end, not selection", async () => {
      const { page } = await harness();
      await call(page, "mount", look);
      const start = await call(page, "point", "view:reminders");
      const drop = { x: start.x + 90, y: start.y + 60 };
      await page.mouse.move(start.x, start.y, { steps: 3 });
      await page.mouse.down();
      await page.mouse.move(drop.x, drop.y, { steps: 12 });
      await page.mouse.up();
      await page.waitForFunction(() => window.graphHarness.events.some(([kind]) => kind === "dragend"));
      await call(page, "settled");
      const recorded = await events(page);
      const end = recorded.find(([kind]) => kind === "dragend");
      assert.equal(end[1], "view:reminders");
      assert.ok(near({ x: end[2], y: end[3] }, drop, 3), "drag end reports the release point");
      assert.ok(!recorded.some(([kind]) => kind === "select"), "a drag must not select");
      assert.ok(near(await call(page, "point", "view:reminders"), drop, 30), "dragged node did not follow the pointer");
      await page.close();
    });

    test("an inactive pane ignores pointer input", async () => {
      const { page } = await harness();
      await call(page, "mount", look);
      const point = await call(page, "point", "view:calendar");
      await call(page, "setActive", false);
      await page.mouse.move(point.x, point.y, { steps: 3 });
      await page.mouse.click(point.x, point.y);
      await page.mouse.click(point.x, point.y, { button: "right" });
      await page.waitForTimeout(150);
      assert.ok(!(await events(page)).some(([kind, id]) => (kind === "select" || kind === "context") || (kind === "hover" && id)));
      await page.close();
    });
  });
}

describe("look switching and preference", () => {
  test("switching look keeps the data, the deletion and the focused node", async () => {
    const { page, problems } = await harness();
    await call(page, "mount", "2d", { deleteOnContextMenu: true });
    const victim = await call(page, "point", "view:flashcards");
    await page.mouse.move(victim.x, victim.y, { steps: 3 });
    await page.mouse.click(victim.x, victim.y, { button: "right" });
    await page.waitForFunction(() => !window.graphHarness.ids().includes("view:flashcards"));
    await call(page, "focus", "view:jobs");
    assert.equal(await call(page, "setLook", "3d"), "3d");
    assert.equal(await call(page, "canvasKind"), "3d");
    assert.equal(await call(page, "point", "view:flashcards"), null);
    const rect = await call(page, "stageRect");
    assert.ok(near(await call(page, "point", "view:jobs"), { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }, 40));
    assert.equal(await call(page, "setLook", "2d"), "2d");
    assert.equal(await call(page, "canvasKind"), "2d");
    assert.deepEqual(problems, []);
    await page.close();
  });

  test("the look preference defaults to 2D, persists and notifies open graphs", async () => {
    const { page } = await harness();
    const result = await page.evaluate(async () => {
      const prefs = await import("/src/lib/graphPreferences.ts");
      localStorage.clear();
      const initial = prefs.graphLook();
      const heard = [];
      window.addEventListener(prefs.GRAPH_SETTINGS_EVENT, () => heard.push(prefs.graphLook()));
      prefs.setGraphLook("3d");
      const stored = prefs.graphLook();
      localStorage.setItem(prefs.GRAPH_LOOK_KEY, "hologram");
      const invalid = prefs.graphLook();
      return { initial, stored, invalid, heard };
    });
    assert.deepEqual(result, { initial: "2d", stored: "3d", invalid: "2d", heard: ["3d"] });
    await page.close();
  });
});
