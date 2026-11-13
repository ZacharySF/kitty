// The first-run welcome card and guided tour (Onboarding.svelte) in the real app against the
// disposable synthetic backend (`kitty-web --demo`). The account and Google sign-in endpoints
// are routed per test, so every Google state renders without a Google client or network.
// Run from web/ with Node >=22, uv and a matching Playwright Chromium:
// PLAYWRIGHT_BROWSERS_PATH=<Nix playwright-driver.browsers store path> \
// PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true node --test tests/onboarding.browser.test.mjs
// ONBOARDING_SHOTS=<dir> also saves a 1440x900 @2x screenshot of every state there.
import { after, before, test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright-core";
import { routeBuild } from "./buildRoutes.mjs";
import { startHarness, startKitty } from "./kittyServer.js";

const GUIDE = "https://github.com/ZacharySF/kitty/blob/main/docs/GOOGLE_ONBOARDING.md";
const SHOTS = process.env.ONBOARDING_SHOTS;
if (SHOTS) mkdirSync(SHOTS, { recursive: true });

let kitty;
let harness;
let browser;
let base;

before(async () => {
  kitty = await startKitty();
  harness = await startHarness(kitty.origin);
  base = harness.resolvedUrls.local[0];
  browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
});
after(async () => {
  await browser?.close();
  await harness?.close();
  await kitty?.stop();
});

/**
 * A first-run profile: the account answers `account()` (mutable through the returned state),
 * Google sign-in is answered by `google` and onboarding saves land in `saved`.
 */
async function open({ configured = false, identity = null, theme, google = {} } = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: SHOTS ? 2 : 1 });
  await kitty.authorize(context);
  const state = { configured, identity, onboarding: { welcome_completed: false, tutorial_completed: false } };
  const saved = [];
  const calls = [];
  await context.route("**/api/account", (route) => route.fulfill({ json: state }));
  await context.route("**/api/account/onboarding", (route) => {
    const body = route.request().postDataJSON();
    saved.push(body);
    Object.assign(state.onboarding, body);
    return route.fulfill({ json: state });
  });
  await context.route("**/api/account/google/*", (route) => {
    const step = new URL(route.request().url()).pathname.split("/").at(-1);
    calls.push(step);
    const answer = google[step]?.(state) ?? { json: { ok: true } };
    return route.fulfill(answer);
  });
  // The 2D look keeps the home behind the card cheap to draw in a software-rendered browser.
  await routeBuild(context, { ...(theme ? { theme: { base: theme } } : {}), ui: { graph: { look: "2d" } } });
  if (SHOTS) context.setDefaultTimeout(180_000);
  const page = await context.newPage();
  const problems = [];
  page.on("pageerror", (error) => problems.push(error.message));
  await page.goto(new URL("#today", base).href);
  const card = page.locator("dialog.onboarding");
  await card.waitFor();
  return { page, card, context, saved, calls, problems, state };
}

async function shot(page, name) {
  if (!SHOTS) return;
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: join(SHOTS, `${name}.png`), timeout: 300_000 });
}

/** The welcome card is portrait: the still of the Brain on top, only words-light controls below. */
async function assertPortrait(page, card) {
  const box = await card.boundingBox();
  assert.ok(box.height > box.width, `the welcome card is ${box.width}x${box.height}, not portrait`);
  const image = card.locator("img.brain");
  await image.waitFor();
  await page.waitForFunction(() => document.querySelector("dialog.onboarding img.brain")?.complete);
  const { natural, top, height } = await image.evaluate((node) => ({ natural: node.naturalWidth, top: node.getBoundingClientRect().top, height: node.getBoundingClientRect().height }));
  assert.ok(natural > 0, "the Brain still did not load");
  assert.ok(top - box.y < 60 && height >= box.height * 0.45, "the Brain does not fill the top half");
  const text = await card.innerText();
  assert.doesNotMatch(text, /Apple/i);
  assert.doesNotMatch(text, /WELCOME TO YOUR WORKSPACE|LOCAL FIRST|Optional means optional|calm workspace/i);
}

test("unconfigured Google: the button waits for setup, Start locally offers the tour, Not now finishes", async () => {
  const { page, card, saved, problems, context } = await open();
  await card.getByRole("heading", { name: "Welcome to rika!" }).waitFor();
  await assertPortrait(page, card);
  assert.ok(await card.getByRole("button", { name: "Continue with Google" }).isDisabled());
  assert.equal(await card.getByRole("link", { name: "setting up" }).getAttribute("href"), GUIDE);
  assert.ok(await card.getByRole("button", { name: /^Theme: / }).isVisible(), "the theme picker is missing");
  await shot(page, "welcome-unconfigured");
  await card.getByRole("button", { name: "Start locally" }).click();
  await card.getByRole("heading", { name: "Take a quick tour?" }).waitFor();
  await assertPortrait(page, card);
  await shot(page, "welcome-tour-offer");
  await card.getByRole("button", { name: "Not now" }).click();
  await card.waitFor({ state: "hidden" });
  assert.deepEqual(saved.at(-1), { welcome_completed: true, tutorial_completed: true });
  assert.deepEqual(problems, []);
  await context.close();
});

test("configured Google: connecting shows the browser hand-off and cancels cleanly", async () => {
  const { page, card, calls, problems, context } = await open({
    configured: true,
    google: {
      start: () => ({ json: { ticket: "t-1", url: "https://accounts.example.test/o/oauth2" } }),
      poll: () => ({ json: { status: "pending" } }),
    },
  });
  const google = card.getByRole("button", { name: "Continue with Google" });
  await google.waitFor();
  assert.ok(await google.isEnabled());
  assert.equal(await card.getByRole("link", { name: "setting up" }).count(), 0);
  await shot(page, "welcome-configured");
  await google.click();
  const link = card.getByRole("link", { name: "Open Google sign-in" });
  await link.waitFor();
  assert.equal(await link.getAttribute("href"), "https://accounts.example.test/o/oauth2");
  assert.equal(await link.getAttribute("target"), "_blank");
  await card.getByRole("status").filter({ hasText: "Finish signing in in your browser." }).waitFor();
  await assertPortrait(page, card);
  await shot(page, "welcome-connecting");
  await card.getByRole("button", { name: "Cancel" }).click();
  await google.waitFor();
  assert.ok(calls.includes("cancel"), `sign-in was not cancelled: ${calls}`);
  assert.deepEqual(problems, []);
  await context.close();
});

test("Google connects: the card names the account and continues to the tour offer", async () => {
  const { page, card, problems, context } = await open({
    configured: true,
    google: {
      start: () => ({ json: { ticket: "t-2", url: "https://accounts.example.test/o/oauth2" } }),
      poll: (state) => {
        state.identity = { sub: "1", email: "ada@example.com", name: "Ada" };
        return { json: { status: "connected" } };
      },
    },
  });
  await card.getByRole("button", { name: "Continue with Google" }).click();
  await card.getByText("Signed in as ada@example.com").waitFor();
  await assertPortrait(page, card);
  await shot(page, "welcome-connected");
  await card.getByRole("button", { name: "Continue", exact: true }).click();
  await card.getByRole("heading", { name: "Take a quick tour?" }).waitFor();
  assert.deepEqual(problems, []);
  await context.close();
});

test("Google errors: a failed start and a failed sign-in both say why and keep local mode", async () => {
  const { page, card, problems, context } = await open({
    configured: true,
    google: {
      start: () => ({ status: 502, json: { error: "Google did not answer. You can start locally." } }),
    },
  });
  await card.getByRole("button", { name: "Continue with Google" }).click();
  await card.getByRole("alert").filter({ hasText: "Google did not answer." }).waitFor();
  assert.ok(await card.getByRole("button", { name: "Start locally" }).isEnabled());
  await assertPortrait(page, card);
  await shot(page, "welcome-error");
  await context.close();

  const second = await open({
    configured: true,
    google: {
      start: () => ({ json: { ticket: "t-3", url: "https://accounts.example.test/o/oauth2" } }),
      poll: () => ({ json: { status: "error", error: "Sign-in was declined." } }),
    },
  });
  await second.card.getByRole("button", { name: "Continue with Google" }).click();
  await second.card.getByRole("alert").filter({ hasText: "Sign-in was declined." }).waitFor();
  await second.card.getByRole("button", { name: "Continue with Google" }).waitFor();
  assert.deepEqual([...problems, ...second.problems], []);
  await second.context.close();
});

test("skip from the welcome card completes onboarding at once", async () => {
  const { card, saved, context } = await open();
  await card.getByRole("button", { name: "Skip onboarding" }).click();
  await card.waitFor({ state: "hidden" });
  assert.deepEqual(saved.at(-1), { welcome_completed: true, tutorial_completed: true });
  await context.close();
});

test("the tour walks every step to the finish card, which closes onboarding", async () => {
  const { page, card, saved, problems, context } = await open();
  await card.getByRole("button", { name: "Start locally" }).click();
  await card.getByRole("button", { name: "Show me around" }).click();
  const tour = card.locator(".tour-card");
  await tour.waitFor();
  assert.deepEqual(saved.at(-1), { welcome_completed: true });
  const progress = tour.getByRole("progressbar", { name: "Tour progress" });
  const total = Number(await progress.getAttribute("aria-valuemax"));
  assert.ok(total >= 5);
  await tour.getByRole("heading", { name: "Tabs and panels" }).waitFor();
  assert.ok(await tour.getByRole("button", { name: "Back" }).isDisabled());
  await page.waitForTimeout(300);
  await shot(page, "tour-step-1");
  for (let index = 1; index < total; index += 1) {
    await tour.getByRole("button", { name: "Next" }).click();
    await page.waitForFunction((value) => document.querySelector('.tour-card [role="progressbar"]')?.getAttribute("aria-valuenow") === String(value), index + 1);
    if (index === 2) { await page.waitForTimeout(300); await shot(page, "tour-step-3"); }
  }
  assert.doesNotMatch(await tour.innerText(), /FIELD GUIDE|MODULE OFF|·/);
  await tour.getByRole("button", { name: "Done" }).click();
  await card.getByRole("heading", { name: "You're set" }).waitFor();
  await card.getByText("The tour is in Settings, Account.").waitFor();
  await assertPortrait(page, card);
  await shot(page, "finish");
  await card.getByRole("button", { name: "Done" }).click();
  await card.waitFor({ state: "hidden" });
  assert.deepEqual(saved.at(-1), { welcome_completed: true, tutorial_completed: true });
  assert.deepEqual(problems, []);
  await context.close();
});

test("a light theme shows the light still of the Brain", async () => {
  const { page, card, context } = await open({ theme: "speed" });
  await card.getByRole("heading", { name: "Welcome to rika!" }).waitFor();
  await page.waitForFunction(() => document.querySelector("dialog.onboarding img.brain")?.getAttribute("src") === "/art/brain-light.webp");
  await assertPortrait(page, card);
  await shot(page, "welcome-unconfigured-light");
  await card.getByRole("button", { name: "Start locally" }).click();
  await card.getByRole("button", { name: "Show me around" }).click();
  await card.locator(".tour-card").waitFor();
  await page.waitForTimeout(300);
  await shot(page, "tour-step-1-light");
  await context.close();
});
