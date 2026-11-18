// rika-ui-polish screenshot + check pass.
// Usage: node .claude/skills/rika-ui-polish/scripts/shots.mjs "<one-use demo link>" [hash-route ...]
// Routes are location.hash values such as today, notes, calendar, jobs, rika, documents.
// Writes PNGs, report.json and shots.json (for scripts/screens/gallery.py) to /tmp/rika-ui-polish/<stamp>/.
// Exit code 0 = every hard check passed, 2 = at least one failed, 1 = could not run.
import { chromium } from "../../../../web/node_modules/playwright-core/index.mjs";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const [link, ...args] = process.argv.slice(2);
if (!link) {
  console.error('usage: shots.mjs "<demo link>" [hash-route ...]');
  process.exit(1);
}
const routes = args.length ? args : ["today"];
const out = `/tmp/rika-ui-polish/${new Date().toISOString().replace(/[:.]/g, "-")}`;
mkdirSync(out, { recursive: true });

const SIZES = [
  { name: "desktop", width: 1568, height: 942 },
  { name: "phone", width: 390, height: 844 },
];
const MOTION = ["no-preference", "reduce"];
const axePath = `${root}web/node_modules/axe-core/axe.min.js`;
const axeSource = existsSync(axePath) ? readFileSync(axePath, "utf8") : null;

const browser = await chromium.launch({ args: ["--enable-unsafe-swiftshader", "--use-angle=swiftshader"] });
const page = await browser.newPage({ viewport: SIZES[0], deviceScaleFactor: 1 });
const report = { out, routes: [], pageErrors: [], axe: axeSource ? "on" : "off: add axe-core to web devDependencies" };
page.on("pageerror", (e) => report.pageErrors.push(e.message));
page.on("console", (m) => { if (m.type() === "error") report.pageErrors.push(m.text()); });
const shots = [];
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

await page.goto(link, { waitUntil: "load" });
await page.waitForTimeout(4000);
const skip = page.getByRole("button", { name: /Skip/ });
if (await skip.count()) { await skip.first().click(); await page.waitForTimeout(1500); }

const facts = () => page.evaluate(() => {
  const doc = document.documentElement;
  const visible = (el) => el.offsetParent !== null || getComputedStyle(el).position === "fixed";
  const running = document.getAnimations().filter((a) => {
    const d = a.effect?.getComputedTiming().duration;
    return a.playState === "running" && typeof d === "number" && d > 0;
  }).length;
  const wordy = [...document.querySelectorAll("p, small, .muted, .hint, .help")]
    .filter((el) => visible(el) && !el.closest("[contenteditable], article, .editor"))
    .map((el) => el.innerText.trim())
    .filter((t) => t.split(/\s+/).length > 12);
  const tiny = [...document.querySelectorAll("button, a[href], input, select, textarea, [role=button], [tabindex]:not([tabindex='-1'])")]
    .filter((el) => {
      if (!visible(el)) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && (r.width < 24 || r.height < 24);
    })
    .map((el) => (el.getAttribute("aria-label") || el.innerText || el.tagName).trim().slice(0, 50));
  return {
    overflowX: doc.scrollWidth - window.innerWidth,
    runningAnimations: running,
    motionMs: getComputedStyle(doc).getPropertyValue("--motion-ms").trim(),
    wordyText: [...new Set(wordy)].slice(0, 12).map((t) => t.slice(0, 100)),
    smallTargets: [...new Set(tiny)].slice(0, 12),
  };
});

async function focusPass(prefix) {
  const stops = [];
  await page.evaluate(() => { document.activeElement?.blur?.(); window.scrollTo(0, 0); });
  for (let i = 0; i < 40; i += 1) {
    await page.keyboard.press("Tab");
    const info = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return null;
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const ring = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== "none";
      const name = (el.getAttribute("aria-label") || el.innerText || el.getAttribute("title") || el.tagName).trim();
      return { name: name.slice(0, 60), tag: el.tagName.toLowerCase(), ring, x: r.x, y: r.y, w: r.width, h: r.height };
    });
    if (!info) break;
    if (stops.length && info.name === stops[0].name && info.x === stops[0].x && info.y === stops[0].y) break;
    if (i < 15 && info.w > 0 && info.h > 0) {
      const file = `${prefix}-focus-${String(i + 1).padStart(2, "0")}.png`;
      const vp = page.viewportSize();
      const x = Math.max(0, info.x - 12), y = Math.max(0, info.y - 12);
      const width = Math.min(vp.width - x, info.w + 24), height = Math.min(vp.height - y, info.h + 24);
      if (width > 0 && height > 0) {
        await page.screenshot({ path: `${out}/${file}`, clip: { x, y, width, height } });
        info.file = file;
      }
    }
    stops.push(info);
  }
  return stops;
}

for (const route of routes) {
  const entry = { route, shots: [], focus: [], axe: null };
  for (const size of SIZES) {
    await page.setViewportSize({ width: size.width, height: size.height });
    for (const motion of MOTION) {
      await page.emulateMedia({ reducedMotion: motion });
      await page.evaluate((h) => { location.hash = h; }, route);
      await page.waitForTimeout(1800);
      const file = `${slug(route)}-${size.name}-${size.width}x${size.height}-${motion}.png`;
      await page.screenshot({ path: `${out}/${file}` });
      shots.push({ file, title: `${route} · ${size.width}×${size.height} · motion ${motion}`, section: route });
      entry.shots.push({ file, size: size.name, motion, ...(await facts()) });
    }
  }
  await page.setViewportSize({ width: SIZES[0].width, height: SIZES[0].height });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  entry.focus = await focusPass(slug(route));
  if (axeSource) {
    await page.evaluate(axeSource);
    entry.axe = await page.evaluate(async () => {
      const r = await window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] } });
      return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length, help: v.help }));
    });
  }
  report.routes.push(entry);
}
await browser.close();

const failures = [];
for (const r of report.routes) {
  for (const s of r.shots) {
    if (s.overflowX > 0) failures.push(`${s.file}: page scrolls sideways by ${s.overflowX}px`);
    if (s.motion === "reduce" && s.runningAnimations > 0) failures.push(`${s.file}: ${s.runningAnimations} animations still running under reduced motion`);
    if (s.motion === "reduce" && s.motionMs && s.motionMs !== "0ms") failures.push(`${s.file}: --motion-ms is ${s.motionMs} under reduced motion`);
  }
  if (!r.focus.length) failures.push(`${r.route}: Tab never reached a control`);
  for (const f of r.focus) if (!f.ring) failures.push(`${r.route}: no visible focus ring on "${f.name}" (${f.tag})`);
  for (const v of r.axe ?? []) if (v.impact === "serious" || v.impact === "critical") failures.push(`${r.route}: axe ${v.id} (${v.impact}, ${v.nodes} nodes): ${v.help}`);
}
report.failures = failures;
report.pageErrors = [...new Set(report.pageErrors)].slice(0, 20);
writeFileSync(`${out}/report.json`, JSON.stringify(report, null, 2));
writeFileSync(`${out}/shots.json`, JSON.stringify(shots));
console.log(`out: ${out}`);
console.log(`shots: ${shots.length}, page errors: ${report.pageErrors.length}, axe: ${report.axe}`);
console.log(failures.length ? `FAIL (${failures.length})\n- ${failures.join("\n- ")}` : "PASS: no hard failures");
process.exit(failures.length ? 2 : 0);
