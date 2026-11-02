// Find jobs (Q22) in a real browser against the disposable synthetic backend
// (`kitty-web --demo`); no private workspace is read. One segmented view control,
// an unboxed reminders strip, one surface around the map, themed dropdowns only.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/jobs.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer as createNetServer } from "node:net";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("..", import.meta.url));
const repository = fileURLToPath(new URL("../..", import.meta.url));
const VIEWPORT = { width: 1280, height: 850 };

let backend;
let server;
let browser;
let base;

// KITTY_TEST_PORT pins the demo backend (and Vite, one above) for shared machines.
const pinnedPort = Number(process.env.KITTY_TEST_PORT) || 0;

function freePort() {
  if (pinnedPort) return Promise.resolve(pinnedPort);
  return new Promise((resolve, reject) => {
    const probe = createNetServer().once("error", reject).listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

before(async () => {
  backend = spawn("uv", ["run", "--quiet", "kitty-web", "--demo", "--port", String(await freePort())], {
    cwd: repository,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const origin = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`demo backend did not start:\n${output}`)), 120_000);
    const read = (chunk) => {
      output += chunk;
      const match = output.match(/website: (http:\/\/\S+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    };
    backend.stdout.on("data", read);
    backend.stderr.on("data", read);
    backend.once("exit", (code) => { clearTimeout(timer); reject(new Error(`demo backend exited (${code}):\n${output}`)); });
  });
  server = await createServer({
    root,
    logLevel: "error",
    server: {
      host: "127.0.0.1",
      port: pinnedPort ? pinnedPort + 1 : 0,
      strictPort: Boolean(pinnedPort),
      proxy: {
        "/api": {
          target: origin,
          changeOrigin: true,
          configure: (proxy) => proxy.on("proxyReq", (request) => request.setHeader("Origin", origin)),
        },
      },
    },
  });
  await server.listen();
  base = server.resolvedUrls.local[0];
  // The jobs map is WebGL-capable: software rendering where there is no GPU.
  browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
});
after(async () => {
  await browser?.close();
  await server?.close();
  backend?.kill();
});

/** Opens a fresh returning-user profile on `hash` with the dock hidden. */
async function open(hash, { viewport = VIEWPORT } = {}) {
  const context = await browser.newContext({ viewport });
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  await context.addInitScript(() => localStorage.setItem("kitty.dock", JSON.stringify({
    mode: "hidden", shown: "terminal", float: false, step: "m", size: null, offset: { right: 16, bottom: 16 },
  })));
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`#${hash}`, base).href);
  return { page, problems, context };
}

/** A box edge that paints: a width, a style and a colour that is not fully transparent. */
const BORDER_PROBE = `(style, side) => {
  const width = parseFloat(style.getPropertyValue("border-" + side + "-width"));
  const kind = style.getPropertyValue("border-" + side + "-style");
  const color = style.getPropertyValue("border-" + side + "-color");
  return width > 0 && kind !== "none" && kind !== "hidden" && !/rgba\\(.*,\\s*0\\)$/.test(color) && color !== "transparent";
}`;

describe("Find jobs", () => {
  test("one segmented Jobs view control: Map · List · Saved · Applications · Resumes · Sources in one pill, between the H1 and Add", async () => {
    const { page, problems, context } = await open("jobs");
    const views = page.getByRole("navigation", { name: "Jobs view" });
    await views.waitFor();
    assert.equal(await page.getByRole("navigation", { name: "Jobs view" }).count(), 1);
    const buttons = views.getByRole("button");
    assert.deepEqual(await buttons.allInnerTexts(), ["Map", "List", "Saved", "Applications", "Resumes", "Sources"]);
    const shape = await views.evaluate((nav, probe) => {
      const painted = eval(probe);
      const edge = (node) => ["top", "right", "bottom", "left"].some((side) => painted(getComputedStyle(node), side));
      const box = nav.getBoundingClientRect();
      const segments = [...nav.querySelectorAll("button")].map((button) => {
        const rect = button.getBoundingClientRect();
        return { height: Math.round(rect.height), bordered: edge(button), top: rect.top, bottom: rect.bottom, parent: button.parentElement === nav };
      });
      return { bordered: edge(nav), radius: parseFloat(getComputedStyle(nav).borderTopLeftRadius), height: Math.round(box.height), top: box.top, bottom: box.bottom, segments };
    }, BORDER_PROBE);
    assert.equal(shape.bordered, true, "the control is not one bordered pill");
    assert.ok(shape.radius >= 12, `the control is not a pill (radius ${shape.radius})`);
    assert.equal(shape.height, 28, `the control is ${shape.height}px, not 28px`);
    assert.equal(new Set(shape.segments.map((segment) => segment.height)).size, 1, "segments differ in height");
    for (const segment of shape.segments) {
      assert.equal(segment.bordered, false, "a segment draws its own border");
      assert.equal(segment.parent, true, "a segment is not a direct child of the pill");
      assert.ok(segment.top >= shape.top && segment.bottom <= shape.bottom, "a segment spills out of the pill");
    }
    assert.equal(await views.getByRole("button", { name: "Map", exact: true }).getAttribute("aria-pressed"), "true");
    assert.equal(await views.getByRole("button", { name: "List", exact: true }).getAttribute("aria-pressed"), "false");

    // The page title is a Fraunces H1 with its count; Add a job link is the 32px primary, last.
    const title = page.getByRole("heading", { level: 1, name: /Find jobs/ });
    assert.match(await title.evaluate((node) => getComputedStyle(node).fontFamily), /Fraunces/);
    const add = page.getByRole("button", { name: "Add a job link" });
    assert.equal(Math.round((await add.boundingBox()).height), 32);
    assert.equal(await add.evaluate((node) => {
      const header = node.closest("header");
      const controls = [...header.querySelectorAll("button, a, [role=combobox]")];
      return controls.at(-1) === node && header.contains(document.querySelector('nav[aria-label="Jobs view"]'));
    }), true, "Add a job link is not the header's last control");

    // List and Map swap presentation; the others navigate.
    await views.getByRole("button", { name: "List", exact: true }).click();
    await page.getByRole("region", { name: "Job results" }).waitFor();
    assert.equal(await views.getByRole("button", { name: "List", exact: true }).getAttribute("aria-pressed"), "true");
    await views.getByRole("button", { name: "Saved", exact: true }).click();
    await page.waitForFunction(() => location.hash === "#favorites");
    const saved = page.getByRole("navigation", { name: "Jobs view" });
    await saved.getByRole("button", { name: "Saved", exact: true }).waitFor();
    assert.equal(await saved.getByRole("button", { name: "Saved", exact: true }).getAttribute("aria-current"), "page");
    assert.equal(await saved.getByRole("button", { name: "Map", exact: true }).getAttribute("aria-pressed"), "false");
    // List from Saved jobs opens Find jobs as a list.
    await saved.getByRole("button", { name: "List", exact: true }).click();
    await page.waitForFunction(() => location.hash === "#jobs");
    await page.getByRole("heading", { level: 1, name: /Find jobs/ }).waitFor();
    await page.getByRole("region", { name: "Job results" }).waitFor();
    assert.equal(await views.getByRole("button", { name: "List", exact: true }).getAttribute("aria-pressed"), "true");
    await views.getByRole("button", { name: "Applications", exact: true }).click();
    await page.waitForFunction(() => location.hash === "#pipeline");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("reminders, deadlines and the weekly goal sit in an unboxed strip; the rest is one popover", async () => {
    const { page, problems, context } = await open("jobs");
    const strip = page.getByRole("region", { name: "Reminders, deadlines and goals" });
    await strip.waitFor();
    assert.ok(await strip.isVisible(), "the strip needs a click to show");
    assert.equal(await strip.locator("details, summary").count(), 0, "the strip folds");
    const box = await strip.evaluate((node, probe) => {
      const painted = eval(probe);
      const style = getComputedStyle(node);
      return {
        top: painted(style, "top"), right: painted(style, "right"), bottom: painted(style, "bottom"), left: painted(style, "left"),
        topWidth: parseFloat(style.borderTopWidth), background: style.backgroundColor, image: style.backgroundImage,
      };
    }, BORDER_PROBE);
    assert.deepEqual({ top: box.top, right: box.right, bottom: box.bottom, left: box.left }, { top: true, right: false, bottom: false, left: false }, "the strip is boxed");
    assert.equal(box.topWidth, 1);
    assert.equal(box.background, "rgba(0, 0, 0, 0)");
    assert.equal(box.image, "none");
    await strip.getByText("This week", { exact: true }).waitFor();
    const goal = strip.getByTitle(/Applications sent this week/);
    await goal.waitFor();
    assert.match((await goal.innerText()).replace(/\s+/g, " "), /\b\d+\/\d+\b/);
    assert.equal(await page.getByText(/DON.T MISS THE NEXT STEP/i).count(), 0);
    assert.equal(await page.getByRole("heading", { name: "Reminders & deadlines" }).count(), 0);

    // Everything else Dashboard showed is behind one popover, unboxed, without fold-outs.
    await strip.getByRole("button", { name: "Search health and weekly goals" }).click();
    const details = page.getByRole("dialog", { name: "Search health and weekly goals" });
    await details.waitFor();
    await details.getByRole("button", { name: /See all reminders/ }).waitFor();
    await details.getByRole("table").waitFor();
    assert.equal(await details.locator("details, summary").count(), 0);
    assert.equal(await details.evaluate((node) => [...node.querySelectorAll("*")].filter((child) => child.matches(".panel, .surface")).length), 0);
    await page.keyboard.press("Escape");
    assert.equal(await page.evaluate(() => localStorage.getItem("kitty.jobs.brief")), null);
    // The strip belongs to Find jobs only.
    await page.evaluate(() => (location.hash = "inbox"));
    await page.getByRole("heading", { name: /New jobs/ }).first().waitFor();
    assert.equal(await page.getByRole("region", { name: "Reminders, deadlines and goals" }).count(), 0);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("the map has one surface border; no Browse-as-list link", async () => {
    const { page, problems, context } = await open("jobs");
    const map = page.getByRole("region", { name: "Jobs map" });
    await map.locator(".canvas").waitFor();
    const borders = await map.locator(".canvas").evaluate((canvas, probe) => {
      const painted = eval(probe);
      const boxed = [];
      for (let node = canvas; node && node !== document.documentElement; node = node.parentElement) {
        const style = getComputedStyle(node);
        if (["top", "right", "bottom", "left"].every((side) => painted(style, side))) boxed.push(node.className || node.tagName);
      }
      return boxed;
    }, BORDER_PROBE);
    assert.ok(borders.length <= 1, `the map sits in ${borders.length} boxes: ${borders.join(" > ")}`);
    const rect = await map.boundingBox();
    assert.ok(rect.height >= 300, `the map is only ${rect.height}px tall`);
    assert.equal(await page.getByRole("button", { name: /as a list/ }).count(), 0);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("no native select on Find jobs; the filters popover changes the results", async () => {
    const { page, problems, context } = await open("jobs");
    await page.getByRole("navigation", { name: "Jobs view" }).getByRole("button", { name: "List", exact: true }).click();
    const results = page.getByRole("region", { name: "Job results" });
    await results.locator("[data-job-id]").first().waitFor();
    assert.equal(await page.locator("select").count(), 0, "a native select is left");
    assert.equal(await page.locator("details, summary").count(), 0, "a fold-out is left");
    await page.getByRole("combobox", { name: "Sort jobs" }).waitFor();
    await page.getByRole("combobox", { name: "Filter by job type or track" }).waitFor();
    assert.equal(await page.getByText(/Ranks help you decide/).count(), 0, "the rank paragraph is inline");
    assert.equal(await page.getByText(/Shift-click checkboxes for a range/).count(), 0, "the shortcut line is inline");

    await page.getByRole("button", { name: "Filters" }).click();
    const filters = page.getByRole("dialog", { name: "Filters" });
    await filters.waitFor();
    assert.equal(await page.locator("select").count(), 0, "the filters popover holds a native select");
    assert.match(await filters.getByText("Rank", { exact: true }).getAttribute("title"), /A is highest priority/);
    const before = await results.locator("[data-job-id]").count();
    const request = page.waitForRequest((request) => /\/api\/jobs\?/.test(request.url()) && new URL(request.url()).searchParams.get("tier") === "C");
    await filters.getByRole("button", { name: "C", exact: true }).click();
    await request;
    await page.waitForFunction((count) => {
      const region = document.querySelector('[aria-label="Job results"]');
      return region?.getAttribute("aria-busy") === "false" && region.querySelectorAll("[data-job-id]").length !== count;
    }, before);
    // Long lists in the popover are the themed listbox.
    await filters.getByRole("combobox", { name: "Eligibility" }).click();
    await page.getByRole("option", { name: /Meets known requirements/ }).waitFor();
    assert.equal(await page.locator("select").count(), 0);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("selected jobs: bulk changes in a popover (t focuses the value), Fill opens the preparation drawer", async () => {
    const { page, problems, context } = await open("jobs");
    await page.getByRole("navigation", { name: "Jobs view" }).getByRole("button", { name: "List", exact: true }).click();
    const results = page.getByRole("region", { name: "Job results" });
    await results.locator("[data-job-id]").first().waitFor();
    await results.getByRole("checkbox").first().check();
    await results.locator("[data-job-id]").first().focus();
    await page.keyboard.press("t");
    const bulk = page.getByRole("dialog", { name: "Change selected jobs" });
    await bulk.waitFor();
    await page.waitForFunction(() => document.activeElement?.matches('[role="combobox"][aria-label="Bulk value"]'));
    assert.equal(await page.locator("select, details, summary").count(), 0);
    await page.keyboard.press("Escape");
    await bulk.waitFor({ state: "detached" });

    // Fill opens the drawer and asks for the reviewed plan, as Shift+F does.
    const preflight = page.waitForRequest((request) => request.url().includes("/api/automation/preflight"));
    await page.getByRole("button", { name: /^Fill 1 selected for review/ }).click();
    await preflight;
    const drawer = page.getByRole("dialog", { name: "Prepare applications" });
    await drawer.waitFor();
    await drawer.getByRole("region", { name: "Application preparation" }).waitFor();
    const review = page.getByRole("dialog", { name: "Review preparation plan" });
    await review.waitFor();
    await page.keyboard.press("Escape");
    await review.waitFor({ state: "hidden" });
    assert.ok(await drawer.isVisible(), "Escape in the plan review closed the drawer");
    await drawer.getByRole("button", { name: "Close" }).click();
    await drawer.waitFor({ state: "detached" });
    await page.getByRole("button", { name: "Preparation" }).click();
    await drawer.waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });
});
