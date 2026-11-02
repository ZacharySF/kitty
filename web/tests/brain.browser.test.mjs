// The Brain look: a mindcorpus-style point cloud, shaped like a neural-network brain, where every record and every edit is a point.
// `/api/brain` is mocked with a synthetic ~20k-node workspace (tests/brainFixture.mjs).
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/brain.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";
import { syntheticBrain } from "./brainFixture.mjs";

const root = fileURLToPath(new URL("..", import.meta.url));
const BRAIN = syntheticBrain({ total: 20000 });
const SECTION_GRAPH = {
  nodes: [
    { id: "notebook:1", label: "Notebook", group: "Notes", entity: "notebook", ref: 1 },
    { id: "page:1", label: "Page", group: "Notes", entity: "page", ref: 1 },
  ],
  links: [{ source: "notebook:1", target: "page:1" }],
  total: 2,
  shown: 2,
};
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

/** A fresh profile whose `/api/brain` answers with `brain` (or `status`), and every other API with synthetic data. */
/**
 * A fresh profile whose `/api/brain` answers with `brain` (or `status`), and every other API with synthetic data.
 * The View panel is opened (most tests use its controls) unless `fresh` keeps the first-run view.
 */
async function open({ reducedMotion = "reduce", brain = BRAIN, status = 200, settings = {}, fresh = false } = {}) {
  const context = await browser.newContext({ viewport: { width: 1200, height: 760 }, reducedMotion });
  await context.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/brain") return status === 200 ? route.fulfill({ json: brain }) : route.fulfill({ status, json: { error: "Not found" } });
    if (path === "/api/atlas") return route.fulfill({ json: { sections: [{ kind: "notes", label: "Notes", view: "notes", count: 3 }] } });
    if (path.startsWith("/api/atlas/sections/")) return route.fulfill({ json: SECTION_GRAPH });
    return route.fulfill({ status: 503, json: { error: "synthetic harness" } });
  });
  if (!fresh) await context.addInitScript((value) => localStorage.setItem("kitty.brain.view", value), JSON.stringify({ panel: true, ...settings }));
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/brain-harness.html", base).href);
  await page.waitForFunction(() => !!window.brainHarness);
  return { page, problems, context };
}
const mount = (page, kind, options = {}) => page.evaluate(([kind, options]) => window.brainHarness.mount(kind, options), [kind, options]);
const screenPoint = (page, id) => page.evaluate((id) => window.brainHarness.screenPoint(id), id);
const worldPoint = (page, id) => page.evaluate((id) => window.brainHarness.worldPoint(id), id);
const drawn = (page) => page.locator("[data-brain-stage] canvas[data-points]");
const stored = (page) => page.evaluate(() => JSON.parse(localStorage.getItem("kitty.brain.view") || "null"));
const pointColour = (page, id) => page.evaluate((id) => window.brainHarness.pointColour(id), id);
const stage = (page) => page.locator("[data-brain-stage]");
const legendDot = (page, key) => page.getByRole("list", { name: "Legend" }).locator(`[data-key="${key}"] i`).evaluate((element) => getComputedStyle(element).backgroundColor);
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(", ")})`;
const near = (a, b, tolerance = 0.02) => a.every((value, c) => Math.abs(value - b[c]) <= tolerance);
const RECORD_IDS = BRAIN.nodes.filter((node) => node.kind === "record").map((node) => node.id);
/** Projected bounding box of the brain: every record, each a neuron in its cortex. */
const brainBox = (page) => page.evaluate((ids) => {
  const box = { left: Infinity, top: Infinity, right: -Infinity, bottom: -Infinity };
  for (const id of ids) {
    const point = window.brainHarness.screenPoint(id);
    if (!point) continue;
    box.left = Math.min(box.left, point.x);
    box.right = Math.max(box.right, point.x);
    box.top = Math.min(box.top, point.y);
    box.bottom = Math.max(box.bottom, point.y);
  }
  return box;
}, RECORD_IDS);
const within = (box, area) => box.left >= area.left && box.right <= area.right && box.top >= area.top && box.bottom <= area.bottom;

async function brainReady(page) {
  await drawn(page).waitFor({ timeout: 30_000 });
  await page.waitForFunction(() => document.querySelector("[data-brain-stage]")?.getAttribute("data-birth") === "done", null, { timeout: 30_000 });
}

describe("brain stage", () => {
  test("draws one point per node on a transparent WebGL canvas; Black restores the night sky", async () => {
    const { page, problems, context } = await open();
    await mount(page, "stage");
    await brainReady(page);
    const canvas = drawn(page);
    assert.equal(Number(await canvas.getAttribute("data-points")), BRAIN.nodes.length);
    const gl = await canvas.evaluate((element) => {
      const context = element.getContext("webgl2") || element.getContext("webgl");
      return context ? context.getContextAttributes().alpha : null;
    });
    assert.equal(gl, true, "the drawing buffer keeps an alpha channel");
    assert.equal(await canvas.getAttribute("data-clear-alpha"), "0", "the renderer clears to nothing");
    const background = () => stage(page).evaluate((element) => getComputedStyle(element).backgroundColor);
    assert.equal(await background(), "rgba(0, 0, 0, 0)");
    assert.equal(await stage(page).getAttribute("data-background"), "transparent");
    await page.getByRole("group", { name: "Background" }).getByRole("button", { name: "Black" }).click();
    assert.equal(await background(), "rgb(0, 0, 0)");
    assert.equal((await stored(page)).background, "black");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("hovering a sphere highlights its group; clicking opens it alone; Escape returns", async () => {
    const { page, problems, context } = await open({ settings: { turning: false, layout: "spheres" } });
    await mount(page, "stage");
    await brainReady(page);
    const jobs = BRAIN.nodes.filter((node) => node.section === "jobs").length;
    const point = await screenPoint(page, "jobs.job:0");
    assert.ok(point, "a job is on screen");
    await page.mouse.move(point.x, point.y, { steps: 2 });
    await page.waitForFunction(() => document.querySelector("[data-brain-stage]")?.getAttribute("data-hover-group") === "jobs");
    assert.equal(await page.getByRole("list", { name: "Legend" }).locator('[data-key="jobs"]').evaluate((element) => element.classList.contains("hot")), true);
    await page.mouse.down();
    await page.mouse.up();
    await page.waitForFunction(() => document.querySelector("[data-brain-stage]")?.getAttribute("data-isolated") === "jobs");
    assert.equal(Number(await drawn(page).getAttribute("data-shown")), jobs, "only the jobs are shown");
    const legend = page.getByRole("list", { name: "Legend" });
    assert.ok(await legend.locator('[data-key="record:job"]').count(), "the legend lists the group's kinds");
    assert.match(await stage(page).innerText(), /JOBS BY KIND/);
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => !document.querySelector("[data-brain-stage]")?.hasAttribute("data-isolated"));
    assert.equal(Number(await drawn(page).getAttribute("data-shown")), BRAIN.nodes.length);
    // The pill returns too.
    await page.mouse.move(point.x, point.y, { steps: 2 });
    await page.waitForFunction(() => document.querySelector("[data-brain-stage]")?.getAttribute("data-hover-group") === "jobs");
    await page.mouse.down();
    await page.mouse.up();
    await page.getByRole("button", { name: /Back to all/ }).click();
    await page.waitForFunction(() => !document.querySelector("[data-brain-stage]")?.hasAttribute("data-isolated"));
    assert.deepEqual(problems, []);
    await context.close();
  });

  for (const groupBy of ["section", "type"]) {
    test(`section labels never overlap (anatomy, by ${groupBy})`, async () => {
      const { page, context } = await open({ settings: { turning: false, labels: "sections", groupBy } });
      await mount(page, "stage");
      await brainReady(page);
      await page.waitForTimeout(300);
      const boxes = await page.locator(".labels span").evaluateAll((elements) =>
        elements.filter((element) => getComputedStyle(element).visibility === "visible").map((element) => {
          const { left, top, right, bottom } = element.getBoundingClientRect();
          return { text: element.textContent, left, top, right, bottom };
        }));
      assert.ok(boxes.length >= 6, `${boxes.length} labels shown`);
      boxes.forEach((a, i) => boxes.slice(i + 1).forEach((b) => {
        const apart = a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top;
        assert.ok(apart, `${a.text} overlaps ${b.text}`);
      }));
      await context.close();
    });
  }

  test("hovering a legend entry shows the kinds inside it, each in its own shade", async () => {
    const { page, context } = await open();
    await mount(page, "stage");
    await brainReady(page);
    await page.getByRole("list", { name: "Legend" }).locator('[data-key="notes"]').hover();
    const kinds = page.getByRole("list", { name: "Notes kinds" });
    await kinds.waitFor();
    const shades = await kinds.locator("li[data-kind] i").evaluateAll((elements) => elements.map((element) => getComputedStyle(element).backgroundColor));
    assert.ok(shades.length >= 4, "page, notebook, folder and edit actions");
    assert.equal(new Set(shades).size, shades.length, "every kind has its own colour");
    await context.close();
  });

  test("a group's colour can be picked, survives a reload and a theme switch, and resets to the theme's", async () => {
    const { page, problems, context } = await open();
    await mount(page, "stage");
    await brainReady(page);
    await page.evaluate(() => window.brainHarness.setTheme("dracula"));
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.legend [data-key="notes"] i')).backgroundColor !== "rgb(255, 61, 61)");
    const dracula = await legendDot(page, "notes");
    const draculaPoint = await pointColour(page, "notes.page:0");
    await page.evaluate(() => window.brainHarness.setTheme("gruvbox"));
    await page.waitForFunction((before) => getComputedStyle(document.querySelector('.legend [data-key="notes"] i')).backgroundColor !== before, dracula);
    assert.notDeepEqual(await pointColour(page, "notes.page:0"), draculaPoint, "the theme recolours the brain");

    await page.getByRole("button", { name: "Colour of Notes" }).click();
    const hex = page.getByRole("textbox", { name: "Hex colour" });
    await hex.fill("#e05a9a");
    await hex.press("Enter");
    const picked = [0xe0, 0x5a, 0x9a].map((value) => value / 255);
    await page.waitForFunction((want) => getComputedStyle(document.querySelector('.legend [data-key="notes"] i')).backgroundColor === want, rgb("#e05a9a"));
    assert.ok(near(await pointColour(page, "notes.page:0"), picked), "the points take the picked colour");
    assert.deepEqual(JSON.parse(await page.evaluate(() => localStorage.getItem("kitty.brain.colors"))).section, { notes: "#e05a9a" });

    await page.reload();
    await page.waitForFunction(() => !!window.brainHarness);
    await page.evaluate(() => window.brainHarness.setTheme("dracula"));
    await mount(page, "stage");
    await brainReady(page);
    assert.equal(await legendDot(page, "notes"), rgb("#e05a9a"), "the pick survives a reload and a theme switch");
    assert.ok(near(await pointColour(page, "notes.page:0"), picked));

    await page.getByRole("button", { name: "Reset colours" }).click();
    await page.waitForFunction((want) => getComputedStyle(document.querySelector('.legend [data-key="notes"] i')).backgroundColor === want, dracula);
    assert.ok(near(await pointColour(page, "notes.page:0"), draculaPoint, 0.001), "reset returns to the theme's colours");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("the legend lists every section with its records + edits", async () => {
    const { page, context } = await open();
    await mount(page, "stage");
    await brainReady(page);
    const legend = page.getByRole("list", { name: "Legend" });
    await assert.doesNotReject(legend.waitFor());
    assert.match(await page.locator("[data-brain-stage]").innerText(), /BY SECTION/);
    await legend.getByRole("button", { name: /more/ }).click().catch(() => {});
    for (const section of BRAIN.sections) {
      const item = legend.locator(`[data-key="${section.id}"]`);
      const nodes = BRAIN.nodes.filter((node) => node.section === section.id);
      assert.equal(await item.locator(".name").innerText(), section.label);
      assert.equal(Number(await item.locator(".count").innerText()), nodes.length, section.id);
    }
    await legend.locator('[data-key="notes"]').click();
    assert.equal(await legend.locator('[data-key="notes"]').getAttribute("aria-pressed"), "true");
    assert.equal(await legend.locator('[data-key="jobs"]').getAttribute("aria-pressed"), "false");
    await context.close();
  });

  test("switching layout moves the points", async () => {
    const { page, context } = await open();
    await mount(page, "stage");
    await brainReady(page);
    const sample = ["notes.page:0", "jobs.job:0", "edit:0", "edit:9000", `edit:${BRAIN.total.edits - 1}`];
    const before = await Promise.all(sample.map((id) => worldPoint(page, id)));
    assert.ok(before.every((point) => Array.isArray(point) && point.length === 3));
    for (const layout of ["Spheres", "Wheel"]) {
      await page.getByRole("group", { name: "Layout" }).getByRole("button", { name: layout }).click();
      assert.equal(await page.getByRole("group", { name: "Layout" }).getByRole("button", { name: layout }).getAttribute("aria-pressed"), "true");
      const after = await Promise.all(sample.map((id) => worldPoint(page, id)));
      const moved = after.filter((point, index) => Math.hypot(...point.map((value, axis) => value - before[index][axis])) > 0.05);
      assert.equal(moved.length, sample.length, `${layout} left points in place`);
      assert.equal(Number(await drawn(page).getAttribute("data-points")), BRAIN.nodes.length);
    }
    assert.equal((await stored(page)).layout, "wheel");
    await context.close();
  });

  test("hovering a point shows its label, section, type and date", async () => {
    const { page, context } = await open({ settings: { turning: false } });
    await mount(page, "stage");
    await brainReady(page);
    const point = await screenPoint(page, "notes.page:0");
    assert.ok(point, "the known record is on screen");
    await page.mouse.move(point.x, point.y, { steps: 2 });
    const tooltip = page.getByRole("tooltip");
    await tooltip.waitFor();
    assert.equal(await tooltip.locator("strong").innerText(), "Notes anchor");
    assert.match(await tooltip.innerText(), /Notes · page · /);
    await context.close();
  });

  test("reset restores the default view", async () => {
    const { page, context } = await open();
    await mount(page, "stage");
    await brainReady(page);
    await page.getByRole("group", { name: "Layout" }).getByRole("button", { name: "Wheel" }).click();
    await page.getByRole("group", { name: "Group by" }).getByRole("button", { name: "Type" }).click();
    await page.getByRole("switch", { name: "Lines" }).click();
    await page.getByRole("slider", { name: "Point size" }).fill("2");
    assert.match(await page.locator("[data-brain-stage]").innerText(), /BY TYPE/);
    const changed = await stored(page);
    assert.deepEqual([changed.layout, changed.groupBy, changed.lines, changed.pointSize], ["wheel", "type", true, 2]);
    await page.getByRole("button", { name: "Reset", exact: true }).click();
    const reset = await stored(page);
    assert.deepEqual(
      { ...reset, panel: undefined },
      { layout: "anatomy", groupBy: "section", labels: "hover", turning: true, speed: 1, lines: false, scaffold: true, pointSize: 1, background: "transparent", panel: undefined },
    );
    assert.equal(await page.getByRole("group", { name: "Layout" }).getByRole("button", { name: "Brain" }).getAttribute("aria-pressed"), "true");
    assert.equal(await page.getByRole("switch", { name: "Lines" }).getAttribute("aria-checked"), "false");
    assert.equal(await page.getByRole("slider", { name: "Point size" }).inputValue(), "1");
    assert.match(await page.locator("[data-brain-stage]").innerText(), /BY SECTION/);
    await context.close();
  });

  test("the View panel starts closed with the brain in view; open, it reframes the brain beside it and is remembered", async () => {
    const { page, problems, context } = await open({ fresh: true });
    await mount(page, "stage");
    await brainReady(page);
    const toggle = page.getByRole("button", { name: "View", exact: true });
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    assert.equal(await page.getByRole("group", { name: "Layout" }).count(), 0);
    const area = await stage(page).evaluate((element) => element.getBoundingClientRect().toJSON());
    const closed = await brainBox(page);
    assert.ok(within(closed, area), `the brain ${JSON.stringify(closed)} leaves the stage ${JSON.stringify(area)}`);

    await toggle.click();
    assert.equal(await toggle.getAttribute("aria-expanded"), "true");
    assert.equal((await stored(page)).panel, true);
    const panel = await page.locator(".panel").evaluate((element) => element.getBoundingClientRect().toJSON());
    let beside = null;
    for (let attempt = 0; attempt < 40; attempt += 1) {
      beside = await brainBox(page);
      if (beside.right <= panel.left - 4) break;
      await page.waitForTimeout(100);
    }
    assert.ok(beside.right <= panel.left - 4, `the panel (from x ${panel.left}) covers the brain (to x ${beside.right})`);
    assert.ok(within(beside, { ...area, right: panel.left }), "the brain stays inside the stage beside the panel");

    await mount(page, "stage");
    await brainReady(page);
    assert.equal(await page.getByRole("button", { name: "View", exact: true }).getAttribute("aria-expanded"), "true", "the open panel is remembered");
    await page.getByRole("button", { name: "View", exact: true }).click();
    assert.equal((await stored(page)).panel, false);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("the brain builds itself and pulses travel its network unless reduced motion is requested", async () => {
    const animated = await open({ reducedMotion: "no-preference" });
    await mount(animated.page, "stage");
    await drawn(animated.page).waitFor({ timeout: 30_000 });
    assert.equal(await animated.page.locator("[data-brain-stage]").getAttribute("data-birth"), "running");
    assert.ok(Number(await drawn(animated.page).getAttribute("data-filaments")) > 1000, "the neural network is drawn");
    assert.equal(await drawn(animated.page).getAttribute("data-pulses"), "on");
    await animated.page.waitForFunction(() => document.querySelector("[data-brain-stage]")?.getAttribute("data-birth") === "done", null, { timeout: 30_000 });
    await animated.context.close();

    const still = await open({ reducedMotion: "reduce" });
    await mount(still.page, "stage");
    await drawn(still.page).waitFor({ timeout: 30_000 });
    assert.equal(await still.page.locator("[data-brain-stage]").getAttribute("data-birth"), "done");
    assert.ok(Number(await drawn(still.page).getAttribute("data-filaments")) > 1000, "the network is still drawn");
    assert.equal(await drawn(still.page).getAttribute("data-pulses"), "off", "but holds still: no pulses");
    await still.page.getByRole("group", { name: "Layout" }).getByRole("button", { name: "Spheres" }).click();
    await still.page.waitForFunction(() => document.querySelector("[data-brain-stage] canvas")?.getAttribute("data-filaments") === "0");
    await still.context.close();
  });

  test("an older backend without /api/brain shows a calm empty state", async () => {
    const { page, problems, context } = await open({ status: 404 });
    await mount(page, "stage");
    await page.getByText("The brain needs the newer backend").waitFor();
    assert.equal(await drawn(page).count(), 0);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("home is the Brain or the 2D atlas", () => {
  test("home opens on the Brain; its switch offers only Brain and 2D, sits beside View in the corner, and remembers the choice", async () => {
    const { page, problems, context } = await open();
    await mount(page, "atlas");
    await brainReady(page);
    const looks = page.getByRole("group", { name: "Home look" });
    assert.deepEqual(await looks.getByRole("button").allInnerTexts(), ["Brain", "2D"], "home offers no 3D");
    assert.equal(await looks.getByRole("button", { name: "Brain" }).getAttribute("aria-pressed"), "true");
    const [switchBox, viewBox] = await Promise.all([looks, page.getByRole("button", { name: "View" })].map((locator) => locator.evaluate((node) => node.getBoundingClientRect().toJSON())));
    assert.ok(switchBox.right <= viewBox.left && Math.abs(switchBox.top - viewBox.top) <= 2, "the look switch is not in View's row");
    assert.equal(await page.locator(".atlas .stage > .canvas").evaluate((element) => getComputedStyle(element).visibility), "hidden", "the paused 2D atlas never shows through the transparent brain");
    // Finding a section from the corner's Sections popover highlights it in the Brain.
    await page.locator(".atlas .stage").getByRole("button", { name: /^Sections \d/ }).click();
    const search = page.getByRole("dialog", { name: "Sections" }).getByRole("searchbox", { name: "Find a section" });
    await search.fill("Notes");
    await search.press("Enter");
    await page.waitForFunction(() => document.querySelector('.legend [data-key="notes"]')?.getAttribute("aria-pressed") === "true");
    await search.press("Escape");
    await looks.getByRole("button", { name: "2D" }).click();
    await page.waitForFunction(() => !document.querySelector("[data-brain-stage]"));
    await page.locator('.atlas canvas[data-graph-look="2d"]').waitFor();
    assert.equal(await page.locator(".atlas .stage > .canvas").evaluate((element) => getComputedStyle(element).visibility), "visible");
    assert.equal(await page.evaluate(() => localStorage.getItem("kitty.home.look")), "2d");
    assert.equal(await page.evaluate(() => localStorage.getItem("kitty.graph.look")), null, "home's choice leaves section graphs alone");
    await mount(page, "atlas");
    await page.locator('.atlas canvas[data-graph-look="2d"]').waitFor();
    assert.equal(await page.locator("[data-brain-stage]").count(), 0, "the 2D choice is remembered");
    await looks.getByRole("button", { name: "Brain" }).click();
    await brainReady(page);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("the Brain stays fitted to its stage with the side rail expanded, collapsed and expanded again", async () => {
    const { page, problems, context } = await open({ settings: { turning: false, panel: false } });
    await mount(page, "atlas");
    await brainReady(page);
    const toggle = page.getByRole("button", { name: "Side widgets" });
    const fit = async (label) => {
      await page.waitForFunction(() => {
        const stage = document.querySelector(".atlas .stage"), canvas = stage?.querySelector("[data-brain-stage] canvas[data-points]");
        if (!canvas?.width) return false;
        const box = canvas.getBoundingClientRect(), outer = stage.getBoundingClientRect();
        return Math.abs(box.width - outer.width) <= 2 && Math.abs(box.height - outer.height) <= 2 && Math.abs(canvas.width / canvas.height - box.width / box.height) < 0.02;
      }, null, { timeout: 20_000, polling: 200 }).catch(() => assert.fail(`${label}: the Brain is not fitted to its stage`));
      const area = await page.locator(".atlas .stage").evaluate((node) => node.getBoundingClientRect().toJSON());
      // The camera reframes: the cat's head stays whole inside the stage once it settles.
      let head;
      for (let attempt = 0; attempt < 20; attempt += 1) {
        head = await headBox(page);
        if (within(head, area)) break;
        await page.waitForTimeout(150);
      }
      assert.ok(within(head, area), `${label}: the cat's head ${JSON.stringify(head)} leaves the stage ${JSON.stringify(area)}`);
      return area;
    };
    const expanded = await fit("expanded");
    await toggle.click();
    await page.locator(".atlas .side.collapsed").waitFor();
    const collapsed = await fit("collapsed");
    assert.ok(collapsed.width - expanded.width >= 150, `the stage only widened ${collapsed.width - expanded.width}px`);
    assert.equal(await page.evaluate(() => localStorage.getItem("kitty.home.rail")), "collapsed");
    await mount(page, "atlas");
    await brainReady(page);
    assert.equal(await page.locator(".atlas .side.collapsed").count(), 1, "the collapsed rail is not remembered");
    await toggle.click();
    const again = await fit("expanded again");
    assert.ok(Math.abs(again.width - expanded.width) <= 1, "the stage did not return to its width");
    assert.deepEqual(problems, []);
    await context.close();
  });

  for (const theme of ["liquid-glass", "kitty"]) {
    test(`on ${theme}, Transparent shows the page's own backdrop behind the Brain, not a darker panel; Black stays black`, async () => {
      const { page, problems, context } = await open({ settings: { turning: false } });
      await page.evaluate((id) => window.brainHarness.setTheme(id), theme);
      await mount(page, "atlas");
      await brainReady(page);
      // Nothing between the Brain and the page paints: no colour, image, blur or shadow.
      const layers = await stage(page).evaluate((element) => {
        const found = [];
        for (let node = element; node && node.id !== "host"; node = node.parentElement) {
          const css = getComputedStyle(node);
          found.push({ layer: node.className, background: css.backgroundColor, image: css.backgroundImage, blur: css.backdropFilter, shadow: css.boxShadow });
        }
        return found;
      });
      assert.ok(layers.some(({ layer }) => /\batlas\b/.test(layer)) && layers.some(({ layer }) => /\bstage\b/.test(layer)), "checks the stage and the atlas");
      for (const { layer, ...paint } of layers) assert.deepEqual(paint, { background: "rgba(0, 0, 0, 0)", image: "none", blur: "none", shadow: "none" }, layer);
      // A bright page backdrop shows through the stage's corner unchanged.
      const backdrop = [40, 170, 230];
      await page.addStyleTag({ content: `html, body { background: rgb(${backdrop}) !important; } body::before, body::after { display: none !important; }` });
      const corner = async () => {
        const box = await stage(page).evaluate((element) => element.getBoundingClientRect().toJSON());
        const shot = await page.screenshot({ clip: { x: box.left + 16, y: box.top + 16, width: 4, height: 4 } });
        return page.evaluate(async (png) => {
          const image = new Image();
          image.src = `data:image/png;base64,${png}`;
          await image.decode();
          const canvas = document.createElement("canvas");
          canvas.width = image.width;
          canvas.height = image.height;
          const context = canvas.getContext("2d");
          context.drawImage(image, 0, 0);
          return Array.from(context.getImageData(1, 1, 1, 1).data.slice(0, 3));
        }, shot.toString("base64"));
      };
      const seen = await corner();
      assert.ok(seen.every((value, c) => Math.abs(value - backdrop[c]) <= 4), `the stage's corner shows ${seen}, not the backdrop ${backdrop}`);
      await page.getByRole("group", { name: "Background" }).getByRole("button", { name: "Black" }).click();
      const black = await corner();
      assert.ok(black.every((value) => value <= 4), `Black paints the stage black, not ${black}`);
      assert.deepEqual(problems, []);
      await context.close();
    });
  }

  for (const [saved, shown] of [["3d", "brain"], ["brain", "brain"], ["2d", "2d"]]) {
    test(`an old shared look of ${saved} opens home as ${shown}`, async () => {
      const { page, problems, context } = await open();
      await mount(page, "atlas", { look: saved });
      if (shown === "brain") await brainReady(page);
      else await page.locator('.atlas canvas[data-graph-look="2d"]').waitFor();
      assert.equal(await page.getByRole("group", { name: "Home look" }).getByRole("button", { name: shown === "brain" ? "Brain" : "2D" }).getAttribute("aria-pressed"), "true");
      assert.equal(await page.evaluate(() => localStorage.getItem("kitty.home.look")), shown);
      assert.deepEqual(problems, []);
      await context.close();
    });
  }

  for (const kind of ["section", "schema"]) {
    test(`a ${kind} graph keeps its own 2D/3D switch, with no Brain`, async () => {
      const { page, problems, context } = await open();
      await mount(page, kind, { look: "3d" });
      await page.locator('canvas[data-graph-look="3d"]').waitFor({ timeout: 30_000 });
      assert.equal(await page.locator("[data-brain-stage]").count(), 0);
      const looks = page.getByRole("group", { name: "Graph look" });
      assert.equal(await looks.getByRole("button", { name: "Brain" }).count(), 0);
      assert.equal(await looks.getByRole("button", { name: "3D" }).getAttribute("aria-pressed"), "true");
      await looks.getByRole("button", { name: "2D" }).click();
      await page.locator('canvas[data-graph-look="2d"]').waitFor({ timeout: 30_000 });
      assert.equal(await page.evaluate(() => localStorage.getItem("kitty.graph.look")), "2d");
      assert.deepEqual(problems, []);
      await context.close();
    });
  }
});
