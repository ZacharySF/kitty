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
import { routeBuild } from "./buildRoutes.mjs";
import { announcement, authorize, exchange } from "./kittyServer.js";

const root = fileURLToPath(new URL("..", import.meta.url));
const repository = fileURLToPath(new URL("../..", import.meta.url));
const VIEWPORT = { width: 1280, height: 850 };

let backend;
let secret;
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
  const { origin, code } = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`demo backend did not start:\n${output}`)), 120_000);
    const read = (chunk) => {
      output += chunk;
      const found = announcement(output);
      if (found) { clearTimeout(timer); resolve(found); }
    };
    backend.stdout.on("data", read);
    backend.stderr.on("data", read);
    backend.once("exit", (code) => { clearTimeout(timer); reject(new Error(`demo backend exited (${code}):\n${output}`)); });
  });
  // The link's one-time code, spent as a browser tab spends it; pages get the secret as rika!'s windows do.
  secret = await exchange(origin, code);
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
          ws: true,
          // Rika's stream upgrades through the same proxy; the backend compares Origin exactly.
          configure: (proxy) => { for (const event of ["proxyReq", "proxyReqWs"]) proxy.on(event, (request) => request.setHeader("Origin", origin)); },
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

/** Opens a fresh returning-user profile on `hash`. */
async function open(hash, { viewport = VIEWPORT } = {}) {
  const context = await browser.newContext({ viewport });
  await authorize(context, secret);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  await routeBuild(context);
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
  test("job search is four steps on every page; Find opens on the list, with the map one press away", async () => {
    const { page, problems, context } = await open("jobs");
    const steps = page.getByRole("navigation", { name: "Job search" });
    await steps.waitFor();
    assert.deepEqual(await steps.locator(".steps button").allInnerTexts(), ["Find", "Saved", "Apply", "Track"]);
    assert.equal(await steps.getByRole("button", { name: "Find", exact: true }).getAttribute("aria-current"), "page");

    // Find jobs lists the jobs first: something to pick is on screen without a press.
    const views = page.getByRole("navigation", { name: "Jobs view" });
    assert.deepEqual(await views.getByRole("button").allInnerTexts(), ["Map", "List"]);
    assert.equal(await views.getByRole("button", { name: "List", exact: true }).getAttribute("aria-pressed"), "true");
    await page.getByRole("region", { name: "Job results" }).locator("[data-job-id]").first().waitFor();

    // The page title is an H1 in the text face with its count; Add a job link is the 32px primary, last.
    const title = page.getByRole("heading", { level: 1, name: /Find jobs/ });
    assert.equal(await title.evaluate((node) => getComputedStyle(node).fontFamily === getComputedStyle(document.body).fontFamily), true, "the title is not in the text face");
    const add = page.getByRole("button", { name: "Add a job link" });
    assert.equal(Math.round((await add.boundingBox()).height), 32);
    assert.equal(await add.evaluate((node) => {
      const header = node.closest("header");
      const controls = [...header.querySelectorAll("button, a, [role=combobox]")];
      return controls.at(-1) === node && header.contains(document.querySelector('nav[aria-label="Jobs view"]'));
    }), true, "Add a job link is not the header's last control");

    // The page is part of the app: no filled card behind the list.
    assert.equal(await page.locator("section.opportunities").evaluate((node) => getComputedStyle(node).backgroundColor), "rgba(0, 0, 0, 0)", "the jobs list sits in a filled card");

    // Map and List swap the presentation.
    await views.getByRole("button", { name: "Map", exact: true }).click();
    await page.locator(".map-stage").waitFor();
    assert.equal(await page.getByRole("region", { name: "Job results" }).count(), 0);
    await views.getByRole("button", { name: "List", exact: true }).click();
    await page.getByRole("region", { name: "Job results" }).waitFor();

    // The same steps lead through the whole section, and name where you are.
    for (const [step, hash, heading] of [["Saved", "#favorites", /^Saved/], ["Apply", "#queue", /^Apply/], ["Track", "#pipeline", /^Track/]]) {
      await page.getByRole("navigation", { name: "Job search" }).getByRole("button", { name: step, exact: true }).click();
      await page.waitForFunction((wanted) => location.hash === wanted, hash);
      await page.getByRole("heading", { name: heading }).first().waitFor();
      assert.equal(await page.getByRole("navigation", { name: "Job search" }).getByRole("button", { name: step, exact: true }).getAttribute("aria-current"), "page", step);
    }
    // Everything else is one menu away, and the menu's button says which of them is open.
    await page.getByRole("navigation", { name: "Job search" }).getByRole("button", { name: "More", exact: true }).click();
    await page.getByRole("button", { name: "Resumes", exact: true }).click();
    await page.waitForFunction(() => location.hash === "#resumes");
    const more = page.getByRole("navigation", { name: "Job search" }).locator("button.more");
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Job search"] button.more')?.textContent === "Resumes");
    assert.equal(await more.getAttribute("aria-current"), "page");
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
    await page.getByRole("button", { name: /^Fill in the form/ }).click();
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
    await page.getByRole("button", { name: "Progress" }).click();
    await drawer.waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });
});
