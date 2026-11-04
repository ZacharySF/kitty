// Settings → Devices (platform design Q16) in a real browser against a stand-in /api/devices
// (tests/devicesHarness.js); no sync listener is involved.
// Run with Node >=22 and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/devices.browser.test.mjs
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("..", import.meta.url));
let server;
let browser;
let base;

before(async () => {
  server = await createServer({ root, logLevel: "error", server: { host: "127.0.0.1", port: 0 } });
  await server.listen();
  base = server.resolvedUrls.local[0];
  browser = await chromium.launch();
});
after(async () => {
  await browser?.close();
  await server?.close();
});

async function harness() {
  const context = await browser.newContext({ viewport: { width: 1000, height: 900 } });
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("tests/devices-harness.html", base).href, { timeout: 90000 });
  await page.getByText("Synthetic desk", { exact: true }).waitFor({ timeout: 60000 });
  return { page, context, problems };
}
const posts = (page) => page.evaluate(() => window.devicesHarness.posts());
const row = (page, name) => page.locator("li.row").filter({ hasText: name });

test("this device and a paired device are described, renamed and paused", async () => {
  const { page, context, problems } = await harness();
  const panel = page.getByRole("region", { name: "Devices" });
  await panel.getByText("3f2a 9c01 77be 0d4e").waitFor();
  await panel.getByText("Listening at 192.0.2.10:47800").waitFor();
  const phone = row(page, "Synthetic phone");
  await phone.getByText("Synced 5 minutes ago").waitFor();
  await phone.getByText("3 changes from this device not received yet").waitFor();
  await phone.getByText("Unsynced changes are keeping history from being compacted.").waitFor();
  await phone.getByText("Found at 192.0.2.13:47800").waitFor();

  await page.getByRole("button", { name: "Rename this device" }).click();
  await page.getByRole("textbox", { name: "Device name" }).fill("  Synthetic workstation ");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await panel.getByText("Synthetic workstation", { exact: true }).waitFor();

  await page.getByRole("button", { name: "Pause sync" }).click();
  await page.getByRole("button", { name: "Resume sync" }).waitFor();
  await phone.getByText("Paused", { exact: true }).waitFor();
  await panel.getByText("Sync is paused on this device.").waitFor();
  assert.equal(await page.getByRole("button", { name: "Sync all now" }).isDisabled(), true);
  await page.getByRole("button", { name: "Resume sync" }).click();
  await page.getByRole("button", { name: "Pause sync" }).waitFor();

  assert.deepEqual(await posts(page), [
    ["/api/devices/name", { name: "Synthetic workstation" }],
    ["/api/devices/pause", { paused: true }],
    ["/api/devices/pause", { paused: false }],
  ]);
  const headers = await page.evaluate(() => window.devicesHarness.calls.find((call) => call.method === "POST").headers);
  assert.equal(headers["X-Kitty-Request"], "1");
  assert.deepEqual(problems, []);
  await context.close();
});

test("showing a code counts down and reports the device that paired", async () => {
  const { page, context, problems } = await harness();
  await page.getByRole("button", { name: "Show a code", exact: true }).click();
  const code = page.getByLabel("Pairing code QRS-TUV");
  await code.waitFor();
  assert.equal(await code.textContent(), "QRS-TUV");
  await page.getByText(/Expires in [45]:\d\d · 3 tries left/).waitFor();
  await page.evaluate(() => { window.devicesHarness.state.pairing.paired = { device_id: "dev-e", name: "Synthetic netbook" }; });
  await page.getByText("Paired with Synthetic netbook.").waitFor();
  await page.getByRole("button", { name: "Done" }).click();
  await page.getByRole("button", { name: "Show a code", exact: true }).waitFor();

  await page.getByRole("button", { name: "Show a code", exact: true }).click();
  await page.getByRole("button", { name: "Cancel pairing code" }).click();
  await code.waitFor({ state: "detached" });
  assert.deepEqual((await posts(page)).map(([url]) => url),
    ["/api/devices/pair/show", "/api/devices/pair/cancel", "/api/devices/pair/show", "/api/devices/pair/cancel"]);
  assert.deepEqual(problems, []);
  await context.close();
});

test("entering a code pairs with a nearby device and shows a refusal", async () => {
  const { page, context, problems } = await harness();
  const nearby = page.getByRole("list", { name: "Devices showing a code" });
  await nearby.getByRole("button", { name: "Pair with Synthetic laptop" }).waitFor();
  assert.equal(await nearby.getByRole("button").count(), 1);
  const pair = page.getByRole("button", { name: "Pair", exact: true });
  assert.equal(await pair.isDisabled(), true);

  await page.getByRole("textbox", { name: "Device address" }).fill("laptop.tailnet.ts.net");
  await page.getByRole("textbox", { name: "Code shown on the other device" }).pressSequentially("qrs tu");
  assert.equal(await page.getByRole("textbox", { name: "Code shown on the other device" }).inputValue(), "QRS-TU");
  assert.equal(await pair.isDisabled(), true);
  await page.getByRole("textbox", { name: "Code shown on the other device" }).press("v");
  await page.evaluate(() => { window.devicesHarness.errors["/api/devices/pair/enter"] = [400, "Wrong code or the code expired"]; });
  await pair.click();
  await page.getByRole("alert").filter({ hasText: "Wrong code or the code expired" }).waitFor();

  await nearby.getByRole("button", { name: "Pair with Synthetic laptop" }).click();
  assert.equal(await page.getByRole("textbox", { name: "Device address" }).inputValue(), "192.0.2.11:47800");
  await pair.click();
  await page.getByText("Paired with Synthetic laptop.").waitFor();
  await row(page, "Synthetic laptop").getByText("Syncing…").waitFor();
  assert.equal(await page.getByRole("textbox", { name: "Code shown on the other device" }).inputValue(), "");
  assert.deepEqual(await posts(page), [
    ["/api/devices/pair/enter", { code: "QRS-TUV", address: "laptop.tailnet.ts.net" }],
    ["/api/devices/pair/enter", { code: "QRS-TUV", address: "192.0.2.11:47800" }],
  ]);
  assert.deepEqual(problems, []);
  await context.close();
});

test("a paired device syncs, takes an address and unpairs after confirmation", async () => {
  const { page, context, problems } = await harness();
  const phone = row(page, "Synthetic phone");
  await page.getByRole("button", { name: "Sync Synthetic phone now" }).click();
  await phone.getByText("Syncing…").waitFor();
  await page.getByRole("button", { name: "Sync all now" }).click();

  await page.getByRole("button", { name: "Edit address for Synthetic phone" }).click();
  await page.getByRole("textbox", { name: "Address for Synthetic phone" }).fill(" phone.tailnet.ts.net ");
  await phone.getByRole("button", { name: "Save" }).click();
  await phone.getByText("Address phone.tailnet.ts.net (set by you)").waitFor();
  await page.getByRole("button", { name: "Edit address for Synthetic phone" }).click();
  await phone.getByRole("button", { name: "Find automatically" }).click();
  await phone.getByText("Found at 192.0.2.13:47800").waitFor();

  await page.evaluate(() => window.devicesHarness.answerConfirm(false));
  await page.getByRole("button", { name: "Unpair Synthetic phone" }).click();
  await page.evaluate(() => window.devicesHarness.answerConfirm(true));
  await page.getByRole("button", { name: "Unpair Synthetic phone" }).click();
  await page.getByText("No paired devices yet.").waitFor();
  assert.match((await page.evaluate(() => window.devicesHarness.confirms))[0], /^Unpair Synthetic phone\?/);
  assert.deepEqual(await posts(page), [
    ["/api/devices/sync", { device_id: "dev-b" }],
    ["/api/devices/sync", {}],
    ["/api/devices/address", { device_id: "dev-b", address: "phone.tailnet.ts.net" }],
    ["/api/devices/address", { device_id: "dev-b", address: null }],
    ["/api/devices/unpair", { device_id: "dev-b" }],
  ]);
  assert.deepEqual(problems, []);
  await context.close();
});
