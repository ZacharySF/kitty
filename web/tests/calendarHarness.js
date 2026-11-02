// Browser fixture for tests/calendarColors.browser.test.mjs. Mounts the real Calendar
// view with an in-memory stand-in for kitty!'s calendar API; every record is synthetic.
import { mount } from "svelte";
import "/src/styles.css";
import "/src/lib/themes/engine.css";
import { applySavedTheme } from "/src/lib/themes/index.ts";
import CalendarHarness from "./CalendarHarness.svelte";

/** Today at `hour` local time, as the UTC instant the server would return. */
const at = (hour) => {
  const day = new Date();
  day.setHours(hour, 0, 0, 0);
  return day.toISOString();
};
const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const event = (id, title, hour, extra = {}) => ({
  id, revision: 1, title, calendar_id: "personal", start: at(hour), end: at(hour + 1), timezone: zone, all_day: false,
  occurrence: at(hour), rrule: "", exdates: [], description: "", location: "", url: "", busy: true, privacy: "default",
  reminders: [10], attendees: [], attachments: [], event_type: "default", google_options: {}, subject_id: null, color: null, ...extra,
});
const subjects = [{ id: 7, name: "Physics", color: "#31a2bc" }];
const calendars = [
  { id: "personal", name: "Personal", color: "c1", subject_id: null, timezone: zone, visible: 1, revision: 1 },
  { id: "legacy", name: "Old club", color: "#123abc", subject_id: null, timezone: zone, visible: 1, revision: 1 },
];
const events = [
  event("linked", "Optics lecture", 9, { subject_id: 7 }),
  event("picked", "Lunch with Mira", 12, { color: "c3" }),
  event("club", "Club night", 15, { calendar_id: "legacy" }),
];

const calls = [];
const harness = { calls, posted: (path) => calls.filter((c) => c.method === "POST" && c.path === path).map((c) => c.body) };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

window.fetch = async (input, init = {}) => {
  const url = new URL(String(input), location.href);
  const method = init.method ?? "GET";
  const body = init.body ? JSON.parse(init.body) : undefined;
  const path = url.pathname.replace(/^\/api/, "");
  calls.push({ path, method, body });
  if (path === "/calendar" && method === "GET")
    return json({ calendars, events, tasks: [], subjects, activity: [], reminders: [], conflicts: [], free: [], busy: [] });
  if (path === "/calendar/time") return json({ value: new Date(body.value).toISOString() });
  if (path === "/calendar/google/status")
    return json({ client_present: false, authorized: false, tasks_authorized: false, steps: [], selected: [], notice: "",
      limits: [], public_operations: {}, calendar_conflicts: [], capabilities: { google: [], account_dependent: [], unsupported: {} } });
  if (path === "/calendar/google/conflicts") return json({ items: [] });
  if (path === "/schema/network") return json({ maps: [], nodes: [] });
  const edit = /^\/calendar\/events\/([a-z]+)$/.exec(path);
  if (edit && method === "GET") return json(events.find((e) => e.id === edit[1]));
  if (edit) {
    const index = events.findIndex((e) => e.id === edit[1]);
    events[index] = { ...events[index], ...body.changes, revision: events[index].revision + 1 };
    return json({ ...events[index], undo_id: calls.length });
  }
  if (path === "/calendar/calendars" && method === "POST") {
    const saved = { visible: 1, revision: 1, subject_id: null, ...body, id: body.id ?? `cal${calls.length}` };
    const index = calendars.findIndex((c) => c.id === saved.id);
    if (index >= 0) calendars[index] = { ...saved, revision: saved.revision + 1 };
    else calendars.push(saved);
    return json(saved);
  }
  return json({ error: "not in harness" }, 404);
};

applySavedTheme();
mount(CalendarHarness, { target: document.getElementById("view") });
window.calendarHarness = harness;
