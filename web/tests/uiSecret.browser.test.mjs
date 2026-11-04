// The per-launch UI secret (src/kitty/web_auth.py) in real browsers.
// 1. Why a header and not a cookie: a cookie set by one 127.0.0.1 port is sent to every other
//    port on 127.0.0.1 (cookies ignore ports), in Chromium and in WebKit (the engine family of
//    the desktop's WebKitGTK), to a subframe as to a top-level page. So an HttpOnly,
//    SameSite=Strict cookie would reach the LearnHarness frame's server and any other local
//    server the browser visits; a header the page adds reaches only rika!.
// 2. Browser mode against a real `kitty-web --demo` (tests/kittyServer.js): the link's one-time
//    code becomes the tab's secret, every request and the live socket carry it, downloads use
//    single-use links, and a tab without the code (or with a spent one) is refused.
// Run with Node >=22 and the matching Playwright browsers:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/uiSecret.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { chromium, webkit } from "playwright-core";
import { startHarness, startKitty } from "./kittyServer.js";

const REFUSAL = "rika! did not recognise this window";
let kitty;
let vite;
let browser;
let base;

before(async () => {
  kitty = await startKitty();
  vite = await startHarness(kitty.origin);
  base = vite.resolvedUrls.local[0];
  browser = await chromium.launch();
});
after(async () => {
  await browser?.close();
  await vite?.close();
  await kitty?.stop();
});

function listen(handler) {
  const server = createServer(handler);
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

for (const [name, engine] of [["Chromium", chromium], ["WebKit", webkit]]) {
  test(`${name}: a 127.0.0.1 cookie reaches every other port, so the UI secret is not a cookie`, async () => {
    const received = [];
    const kittyLike = await listen((request, response) => {
      response.setHeader("Set-Cookie", "kitty_ui=secret-value; HttpOnly; SameSite=Strict; Path=/");
      response.setHeader("Content-Type", "text/html");
      response.end("<!doctype html><title>rika!</title>");
    });
    const otherLocal = await listen((request, response) => {
      received.push({ path: request.url, cookie: request.headers.cookie ?? "" });
      response.setHeader("Content-Type", "text/html");
      response.end("<!doctype html><title>another local server</title>");
    });
    const other = `http://127.0.0.1:${otherLocal.address().port}`;
    const engineBrowser = await engine.launch();
    try {
      const page = await engineBrowser.newPage();
      await page.goto(`http://127.0.0.1:${kittyLike.address().port}/`);
      // A subframe on another port, as the LearnHarness frame on 127.0.0.1:3210 is.
      await page.evaluate((url) => new Promise((resolve) => {
        const frame = document.createElement("iframe");
        frame.onload = resolve;
        frame.src = url;
        document.body.append(frame);
      }), `${other}/frame`);
      // And a page on another port opened directly (typed, or launched by a program).
      await page.goto(`${other}/top`);
      assert.deepEqual(received.map((item) => [item.path, item.cookie]),
        [["/frame", "kitty_ui=secret-value"], ["/top", "kitty_ui=secret-value"]]);
    } finally {
      await engineBrowser.close();
      kittyLike.close();
      otherLocal.close();
    }
  });
}

test("browser mode: the link's code becomes this tab's secret; the API, live socket and downloads carry it", async () => {
  const code = await kitty.nextCode();
  const context = await browser.newContext({ viewport: { width: 1280, height: 850 }, acceptDownloads: true });
  // A returning user: the first-run welcome guide is covered by its own tests.
  await context.route("**/api/account", (route) =>
    route.request().method() === "GET"
      ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } })
      : route.continue());
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  const unsigned = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    const signed = request.headers()["x-kitty-secret"] || url.searchParams.has("ticket");
    if (url.pathname.startsWith("/api/") && url.pathname !== "/api/session" && !signed) unsigned.push(url.pathname);
  });
  const live = new Promise((resolve) => page.on("websocket", (socket) => {
    socket.on("framereceived", (frame) => {
      if (String(frame.payload).includes('"type": "hello"') || String(frame.payload).includes('"type":"hello"')) resolve(socket.url());
    });
  }));
  await page.goto(`${base}?code=${code}#settings/build`);
  // The code leaves the address bar at once; the route stays.
  await page.waitForFunction(() => !location.search.includes("code"));
  assert.equal(new URL(page.url()).hash, "#settings/build");
  assert.ok((await live).includes("/api/live/socket"), "the live channel opened with the secret");
  const secret = await page.evaluate(() => sessionStorage.getItem("kitty.ui-secret"));
  assert.equal(secret, kitty.secret);
  // A download link follows a single-use ticket.
  const link = page.getByRole("link", { name: "Export build" });
  await link.waitFor();
  const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
  const exported = JSON.parse(await readFile(await download.path(), "utf8"));
  assert.equal(exported.schema, 1);
  // A reload in the same tab keeps working; a write goes through.
  await page.reload();
  await page.getByRole("link", { name: "Export build" }).waitFor();
  const note = await page.evaluate(async () => (await import("/src/lib/api.ts")).api("/quick-notes/create", { body: "from the browser tab" }));
  assert.equal(note.body, "from the browser tab");
  assert.deepEqual(unsigned, [], "every API request carried the secret or a ticket");
  assert.deepEqual(problems, []);
  await context.close();
});

test("browser mode: a tab without the code, or with a spent one, is refused", async () => {
  const code = await kitty.nextCode();
  const first = await browser.newContext();
  const used = await first.newPage();
  await used.goto(`${base}?code=${code}`);
  await used.waitForFunction(() => sessionStorage.getItem("kitty.ui-secret") !== null);
  await first.close();
  for (const address of [`${base}?code=${code}`, base]) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const bootstrap = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/bootstrap");
    await page.goto(address);
    assert.equal((await bootstrap).status(), 401, address);
    await page.getByText(REFUSAL).first().waitFor();
    assert.equal(await page.evaluate(() => sessionStorage.getItem("kitty.ui-secret")), null);
    await context.close();
  }
});
