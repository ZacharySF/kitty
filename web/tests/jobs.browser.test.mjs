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

/** Opens a fresh returning-user profile on `hash`; `setup` routes the context before the page loads. */
async function open(hash, { viewport = VIEWPORT, setup } = {}) {
  const context = await browser.newContext({ viewport });
  await authorize(context, secret);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  await routeBuild(context);
  await setup?.(context);
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

describe("Career", () => {
  /** The rail's place labels, top to bottom (the quieter links are not places). */
  const placeLabels = (rail) => rail.locator("a[data-place].place .label").allInnerTexts();
  const place = (rail, label) => rail.locator("a.place", { has: rail.page().locator(".label", { hasText: new RegExp(`^${label}$`) }) });
  const summaryAnswer = (page) => page.waitForResponse((response) => new URL(response.url()).pathname === "/api/career/summary").then((response) => response.json());
  const jobsTotal = (page, view) => page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/jobs" && url.searchParams.get("view") === view;
  }).then((response) => response.json()).then((body) => body.total);

  test("Career is a left rail with live counts; Find opens on the list, with the map one press away", async () => {
    const { page, problems, context } = await open("jobs");
    const rail = page.getByRole("navigation", { name: "Career" });
    await rail.waitFor();
    assert.deepEqual(await placeLabels(rail), ["Overview", "New jobs", "All jobs", "Queue", "Saved", "Applied", "Interviews", "Reminders", "Resumes", "Profile"]);
    assert.equal(await place(rail, "All jobs").getAttribute("aria-current"), "page");
    assert.equal(await rail.locator('[aria-current="page"]').count(), 1, "more than one place is current");
    // The rail sits left of the page, full width (labels showing) on a wide window.
    const [railBox, pageBox] = [await rail.boundingBox(), await page.locator("section.opportunities").boundingBox()];
    assert.ok(railBox.x + railBox.width <= pageBox.x, `the rail overlaps the page (${railBox.x + railBox.width} > ${pageBox.x})`);
    assert.ok(railBox.width >= 180, `the rail is folded on a wide window (${railBox.width}px)`);

    // Find jobs lists the jobs first: something to pick is on screen without a press.
    const views = page.getByRole("navigation", { name: "Jobs view" });
    await views.waitFor();
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

    // Applying is part of Find: one press narrows it to the jobs ready to apply to, and back.
    const ready = page.getByRole("button", { name: "Ready to apply", exact: true });
    assert.equal(await ready.getAttribute("aria-pressed"), "false");
    await ready.click();
    await page.waitForFunction(() => location.hash === "#queue");
    await page.getByRole("button", { name: /^Fill in the form/ }).waitFor();
    assert.equal(await page.getByRole("button", { name: "Ready to apply", exact: true }).getAttribute("aria-pressed"), "true");
    assert.equal(await place(rail, "Queue").getAttribute("aria-current"), "page");
    await page.getByRole("button", { name: "Ready to apply", exact: true }).click();
    await page.waitForFunction(() => location.hash === "#jobs");

    // Each count is the size of the list its link opens, and the rail shows it.
    for (const [label, hash, view, key] of [["New jobs", "#inbox", "inbox", "new"], ["Queue", "#queue", "apply", "queued"], ["Saved", "#favorites", "favorites", "saved"]]) {
      const [summary, total] = [summaryAnswer(page), jobsTotal(page, view)];
      await place(rail, label).click();
      await page.waitForFunction((wanted) => location.hash === wanted, hash);
      const [counts, listed] = [(await summary).counts, await total];
      assert.equal(counts[key], listed, `${label}: the rail counts ${counts[key]}, the list holds ${listed}`);
      assert.equal(await place(rail, label).getAttribute("aria-current"), "page", label);
      const shown = place(rail, label).locator(".count");
      if (listed) await page.waitForFunction(([name, value]) => [...document.querySelectorAll('nav[aria-label="Career"] a.place')]
        .find((link) => link.querySelector(".label")?.textContent === name)?.querySelector(".count")?.textContent === value, [label, listed.toLocaleString("en-US")]);
      else assert.equal(await shown.count(), 0, `${label} shows a zero count`);
    }
    // Track is Applied; Interviews opens the same board on its Interview column.
    for (const label of ["Applied", "Interviews"]) {
      await place(rail, label).click();
      await page.waitForFunction(() => location.hash === "#pipeline");
      await page.getByRole("region", { name: "Application pipeline" }).waitFor();
      assert.equal(await place(rail, "Applied").getAttribute("aria-current"), "page", label);
    }
    await place(rail, "All jobs").click();
    await page.waitForFunction(() => location.hash === "#jobs");
    await page.getByRole("heading", { level: 1, name: /Find jobs/ }).waitFor();
    // The rest of Career is a quieter link away, and the open one is marked.
    const more = rail.getByRole("group", { name: "More Career pages" });
    await more.getByRole("link", { name: "Resume files" }).click();
    await page.waitForFunction(() => location.hash === "#resumes");
    assert.equal(await more.getByRole("link", { name: "Resume files" }).getAttribute("aria-current"), "page");
    assert.equal(await rail.locator('[aria-current="page"]').count(), 1);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("legacy Apply and Find links open the merged Find page, marked on the rail", async () => {
    for (const [hash, pressed, current] of [["queue", "true", "Queue"], ["apply", "true", "Queue"], ["jobs", "false", "All jobs"], ["find", "false", "All jobs"]]) {
      const { page, problems, context } = await open(hash);
      const rail = page.getByRole("navigation", { name: "Career" });
      await rail.waitFor();
      assert.equal(await place(rail, current).getAttribute("aria-current"), "page", hash);
      assert.equal(await page.getByRole("button", { name: "Ready to apply", exact: true }).getAttribute("aria-pressed"), pressed, hash);
      assert.deepEqual(problems, []);
      await context.close();
    }
  });

  test("the Career group opens on its Overview: the week, strong matches, deadlines, waiting emails and Rika", async () => {
    const { page, problems, context } = await open("calendar", { viewport: { width: 1568, height: 942 } });
    const groups = page.getByRole("navigation", { name: "Workspace groups" });
    await groups.getByRole("button", { name: "Career", exact: true }).click();
    await page.waitForFunction(() => location.hash === "#career");
    const rail = page.getByRole("navigation", { name: "Career" });
    assert.equal(await place(rail, "Overview").getAttribute("aria-current"), "page");
    await page.getByRole("heading", { level: 1, name: "This week" }).waitFor();
    for (const name of ["This week's goal", "New strong matches", "Deadlines this week", "Status emails waiting"])
      await page.getByRole("heading", { level: 2, name: new RegExp(`^${name}`) }).waitFor();
    const suggestion = page.getByRole("region", { name: "Rika suggests" });
    assert.equal(await suggestion.getByRole("button", { name: "Ask Rika" }).count(), 1);
    // A strong match opens its job in Find.
    const match = page.locator("button.match").first();
    if (await match.count()) {
      await match.click();
      await page.waitForFunction(() => location.hash === "#jobs");
      await place(rail, "Overview").click();
      await page.waitForFunction(() => location.hash === "#career");
    }

    // Track sits beside the rail with Rika at its right at 1568x942, below it when narrow.
    await place(rail, "Applied").click();
    await page.waitForFunction(() => location.hash === "#pipeline");
    await page.getByRole("heading", { level: 1, name: "This week" }).waitFor({ state: "detached" });
    const board = page.getByRole("region", { name: "Application pipeline" });
    const rika = page.getByRole("region", { name: "Rika", exact: true });
    await board.waitFor();
    await rika.waitFor();
    const [boardBox, rikaBox] = [await board.boundingBox(), await rika.boundingBox()];
    assert.ok(rikaBox.x >= boardBox.x + boardBox.width, `Rika is not to the right of the board (${rikaBox.x} < ${boardBox.x + boardBox.width})`);
    assert.ok(rikaBox.x + rikaBox.width <= 1568, "Rika runs off the screen");
    assert.ok(rikaBox.y < 942 && boardBox.y < 942, "the board or Rika starts below the fold");
    // The board stays readable: the funnel and its columns fit without sideways scrolling.
    assert.equal(await page.locator(".pipeline .board").evaluate((node) => node.scrollWidth <= node.clientWidth + 1), true, "the board scrolls sideways at 1568px");

    // Narrow, Rika moves below the board instead of squeezing it.
    await page.setViewportSize({ width: 900, height: 942 });
    await page.waitForFunction(() => {
      const board = document.querySelector('[aria-label="Application pipeline"]');
      const rika = [...document.querySelectorAll("section.home-widget")].find((node) => node.querySelector("h3")?.textContent === "Rika");
      return board && rika && rika.getBoundingClientRect().top >= board.getBoundingClientRect().bottom;
    });
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("AI sorting controls share one height and line", async () => {
    const { page, problems, context } = await open("triage", { viewport: { width: 1568, height: 942 } });
    const form = page.getByRole("form", { name: "Sort jobs with AI" });
    await form.waitFor();
    const controls = [
      form.getByRole("combobox", { name: "Track" }),
      form.getByRole("spinbutton", { name: "Jobs to send" }),
      form.getByRole("button", { name: "See the price" }),
      form.getByRole("button", { name: "Sort with free rules" }),
    ];
    const boxes = [];
    for (const control of controls) boxes.push(await control.boundingBox());
    const heights = boxes.map((box) => Math.round(box.height));
    assert.ok(Math.max(...heights) - Math.min(...heights) <= 1, `the controls differ in height: ${heights}`);
    const middles = boxes.map((box) => box.y + box.height / 2);
    assert.ok(Math.max(...middles) - Math.min(...middles) <= 1, `the controls are not on one line: ${middles}`);
    assert.deepEqual(problems, []);
    await context.close();
  });

  /** Synthetic rail counts, the same on every read. */
  const COUNTS = { new: 12, queued: 3, saved: 0, active: 9, interviews: 1, reminders: 0, emails: 2 };
  const routeCounts = (context) => context.route("**/api/career/summary", (route) => route.fulfill({ json: { counts: COUNTS } }));

  test("inside the Resume Workshop the rail folds to icons with count dots; hover, the toggle or Tab opens it over the page", async () => {
    const { page, problems, context } = await open("workshop", { setup: routeCounts });
    const rail = page.getByRole("navigation", { name: "Career" });
    await rail.waitFor();
    const workshop = page.locator("section.workshop");
    await workshop.waitFor();
    const folded = await rail.boundingBox();
    assert.ok(folded.width <= 60, `the rail is not folded in the Workshop (${folded.width}px)`);
    const before = (await workshop.boundingBox()).x;
    assert.ok(before >= folded.x + folded.width, "the Workshop starts under the strip");
    // Icons with count dots: a number for what asks for you, a plain dot for the rest, none at zero.
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Career"] a[data-place="new"] .dot')?.textContent === "12");
    const dot = (key) => rail.locator(`a[data-place="${key}"] .dot`);
    assert.equal(await dot("new").getAttribute("data-kind"), "number");
    assert.equal(await dot("interviews").textContent(), "1");
    assert.equal(await dot("queue").getAttribute("data-kind"), "dot");
    assert.equal(await dot("saved").count(), 0);
    assert.equal(await dot("reminders").count(), 0);
    assert.equal(await rail.locator(".label").first().evaluate((node) => node.getBoundingClientRect().width <= 1), true, "labels show on the strip");
    // The links keep their names; Resumes is the current place.
    assert.equal(await rail.getByRole("link", { name: /^Resumes/ }).getAttribute("aria-current"), "page");

    // Hover opens the full rail over the page without moving it.
    await rail.hover();
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Career"]').getBoundingClientRect().width >= 220);
    assert.equal((await workshop.boundingBox()).x, before, "opening the rail pushed the Workshop");
    await page.waitForFunction(() => [...document.querySelectorAll('nav[aria-label="Career"] a.place .label')].every((node) => node.getBoundingClientRect().width > 1));
    await page.mouse.move(1200, 700);
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Career"]').getBoundingClientRect().width <= 60);

    // The toggle opens it and says so; Escape folds it again.
    const toggle = rail.getByRole("button", { name: "Open the Career menu" });
    await toggle.click();
    await page.mouse.move(1200, 700);
    assert.equal(await rail.getByRole("button", { name: "Close the Career menu" }).getAttribute("aria-expanded"), "true");
    assert.ok((await rail.boundingBox()).width >= 220);
    await page.keyboard.press("Escape");
    await page.waitForFunction(() => document.querySelector('nav[aria-label="Career"]').getBoundingClientRect().width <= 60);

    // Keyboard: tabbing into the strip opens it; Enter follows a link.
    await rail.getByRole("button", { name: "Open the Career menu" }).focus();
    await page.keyboard.press("Tab");
    await page.waitForFunction(() => document.activeElement?.closest('nav[aria-label="Career"]') && document.querySelector('nav[aria-label="Career"]').getBoundingClientRect().width >= 220);
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("data-place")), "overview");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => location.hash === "#career");
    await page.getByRole("heading", { level: 1, name: "This week" }).waitFor();
    assert.ok((await rail.boundingBox()).width >= 180, "the rail stays folded outside the Workshop");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("each Overview part says plainly when it has nothing", async () => {
    const { page, problems, context } = await open("career", {
      setup: async (context) => {
        await routeCounts(context);
        await context.route("**/api/dashboard", (route) => route.fulfill({ json: { day: "2026-11-20", weekly: [], reminders: [], deadlines: [], proposals: 0 } }));
        await context.route((url) => url.pathname === "/api/jobs" && url.searchParams.get("tier") === "A", (route) => route.fulfill({ json: { jobs: [], total: 0 } }));
        await context.route("**/api/mail", (route) => route.fulfill({ json: { proposals: [] } }));
        await context.route("**/api/rika/learnings", (route) => route.fulfill({ json: { learnings: [], jobs: {}, refreshed_at: null, forgotten: 0 } }));
      },
    });
    await page.getByRole("heading", { level: 1, name: "This week" }).waitFor();
    for (const text of ["No weekly goal yet.", "No new Rank A jobs right now.", "Nothing closes or is due in the next seven days.",
      "No status emails waiting.", "Nothing yet. Rika learns"])
      await page.getByText(text).first().waitFor();
    // With nothing waiting, Rika points at the new jobs.
    const suggestion = page.getByRole("region", { name: "Rika suggests" });
    await suggestion.getByText("You're on track this week").waitFor();
    await page.getByRole("button", { name: /^All 12 new jobs/ }).click();
    await page.waitForFunction(() => location.hash === "#inbox");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("Find jobs", () => {

  test("Scrape shows the brain in place of the list while the sources run, then reloads and says what came in", async () => {
    const { page, problems, context } = await open("jobs");
    const results = page.getByRole("region", { name: "Job results" });
    await results.locator("[data-job-id]").first().waitFor();
    // The demo never reaches the network: the run and its task are answered here, the rest is the app's.
    const task = { id: "a".repeat(32), kind: "discovery", status: "running", started_at: new Date().toISOString(), finished_at: null, result: null, error: null };
    let polls = 0;
    let listed = 0;
    await page.route("**/api/sources/run", (route) => route.fulfill({ json: { task } }));
    await page.route(`**/api/tasks/${task.id}`, (route) => {
      polls += 1;
      const done = polls >= 2;
      return route.fulfill({ json: done ? { ...task, status: "completed", finished_at: new Date().toISOString(),
        result: { sources: { ashby: { status: "ok", new: 2 }, greenhouse: { status: "ok", new: 1 }, lever: { status: "error", new: 0 } } } } : task });
    });
    page.on("request", (request) => { if (request.url().includes("/api/jobs?")) listed += 1; });
    const before = listed;
    await page.getByRole("button", { name: "Scrape", exact: true }).click();
    const loader = page.locator(".scrape-stage").getByRole("status");
    await loader.waitFor();
    assert.equal((await loader.innerText()).trim(), "Scraping your job sources…");
    assert.equal(await loader.locator("canvas, svg").count() > 0, true, "the loader draws no brain");
    assert.equal(await results.count(), 0, "the list is still shown under the loader");
    assert.equal(await page.getByRole("button", { name: "Scraping…", exact: true }).isDisabled(), true);
    await results.locator("[data-job-id]").first().waitFor({ timeout: 15_000 });
    assert.equal(await page.locator(".scrape-stage").count(), 0);
    assert.ok(listed > before, "the list was not reloaded after the scrape");
    assert.match(await page.locator(".action-feedback").innerText(), /3 new jobs from 3 sources; 1 did not finish\./);
    await page.unroute("**/api/sources/run");

    // A refusal (the demo's own answer here) leaves the list and says why.
    await page.getByRole("button", { name: "Scrape", exact: true }).click();
    await page.getByRole("alert").first().waitFor();
    assert.ok(await results.isVisible(), "the list went away on a refused scrape");
    assert.deepEqual(problems.filter((problem) => !/403/.test(problem)), []);
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
    await results.locator("tbody").getByRole("checkbox").first().check();
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

  /** The covered count in each shown fit cell, null where the posting lists none. */
  const fitCounts = (page) => page.locator(".job-table tbody .col-fit").evaluateAll((cells) =>
    cells.map((cell) => { const found = /^(\d+) of (\d+) skills?$/.exec(cell.textContent.trim()); return found ? Number(found[1]) : null; }));

  test("the job list is a dense table: default columns, one fit request per batch, sortable headings, a column menu that persists", async () => {
    const { page, problems, context } = await open("jobs");
    const results = page.getByRole("region", { name: "Job results" });
    await results.locator("[data-job-id]").first().waitFor();

    // Role & company, Location & remote, Fit, Pay, Posted & deadline; the queue state is hidden.
    const headings = () => page.locator(".job-table thead th[scope=col]").evaluateAll((cells) => cells.map((cell) => cell.className.match(/col-(\w+)/)?.[1]).filter(Boolean));
    assert.deepEqual(await headings(), ["role", "place", "fit", "pay", "dates"]);
    assert.deepEqual(await page.locator(".job-table thead .col-role button").allTextContents(), ["Role↕", "Company↕"]);

    // One request answers the whole batch's fit cells (and pay), not one per row.
    const fitRequests = [];
    page.on("request", (request) => { if (request.url().includes("/api/jobs/fit?")) fitRequests.push(request.url()); });
    const answered = page.waitForResponse((response) => response.url().includes("/api/jobs/fit?"));
    await page.reload();
    await results.locator("[data-job-id]").first().waitFor();
    assert.equal((await answered).status(), 200);
    await page.waitForFunction(() => [...document.querySelectorAll(".job-table tbody .col-fit")].some((cell) => cell.textContent.trim()));
    const rows = await results.locator("[data-job-id]").count();
    assert.equal(fitRequests.length, 1, "the table asked for fit more than once");
    assert.equal(new URL(fitRequests[0]).searchParams.get("ids").split(",").length, rows);
    for (const text of await page.locator(".job-table tbody .col-fit").allInnerTexts())
      assert.match(text.trim(), /^(\d+ of \d+ skills?|No skills listed)$/);

    // Fit sorts by skills covered, most first, then flips; jobs naming no skills stay last.
    const fitHeading = page.locator(".job-table thead th.col-fit");
    await fitHeading.getByRole("button", { name: /Fit/ }).click();
    assert.equal(await fitHeading.getAttribute("aria-sort"), "descending");
    const down = await fitCounts(page);
    const known = down.filter((value) => value !== null);
    assert.deepEqual(known, [...known].sort((a, b) => b - a));
    assert.deepEqual(down.slice(0, known.length), known, "a job without skills sorted above one with them");
    await fitHeading.getByRole("button", { name: /Fit/ }).click();
    assert.equal(await fitHeading.getAttribute("aria-sort"), "ascending");
    const up = (await fitCounts(page)).filter((value) => value !== null);
    assert.deepEqual(up, [...up].sort((a, b) => a - b));

    // Role sorts A to Z; arrows still walk the rows in the order shown.
    await page.locator(".job-table thead .col-role").getByRole("button", { name: /^Role/ }).click();
    assert.equal(await page.locator(".job-table thead th.col-fit").getAttribute("aria-sort"), "none");
    const titles = await results.locator("[data-job-id]").allInnerTexts();
    assert.deepEqual(titles, [...titles].sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase())));
    await results.locator("[data-job-id]").first().focus();
    await page.keyboard.press("ArrowDown");
    assert.equal(await page.evaluate(() => document.activeElement?.textContent), titles[1]);
    assert.equal(await results.locator("tbody tr").nth(1).evaluate((row) => row.classList.contains("focused")), true);
    // Space ticks the focused row for the batch actions; the checkbox shows it.
    await page.keyboard.press(" ");
    assert.equal(await results.locator("tbody tr").nth(1).getByRole("checkbox").isChecked(), true);
    await page.getByRole("button", { name: "Clear selection" }).click();

    // The column menu shows the queue state; the choice survives a reload in this browser.
    await page.getByRole("button", { name: "Choose columns" }).click();
    const menu = page.getByRole("dialog", { name: "Columns" });
    assert.equal(await menu.getByRole("checkbox", { name: "Role & company" }).isDisabled(), true);
    await menu.getByRole("checkbox", { name: "Queue state" }).check();
    await page.locator(".job-table thead th.col-queue").waitFor();
    await page.keyboard.press("Escape");
    await page.reload();
    await results.locator("[data-job-id]").first().waitFor();
    assert.deepEqual(await headings(), ["role", "place", "fit", "pay", "dates", "queue"]);
    await page.getByRole("button", { name: "Choose columns" }).click();
    await page.getByRole("dialog", { name: "Columns" }).getByRole("checkbox", { name: "Queue state" }).uncheck();
    await page.locator(".job-table thead th.col-queue").waitFor({ state: "detached" });
    await page.keyboard.press("Escape");

    // A narrow window drops the least needed columns first; role and fit stay.
    await page.setViewportSize({ width: 760, height: 850 });
    await page.waitForFunction(() => getComputedStyle(document.querySelector(".job-table thead th.col-pay")).display === "none");
    assert.equal(await page.locator(".job-table thead th.col-role").isVisible(), true);
    assert.equal(await page.locator(".job-table thead th.col-fit").isVisible(), true);
    assert.equal(await results.evaluate((node) => node.scrollWidth <= node.clientWidth + 1), true, "the table scrolls sideways when narrow");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("a job opens on Queue, Skip, Save and Tailor resume, then Fit, Posting and Application tabs", async () => {
    const { page, problems, context } = await open("jobs");
    const results = page.getByRole("region", { name: "Job results" });
    await results.locator("[data-job-id]").first().click();
    const detail = page.getByRole("complementary", { name: "Opportunity details" });
    const decide = detail.getByRole("group", { name: "Decide on this job" });
    await decide.waitFor();
    // A job already in the queue says so on the same button.
    assert.deepEqual((await decide.getByRole("button").allInnerTexts()).map((text) => text.trim().replace(/^Queued$/, "Queue")), ["Queue", "Skip", "Save", "Tailor resume"]);

    const tabs = detail.getByRole("tablist", { name: "Job details" });
    assert.deepEqual(await tabs.getByRole("tab").allInnerTexts(), ["Fit", "Posting", "Application"]);
    assert.equal(await tabs.getByRole("tab", { name: "Fit" }).getAttribute("aria-selected"), "true");
    const fit = detail.getByRole("tabpanel", { name: "Fit" });
    await fit.getByText("Requirements today").waitFor();
    // The checklist (or why there is none) replaces the old rank chip.
    await page.waitForFunction(() => /skills this posting asks for are covered|names none of the skills|no saved description/i
      .test(document.querySelector('[role="tabpanel"]:not([hidden])')?.textContent ?? ""));
    assert.equal(await detail.getByText(/Top match|Good match|Low match|Is this job right for you/).count(), 0);
    const checklist = fit.getByRole("list", { name: "Posting skills" });
    if (await checklist.count()) {
      const tally = await fit.locator(".tally").innerText();
      const [, covered, total] = /(\d+) of (\d+)/.exec(tally).map(Number);
      assert.equal(await checklist.getByRole("listitem").count(), total);
      assert.equal(await checklist.locator("li.on").count(), covered);
    }

    // Posting: the description and the original posting; arrows move along the tabs.
    await tabs.getByRole("tab", { name: "Posting" }).click();
    const posting = detail.getByRole("tabpanel", { name: "Posting" });
    await posting.getByText("Description", { exact: true }).waitFor();
    assert.equal(await fit.isHidden(), true);
    assert.ok(await posting.getByRole("link", { name: /Read original posting/ }).count() + await posting.getByText("Posting unavailable").count() > 0);
    await page.keyboard.press("ArrowRight");
    assert.equal(await tabs.getByRole("tab", { name: "Application" }).getAttribute("aria-selected"), "true");
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("role")), "tab");
    const application = detail.getByRole("tabpanel", { name: "Application" });
    for (const heading of ["Resume for this application", "Record a submission", "Your notes"])
      await application.getByRole("heading", { name: heading }).waitFor();

    // A key reaches its control on the Application tab from any tab (e: the notes).
    await tabs.getByRole("tab", { name: "Fit" }).click();
    await detail.locator("h2").focus();
    await page.keyboard.press("e");
    await page.waitForFunction(() => document.activeElement?.tagName === "TEXTAREA");
    assert.equal(await tabs.getByRole("tab", { name: "Application" }).getAttribute("aria-selected"), "true");

    // Queue is a decision you can take back from the same button.
    const queue = decide.getByRole("button", { name: /^Queued?$/ });
    const was = await queue.getAttribute("aria-pressed");
    const flipped = was === "true" ? "false" : "true";
    await queue.click();
    await page.waitForFunction((wanted) => document.querySelector('[aria-label="Decide on this job"] button')?.getAttribute("aria-pressed") === wanted, flipped);
    assert.equal((await queue.innerText()).trim(), flipped === "true" ? "Queued" : "Queue");
    await queue.click();
    await page.waitForFunction((wanted) => document.querySelector('[aria-label="Decide on this job"] button')?.getAttribute("aria-pressed") === wanted, was);

    // Tailor resume opens the Resume Workshop for this job.
    await decide.getByRole("button", { name: "Tailor resume" }).click();
    await page.waitForFunction(() => location.hash === "#workshop");
    await page.waitForFunction(() => /tailor it for/.test(document.body.innerText)
      || document.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.startsWith("Tailor"));
    assert.deepEqual(problems, []);
    await context.close();
  });
});
