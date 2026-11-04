// Goals and the morning brief (docs/rika-design.md Q14 A, Q15 A) in a real browser against
// the disposable synthetic backend (`kitty-web --demo`, whose seed holds two goals, Rika's
// finds for them and today's brief; the demo never calls OpenRouter or Gmail).
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/goals.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";
import { routeBuild } from "./buildRoutes.mjs";
import { announcement, authorize, exchange } from "./kittyServer.js";

const root = fileURLToPath(new URL("..", import.meta.url));
const repository = fileURLToPath(new URL("../..", import.meta.url));
const VIEWPORT = { width: 1440, height: 900 };
/** FrontendGoals' demo port range is 9031-9039. */
const PORT = Number(process.env.GOALS_DEMO_PORT ?? 9032);
const INTERNSHIP = "Land a summer 2027 internship";
const DECK = "Finish the frontend interview deck";

let backend;
let origin;
let secret;
let server;
let browser;
let base;

before(async () => {
  backend = spawn("uv", ["run", "--quiet", "kitty-web", "--demo", "--port", String(PORT)], { cwd: repository, stdio: ["ignore", "pipe", "pipe"] });
  const found = await new Promise((resolve, reject) => {
    let output = "";
    const timer = setTimeout(() => reject(new Error(`demo backend did not start:\n${output}`)), 120_000);
    const read = (chunk) => {
      output += chunk;
      const announced = announcement(output);
      if (announced) { clearTimeout(timer); resolve(announced); }
    };
    backend.stdout.on("data", read);
    backend.stderr.on("data", read);
    backend.once("exit", (code) => { clearTimeout(timer); reject(new Error(`demo backend exited (${code}):\n${output}`)); });
  });
  origin = found.origin;
  // The link's one-time code, spent as a browser tab spends it; pages get the secret as rika!'s windows do.
  secret = await exchange(origin, found.code);
  // The demo sweeps at launch; running it once more makes today's brief certain.
  assert.ok((await call("/rika/brief/run", {})).brief, "the demo wrote no brief");
  server = await createServer({
    root,
    logLevel: "error",
    server: {
      host: "127.0.0.1",
      port: 0,
      proxy: {
        "/api": {
          target: origin,
          changeOrigin: true,
          ws: true,
          configure: (proxy) => { for (const event of ["proxyReq", "proxyReqWs"]) proxy.on(event, (request) => request.setHeader("Origin", origin)); },
        },
      },
    },
  });
  await server.listen();
  base = server.resolvedUrls.local[0];
  browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
});
after(async () => {
  await browser?.close();
  await server?.close();
  backend?.kill();
});

/** The demo API, called directly to read state the page should show. */
async function call(path, body) {
  const response = await fetch(`${origin}/api${path}`, body === undefined ? { headers: { "X-Kitty-Secret": secret } } : {
    method: "POST",
    body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", Origin: origin, "X-Kitty-Request": "1", "X-Kitty-Secret": secret },
  });
  assert.equal(response.status, 200, `${path}: ${response.status}`);
  return response.json();
}
const goalNamed = async (title) => (await call("/goals")).goals.find((goal) => goal.title === title);

async function open(hash, { theme, reducedMotion = "no-preference", quiet } = {}) {
  const context = await browser.newContext({ viewport: VIEWPORT, reducedMotion });
  await authorize(context, secret);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue());
  // The theme and quiet hours are build preferences, layered over the demo's build for this window.
  await routeBuild(context, {
    ...(theme ? { theme: { base: theme } } : {}),
    ...(quiet ? { ui: { notifications: { quiet: { enabled: quiet.enabled, from: quiet.from, to: quiet.to,
      let_calendar: quiet.letCalendar ?? true, digest: quiet.digest ?? true } } } } : {}),
  });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(hash, base).href);
  return { page, problems, context };
}
const detail = (page) => page.locator(".goals .detail");
const found = (page) => page.getByRole("complementary", { name: "Progress and Rika's finds" });

describe("Goals view", () => {
  test("Goals is a Plan view: the list, then a goal's why, measures, links, progress and Rika's finds", async () => {
    const internship = await goalNamed(INTERNSHIP);
    const { page, problems, context } = await open(`#goals/${internship.id}`);
    await page.getByRole("heading", { name: INTERNSHIP, level: 2 }).waitFor({ timeout: 60_000 });
    const list = page.getByRole("complementary", { name: "Your goals" });
    assert.equal(await list.getByRole("listitem").count(), 2);
    assert.equal(await list.getByRole("button", { name: new RegExp(INTERNSHIP) }).getAttribute("aria-current"), "true");
    await page.getByRole("navigation", { name: "Workspace groups" }).getByRole("button", { name: "Plan" }).waitFor();
    const page1 = detail(page);
    assert.ok(await page1.getByText(/^by .+ · 120 days left$/).isVisible(), "the target-date chip is missing");
    assert.ok(await page1.getByText(internship.why).isVisible());
    const bars = page1.getByRole("progressbar");
    assert.equal(await bars.count(), internship.measures.length);
    assert.equal(await bars.first().getAttribute("aria-valuetext"), `${internship.measures[0].current} / 40`);
    assert.equal(await page1.getByRole("spinbutton", { name: "Interviews: amount so far" }).inputValue(), "2");
    assert.equal(await page1.getByRole("spinbutton", { name: "Applications sent: amount so far" }).count(), 0, "an auto measure is editable");
    assert.ok(await page1.getByText(`0 / 600 min`).isVisible());
    assert.ok(await page1.getByRole("heading", { name: `Linked · ${internship.links_count}` }).isVisible());
    assert.ok(await found(page).getByRole("img", { name: `${Math.round(internship.progress * 100)}% of the way` }).isVisible());
    assert.ok(await found(page).getByRole("heading", { name: `Rika found · ${internship.suggestions_count}` }).isVisible());
    assert.equal(await found(page).getByRole("article").count(), Math.min(4, internship.suggestions_count));
    if (internship.suggestions_count > 4) {
      await found(page).getByRole("button", { name: `Show ${internship.suggestions_count - 4} more` }).click();
      assert.equal(await found(page).getByRole("article").count(), internship.suggestions_count);
    }
    // The breadcrumb names the goal.
    assert.ok(await page.getByRole("navigation", { name: "Schema tabs" }).getByText(INTERNSHIP).isVisible());
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Link and Not relevant settle Rika's finds; a manual measure moves progress", async () => {
    const before = await goalNamed(INTERNSHIP);
    const { page, problems, context } = await open(`#goals/${before.id}`);
    await page.getByRole("heading", { name: INTERNSHIP, level: 2 }).waitFor({ timeout: 60_000 });
    const first = found(page).getByRole("article").first();
    const title = await first.getAttribute("aria-label");
    await first.getByRole("button", { name: "Link", exact: true }).click();
    await found(page).getByRole("heading", { name: `Rika found · ${before.suggestions_count - 1}` }).waitFor();
    await detail(page).getByRole("heading", { name: `Linked · ${before.links_count + 1}` }).waitFor();
    assert.ok(await detail(page).getByRole("button", { name: new RegExp(title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }).first().isVisible());
    await found(page).getByRole("article").first().getByRole("button", { name: "Not relevant" }).click();
    await found(page).getByRole("heading", { name: `Rika found · ${before.suggestions_count - 2}` }).waitFor();
    const after = await goalNamed(INTERNSHIP);
    assert.equal(after.links_count, before.links_count + 1);
    assert.equal(after.suggestions_count, before.suggestions_count - 2);

    const interviews = detail(page).getByRole("spinbutton", { name: "Interviews: amount so far" });
    await interviews.fill("4");
    await interviews.press("Enter");
    await interviews.blur();
    await page.waitForFunction(async () => {
      const goals = (await (await fetch("/api/goals", { headers: { "X-Kitty-Secret": window.__KITTY_UI_SECRET__ } })).json()).goals;
      return goals.some((goal) => goal.measures.some((measure) => measure.label === "Interviews" && measure.current === 4));
    });
    const moved = await goalNamed(INTERNSHIP);
    await found(page).getByRole("img", { name: `${Math.round(moved.progress * 100)}% of the way` }).waitFor();
    assert.ok(moved.progress > before.progress);
    // A negative amount is refused in place.
    await interviews.fill("-1");
    await interviews.blur();
    await detail(page).getByRole("alert").filter({ hasText: "needs an amount of 0 or more" }).waitFor();
    assert.equal(await interviews.inputValue(), "4");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("the link picker searches what the modules hold and links a choice", async () => {
    const deck = await goalNamed(DECK);
    const { page, problems, context } = await open(`#goals/${deck.id}`);
    await page.getByRole("heading", { name: DECK, level: 2 }).waitFor({ timeout: 60_000 });
    await detail(page).getByRole("button", { name: "Link item" }).click();
    const picker = page.getByRole("dialog", { name: "Link an item to this goal" });
    await picker.waitFor();
    assert.equal(await picker.getByRole("searchbox", { name: "Search items to link" }).evaluate((node) => node === document.activeElement), true);
    await picker.getByRole("searchbox", { name: "Search items to link" }).fill("intern");
    const choice = picker.getByRole("list", { name: "Items to link" }).getByRole("button").first();
    await choice.waitFor({ timeout: 10_000 });
    await choice.click();
    await picker.waitFor({ state: "detached" });
    await detail(page).getByRole("heading", { name: `Linked · ${deck.links_count + 1}` }).waitFor();
    assert.equal((await goalNamed(DECK)).links_count, deck.links_count + 1);
    // Unlinking takes it off again.
    await detail(page).getByRole("button", { name: /^Unlink / }).first().click();
    await detail(page).getByRole("heading", { name: `Linked · ${deck.links_count}` }).waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("a goal is created, edited and deleted in the side drawer", async () => {
    const { page, problems, context } = await open("#goals");
    await page.getByRole("heading", { name: /./, level: 2 }).first().waitFor({ timeout: 60_000 });
    await page.getByRole("button", { name: "New goal" }).first().click();
    const drawer = page.getByRole("dialog", { name: "New goal" });
    await drawer.waitFor();
    assert.equal(await drawer.getByRole("textbox", { name: "Title" }).evaluate((node) => node === document.activeElement), true);
    await drawer.getByRole("textbox", { name: "Title" }).fill("Run a half marathon");
    await drawer.getByRole("textbox", { name: "Why" }).fill("Finish strong in the spring race.");
    await drawer.getByLabel("Target date").fill("2027-04-12");
    const measure = drawer.getByRole("group", { name: "Measure 1" });
    await measure.getByRole("textbox", { name: "Name" }).fill("Long run");
    await drawer.getByRole("button", { name: "Create goal" }).click();
    await drawer.getByRole("alert").filter({ hasText: "“Long run” needs a target above 0." }).waitFor();
    await drawer.getByRole("group", { name: "Long run" }).getByRole("spinbutton", { name: "Target" }).fill("21");
    await drawer.getByRole("group", { name: "Long run" }).getByRole("textbox", { name: "Unit" }).fill("km");
    await drawer.getByRole("button", { name: "Create goal" }).click();
    await drawer.waitFor({ state: "detached" });
    await page.getByRole("heading", { name: "Run a half marathon", level: 2 }).waitFor();
    assert.ok(await detail(page).getByText("/ 21 km").isVisible());
    const created = await goalNamed("Run a half marathon");
    assert.deepEqual(created.measures.map(({ label, target, unit, auto }) => ({ label, target, unit, auto })), [{ label: "Long run", target: 21, unit: "km", auto: null }]);
    assert.ok(page.url().endsWith(`#goals/${created.id}`), page.url());

    await detail(page).getByRole("button", { name: "Edit goal" }).click();
    const edit = page.getByRole("dialog", { name: "Edit goal" });
    await edit.getByRole("textbox", { name: "Title" }).fill("Run a spring half marathon");
    await edit.getByRole("button", { name: "Save" }).click();
    await page.getByRole("heading", { name: "Run a spring half marathon", level: 2 }).waitFor();
    assert.ok(await page.getByRole("complementary", { name: "Your goals" }).getByRole("button", { name: /Run a spring half marathon/ }).isVisible());

    await detail(page).getByRole("button", { name: "Edit goal" }).click();
    await page.getByRole("dialog", { name: "Edit goal" }).getByRole("button", { name: "Delete goal" }).click();
    await page.getByRole("dialog", { name: "Edit goal" }).getByRole("button", { name: "Delete for good" }).click();
    await page.getByRole("dialog", { name: "Edit goal" }).waitFor({ state: "detached" });
    await page.getByRole("complementary", { name: "Your goals" }).getByRole("button", { name: /Run a spring half marathon/ }).waitFor({ state: "detached" });
    assert.equal(await goalNamed("Run a spring half marathon"), undefined);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("a stale edit is refused and the drawer reloads the goal instead of overwriting it", async () => {
    const deck = await goalNamed(DECK);
    const { page, problems, context } = await open(`#goals/${deck.id}`);
    await page.getByRole("heading", { name: DECK, level: 2 }).waitFor({ timeout: 60_000 });
    await detail(page).getByRole("button", { name: "Edit goal" }).click();
    const edit = page.getByRole("dialog", { name: "Edit goal" });
    await edit.waitFor();
    // Someone else changes the goal meanwhile (another tab, Rika).
    await call(`/goals/${deck.id}`, { revision: deck.revision, why: "Changed elsewhere." });
    await edit.getByRole("textbox", { name: "Title" }).fill("Overwrite attempt");
    await edit.getByRole("button", { name: "Save" }).click();
    await edit.getByRole("alert").filter({ hasText: "changed while you were editing" }).waitFor();
    assert.equal(await edit.getByRole("textbox", { name: "Why" }).inputValue(), "Changed elsewhere.");
    assert.equal((await goalNamed(DECK)).why, "Changed elsewhere.");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("Morning brief", () => {
  test("home shows today's brief; the bar chip's popover lists it, marks it seen and opens a goal", async () => {
    const { brief } = await call("/rika/brief");
    assert.ok(brief.items.length > 0, "the demo brief has no items");
    const { page, problems, context } = await open("#today");
    const widget = page.getByRole("complementary", { name: "Home widgets" }).getByRole("region", { name: "Morning brief" });
    await widget.waitFor({ timeout: 60_000 });
    // Compact on home: three one-line items, then a link to the rest on Goals.
    assert.equal(await widget.getByRole("list").getByRole("link").count(), Math.min(3, brief.items.length));
    if (brief.items.length > 3) assert.ok(await widget.getByRole("link", { name: `${brief.items.length - 3} more in Goals` }).isVisible());
    assert.ok(await widget.getByText(brief.items[0].title).first().isVisible());
    const chip = page.getByRole("region", { name: "Rika" }).getByRole("button", { name: /^Morning brief, / });
    assert.equal(await chip.textContent(), `Morning brief · ${brief.items.length}`);
    // Keyboard: focus the chip and open it with Enter.
    await chip.focus();
    await page.keyboard.press("Enter");
    const popover = page.getByRole("dialog", { name: "Morning brief" });
    await popover.waitFor();
    const chipBox = await chip.boundingBox();
    const popBox = await popover.boundingBox();
    assert.ok(popBox.y + popBox.height <= chipBox.y, "the popover is not above the bar");
    assert.equal(await popover.getByRole("list").getByRole("link").count(), brief.items.length);
    assert.ok(await popover.getByRole("link", { name: "Open Goals" }).isVisible());
    await page.waitForFunction(async () => (await (await fetch("/api/rika/brief", { headers: { "X-Kitty-Secret": window.__KITTY_UI_SECRET__ } })).json()).brief.seen === true);
    await page.waitForFunction(() => !document.querySelector(".brief-chip")?.getAttribute("aria-label")?.endsWith(", new"));
    await page.keyboard.press("Escape");
    await popover.waitFor({ state: "detached" });
    assert.equal(await chip.evaluate((node) => node === document.activeElement), true, "Escape did not return focus to the chip");
    // An item opens its goal.
    await chip.click();
    const item = brief.items[0];
    await popover.getByRole("link", { name: new RegExp(item.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }).first().click();
    await page.getByRole("heading", { name: item.goal_title, level: 2 }).waitFor({ timeout: 30_000 });
    assert.ok(page.url().includes(`#goals/${item.goal_id}`), page.url());
    await popover.waitFor({ state: "detached" });
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Ctrl K offers Morning brief and Goals", async () => {
    const { page, problems, context } = await open("#today");
    await page.getByRole("region", { name: "Rika" }).getByRole("button", { name: /^Morning brief, / }).waitFor({ timeout: 60_000 });
    await page.keyboard.press("Control+k");
    await page.getByRole("combobox", { name: /^Search sections/ }).fill("Morning brief");
    await page.keyboard.press("Enter");
    await page.getByRole("dialog", { name: "Morning brief" }).waitFor();
    await page.keyboard.press("Escape");
    await page.keyboard.press("Control+k");
    await page.getByRole("combobox", { name: /^Search sections/ }).fill("Goals");
    await page.keyboard.press("Enter");
    await page.getByRole("complementary", { name: "Your goals" }).waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("Rika alerts", () => {
  test("an urgent match toasts once as a strong match, and Open shows its goal", async () => {
    const { alerts } = await call("/rika/alerts");
    assert.ok(alerts.length > 0, "the demo has no urgent match");
    const alert = alerts[0];
    const { page, problems, context } = await open("#today");
    const toast = page.getByRole("list", { name: "New notifications" }).getByRole("listitem").filter({ hasText: alert.title }).first();
    await toast.waitFor({ timeout: 60_000 });
    assert.match(await toast.textContent(), /Rika · strong match/);
    await toast.getByRole("button", { name: "Open", exact: true }).click();
    const goal = (await call("/goals")).goals.find((item) => item.id === alert.goal_id);
    await page.getByRole("heading", { name: goal.title, level: 2 }).waitFor({ timeout: 30_000 });
    // Delivered once: a reload does not toast it again.
    await page.reload();
    await page.getByRole("heading", { name: goal.title, level: 2 }).waitFor({ timeout: 60_000 });
    await page.waitForTimeout(1500);
    assert.equal(await page.getByText("Rika · strong match").count(), 0);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("quiet hours hold Rika alerts like other sources", async () => {
    const { alerts } = await call("/rika/alerts");
    const minute = new Date().getHours() * 60 + new Date().getMinutes();
    const quiet = { enabled: true, from: (minute + 1380) % 1440, to: (minute + 60) % 1440, letCalendar: true, digest: true };
    const { page, problems, context } = await open("#today", { quiet });
    await page.getByRole("button", { name: /^Notifications, / }).waitFor({ timeout: 60_000 });
    await page.waitForFunction((key) => JSON.parse(localStorage.getItem("kitty.notify.ledger") ?? "{}").held?.includes(key), alerts[0].key, { timeout: 30_000 });
    assert.equal(await page.getByText("Rika · strong match").count(), 0, "an alert toasted during quiet hours");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("themes and motion", () => {
  for (const theme of ["kitty", "liquid-glass"]) {
    test(`the Goals view and the brief popover render in the ${theme} theme`, async () => {
      const internship = await goalNamed(INTERNSHIP);
      const { page, problems, context } = await open(`#goals/${internship.id}`, { theme });
      await page.getByRole("heading", { name: INTERNSHIP, level: 2 }).waitFor({ timeout: 60_000 });
      assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), theme);
      // Text and controls stay readable: the title is opaque ink, never the surface colour.
      const [ink, fill] = await detail(page).evaluate((node) => [getComputedStyle(node.querySelector("h2")).color, getComputedStyle(node).backgroundColor]);
      assert.notEqual(ink, fill);
      await page.getByRole("region", { name: "Rika" }).getByRole("button", { name: /^Morning brief, / }).click();
      const popover = page.getByRole("dialog", { name: "Morning brief" });
      await popover.waitFor();
      assert.ok((await popover.boundingBox()).width >= 300);
      assert.deepEqual(problems, []);
      await context.close();
    });
  }

  test("Reduce motion stills the measure bars and the progress ring", async () => {
    const internship = await goalNamed(INTERNSHIP);
    const { page, problems, context } = await open(`#goals/${internship.id}`, { reducedMotion: "reduce" });
    await page.getByRole("heading", { name: INTERNSHIP, level: 2 }).waitFor({ timeout: 60_000 });
    const durations = await detail(page).evaluate((node) => [
      getComputedStyle(node.querySelector("[role=progressbar] > span")).transitionDuration,
      getComputedStyle(node.querySelector(".timer-ring .progress")).transitionDuration,
    ]);
    // No visible transition is left (at most a millisecond).
    for (const duration of durations) assert.ok(parseFloat(duration) <= 0.001, `still moves for ${duration}`);
    assert.deepEqual(problems, []);
    await context.close();
  });
});
