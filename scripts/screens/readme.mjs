// README screenshots: every image in docs/screenshots/readme, as 1400x875 JPEGs of a fresh demo
// (modules.jpg is an 800x486 crop). Black theme first, then the Kitty theme; kitty-1..3 are three
// film stills, so copy the one you like to kitty.jpg. The demo's calendar is nearly empty, so this
// adds a synthetic week of events to the demo workspace first (run it against a throwaway demo).
// Usage: node scripts/screens/readme.mjs <demo-link> <out dir>
import { chromium } from "../../web/node_modules/playwright-core/index.mjs";
import { mkdirSync } from "node:fs";
const out = process.argv[3];
mkdirSync(out, { recursive: true });
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 875 }, deviceScaleFactor: 1 });
page.setDefaultTimeout(8000);
const errors = []; page.on("pageerror", (e) => errors.push(e.message));
const shot = (name, clip) => page.screenshot({ path: `${out}/${name}.jpg`, type: "jpeg", quality: 84, ...(clip ? { clip } : {}) });
const go = async (hash, wait = 2600) => { await page.evaluate((h) => { location.hash = h; }, hash); await page.waitForTimeout(wait); };
const rest = async () => { await page.mouse.move(1390, 860); await page.waitForTimeout(400); };
const attempt = async (label, run) => { try { await run(); } catch (e) { console.log("skipped", label, String(e.message).split("\n")[0].slice(0, 200)); } };
const theme = async (id) => { await go("settings/appearance", 2200); await page.locator(`input[name="appearance-theme"][value="${id}"]`).check({ force: true }); await page.waitForTimeout(2500); };

await page.goto(process.argv[2], { waitUntil: "load" });
await page.waitForTimeout(4500);
await shot("welcome");
const skip = page.getByRole("button", { name: /Skip/ });
if (await skip.count()) { await skip.first().click(); await page.waitForTimeout(2500); }

// A synthetic week (Monday to Sunday of this week, in the browser's zone) so home and the calendar have a day to show.
await attempt("week", async () => {
  const added = await page.evaluate(async () => {
    const monday = new Date(); monday.setHours(0, 0, 0, 0); monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
    const day = (offset) => { const d = new Date(monday); d.setDate(d.getDate() + offset); return d; };
    const pad = (n) => String(n).padStart(2, "0");
    const date = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    const zone = (d) => { const m = -d.getTimezoneOffset(); return `${m < 0 ? "-" : "+"}${pad(Math.floor(Math.abs(m) / 60))}:${pad(Math.abs(m) % 60)}`; };
    const at = (offset, hm) => { const d = day(offset); return `${date(d)}T${hm}:00${zone(d)}`; };
    const timed = [
      ["Optics lecture", 0, "09:00", "10:30", "Hall B"], ["Sprint planning", 0, "11:00", "12:00", ""],
      ["Linear algebra", 0, "13:00", "14:30", "Room 204"], ["Run", 0, "17:45", "18:30", ""],
      ["1:1 with Sam", 1, "10:00", "10:45", ""], ["Lunch with Mira", 1, "12:00", "13:00", "Café Rose"],
      ["Thesis writing", 1, "14:00", "16:30", ""], ["Optics lab", 2, "09:00", "11:00", "Lab 3"],
      ["Dentist", 2, "13:30", "14:15", ""], ["Team sync", 3, "09:30", "10:00", ""],
      ["Frontend deck review", 3, "11:00", "12:30", ""], ["Linear algebra", 3, "13:00", "14:30", "Room 204"],
      ["Demo day", 4, "10:00", "12:00", ""], ["Lunch", 4, "12:00", "13:00", ""], ["Climbing", 4, "17:30", "19:00", ""],
      ["Farmers market", 5, "09:00", "10:30", ""], ["Thesis writing", 5, "11:00", "14:00", ""],
      ["Run", 6, "08:00", "09:00", ""], ["Optics problem set", 6, "10:00", "12:00", ""], ["Call the landlord", 6, "15:00", "15:30", ""],
    ];
    const headers = { "X-Kitty-Secret": sessionStorage.getItem("kitty.ui-secret"), "X-Kitty-Request": "1", "Content-Type": "application/json" };
    const base = { calendar_id: "personal", timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, description: "", url: "", rrule: "",
      exdates: [], privacy: "default", busy: true, reminders: [10], attendees: [], attachments: [], event_type: "default",
      google_options: {}, subject_id: null, color: null };
    const events = [
      ...timed.map(([title, d, start, end, location]) => ({ title, location, all_day: false, start: at(d, start), end: at(d, end) })),
      { title: "Conference", location: "", all_day: true, start: date(day(2)), end: date(day(4)) },
      { title: "Mum's birthday", location: "", all_day: true, start: date(day(6)), end: date(day(7)) },
    ];
    let ok = 0;
    for (const event of events) ok += (await fetch("/api/calendar/events", { method: "POST", headers, body: JSON.stringify({ ...base, ...event }) })).ok ? 1 : 0;
    return `${ok}/${events.length}`;
  });
  console.log("calendar events added:", added);
});

await go("today", 3200);
await attempt("offer", async () => { const b = page.getByRole("region", { name: "Home template" }).getByRole("button", { name: "Not now" }); if (await b.count()) await b.click(); });
await rest();
await shot("home");
await attempt("calendar", async () => {
  await go("calendar", 3000);
  await page.getByRole("radio", { name: "Week", exact: true }).or(page.getByRole("button", { name: "Week", exact: true })).first().click({ force: true });
  await page.waitForTimeout(1500);
  // Start the visible hours just before 08:00.
  await page.evaluate(() => {
    const grid = document.querySelector(".time-scroll");
    const label = [...grid.querySelectorAll("*")].find((e) => e.children.length === 0 && e.textContent.trim() === "08:00");
    grid.scrollTop += label.getBoundingClientRect().top - grid.getBoundingClientRect().top - 102;
  });
  await rest();
  await shot("calendar");
});
await attempt("brain", async () => { await go("brain", 6000); await rest(); await shot("brain"); });
await attempt("rika", async () => { await go("rika", 3000); await rest(); await shot("rika"); });
await attempt("jobs", async () => { await go("queue", 2800); for (const i of [0, 2]) await page.locator(".row-check").nth(i).check(); await page.mouse.move(700, 500); await page.waitForTimeout(500); await shot("jobs"); for (const i of [0, 2]) await page.locator(".row-check").nth(i).uncheck(); });
await attempt("edit", async () => {
  await go("jobs", 2800);
  await page.keyboard.press("Control+Shift+Space"); await page.waitForTimeout(1200);
  const widget = async (name) => {
    await page.getByRole("button", { name: /^Widgets/ }).first().click(); await page.waitForTimeout(700);
    await page.getByRole("menuitem", { name, exact: true }).first().click(); await page.waitForTimeout(1100);
  };
  await widget("Clock");
  await widget("Quick note");
  // Both land at the top right, the quick note over the clock: drag the grip on top down below the clock.
  const from = await page.evaluate(() => {
    for (const grip of document.querySelectorAll('button[aria-label="Move widget"]')) {
      const box = grip.getBoundingClientRect();
      const x = box.left + box.width / 2, y = box.top + box.height / 2;
      if (box.width && grip.contains(document.elementFromPoint(x, y))) return { x, y };
    }
    throw new Error("no visible Move widget grip");
  });
  await page.mouse.move(from.x, from.y); await page.mouse.down();
  for (let i = 1; i <= 12; i += 1) { await page.mouse.move(from.x, from.y + i * 25); await page.waitForTimeout(30); }
  await page.mouse.up(); await page.waitForTimeout(900);
  await page.mouse.move(from.x + 160, from.y + 325); await page.waitForTimeout(600);
  await shot("edit");
  await page.reload({ waitUntil: "load" }); await page.waitForTimeout(4200);
});
await attempt("modules", async () => {
  await go("settings/modules", 2600);
  await rest();
  await shot("modules", { x: 425, y: 84, width: 800, height: 486 });
  const themes = page.getByRole("switch", { name: "Themes" });
  if ((await themes.getAttribute("aria-checked")) === "false") { await themes.click(); await page.waitForTimeout(900); }
  // Save only when the switch left a pending change.
  const save = page.getByRole("button", { name: "Save", exact: true });
  if (await save.isEnabled().catch(() => false)) { await save.click(); await page.waitForTimeout(4000); }
});
await attempt("themes", async () => {
  await go("today", 2600);
  await page.mouse.click(700, 800); await page.waitForTimeout(300);
  await page.keyboard.press("Control+y"); await page.waitForTimeout(1800);
  await shot("themes");
  await page.keyboard.press("Escape"); await page.waitForTimeout(800);
});
await attempt("kitty", async () => {
  await theme("aero");
  for (let i = 0; i < 3; i += 1) { await go("today", 2800); await rest(); await shot(`kitty-${i + 1}`); await page.reload({ waitUntil: "load" }); await page.waitForTimeout(4200); }
  await theme("kitty");
});
console.log("errors:", errors.slice(0, 3));
await browser.close();
