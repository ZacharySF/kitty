// Settings as a left index of short pages (Q23), in the real app against the disposable
// synthetic backend (`kitty-web --demo`); no private workspace is read.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/settings.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer as createNetServer } from "node:net";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";
import { DOCK, routeBuild } from "./buildRoutes.mjs";
import { announcement, authorize, exchange } from "./kittyServer.js";

const root = fileURLToPath(new URL("..", import.meta.url));
const repository = fileURLToPath(new URL("../..", import.meta.url));
const DEFAULT = { width: 1280, height: 850 };
const GUIDE = "https://github.com/ZacharySF/kitty/blob/main/docs/GOOGLE_ONBOARDING.md";
const PAGES = ["Appearance", "Desktop", "Quiet hours", "Build", "Account", "Modules", "Graphs", "Tags", "Connected apps", "Keys", "Devices"];
/** The platform pages' own regions (their components keep their names; Build's is "Build settings"). */
const REGIONS = [...PAGES, "Build settings"];

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
          configure: (proxy) => proxy.on("proxyReq", (request) => request.setHeader("Origin", origin)),
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

/** Opens a fresh returning-user profile on `hash` with the dock hidden. */
async function open(hash, { viewport = DEFAULT } = {}) {
  const context = await browser.newContext({ viewport });
  await authorize(context, secret);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  const prefs = await routeBuild(context, { ui: { dock: DOCK.hidden } });
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`#${hash}`, base).href);
  return { page, problems, context, prefs };
}

const index = (page) => page.getByRole("navigation", { name: "Settings pages" });
const current = (page) => index(page).locator('[aria-current="page"]');
const region = (page, name) => page.getByRole("region", { name, exact: true });
/** The one page shown beside the index: every page region the settings view renders. */
const shownPages = (page) => page.evaluate((names) =>
  [...document.querySelectorAll("section[aria-label]")]
    .filter((node) => names.includes(node.getAttribute("aria-label")) && node.getClientRects().length)
    .map((node) => node.getAttribute("aria-label")), REGIONS);

/** Elements inside `scope` drawn as their own box: a visible border all round over a painted fill. */
const nestedBoxes = (locator) => locator.evaluate((scope) => {
  const painted = (color) => {
    const alpha = /rgba?\([^)]*?,\s*([\d.]+)\)/.exec(color)?.[1];
    return color !== "transparent" && (alpha === undefined ? true : Number(alpha) > 0.02);
  };
  const boxes = [];
  for (const node of scope.querySelectorAll("*")) {
    // Controls (buttons, fields, switches, segments) and the appearance preview may draw their own chrome.
    if (node.closest("button, input, select, textarea, label, a, [role=switch], [role=img], [role=slider], [role=group][aria-label='Appearance presets']")) continue;
    const box = node.getBoundingClientRect();
    if (box.width < 120 || box.height < 40) continue;
    const style = getComputedStyle(node);
    const sides = ["Top", "Right", "Bottom", "Left"].every((side) =>
      parseFloat(style[`border${side}Width`]) > 0 && style[`border${side}Style`] !== "none" && painted(style[`border${side}Color`]));
    const fill = painted(style.backgroundColor) || style.backgroundImage !== "none";
    if (sides && fill) boxes.push(`${node.tagName.toLowerCase()}.${[...node.classList].join(".")}`);
  }
  return boxes;
});

describe("settings index", () => {
  test("a Fraunces title, a sentence-case grouped index of the derived pages, and one page at a time", async () => {
    const { page, problems, context } = await open("settings");
    await index(page).waitFor();
    const title = page.getByRole("heading", { level: 1, name: "Settings" });
    assert.match(await title.evaluate((node) => getComputedStyle(node).fontFamily), /Fraunces/);
    const items = index(page).locator("a, button");
    await page.waitForFunction(() => document.querySelectorAll('nav[aria-label="Settings pages"] :is(a, button)').length === 13);
    assert.deepEqual(await items.allTextContents().then((texts) => texts.map((text) => text.trim())),
      [...PAGES, "Profile & saved answers", "AI & privacy"]);
    for (const item of await items.all()) assert.equal(await item.locator("svg").count(), 1, "index item without its icon");
    const groups = await index(page).locator("h2").evaluateAll((nodes) => nodes.map((node) => {
      const style = getComputedStyle(node);
      return { text: node.textContent.trim(), family: style.fontFamily, weight: style.fontWeight, transform: style.textTransform, italic: style.fontStyle };
    }));
    assert.deepEqual(groups.map((group) => group.text), ["General", "Workspace", "Connections", "Careers & AI"]);
    for (const group of groups) {
      assert.match(group.family, /^"?Inter/, `${group.text} is not Inter`);
      assert.equal(group.weight, "600");
      assert.equal(group.transform, "none");
      assert.equal(group.italic, "normal");
    }
    // No slug: Appearance, alone.
    assert.equal((await current(page).textContent()).trim(), "Appearance");
    await region(page, "Appearance").waitFor();
    assert.deepEqual(await shownPages(page), ["Appearance"]);
    assert.equal(await page.locator('[data-tutorial="modules"]').count(), 1, "the tour cannot find Modules from Appearance");

    // Choosing a page shows only it and deep-links it.
    await index(page).getByRole("link", { name: "Modules" }).click();
    await region(page, "Modules").waitFor();
    assert.equal(await page.evaluate(() => location.hash), "#settings/modules");
    assert.deepEqual(await shownPages(page), ["Modules"]);
    assert.equal((await current(page).textContent()).trim(), "Modules");

    // Keyboard: Tab reaches the index and Enter opens a page.
    await index(page).getByRole("link", { name: "Graphs" }).focus();
    await page.keyboard.press("Enter");
    await region(page, "Graphs").waitFor();
    assert.equal(await page.evaluate(() => location.hash), "#settings/graphs");

    // Back returns to the previous page, and then to the first one.
    await page.goBack();
    await region(page, "Modules").waitFor();
    assert.equal(await page.evaluate(() => location.hash), "#settings/modules");
    assert.deepEqual(await shownPages(page), ["Modules"]);
    await page.goBack();
    await region(page, "Appearance").waitFor();
    assert.deepEqual(await shownPages(page), ["Appearance"]);
    await page.goForward();
    await region(page, "Modules").waitFor();

    // Careers pages are their own views.
    await index(page).getByRole("button", { name: "AI & privacy" }).click();
    await page.waitForFunction(() => location.hash === "#ai");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("a deep link opens its page directly; Find a setting filters the index by name and keyword", async () => {
    const { page, problems, context } = await open("settings/graphs");
    await region(page, "Graphs").waitFor();
    assert.deepEqual(await shownPages(page), ["Graphs"]);
    assert.equal((await current(page).textContent()).trim(), "Graphs");
    const find = page.getByRole("searchbox", { name: "Find a setting" });
    const names = () => index(page).locator("a, button").allTextContents().then((texts) => texts.map((text) => text.trim()));
    await find.fill("quiet");
    assert.deepEqual(await names(), ["Quiet hours"]);
    await find.fill("physics");
    assert.deepEqual(await names(), ["Graphs"]);
    assert.deepEqual(await index(page).locator("h2").allTextContents(), ["Workspace"], "an empty group kept its label");
    await find.fill("zzzz");
    assert.deepEqual(await names(), []);
    await page.getByText("No setting matches “zzzz”.").waitFor();
    await find.fill("google");
    assert.deepEqual(await names(), ["Account"]);
    await find.press("Enter");
    await region(page, "Account").waitFor();
    assert.equal(await page.evaluate(() => location.hash), "#settings/account");
    await find.fill("");
    assert.equal((await names()).length, 13);
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("settings pages", () => {
  test("Modules: one switch and one description per module; Save and Discard follow the selection", async () => {
    const { page, problems, context } = await open("settings/modules");
    const modules = region(page, "Modules");
    await modules.getByRole("switch").first().waitFor();
    const available = await page.evaluate(async () => (await (await fetch("/api/modules", { headers: { "X-Kitty-Secret": window.__KITTY_UI_SECRET__ } })).json()).available);
    assert.ok(available.length >= 3);
    const text = await modules.evaluate((node) => node.innerText);
    for (const item of available) {
      assert.equal(text.split(item.description).length - 1, 1, `${item.label}'s description is not printed exactly once`);
      const toggle = modules.getByRole("switch", { name: item.label, exact: true });
      assert.equal(await toggle.count(), 1);
    }
    assert.doesNotMatch(text, /Not active now|Active now/, "the running state still prints as a sentence per row");
    const save = modules.getByRole("button", { name: "Save modules" });
    const discard = modules.getByRole("button", { name: /^Discard/ });
    assert.equal(await save.isDisabled(), true);
    const toggle = modules.getByRole("switch", { name: available.at(-1).label, exact: true });
    const before = await toggle.getAttribute("aria-checked");
    await toggle.click();
    assert.notEqual(await toggle.getAttribute("aria-checked"), before);
    assert.equal(await save.isDisabled(), false);
    // Unsaved state is the tab's dot, not a sentence.
    await page.getByRole("tab", { selected: true }).getByRole("img", { name: /Unsaved/ }).waitFor();
    assert.equal(await modules.getByText("Unsaved selection.").count(), 0);
    await discard.click();
    assert.equal(await toggle.getAttribute("aria-checked"), before);
    assert.equal(await save.isDisabled(), true);
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Graphs: segmented default layout; slider explanations are tooltips and descriptions, not visible text", async () => {
    const { page, problems, context, prefs } = await open("settings/graphs");
    const graphs = region(page, "Graphs");
    await graphs.waitFor();
    const layout = graphs.getByRole("button", { name: "Force", exact: true });
    await layout.click();
    assert.equal(await layout.getAttribute("aria-pressed"), "true");
    for (let attempt = 0; attempt < 50 && prefs.ui?.graph?.layout !== "force"; attempt += 1) await page.waitForTimeout(50);
    assert.equal(prefs.ui?.graph?.layout, "force", "the default layout is not saved to the build");
    for (const name of ["Repulsion", "Spring", "Damping"]) {
      const slider = graphs.getByRole("slider", { name, exact: true });
      const help = await slider.evaluate((input) => {
        const node = document.getElementById(input.getAttribute("aria-describedby") ?? "");
        if (!node) return null;
        const box = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return { text: node.textContent.trim(), area: box.width * box.height, clipped: style.clip !== "auto" || style.clipPath !== "none" };
      });
      assert.ok(help && help.text.split(" ").length >= 5, `${name} has no description`);
      assert.ok(help.area <= 1 && help.clipped, `${name}'s explanation is visible text (${help.area}px²)`);
      const tip = graphs.locator(`[title="${help.text}"]`);
      assert.equal(await tip.count(), 1, `${name} has no ⓘ tooltip`);
      assert.ok((await tip.boundingBox()).width > 0, `${name}'s ⓘ is not shown`);
      // Nothing else draws the explanation.
      const drawn = await graphs.evaluate((scope, text) => [...scope.querySelectorAll("*")].filter((node) =>
        [...node.childNodes].some((child) => child.nodeType === Node.TEXT_NODE && child.textContent.includes(text))
        && node.getBoundingClientRect().width * node.getBoundingClientRect().height > 1).length, help.text);
      assert.equal(drawn, 0, `${name}'s explanation is printed`);
    }
    const spring = graphs.getByRole("slider", { name: "Spring", exact: true });
    // A slider is a bare track, not a field box.
    const track = await spring.evaluate((node) => {
      const style = getComputedStyle(node);
      return { border: style.borderTopWidth, fill: style.backgroundColor };
    });
    assert.deepEqual(track, { border: "0px", fill: "rgba(0, 0, 0, 0)" });
    await spring.fill("0.2");
    for (let attempt = 0; attempt < 50 && prefs.ui?.graph?.physics?.spring !== 0.2; attempt += 1) await page.waitForTimeout(50);
    assert.equal(prefs.ui.graph.physics.spring, 0.2);
    assert.equal(await graphs.getByText(/Saved · open schemas/).count(), 0);
    await graphs.getByRole("button", { name: "Reset to defaults" }).click();
    for (let attempt = 0; attempt < 50 && prefs.ui?.graph?.layout !== "tree"; attempt += 1) await page.waitForTimeout(50);
    assert.equal(prefs.ui.graph.layout, "tree");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Account: Google is one row with the setup guide link, no fold-out; the tour is one row", async () => {
    const { page, problems, context } = await open("settings/account");
    const account = region(page, "Account");
    await account.getByRole("button", { name: "Connect Google" }).waitFor();
    const guide = account.getByRole("link", { name: /Setup guide/ });
    assert.equal(await guide.getAttribute("href"), GUIDE);
    assert.equal(await guide.getAttribute("target"), "_blank");
    assert.match(await guide.getAttribute("rel"), /noreferrer/);
    assert.equal(await account.locator("details, summary").count(), 0);
    await account.getByText("Optional sign-in. Your data stays local.").waitFor();
    assert.doesNotMatch(await account.innerText(), /does not sync your data|OAuth|KITTY_GOOGLE/);
    await account.getByRole("button", { name: "Take the tour" }).waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("every page is one surface with hairline sections, no nested boxes and no native select", async () => {
    const { page, problems, context } = await open("settings");
    await index(page).waitFor();
    for (const name of PAGES) {
      await index(page).getByRole("link", { name, exact: true }).click();
      const label = name === "Build" ? "Build settings" : name;
      const shown = region(page, label);
      await shown.waitFor();
      await page.waitForTimeout(150);
      assert.deepEqual(await shownPages(page), [label]);
      const surface = await shown.evaluate((node) => {
        const style = getComputedStyle(node);
        return { border: parseFloat(style.borderTopWidth), radius: parseFloat(style.borderTopLeftRadius) };
      });
      assert.ok(surface.border > 0 && surface.radius > 0, `${name} is not drawn as a surface`);
      assert.deepEqual(await nestedBoxes(shown), [], `${name} nests boxes inside its surface`);
      assert.equal(await page.locator(".settings-workspace select, main select").count(), 0, `${name} renders a native select`);
      assert.equal(await shown.locator("details").count(), 0, `${name} keeps a fold-out`);
    }
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Desktop and Quiet hours keep their controls with tooltips instead of paragraphs", async () => {
    const { page, problems, context } = await open("settings/quiet-hours");
    const quiet = region(page, "Quiet hours");
    await quiet.getByRole("slider", { name: "Quiet from" }).waitFor();
    for (const name of ["Quiet hours", "Let calendar events through", "Morning digest"])
      await quiet.getByRole("switch", { name, exact: true }).waitFor();
    assert.doesNotMatch(await quiet.innerText(), /kitty checks reminders only while it runs/);
    await page.goto(new URL("#settings/desktop", base).href);
    const desktop = region(page, "Desktop");
    await desktop.waitFor();
    await desktop.getByText(/desktop app/).waitFor();
    const words = (await desktop.locator(".br-panel__header ~ *").innerText()).trim().split(/\s+/).length;
    assert.ok(words <= 12, `Desktop prints ${words} words in the browser`);
    assert.deepEqual(problems, []);
    await context.close();
  });
});
