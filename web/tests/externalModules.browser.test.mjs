// Add-on modules (Settings → Modules → Add-on modules), in the real app against the disposable
// synthetic backend (`kitty-web --demo`) and a tiny local stand-in for GitHub's REST API
// (KITTY_GITHUB_API). The module tarballs are synthetic and built here with the `tar` CLI.
// Run from web/ with Node >=22, uv, tar and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/externalModules.browser.test.mjs
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer as createHttpServer } from "node:http";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";
import { routeBuild } from "./buildRoutes.mjs";
import { announcement, authorize, exchange } from "./kittyServer.js";

const root = fileURLToPath(new URL("..", import.meta.url));
const repository = fileURLToPath(new URL("../..", import.meta.url));

let work;
let github;
let githubOrigin;
let backend;
let secret;
let server;
let browser;
let base;
/** Requests the GitHub stand-in answered, as "METHOD path". */
const hits = [];

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = createNetServer().once("error", reject).listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

/** A synthetic python-kind module with an element view, packed as `<id>-<version>.tar.gz`. */
function buildModule(id, name, version = "0.1.0") {
  const folder = join(work, id);
  const packageName = id.replaceAll("-", "_");
  mkdirSync(join(folder, packageName), { recursive: true });
  mkdirSync(join(folder, "ui"), { recursive: true });
  writeFileSync(join(folder, "rika-module.json"), JSON.stringify({
    id, name, version, api: 1, description: `${name}: a synthetic test module.`, license: "MIT", kind: "python",
    python: { package: packageName },
    view: { id: "demo", label: name, icon: "study", group: "study", first: true, ui: "element", element: { script: "ui/view.js", tag: `${id}-view` } },
  }, null, 2));
  writeFileSync(join(folder, packageName, "__init__.py"),
    "def get(app, conn, path, query):\n    return {\"hello\": path}\n\n\ndef post(app, conn, path, body):\n    return {\"ok\": True}\n");
  writeFileSync(join(folder, "ui", "view.js"),
    `customElements.define(${JSON.stringify(`${id}-view`)}, class extends HTMLElement { connectedCallback() { this.textContent = "${name} page"; } });\n`);
  const file = join(work, `${id}-${version}.tar.gz`);
  execFileSync("tar", ["-czf", file, "-C", folder, "."]);
  return readFileSync(file);
}

/** `releases/latest` for each repository; `digest` may be forced wrong to test the checksum guard. */
const releases = new Map();
function publish(repo, id, bytes, { digest, version = "0.1.0" } = {}) {
  const asset = releases.size + 1;
  releases.set(repo, {
    bytes,
    asset,
    body: {
      tag_name: `v${version}`,
      name: `v${version}`,
      assets: [{
        name: `${id}-${version}.tar.gz`,
        url: `${githubOrigin}/repos/${repo}/releases/assets/${asset}`,
        browser_download_url: `${githubOrigin}/download/${repo}/${id}-${version}.tar.gz`,
        size: bytes.length,
        digest: `sha256:${digest ?? createHash("sha256").update(bytes).digest("hex")}`,
      }],
    },
  });
}

before(async () => {
  work = mkdtempSync(join(tmpdir(), "rika-addons-"));
  github = createHttpServer((request, response) => {
    hits.push(`${request.method} ${request.url}`);
    const latest = /^\/repos\/([^/]+\/[^/]+)\/releases\/latest$/.exec(request.url ?? "");
    const asset = /^\/repos\/([^/]+\/[^/]+)\/releases\/assets\/(\d+)$/.exec(request.url ?? "");
    const release = releases.get((latest ?? asset)?.[1]);
    if (latest && release) {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify(release.body));
    } else if (asset && release && Number(asset[2]) === release.asset) {
      response.writeHead(200, { "Content-Type": "application/octet-stream", "Content-Length": release.bytes.length });
      response.end(release.bytes);
    } else {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ message: "Not Found" }));
    }
  });
  await new Promise((resolve) => github.listen(0, "127.0.0.1", resolve));
  githubOrigin = `http://127.0.0.1:${github.address().port}`;
  publish("octo-org/rika-demo", "rika-demo", buildModule("rika-demo", "Demo"));
  publish("octo-org/rika-tampered", "rika-tampered", buildModule("rika-tampered", "Tampered"), { digest: "0".repeat(64) });

  backend = spawn("uv", ["run", "--quiet", "kitty-web", "--demo", "--port", String(await freePort())], {
    cwd: repository,
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, KITTY_GITHUB_API: githubOrigin, KITTY_KEYRING: "none", KITTY_SYNC: "off" },
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
  secret = await exchange(origin, code);
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
        "/modules": { target: origin, changeOrigin: true },
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
  await new Promise((resolve) => (github ? github.close(resolve) : resolve()));
  if (work) rmSync(work, { recursive: true, force: true });
});

/**
 * A fresh returning-user profile on `hash`; `views` adds active add-on views to bootstrap,
 * `routes(context)` stubs more requests before the page loads.
 */
async function open(hash, { views, routes } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 } });
  await authorize(context, secret);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  await routeBuild(context);
  if (views)
    await context.route("**/api/bootstrap", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.modules.external = { installed: [], token: { configured: false }, ...body.modules.external, views };
      await route.fulfill({ response, json: body });
    });
  await routes?.(context);
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL(`#${hash}`, base).href);
  return { page, problems, context };
}
const addons = (page) => page.getByRole("region", { name: "Add-on modules", exact: true });

/** Holds `/api/external-modules/install` until the returned release runs, so the cover can be inspected. */
async function holdInstall(context) {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  await context.route("**/api/external-modules/install", async (route) => { await held; await route.continue(); });
  return () => release();
}

/** The full-window download cover: says what is downloading, is busy, and blocks the page underneath. */
async function assertCover(page, { name, reduced = false }) {
  const cover = page.getByRole("dialog", { name: `Downloading ${name}…` });
  await cover.waitFor();
  assert.equal(await cover.getAttribute("aria-busy"), "true");
  await cover.getByRole("status").filter({ hasText: `Downloading ${name}…` }).waitFor();
  const state = await cover.evaluate((node) => {
    const box = node.getBoundingClientRect();
    const target = document.querySelector("form button[type=submit]")?.getBoundingClientRect();
    return {
      box: [box.x, box.y, box.width, box.height],
      viewport: [0, 0, innerWidth, innerHeight],
      modal: node.matches(":modal"),
      covered: !!target && node.contains(document.elementFromPoint(target.x + target.width / 2, target.y + target.height / 2)),
    };
  });
  assert.deepEqual(state.box, state.viewport, "the cover does not fill the window");
  assert.ok(state.modal && state.covered, JSON.stringify(state));
  await page.keyboard.press("Escape");
  assert.ok(await cover.isVisible(), "Escape hid a running download");
  const canvas = cover.locator("canvas.brain-cloud");
  const frame = await canvas.evaluate((node) => node.toDataURL());
  if (reduced) {
    await page.waitForTimeout(150);
    assert.equal(await canvas.evaluate((node) => node.toDataURL()), frame, "reduced motion still turns the brain");
  } else {
    await page.waitForFunction((frame) => document.querySelector("canvas.brain-cloud")?.toDataURL() !== frame, frame);
  }
  return cover;
}

describe("Settings → Modules → Add-on modules", () => {
  test("shows the GitHub token status and links to Keys", async () => {
    const { page, problems, context } = await open("settings/modules");
    const section = addons(page);
    await section.waitFor();
    const text = await section.innerText();
    assert.match(text, /GitHub token\s*Not set/i);
    await section.getByRole("button", { name: "Add in Keys" }).click();
    await page.getByRole("region", { name: "Keys", exact: true }).waitFor();
    assert.equal(await page.evaluate(() => location.hash), "#settings/keys");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("a repository that is not owner/repo is refused in plain words before anything is fetched", async () => {
    const { page, problems, context } = await open("settings/modules");
    const section = addons(page);
    const before = hits.length;
    await section.getByRole("textbox", { name: "GitHub repository" }).fill("just-a-name");
    await section.getByRole("button", { name: "Add", exact: true }).click();
    await section.getByRole("alert").filter({ hasText: "Enter the repository as owner/repo" }).waitFor();
    assert.equal(await section.getByRole("textbox", { name: "GitHub repository" }).getAttribute("aria-invalid"), "true");
    assert.equal(hits.length, before, "an invalid name reached GitHub");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("a download whose checksum does not match is refused under the cover, which then clears to the error", async () => {
    const { page, problems, context } = await open("settings/modules");
    const section = addons(page);
    const release = await holdInstall(context);
    await section.getByRole("textbox", { name: "GitHub repository" }).fill("octo-org/rika-tampered");
    const add = section.getByRole("button", { name: "Add", exact: true });
    await add.click();
    const cover = await assertCover(page, { name: "octo-org/rika-tampered" });
    release();
    await cover.waitFor({ state: "hidden", timeout: 30_000 });
    const alert = section.getByRole("alert").filter({ hasText: "does not match the release's published SHA-256 checksum" });
    await alert.waitFor();
    assert.doesNotMatch(await alert.innerText(), /Traceback|Exception|\bError:/);
    assert.equal(await section.locator('[data-module="rika-tampered"]').count(), 0);
    assert.ok(hits.includes("GET /repos/octo-org/rika-tampered/releases/latest"));
    await page.waitForFunction(() => document.activeElement?.textContent?.trim() === "Add");
    assert.ok(await add.isEnabled(), "the form stays usable after a failure");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("adding owner/repo shows the cover while it downloads, then lists the module to restart", async () => {
    const { page, problems, context } = await open("settings/modules");
    await page.emulateMedia({ reducedMotion: "reduce" });
    const section = addons(page);
    const release = await holdInstall(context);
    await section.getByRole("textbox", { name: "GitHub repository" }).fill("octo-org/rika-demo");
    await section.getByRole("button", { name: "Add", exact: true }).click();
    const cover = await assertCover(page, { name: "octo-org/rika-demo", reduced: true });
    release();
    await cover.waitFor({ state: "hidden", timeout: 30_000 });
    const row = section.locator('[data-module="rika-demo"]');
    await row.waitFor();
    const text = await row.innerText();
    for (const fact of ["Demo", "0.1.0", "octo-org/rika-demo", "License: MIT", "Restart to install"])
      assert.ok(text.toLowerCase().includes(fact.toLowerCase()), `${fact} is missing from the row:\n${text}`);
    const modules = await page.evaluate(async () =>
      (await (await fetch("/api/modules", { headers: { "X-Kitty-Secret": window.__KITTY_UI_SECRET__ } })).json()));
    const installed = modules.external.installed.find((item) => item.id === "rika-demo");
    assert.equal(installed.status, "restart-to-install");
    assert.equal(modules.restart_required, true);
    await section.getByRole("status").filter({ hasText: "Restart rika! to finish" }).waitFor();
    assert.ok(hits.some((hit) => hit.startsWith("GET /repos/octo-org/rika-demo/releases/assets/")), "the asset was downloaded");
    assert.equal(await section.getByRole("textbox", { name: "GitHub repository" }).inputValue(), "");
    await page.waitForFunction(() => document.activeElement?.getAttribute("placeholder") === "owner/repo");
    assert.deepEqual(problems, []);
    await context.close();
  });
});

describe("active add-on views", () => {
  const element = {
    module: "rika-fake", version: "0.1.0", id: "fake", label: "Fake study", icon: "no-such-icon", group: "study", first: true,
    ui: "element", script: "/modules/rika-fake/0.1.0/ui/view.js", tag: "rika-fake-view",
  };
  const frame = {
    module: "rika-frame", version: "0.1.0", id: "app", label: "Frame app", icon: "study", group: "plan", first: false, ui: "iframe",
  };

  const fakeScript = (route) => route.fulfill({
    contentType: "text/javascript",
    body: `customElements.define("rika-fake-view", class extends HTMLElement {
      async connectedCallback() {
        const answer = await this.rika.get("hello");
        this.textContent = this.rika.module + " " + this.rika.version + " says " + answer.message;
      }
    });`,
  });

  test("an element view leads its group, mounts its custom element and reaches only its own API", async () => {
    const { page, problems, context } = await open("today", {
      views: [element, frame],
      routes: async (context) => {
        await context.route("**/modules/rika-fake/0.1.0/ui/view.js", fakeScript);
        await context.route("**/api/modules/rika-fake/hello", (route) => route.fulfill({ json: { message: "hi" } }));
      },
    });
    // Study's header button opens the group's first view: the add-on placed first.
    await page.getByRole("navigation", { name: "Workspace groups" }).getByRole("button", { name: "Study" }).click();
    await page.getByText("rika-fake 0.1.0 says hi").waitFor();
    assert.equal(await page.evaluate(() => location.hash), "#m/rika-fake/fake");
    assert.equal(await page.title(), "Fake study · rika!");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("an element view whose script never loads says so plainly and offers Try again", async () => {
    const { page, context } = await open("m/rika-fake/fake", {
      views: [element],
      routes: (context) => context.route("**/modules/rika-fake/0.1.0/ui/view.js*", (route) => route.fulfill({ status: 404, body: "" })),
    });
    const view = page.getByRole("region", { name: "Fake study", exact: true });
    await view.getByRole("alert").filter({ hasText: "Fake study could not load. Its page script could not be loaded." }).waitFor();
    await view.getByRole("button", { name: "Try again" }).waitFor();
    await context.close();
  });

  test("an iframe view that cannot start shows the reason and Try again", async () => {
    const { page, context } = await open("m/rika-frame/app", {
      views: [frame],
      routes: async (context) => {
        await context.route("**/api/modules/rika-frame", (route) => route.fulfill({ json: { url: "http://127.0.0.1:9/", available: false } }));
        await context.route("**/api/modules/rika-frame/start", (route) =>
          route.fulfill({ status: 503, json: { error: "The add-on's app did not answer in time." } }));
      },
    });
    const view = page.getByRole("region", { name: "Frame app", exact: true });
    await view.getByRole("alert").filter({ hasText: "Frame app could not start: The add-on's app did not answer in time." }).waitFor();
    await view.getByRole("button", { name: "Try again" }).waitFor();
    await context.close();
  });
});
