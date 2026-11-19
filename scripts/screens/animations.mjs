// Animations v2: frames taken while things move (a film strip per move), a video, and checks.
import { chromium } from "../../web/node_modules/playwright-core/index.mjs";
import { mkdirSync, readdirSync, renameSync, writeFileSync, rmSync } from "node:fs";
const out = process.argv[3] ?? "/tmp/design-labs/animations";
rmSync(out, { recursive: true, force: true }); mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, recordVideo: { dir: out, size: { width: 1440, height: 900 } } });
const page = await context.newPage();
const errors = []; page.on("pageerror", (e) => errors.push(e.message));
const go = async (hash, wait = 1500) => { await page.evaluate((h) => { location.hash = h; }, hash); await page.waitForTimeout(wait); };
const results = {}; const strips = [];
await page.goto(process.argv[2], { waitUntil: "load" });
await page.waitForTimeout(4500);
const skip = page.getByRole("button", { name: /Skip/ });
if (await skip.count()) { await skip.first().click(); await page.waitForTimeout(2500); }
const offer = page.getByRole("region", { name: "Home template" }).getByRole("button", { name: "Not now" });
if (await offer.count()) await offer.click();
await go("settings/modules", 2200);
await page.getByRole("switch", { name: "Animations" }).click();
await page.waitForTimeout(1500);
results.anim = await page.evaluate(() => document.documentElement.dataset.anim);
await go("today", 2200);

// A strip: do `act`, then take `count` frames as fast as the browser gives them.
let n = 0;
const strip = async (title, act, count = 8, gap = 130) => {
  n += 1; const frames = [];
  await act();
  for (let i = 0; i < count; i += 1) {
    const file = `${String(n).padStart(2, "0")}-${String(i + 1)}.jpg`;
    await page.screenshot({ path: `${out}/${file}`, type: "jpeg", quality: 60 });
    frames.push(file);
    await page.waitForTimeout(gap);
  }
  strips.push({ title, frames });
  await page.waitForTimeout(3800);
};
const bar = page.locator(".workbench-top");
// First a lap at real speed for the video, then the same moves slowed to a seventh for the strips.
for (const name of ["Plan", "Job search", "Study", "Rika"]) { await bar.getByRole("button", { name, exact: true }).click(); await page.waitForTimeout(1500); }
await page.locator("a.wordmark").click(); await page.waitForTimeout(1600);
await go("jobs", 1700);
for (const name of ["Saved", "Apply", "Track", "Find"]) { await page.locator(".jobs-nav").getByRole("button", { name, exact: true }).click(); await page.waitForTimeout(1300); }
await page.keyboard.press("Control+Space"); await page.waitForTimeout(1100); await page.keyboard.press("Escape"); await page.waitForTimeout(500);
await page.locator("a.wordmark").click(); await page.waitForTimeout(1600);
{ const b = await page.locator(".board .slot").first().boundingBox(); await page.mouse.move(b.x + 20, b.y + 20); await page.mouse.move(b.x + b.width - 30, b.y + b.height - 20, { steps: 25 }); await page.mouse.move(b.x + 60, b.y + 30, { steps: 25 }); }
await page.waitForTimeout(600);
const cdp = await context.newCDPSession(page);
await cdp.send("Animation.enable");
await cdp.send("Animation.setPlaybackRate", { playbackRate: 0.14 });
await strip("Home → Plan: the page slides in from the right, the old one leaves left", () => bar.getByRole("button", { name: "Plan", exact: true }).click());
await strip("Plan → Job search: the marker slides along the bar", () => bar.getByRole("button", { name: "Job search", exact: true }).click());
await strip("Job search → Study: back the other way", () => bar.getByRole("button", { name: "Study", exact: true }).click());
await strip("Going home: the page steps back", () => page.locator("a.wordmark").click());
await strip("Opening from a widget: the page grows out of where you pressed", async () => { await page.locator(".board .slot").nth(1).locator("a, button").first().click({ force: true }).catch(() => {}); });
await go("jobs", 1800);
const steps = page.locator(".jobs-nav");
await strip("Find → Saved: a sideways step, and the step marker slides", () => steps.getByRole("button", { name: "Saved", exact: true }).click());
await strip("Saved → Apply", () => steps.getByRole("button", { name: "Apply", exact: true }).click());
await strip("Ticking a row: the bar springs up", async () => { await page.locator(".row-check").first().check(); });
await strip("Search opens with a spring", () => page.keyboard.press("Control+Space"));
await page.keyboard.press("Escape"); await page.waitForTimeout(500);
await go("today", 1800);
await strip("A widget leans toward the pointer and carries its light", async () => { const b = await page.locator(".board .slot").first().boundingBox(); await page.mouse.move(b.x + 20, b.y + 20); await page.mouse.move(b.x + b.width - 30, b.y + b.height - 20, { steps: 6 }); }, 4, 500);
await strip("A press leaves a ring", async () => { const b = await page.locator(".board .slot").nth(2).boundingBox(); await page.mouse.move(b.x + 120, b.y + 40); await page.mouse.down(); }, 5, 300);
await page.mouse.up();

// Checks.
await cdp.send("Animation.setPlaybackRate", { playbackRate: 1 });
await page.waitForTimeout(1500);
results.ghostsLeft = await page.locator(".anim-ghost, .anim-pill, .anim-ring").count();
await page.waitForTimeout(1200);
results.ghostsLeftAfter = await page.locator(".anim-ghost, .anim-pill, .anim-ring").count();
await page.emulateMedia({ reducedMotion: "reduce" });
await go("calendar", 250);
results.reduced = await page.evaluate(() => ({ running: document.getAnimations().length, ghosts: document.querySelectorAll(".anim-ghost, .anim-pill, .anim-ring").length }));
await page.emulateMedia({ reducedMotion: "no-preference" });
await go("settings/modules", 2000);
await page.getByRole("switch", { name: "Animations" }).click(); await page.waitForTimeout(800);
await go("jobs", 300);
results.off = await page.evaluate(() => ({ anim: document.documentElement.dataset.anim, running: document.getAnimations().length, ghosts: document.querySelectorAll(".anim-ghost").length }));
results.errors = errors;
await context.close(); await browser.close();
const video = readdirSync(out).find((name) => name.endsWith(".webm"));
if (video) renameSync(`${out}/${video}`, `${out}/animations.webm`);
writeFileSync(`${out}/index.html`, `<!doctype html><meta charset="utf-8"><title>rika! — Animations</title>
<body style="margin:0;background:#000;color:#eee;font:18px system-ui;padding:32px">
<h1 style="font-weight:600">Animations</h1>
<p style="color:#999">Settings → Modules → Animations. The video starts with a lap at real speed (then the same moves slowed down, which is how the frames under it were taken).</p>
<video src="animations.webm" controls autoplay loop muted style="width:100%;max-width:1440px;border:1px solid #222"></video>
${strips.map((s) => `<h2 style="font-weight:600;margin:44px 0 12px">${s.title}</h2><div style="display:grid;grid-template-columns:repeat(${Math.min(4, s.frames.length)},1fr);gap:6px">${s.frames.map((f) => `<a href="${f}"><img src="${f}" style="width:100%;display:block;border:1px solid #222"></a>`).join("")}</div>`).join("")}`);
console.log(JSON.stringify(results, null, 1));
