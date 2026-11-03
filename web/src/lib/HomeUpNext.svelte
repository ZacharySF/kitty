<script lang="ts">
  import { api } from "./api";
  import HomeWidget from "./HomeWidget.svelte";
  import { activeModules } from "./modules.svelte";
  import { launchMath, type MathLaunch } from "./math/launch";
  import { viewEnabled, type View } from "./workspace";

  /**
   * Q13: what comes after today's agenda, soonest first: open dated tasks and job-search
   * reminders (overdue ones on top), cards due for review, then the next seven days of the
   * calendar, where deadlines and follow-ups from every module land.
   */
  let { id, revision, onview, limit = 6 }: { id: string; revision: number; onview: (view: View) => unknown; limit?: number } = $props();

  type Event = { id: string; occurrence?: string; title: string; start: string; all_day: boolean; calendar_id: string; source_key?: string | null };
  type Task = { id: string; title: string; due: string; status: string };
  type Reminder = { id: number; kind: string; due_at: string; label: string };
  type Row = { key: string; title: string; kind: string; at: number; allDay: boolean; when: string; tone: string; view: View; overdue?: boolean; launch?: MathLaunch };
  type MathNext = { kind: "lesson" | "review" | null; topic_id?: string; title?: string };
  type MathHistory = { sprints: { finished_at: string }[]; streak_days: number };

  const DAY = 86_400_000;
  /** Calendar copies from other modules, named by the key their source gives them. */
  const SOURCES: [RegExp, string][] = [
    [/^(job|opportunity):.*:deadline$/, "Deadline"],
    [/^application:.*:next_action_at$/, "Follow-up"],
    [/^application:/, "Applied"],
    [/^reminder:/, "Reminder"],
    [/^status:/, "Interview"],
  ];

  let rows = $state<Row[] | null>(null);
  let error = $state("");
  let now = $state(Date.now());

  const midnight = (at: number) => {
    const day = new Date(at);
    day.setHours(0, 0, 0, 0);
    return day.getTime();
  };
  /** "YYYY-MM-DD" is a local all-day date; anything else is an instant. */
  const instant = (value: string) => (/^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00`).getTime() : Date.parse(value));
  function day(at: number, allDay: boolean) {
    const offset = Math.round((midnight(at) - midnight(now)) / DAY);
    const name = offset === -1 ? "Yesterday" : offset === 0 ? "Today" : offset === 1 ? "Tomorrow"
      : new Date(at).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
    return allDay ? name : `${name} ${new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" })}`;
  }
  /** A timed item within the next day counts down; an overdue one says so. */
  function soon(row: Row) {
    if (row.overdue) return "overdue";
    if (row.allDay || row.at - now > DAY || row.at < now) return "";
    const minutes = Math.max(1, Math.round((row.at - now) / 60_000));
    return `in ${minutes < 60 ? `${minutes}m` : `${Math.floor(minutes / 60)}h${minutes % 60 ? ` ${minutes % 60}m` : ""}`}`;
  }

  let request = 0;
  async function load() {
    const serial = ++request;
    const enabled = activeModules.enabled;
    const today = midnight(Date.now());
    const tomorrow = today + DAY;
    const end = today + 8 * DAY;
    try {
      const [calendar, cards, reminders, mathNext, sprints] = await Promise.all([
        viewEnabled("calendar", enabled)
          ? api<{ events: Event[]; tasks: Task[]; calendars: { id: string; color: string }[] }>(`/calendar?${new URLSearchParams({
              start: new Date(tomorrow).toISOString(),
              end: new Date(end).toISOString(),
              timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
            })}`)
          : null,
        viewEnabled("flashcards", enabled) ? api<{ stats: { due: number } }>("/flashcards") : null,
        viewEnabled("reminders", enabled) ? api<{ due: Reminder[]; upcoming: Reminder[] }>("/reminders") : null,
        // Math is a nudge, not a deadline: if it can't be read, the rest of Up next still shows.
        viewEnabled("math", enabled) ? api<MathNext>("/math/next").catch(() => null) : null,
        viewEnabled("math", enabled)
          ? api<MathHistory>(`/math/sprints/history?${new URLSearchParams({ timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC" })}`).catch(() => null)
          : null,
      ]);
      if (serial !== request) return;
      now = Date.now();
      const colours = new Map(calendar?.calendars.map((item) => [item.id, item.color]) ?? []);
      const next: Row[] = [];
      for (const task of calendar?.tasks ?? []) {
        if (!task.due || task.status === "done" || task.status === "cancelled") continue;
        const at = instant(task.due);
        const allDay = task.due.length === 10;
        if (!(at < end)) continue;
        next.push({ key: `task:${task.id}`, title: task.title, kind: "Task", at, allDay, when: day(at, allDay), tone: "var(--accent)", view: "calendar",
          overdue: allDay ? at < today : at < now });
      }
      if (cards?.stats.due) next.push({ key: "cards", title: "Flashcards", kind: "Cards", at: today, allDay: true, when: `${cards.stats.due} due`, tone: "var(--c4)", view: "flashcards" });
      if (mathNext?.kind)
        next.push({ key: "math:next", title: mathNext.kind === "lesson" ? "Math: next lesson" : "Math: review due", kind: "Math", at: today, allDay: true,
          when: mathNext.title ?? "", tone: "var(--c3)", view: "math", launch: mathNext.kind === "lesson" ? "lesson" : "practice" });
      // The streak nudge only shows on a day without a sprint yet.
      if (sprints?.sprints.length && !sprints.sprints.some((run) => Date.parse(run.finished_at) >= today))
        next.push({ key: "math:streak", title: sprints.streak_days ? `Sprint today to keep your ${sprints.streak_days}-day streak` : "Start a sprint streak today",
          kind: "Sprint", at: today, allDay: true, when: "mental math", tone: "var(--c3)", view: "math", launch: "sprint" });
      for (const reminder of [...(reminders?.due ?? []), ...(reminders?.upcoming ?? [])]) {
        const at = Date.parse(reminder.due_at);
        if (!(at < end)) continue;
        next.push({ key: `reminder:${reminder.id}`, title: reminder.label, kind: reminder.kind === "followup" ? "Follow-up" : "Reminder", at, allDay: false,
          when: day(at, false), tone: "var(--c3)", view: "reminders", overdue: at < now });
      }
      for (const event of calendar?.events ?? []) {
        const at = instant(event.start);
        // Today's events are the agenda's; Up next starts tomorrow. Reminders read directly are not repeated.
        if (!(at >= tomorrow && at < end) || (reminders && event.source_key?.startsWith("reminder:"))) continue;
        const kind = SOURCES.find(([pattern]) => pattern.test(event.source_key ?? ""))?.[1];
        // A copied deadline or reminder is titled "Kind · …"; the kind chip already says it.
        const title = kind ? event.title.replace(/^[^·]+·\s*/, "") : event.title;
        next.push({ key: `event:${event.id}:${event.occurrence ?? event.start}`, title, kind: kind ?? "Event", at, allDay: event.all_day, when: day(at, event.all_day),
          tone: colours.get(event.calendar_id) || "var(--c2)", view: "calendar" });
      }
      rows = next.sort((a, b) => a.at - b.at);
      error = "";
    } catch (cause) {
      if (serial === request) error = (cause as Error).message;
    }
  }
  $effect(() => {
    void revision;
    void load();
  });
</script>

<HomeWidget {id} title="Up next" eyebrow={rows?.length ? String(rows.length) : ""}>
  {#if error}
    <p class="note" role="alert">{error}</p>
  {:else if !rows}
    <p class="note" role="status">Reading what's next…</p>
  {:else if !rows.length}
    <p class="note">Nothing due this week.</p>
  {:else}
    <ul>
      {#each rows.slice(0, limit) as row (row.key)}
        {@const chip = soon(row)}
        <li>
          <button type="button" onclick={() => { if (row.launch) launchMath(row.launch); onview(row.view); }}>
            <span class="check" style:--tone={row.tone} aria-hidden="true"></span>
            <span class="what">
              <strong>{row.title}</strong>
              <small><span class="kind">{row.kind}</span>{row.when}</small>
            </span>
            {#if chip}<span class="soon" class:late={row.overdue}>{chip}</span>{/if}
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</HomeWidget>

<style>
  ul {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li + li {
    border-top: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
  }
  button {
    display: grid;
    grid-template-columns: 14px minmax(0, 1fr) auto;
    align-items: center;
    gap: 10px;
    width: 100%;
    min-height: 44px;
    padding: 4px 4px 4px 2px;
    border: 0;
    background: transparent;
    color: var(--ink);
    text-align: left;
    cursor: pointer;
  }
  button:hover strong {
    color: var(--accent);
  }
  .check {
    width: 14px;
    height: 14px;
    border: 1.5px solid color-mix(in srgb, var(--tone) 80%, var(--muted));
    border-radius: 50%;
  }
  .what {
    display: grid;
    gap: 2px;
    min-width: 0;
  }
  strong {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font: 500 12.5px/1.3 var(--font-ui);
  }
  small {
    display: flex;
    gap: 8px;
    overflow: hidden;
    color: var(--muted);
    font-size: 11px;
    white-space: nowrap;
  }
  .kind {
    color: var(--subtle);
    font: 500 9.5px/1.6 var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .soon {
    padding: 2px 7px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--muted);
    font: 500 9.5px/1.2 var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    white-space: nowrap;
  }
  .soon.late {
    border-color: color-mix(in srgb, var(--warning) 60%, transparent);
    color: var(--warning);
  }
  .note {
    margin: 0 0 6px;
    color: var(--muted);
    font-size: 12px;
  }
</style>
