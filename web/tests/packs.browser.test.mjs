// Module packs end to end (docs/rust-core-design.md, milestone 3): the real built website served
// by rika-server without the Brain's files, as a base install ships it; a loopback stand-in for
// the GitHub release with the pack built by scripts/build-packs.py and its index signed by a
// throwaway key; Settings → Modules downloads it with progress, the Brain turns on, and after a
// restart the home draws it from the pack. Synthetic workspace only.
// Run from web/ after `npm run build`, with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/packs.browser.test.mjs
// KITTY_SHOTS_DIR=<folder> also saves screenshots of Settings → Modules there.
import { after, before, describe, test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash, generateKeyPairSync, randomBytes, sign } from "node:crypto";
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { startKitty } from "./kittyServer.js";

const web = fileURLToPath(new URL("..", import.meta.url));
const repo = fileURLToPath(new URL("../..", import.meta.url));
const shots = process.env.KITTY_SHOTS_DIR;
const VIEWPORT = { width: 1280, height: 850 };

/** A throwaway minisign key, in tauri.conf.json's form, and `tauri signer sign`'s signature. */
function minisignKey() {
  const { publicKey, privateKey } = generateKeyPairSync("ed25519");
  const raw = Buffer.from(publicKey.export({ format: "jwk" }).x, "base64url");
  const id = randomBytes(8);
  const text = `untrusted comment: minisign public key\n${Buffer.concat([Buffer.from("Ed"), id, raw]).toString("base64")}\n`;
  return {
    public: Buffer.from(text).toString("base64"),
    sign(data) {
      const signature = sign(null, createHash("blake2b512").update(data).digest(), privateKey);
      const comment = "timestamp:0\tfile:packs.json";
      const overall = sign(null, Buffer.concat([signature, Buffer.from(comment)]), privateKey);
      return Buffer.from(`untrusted comment: signature from tauri secret key\n${Buffer.concat([Buffer.from("ED"), id, signature]).toString("base64")}\n`
        + `trusted comment: ${comment}\n${overall.toString("base64")}\n`).toString("base64");
    },
  };
}

let work;
let dist;
let listed;
let index;
let release;
let releaseUrl;
/** Resolved to let the held download finish. */
let letGo;
const held = new Promise((resolve) => { letGo = resolve; });
let key;
let root;
let kitty;
let browser;

before(async () => {
  if (!existsSync(join(web, "dist/pack-files.json"))) throw new Error("Build the website first: npm run build");
  work = mkdtempSync(join(tmpdir(), "kitty-packs-"));
  dist = join(work, "dist");
  cpSync(join(web, "dist"), dist, { recursive: true });
  listed = JSON.parse(readFileSync(join(dist, "pack-files.json"), "utf8")).brain;
  const out = join(work, "release");
  // The release's own build step: the Brain leaves this base website and becomes the pack.
  execFileSync("uv", ["run", "--project", repo, "python", join(repo, "scripts/build-packs.py"), "--dist", dist, "--out", out], { stdio: "pipe" });
  index = JSON.parse(readFileSync(join(out, "packs.json"), "utf8"));
  key = minisignKey();
  writeFileSync(join(out, "packs.json.sig"), key.sign(readFileSync(join(out, "packs.json"))));
  // Serves the release; the archive's second half waits for the test, so progress is visible.
  release = createServer((request, response) => {
    const name = request.url.slice(1);
    const path = join(out, name);
    if (name.includes("/") || !existsSync(path)) { response.writeHead(404).end(); return; }
    const data = readFileSync(path);
    response.writeHead(200, { "Content-Length": data.length });
    if (!name.endsWith(".tar.gz")) { response.end(data); return; }
    response.write(data.subarray(0, data.length >> 1));
    void held.then(() => response.end(data.subarray(data.length >> 1)));
  });
  await new Promise((resolve) => release.listen(0, "127.0.0.1", resolve));
  releaseUrl = `http://127.0.0.1:${release.address().port}/`;
  root = join(work, "root");
  mkdirSync(join(root, "config"), { recursive: true });
  writeFileSync(join(root, "config/build.json"), JSON.stringify({ schema: 1, modules: ["workspace"] }));
  kitty = await start();
  browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
  if (shots) mkdirSync(shots, { recursive: true });
});

after(async () => {
  letGo();
  await browser?.close();
  await kitty?.stop();
  await new Promise((resolve) => release ? release.close(resolve) : resolve());
  if (work) rmSync(work, { recursive: true, force: true });
});

function start() {
  return startKitty({
    root,
    args: ["--static-dir", dist],
    env: { XDG_DATA_HOME: join(work, "xdg"), KITTY_PACKS_URL: releaseUrl, KITTY_PACKS_KEY: key.public },
  });
}

/** rika! as the desktop window opens it: rika-server's own origin, the secret handed in. */
async function open(hash = "") {
  const context = await browser.newContext({ viewport: VIEWPORT });
  await kitty.authorize(context);
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue(),
  );
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(`${kitty.origin}/${hash}`);
  return { page, context, problems };
}

const modulesRegion = (page) => page.getByRole("region", { name: "Modules", exact: true });
const brainRow = (page) => modulesRegion(page).locator('[data-module="brain"]');
const asset = (page, name) => page.evaluate(async (path) => (await fetch(path)).status, `/${name}`);
const megabytes = (bytes) => `${Math.max(0.1, bytes / 1e6).toFixed(1)} MB`;

async function shot(page, name) {
  if (shots) await modulesRegion(page).screenshot({ path: join(shots, `${name}.png`) });
}

describe("module packs", () => {
  test("a base install has no Brain files; home shows no Brain and Settings → Modules offers it", async () => {
    const { page, context, problems } = await open();
    await page.locator(".home").getByRole("region", { name: "Rika" }).waitFor();
    assert.equal(await page.getByRole("heading", { name: "Brain isn't installed" }).count(), 0, "home offers the Brain's download");
    assert.equal(await page.getByRole("navigation", { name: "Workspace groups" }).getByRole("button", { name: "Brain" }).count(), 0, "the Brain is in the navigation");
    if (shots) await page.screenshot({ path: join(shots, "home-brain-not-installed.png") });
    for (const name of listed) assert.equal(await asset(page, name), 404, `${name} is still served`);
    await page.goto(`${kitty.origin}/#settings/modules`);
    await brainRow(page).getByText(`Download · ${megabytes(index.packs[0].size)}`).waitFor();
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("Settings → Modules downloads the Brain with progress, installs it and lets it be turned on", async () => {
    const { page, context, problems } = await open("#settings/modules");
    const row = brainRow(page);
    const size = megabytes(index.packs[0].size);
    await row.getByText(`Download · ${size}`).waitFor();
    const toggle = row.getByRole("switch", { name: "Brain" });
    assert.equal(await toggle.isDisabled(), true, "Brain turns on only once its pack is here");
    assert.equal(await toggle.getAttribute("title"), "Download Brain first");
    await shot(page, "modules-available");

    await row.getByRole("button", { name: "Download Brain" }).click();
    const bar = row.getByRole("progressbar", { name: "Brain download" });
    await bar.waitFor();
    await page.waitForFunction(() => Number(document.querySelector('[data-module="brain"] [role="progressbar"]')?.getAttribute("aria-valuenow")) > 0);
    const done = Number(await bar.getAttribute("aria-valuenow"));
    assert.ok(done > 0 && done < 100, `progress ${done}%`);
    assert.match(await row.innerText(), new RegExp(`${done}%`));
    await shot(page, "modules-downloading");

    letGo();
    await row.getByText(`Installed · ${megabytes(index.packs[0].installed_size)}`).waitFor();
    const remove = row.getByRole("button", { name: "Remove Brain download" });
    assert.equal(await remove.isDisabled(), false);
    assert.equal(await toggle.isDisabled(), false);
    for (const name of listed) assert.equal(await asset(page, name), 200, `${name} is not served from the pack`);
    await shot(page, "modules-installed");

    await toggle.click();
    await modulesRegion(page).getByRole("button", { name: "Save modules" }).click();
    await row.getByText("Restart").waitFor();
    assert.equal(await remove.isDisabled(), true, "an enabled module's pack stays");
    assert.equal(await remove.getAttribute("title"), "Turn Brain off and save before removing it");
    assert.deepEqual((await kitty.request("/modules")).configured, ["workspace", "brain"]);
    await shot(page, "modules-brain-on");
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("after a restart the Brain is its own place, drawn from the pack", async () => {
    await kitty.stop();
    kitty = await start();
    const { page, context, problems } = await open();
    await page.getByRole("navigation", { name: "Workspace groups" }).getByRole("button", { name: "Brain" }).click();
    await page.waitForFunction(() => location.hash === "#brain");
    // The Brain (WebGL in software here) or, without WebGL, the 2D map: never the card.
    await page.locator(".atlas .stage canvas >> visible=true").first().waitFor({ timeout: 30_000 });
    assert.equal(await page.getByRole("heading", { name: "Brain isn't installed" }).count(), 0);
    const stage = await page.locator(".atlas .stage").getAttribute("aria-label");
    assert.ok(["Brain of every record and edit in your workspace", "2D atlas of your workspace"].includes(stage), stage);
    if (stage.startsWith("Brain")) await page.locator("[data-brain-stage] canvas").first().waitFor();
    if (shots) await page.screenshot({ path: join(shots, "brain-from-pack.png") });
    assert.deepEqual(problems, []);
    await context.close();
  });

  test("with the Brain off its download can be removed", async () => {
    await kitty.request("/modules", { enabled: ["workspace"] });
    const { page, context, problems } = await open("#settings/modules");
    const row = brainRow(page);
    await row.getByRole("button", { name: "Remove Brain download" }).click();
    await row.getByText(`Download · ${megabytes(index.packs[0].size)}`).waitFor();
    for (const name of listed) assert.equal(await asset(page, name), 404);
    const packs = await kitty.request("/packs");
    assert.equal(packs.packs.find((pack) => pack.id === "brain").state, "available");
    assert.deepEqual(problems, []);
    await context.close();
  });
});
