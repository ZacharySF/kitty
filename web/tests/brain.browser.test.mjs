// The Brain look: a mindcorpus-style point cloud where every record and every edit is a point.
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
async function open({ reducedMotion = "reduce", brain = BRAIN, status = 200, settings } = {}) {
  const context = await browser.newContext({ viewport: { width: 1200, height: 760 }, reducedMotion });
  await context.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/brain") return status === 200 ? route.fulfill({ json: brain }) : route.fulfill({ status, json: { error: "Not found" } });
    if (path === "/api/atlas") return route.fulfill({ json: { sections: [{ kind: "notes", label: "Notes", view: "notes", count: 3 }] } });
    if (path.startsWith("/api/atlas/sections/")) return route.fulfill({ json: SECTION_GRAPH });
    return route.fulfill({ status: 503, json: { error: "synthetic harness" } });
  });
  if (settings) await context.addInitScript((value) => localStorage.setItem("kitty.brain.view", value), JSON.stringify(settings));
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

async function brainReady(page) {
  await drawn(page).waitFor({ timeout: 30_000 });
  await page.waitForFunction(() => document.querySelector("[data-brain-stage]")?.getAttribute("data-birth") === "done", null, { timeout: 30_000 });
}

describe("brain stage", () => {
  test("draws one point per node on a WebGL canvas, black regardless of theme", async () => {
    const { page, problems, context } = await open();
    await mount(page, "stage");
    await brainReady(page);
    const canvas = drawn(page);
    assert.equal(Number(await canvas.getAttribute("data-points")), BRAIN.nodes.length);
    assert.equal(await canvas.evaluate((element) => !!(element.getContext("webgl2") || element.getContext("webgl"))), true);
    const background = await page.locator("[data-brain-stage]").evaluate((element) => getComputedStyle(element).backgroundColor);
    assert.equal(background, "rgb(0, 0, 0)");
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
    await page.getByRole("button", { name: "Reset" }).click();
    const reset = await stored(page);
    assert.deepEqual(
      { ...reset, panel: undefined },
      { layout: "anatomy", groupBy: "section", labels: "hover", turning: true, speed: 1, lines: false, scaffold: true, pointSize: 1, panel: undefined },
    );
    assert.equal(await page.getByRole("group", { name: "Layout" }).getByRole("button", { name: "Anatomy" }).getAttribute("aria-pressed"), "true");
    assert.equal(await page.getByRole("switch", { name: "Lines" }).getAttribute("aria-checked"), "false");
    assert.equal(await page.getByRole("slider", { name: "Point size" }).inputValue(), "1");
    assert.match(await page.locator("[data-brain-stage]").innerText(), /BY SECTION/);
    await context.close();
  });

  test("the view panel collapses and remembers it", async () => {
    const { page, context } = await open();
    await mount(page, "stage");
    await brainReady(page);
    const toggle = page.getByRole("button", { name: "View", exact: true });
    assert.equal(await toggle.getAttribute("aria-expanded"), "true");
    await toggle.click();
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    assert.equal(await page.getByRole("group", { name: "Layout" }).count(), 0);
    assert.equal((await stored(page)).panel, false);
    await context.close();
  });

  test("the brain builds itself unless reduced motion is requested", async () => {
    const animated = await open({ reducedMotion: "no-preference" });
    await mount(animated.page, "stage");
    await drawn(animated.page).waitFor({ timeout: 30_000 });
    assert.equal(await animated.page.locator("[data-brain-stage]").getAttribute("data-birth"), "running");
    await animated.page.waitForFunction(() => document.querySelector("[data-brain-stage]")?.getAttribute("data-birth") === "done", null, { timeout: 30_000 });
    await animated.context.close();

    const still = await open({ reducedMotion: "reduce" });
    await mount(still.page, "stage");
    await drawn(still.page).waitFor({ timeout: 30_000 });
    assert.equal(await still.page.locator("[data-brain-stage]").getAttribute("data-birth"), "done");
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

describe("brain is a home-only look", () => {
  test("home shows the brain and returns to the 2D atlas", async () => {
    const { page, problems, context } = await open();
    await mount(page, "atlas", { look: "brain" });
    await brainReady(page);
    const looks = page.getByRole("group", { name: "Graph look" });
    assert.equal(await looks.getByRole("button", { name: "Brain" }).getAttribute("aria-pressed"), "true");
    await looks.getByRole("button", { name: "2D" }).click();
    await page.waitForFunction(() => !document.querySelector("[data-brain-stage]"));
    await page.locator('.atlas canvas[data-graph-look="2d"]').waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem("kitty.graph.look")), "2d");
    await looks.getByRole("button", { name: "Brain" }).click();
    await brainReady(page);
    assert.deepEqual(problems, []);
    await context.close();
  });

  for (const kind of ["section", "schema"]) {
    test(`a ${kind} graph clamps the brain preference to its own 3D graph`, async () => {
      const { page, problems, context } = await open();
      await mount(page, kind, { look: "brain" });
      await page.locator('canvas[data-graph-look="3d"]').waitFor({ timeout: 30_000 });
      assert.equal(await page.locator("[data-brain-stage]").count(), 0);
      const looks = page.getByRole("group", { name: "Graph look" });
      assert.equal(await looks.getByRole("button", { name: "Brain" }).count(), 0);
      assert.equal(await looks.getByRole("button", { name: "3D" }).getAttribute("aria-pressed"), "true");
      assert.deepEqual(problems, []);
      await context.close();
    });
  }
});
