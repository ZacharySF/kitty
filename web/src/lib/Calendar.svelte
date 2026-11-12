<script lang="ts">
  import { onMount, untrack, tick } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api, safeUrl, type TaskRecord } from "./api";
  import TaskMapAction from "./TaskMapAction.svelte";
  import CalendarDateTime from "./CalendarDateTime.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  import { currentTheme, THEME_EVENT } from "./themes";
  import { getTheme } from "./themes/catalog";
  import { blockText, calendarPaint, itemPaint, nextSlot, subjectOf, type Subject } from "./calendar/palette";
  import ColorField from "./calendar/ColorField.svelte";
  import DayAgenda, { type AgendaItem } from "./calendar/DayAgenda.svelte";
  import RangePill, { VIEWS, type CalendarView } from "./calendar/RangePill.svelte";
  import YearHeatmap from "./calendar/YearHeatmap.svelte";
  import MiniMonth from "./calendar/MiniMonth.svelte";
  import DayPlan from "./calendar/DayPlan.svelte";
  import { nextFreeGap, PLAN_END, PLAN_LENGTH, PLAN_START } from "./calendar/plan";
  import { addDays, dateAt, dayLoad, localInput, placeTimed, shiftWall, slot, wallParts, weekStart,
    type TimedPlacement } from "./calendar/dates";
  import EditDrawer from "./ui/EditDrawer.svelte";
  import Select from "./ui/Select.svelte";
  import CalendarQuickAdd from "./calendar/CalendarQuickAdd.svelte";
  import { wallAt, type QuickKind, type QuickPlan, type QuickSlot } from "./calendar/quickAdd";
  import QuickUnderstood from "./calendar/QuickUnderstood.svelte";
  import { parseQuickEvent, type QuickEvent } from "./calendar/quickParse";
  import { notifier } from "./notifications.svelte";
  import CalendarIntegrations from "./calendar/CalendarIntegrations.svelte";
  import type { Cal, CalendarEvent as Event } from "./calendar/types";
  let { revision = 0, demo = false, active = true }: {
    revision?: number; demo?: boolean; active?: boolean;
  } = $props();
  const pane = useWorkspaceContext();
  const isActive = $derived(active && pane.active);
  type Task = {
    id?: string;
    revision?: number;
    title: string;
    description: string;
    due: string;
    timezone: string;
    priority: string;
    status: string;
    tags: string[];
    project: string;
    subject_id?: number | null;
    calendar_id: string;
    repeat_frequency: string;
    repeat_interval: number;
    repeat_until: string;
    next_task?: Task | null;
    undo_id?: number;
  };
  type Snapshot = {
    calendars: Cal[];
    events: Event[];
    tasks: Task[];
    subjects: Subject[];
    activity: Record<string, number | string>[];
    conflicts: {
      event_id: string;
      with: string[];
      start: string;
      end: string;
    }[];
    free: { start: string; end: string }[];
  };
  let data = $state<Snapshot>({
    calendars: [],
    events: [],
    tasks: [],
    subjects: [],
    activity: [],
    conflicts: [],
    free: [],
  });
  let view = $state<CalendarView>("week");
  let section = $state("calendar");
  const initialDate = new Date();
  const initialDay = `${initialDate.getFullYear()}-${String(initialDate.getMonth() + 1).padStart(2, "0")}-${String(initialDate.getDate()).padStart(2, "0")}`;
  let anchor = $state(initialDay);
  let selectedDate = $state(initialDay);
  let tz = $state(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  let search = $state("");
  let error = $state("");
  let notice = $state("");
  let loading = $state(false);
  let pending = $state(0);
  let undoId = $state<number | null>(null);
  let editor = $state<Event | null>(null);
  let originalEditor: Event | null = null;
  let editBase: Event | null = null;
  let taskEditor = $state<Task | null>(null);
  let editScope = $state("this");
  let startInput = $state("");
  let endInput = $state("");
  let fold = $state(0);
  let foldChanged = false;
  let googleOptionsText = $state("{}");
  let attendeesText = $state("");
  let attachmentsText = $state("");
  let remindersText = $state("10");
  const reminderChips: [number, string][] = [
    [5, "5 minutes before"], [10, "10 minutes before"], [60, "1 hour before"], [1440, "1 day before"],
  ];
  const reminderList = $derived(remindersText.split(",").filter((v) => v.trim()).map(Number));
  function toggleReminder(minutes: number) {
    remindersText = (reminderList.includes(minutes)
      ? reminderList.filter((m) => m !== minutes)
      : [...reminderList, minutes].sort((a, b) => a - b)).join(",");
  }
  /** Raw title text as typed; `editor.title` holds what remains after natural-language parsing. */
  let titleDraft = $state("");
  let quick = $state<QuickEvent | null>(null);
  let exdatesText = $state("");
  let calendarEditor = $state<Partial<Cal> | null>(null);
  let rangeStart = $state<string | null>(null);
  let rangeEnd = $state<string | null>(null);
  let rangeAllDay = $state(false);
  let dragEvent: Event | null = null;
  let dragMode = "move";
  let dragTarget = $state("");
  let invalidDropTarget = $state("");
  let dragMessage = $state("");
  let suppressClickUntil = 0;
  let taskTrayOpen = $state(true);
  /** Q20: the pill's quick add; a clicked slot or range prefills its time, else the selected day. */
  let quickOpen = $state(false);
  let quickKind = $state<QuickKind>("event");
  let quickPick = $state<QuickSlot | null>(null);
  const quickSlot = $derived(quickPick ?? { start: selectedDate });
  $effect(() => {
    if (!quickOpen) quickPick = null;
  });
  // One drawer at a time: opening an event closes the task drawer and vice versa.
  $effect.pre(() => {
    if (editor) untrack(() => (taskEditor = null));
  });
  $effect.pre(() => {
    if (taskEditor) untrack(() => (editor = null));
  });
  /** Q3/Q9: saving reports to the tab's status dot instead of "saved locally" notices. */
  $effect(() => {
    if (!active) return;
    pane.setStatus(error ? { state: "failed", detail: error } : pending > 0 ? { state: "unsaved", detail: "Saving…" } : { state: "saved" });
    return () => pane.setStatus(null);
  });
  const trayTasks = $derived(data.tasks.filter((task) =>
    task.status !== "done" && task.status !== "cancelled" &&
    (!task.due || task.due.length === 10 || parts(task.due).date === selectedDate),
  ));
  let taskDueInput = $state("");
  let taskDueTimed = $state(false);
  function modal(node: HTMLDialogElement) {
    const previous = document.activeElement as HTMLElement | null;
    if (isActive) {
      node.showModal();
      node.querySelector<HTMLInputElement>("input:not([type=checkbox])")?.focus();
    }
    return { destroy: () => { if (isActive) previous?.focus(); } };
  }
  const statusOptions = [
    { value: "todo", label: "To do" }, { value: "doing", label: "In progress" },
    { value: "done", label: "Done" }, { value: "cancelled", label: "Cancelled" },
  ];
  const hours = Array.from({ length: 48 }, (_, i) => i);
  let focusedSlot = $state(18);
  let clock = $state(new Date().toISOString());
  function scrollToMorning(node: HTMLDivElement) {
    const frame = requestAnimationFrame(() => (node.scrollTop = 8 * 72));
    return { destroy: () => cancelAnimationFrame(frame) };
  }
  let loadCounter = 0;
  let firstDay = $derived(
    view === "month"
      ? weekStart(anchor.slice(0, 8) + "01")
      : view === "week"
        ? weekStart(anchor)
        : view === "year"
          ? anchor.slice(0, 4) + "-01-01"
          : anchor,
  );
  const dayCount = $derived(
    view === "month" ? 42
      : view === "week" ? 7
      : view === "agenda" ? 31
      : view === "year" ? (Number(anchor.slice(0, 4)) % 4 === 0 && (Number(anchor.slice(0, 4)) % 100 !== 0 || Number(anchor.slice(0, 4)) % 400 === 0) ? 366 : 365)
      : 1,
  );
  let days = $derived(Array.from({ length: dayCount }, (_, i) => addDays(firstDay, i)));
  const dateTitle = (day: string, options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat(undefined, { ...options, timeZone: "UTC" }).format(dateAt(day));
  const rangeHeading = $derived(
    view === "month"
      ? dateTitle(anchor, { month: "long", year: "numeric" })
      : view === "year"
        ? anchor.slice(0, 4)
        : view === "day" || view === "plan"
          ? dateTitle(anchor, { weekday: "long", month: "long", day: "numeric", year: "numeric" })
          : `${dateTitle(firstDay, { month: "short", day: "numeric" })} – ${dateTitle(days[days.length - 1], { month: "short", day: "numeric", year: "numeric" })}`,
  );
  function chooseDate(day: string) {
    if (!day) return;
    anchor = day;
    selectedDate = day;
  }
  const today = $derived(parts(clock).date);
  const currentMinute = $derived(parts(clock).hour * 60 + parts(clock).minute);
  /** Wall parts in the display timezone unless another zone is given. */
  function parts(value: string, zoneName = tz) {
    return wallParts(value, zoneName);
  }
  async function resolveTime(value: string, zoneName = tz) {
    return (
      await api<{ value: string }>("/calendar/time", {
        value,
        timezone: zoneName,
        fold,
      })
    ).value;
  }
  function label(value: string, allDay = false) {
    return allDay
      ? value
      : new Intl.DateTimeFormat(undefined, {
          timeZone: tz,
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        }).format(new Date(value));
  }
  function eventsOn(day: string) {
    return shownEvents.filter((e) =>
      e.all_day
        ? e.start <= day && e.end > day
        : parts(e.start).date <= day &&
          parts(e.end).date >= day &&
          !(parts(e.end).date === day && parts(e.end).time === "00:00"),
    );
  }
  /** Events as drawn: the one open in the drawer shows its unsaved title, calendar, place and times. */
  const shownEvents = $derived.by(() => {
    const open = editor;
    const base = originalEditor;
    if (!open?.id || !base || open.all_day || base.all_day || !startInput || !endInput) return data.events;
    // Same zone as the saved event, so its UTC offset carries over to the edited wall times.
    const offset = Date.parse(base.start) - Date.parse(`${localInput(base.start, false, base.timezone)}:00Z`);
    const start = new Date(Date.parse(`${startInput}:00Z`) + offset).toISOString();
    const end = new Date(Date.parse(`${endInput}:00Z`) + offset).toISOString();
    return data.events.map((e) => e.id === open.id && e.occurrence === open.occurrence
      ? { ...e, title: open.title, calendar_id: open.calendar_id, subject_id: open.subject_id, color: open.color,
        location: open.location, start, end } : e);
  });
  function shiftTime(minutes: number, which: "start" | "end") {
    if (which === "start") {
      startInput = shiftWall(startInput, minutes);
      endInput = shiftWall(endInput, minutes);
    } else if (Date.parse(`${shiftWall(endInput, minutes)}:00Z`) - Date.parse(`${startInput}:00Z`) >= 15 * 60000)
      endInput = shiftWall(endInput, minutes);
  }
  /** Title typing: recognised day, time, length, place and repeat fill the fields; the rest is the title. */
  function typeTitle(raw: string) {
    titleDraft = raw;
    if (!editor) return;
    const parsed = parseQuickEvent(raw, today);
    quick = parsed.spans.length ? parsed : null;
    editor.title = quick ? parsed.title : raw;
    if (!quick) return;
    if (parsed.date !== null || parsed.start !== null) {
      const length = editor.all_day ? 60 : (Date.parse(`${endInput}:00Z`) - Date.parse(`${startInput}:00Z`)) / 60000 || 60;
      const start = parsed.start ?? (editor.all_day ? 9 * 60 : Number(startInput.slice(11, 13)) * 60 + Number(startInput.slice(14, 16)));
      const day = parsed.date ?? startInput.slice(0, 10);
      editor.all_day = false;
      startInput = shiftWall(`${day}T00:00`, start);
      endInput = shiftWall(startInput, parsed.end !== null ? parsed.end - start : parsed.minutes ?? length);
    } else if (parsed.minutes !== null && !editor.all_day) endInput = shiftWall(startInput, parsed.minutes);
    if (parsed.where) editor.location = parsed.where;
    if (parsed.rrule) editor.rrule = parsed.rrule;
  }
  function tasksOn(day: string) {
    return data.tasks.filter(
      (t) =>
        t.due &&
        t.status !== "cancelled" &&
        (t.due.length === 10 ? t.due : parts(t.due).date) === day,
    );
  }
  function timedEvents(day: string): TimedPlacement<Event>[] {
    return placeTimed(eventsOn(day).filter((event) => !event.all_day), day, tz);
  }
  const weekEvents = $derived.by(() =>
    Object.fromEntries(
      view === "week" || view === "day"
        ? days.map((day) => [day, timedEvents(day)])
        : [],
    ) as Record<string, TimedPlacement<Event>[]>,
  );
  const weekTasks = $derived.by(() => {
    const slots: Record<string, Task[][]> = {};
    if (view !== "week" && view !== "day") return slots;
    for (const day of days) slots[day] = Array.from({ length: 48 }, () => []);
    for (const task of data.tasks) {
      if (task.due.length <= 10 || task.status === "cancelled") continue;
      const time = parts(task.due);
      slots[time.date]?.[Math.floor((time.hour * 60 + time.minute) / 30)].push(task);
    }
    return slots;
  });
  let theme = $state(getTheme(currentTheme()));
  /** An event's colour: its (or its calendar's) study subject's, else its palette slot, else its calendar's. */
  const paint = (e: Pick<Event, "calendar_id" | "subject_id" | "color">) => itemPaint(e, data.calendars, data.subjects).color;
  const calendarColor = (c: Cal) => calendarPaint(c, data.subjects).color;
  /** A task's colour when it is linked to a study subject; unlinked tasks stay muted. */
  const taskColor = (t: Task) => subjectOf(t, data.subjects)?.color;
  /** Filled-block colours for an event and the text that reads on them. */
  function blockStyle(e: Event) {
    const fill = paint(e);
    return `--block:${fill};--block-ink:${blockText(theme, fill)}`;
  }
  const editorCalendar = $derived(editor ? data.calendars.find((c) => c.id === editor?.calendar_id) : undefined);
  /** The drawer's "Calendar colour" default: the event's calendar paint. */
  const calendarDefault = $derived(editorCalendar ? { ...calendarPaint(editorCalendar, data.subjects), name: editorCalendar.name } : null);
  async function load() {
    const serial = ++loadCounter;
    loading = true;
    try {
      const start = await resolveTime(firstDay + "T00:00");
      const end = await resolveTime(
        addDays(days[days.length - 1], 1) + "T00:00",
      );
      const result = await api<Snapshot>(
        `/calendar?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&timezone=${encodeURIComponent(tz)}&search=${encodeURIComponent(search)}`,
      );
      if (serial === loadCounter) {
        data = result;
        loadVersion += 1;
      }
    } catch (e) {
      if (serial === loadCounter) error = String(e);
    } finally {
      if (serial === loadCounter) loading = false;
    }
  }
  let loadVersion = $state(0);
  const yearMinutes = $derived(view === "year"
    ? Object.fromEntries(Object.entries(dayLoad(data.events, tz)).map(([day, load]) => [day, load.minutes]))
    : {});
  async function loadBusy(from: string, to: string) {
    const start = await resolveTime(from + "T00:00");
    const end = await resolveTime(to + "T00:00");
    const result = await api<Snapshot>(
      `/calendar?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&timezone=${encodeURIComponent(tz)}`,
    );
    return dayLoad(result.events, tz);
  }
  /** Open tasks on the plan day (undated, dated that day, or timed that day), with planned start minute. */
  const planTasks = $derived(view === "plan"
    ? data.tasks
        .filter((t) => t.id && t.status !== "cancelled" &&
          (!t.due || (t.due.length === 10 ? t.due : parts(t.due).date) === anchor) &&
          (t.status !== "done" || !!t.due))
        .map((t) => ({ task: t, at: t.due.length > 10 ? parts(t.due).hour * 60 + parts(t.due).minute : null }))
    : []);
  async function planTask(id: string) {
    const entry = planTasks.find((item) => item.task.id === id);
    if (!entry) return;
    const busy: [number, number][] = [
      ...timedEvents(anchor).map((p): [number, number] => [p.start, p.end]),
      ...planTasks.filter((item) => item.at !== null).map((item): [number, number] => [item.at!, item.at! + PLAN_LENGTH]),
    ];
    const from = Math.max(PLAN_START, anchor === today ? currentMinute : 0);
    const start = nextFreeGap(busy, from, PLAN_LENGTH);
    if (start === null) {
      notice = `No free ${PLAN_LENGTH}-minute gap left before ${String(PLAN_END / 60)}:00.`;
      return;
    }
    const wall = `${anchor}T${String(Math.floor(start / 60)).padStart(2, "0")}:${String(start % 60).padStart(2, "0")}`;
    await changed(async () =>
      api<Task>("/calendar/tasks", { id, revision: entry.task.revision, due: await resolveTime(wall, tz) }));
  }
  async function unplanTask(id: string) {
    const entry = planTasks.find((item) => item.task.id === id);
    if (entry) await changed(() => api<Task>("/calendar/tasks", { id, revision: entry.task.revision, due: anchor }));
  }
  async function run(work: () => Promise<unknown>) {
    error = "";
    notice = "";
    pending += 1;
    try {
      return await work();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      pending -= 1;
    }
  }
  async function changed(
    work: () => Promise<{ undo_id?: number; next_task?: Task | null }>,
  ) {
    return run(async () => {
      const result = await work();
      undoId = result.undo_id || null;
      await load();
      if (result.next_task)
        notice = `Completed instance kept in history. Next due: ${label(result.next_task.due, result.next_task.due.length === 10)}.`;
      return result;
    });
  }
  function navigate(step: number) {
    if (view === "month" || view === "year") {
      const d = dateAt(anchor);
      if (view === "year") d.setUTCFullYear(d.getUTCFullYear() + step, d.getUTCMonth(), 1);
      else d.setUTCMonth(d.getUTCMonth() + step, 1);
      anchor = d.toISOString().slice(0, 10);
    } else
      anchor = addDays(
        anchor,
        step * (view === "week" ? 7 : view === "agenda" ? 31 : 1),
      );
    selectedDate = anchor;
  }
  function setView(next: CalendarView) {
    view = next;
    anchor = selectedDate;
  }
  let shortcutFlash = $state("");
  let flashTimer: ReturnType<typeof setTimeout> | undefined;
  let calendarLayout = $state<HTMLDivElement>();
  /** Single-key shortcuts while focus is inside the calendar: D/W/M/A/Y/P views, J/K step, T today, N add. */
  function calendarKey(event: KeyboardEvent) {
    const target = event.target as HTMLElement;
    if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.defaultPrevented ||
        target.isContentEditable || target.closest("input, textarea, select")) return;
    const key = event.key.toLowerCase();
    const hit = VIEWS.find((v) => v.key.toLowerCase() === key);
    if (hit) setView(hit.id);
    else if (key === "j") navigate(-1);
    else if (key === "k") navigate(1);
    else if (key === "t") chooseDate(today);
    else if (key === "n") {
      event.preventDefault();
      void openQuick(selectedDate);
      return;
    }
    else return;
    event.preventDefault();
    shortcutFlash = key.toUpperCase();
    // The focused cell may vanish with the old view; keep focus in the calendar so keys keep working.
    void tick().then(() => {
      if (calendarLayout && !calendarLayout.contains(document.activeElement)) calendarLayout.focus({ preventScroll: true });
    });
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => (shortcutFlash = ""), 700);
  }
  /** Opens the pill's quick add at `start` (a day or a wall time), ending at `end` when a range was picked. */
  async function openQuick(start: string, end?: string) {
    if (!isActive) return;
    error = "";
    if (section !== "calendar") {
      section = "calendar";
      await tick();
    }
    selectedDate = start.slice(0, 10);
    quickPick = { start, end };
    quickOpen = true;
  }
  /** Creates the typed event or task; `more` then opens it in the drawer. */
  async function quickSave(plan: QuickPlan, kind: QuickKind, more: boolean) {
    const calendar_id = (data.calendars.find((c) => c.visible) || data.calendars[0])?.id || "personal";
    const at = (minutes: number) => resolveTime(wallAt(plan.day, minutes), tz);
    const result = await changed(async () => kind === "task"
      ? api<Task>("/calendar/tasks", {
          title: plan.title, description: "", due: plan.start === null ? plan.day : await at(plan.start), timezone: tz,
          priority: "normal", status: "todo", tags: [], project: "", subject_id: null, calendar_id,
          repeat_frequency: /^FREQ=(\w+)/.exec(plan.rrule)?.[1] ?? "", repeat_interval: 1, repeat_until: "",
        })
      : api<Event>("/calendar/events", {
          title: plan.title, calendar_id, timezone: tz, all_day: plan.start === null,
          start: plan.start === null ? plan.day : await at(plan.start),
          end: plan.start === null ? plan.endDay : await at(plan.end ?? plan.start + 60),
          description: "", location: plan.where, url: "", rrule: plan.rrule, exdates: [], privacy: "default", busy: true,
          reminders: [10], attendees: [], attachments: [], event_type: "default", google_options: {}, subject_id: null, color: null,
        }));
    if (!result) return false;
    if (more && kind === "task") taskEditor = { ...(result as Task) };
    else if (more) openEvent(result as Event);
    return true;
  }
  function editorFields(e: Event) {
    startInput = localInput(e.start, e.all_day, e.timezone);
    endInput = localInput(e.end, e.all_day, e.timezone);
    attendeesText = e.attendees
      .map((a) => `${a.email},${a.name},${a.response},${a.optional}`)
      .join("\n");
    attachmentsText = e.attachments
      .map((a) => `${a.title} | ${a.url}`)
      .join("\n");
    exdatesText = e.exdates.join("\n");
    remindersText = e.reminders.join(",");
    titleDraft = e.title;
    quick = null;
    foldChanged = false;
    googleOptionsText = JSON.stringify(e.google_options, null, 2);
  }
  function openEvent(e: Event) {
    if (!isActive || Date.now() < suppressClickUntil) return;
    originalEditor = $state.snapshot(e);
    editBase = structuredClone(originalEditor);
    editor = structuredClone(originalEditor);
    editScope = e.rrule ? "this" : "all";
    fold = 0;
    editorFields(e);
  }
  async function changeScope() {
    if (!editor || !originalEditor) return;
    const occurrence = originalEditor.occurrence;
    await run(async () => {
      let source: Event;
      if (editScope === "this") source = structuredClone(originalEditor!);
      else {
        source = await api<Event>(`/calendar/events/${editor!.id}`);
        if (editScope === "future") {
          const span =
            new Date(
              source.end + (source.all_day ? "T00:00:00Z" : ""),
            ).getTime() -
            new Date(
              source.start + (source.all_day ? "T00:00:00Z" : ""),
            ).getTime();
          const end = new Date(
            new Date(
              occurrence + (source.all_day ? "T00:00:00Z" : ""),
            ).getTime() + span,
          ).toISOString();
          source = {
            ...source,
            start: occurrence,
            end: source.all_day ? end.slice(0, 10) : end,
          };
        }
      }
      editBase = structuredClone(source);
      editor = { ...source, occurrence };
      editorFields(editor);
    });
  }
  async function saveEvent(remove = false) {
    if (!editor) return;
    const e = editor;
    await changed(async () => {
      const savedTime = async (input: string, original: string) =>
        e.all_day
          ? input
          : original &&
              !foldChanged &&
              input === localInput(original, false, e.timezone)
            ? original
            : await resolveTime(input, e.timezone);
      const start = await savedTime(startInput, e.start);
      const end = await savedTime(endInput, e.end);
      const attendees = attendeesText
        .split("\n")
        .filter((v) => v.trim())
        .map((line) => {
          const [
            email,
            name = "",
            response = "needsAction",
            optional = "false",
          ] = line.split(",");
          return {
            email: email.trim(),
            name: name.trim(),
            response: response.trim(),
            optional: optional.trim() === "true",
          };
        });
      const attachments = attachmentsText
        .split("\n")
        .filter((v) => v.trim())
        .map((line) => {
          const [title, ...url] = line.split("|");
          return { title: title.trim(), url: url.join("|").trim() };
        });
      const changes = {
        title: e.title,
        calendar_id: e.calendar_id,
        start,
        end,
        timezone: e.timezone,
        all_day: e.all_day,
        description: e.description,
        location: e.location,
        url: e.url,
        rrule: e.rrule,
        exdates: exdatesText.split("\n").filter((v) => v.trim()),
        privacy: e.privacy,
        busy: e.busy,
        reminders: remindersText
          .split(",")
          .filter((v) => v.trim())
          .map(Number),
        attendees,
        attachments,
        event_type: e.event_type,
        google_options: JSON.parse(googleOptionsText),
        subject_id: e.subject_id ?? null,
        color: e.color ?? null,
      };
      const base = editScope === "all" ? await api<Event>(`/calendar/events/${e.id}`) : editBase;
      const patch = Object.fromEntries(
        Object.entries(changes).filter(
          ([key, value]) => JSON.stringify(value) !== JSON.stringify(base?.[key as keyof Event] ?? null),
        ),
      );
      const result = await api<Event>(`/calendar/events/${e.id}`, {
        revision: e.revision,
        scope: editScope,
        occurrence: e.occurrence,
        changes: patch,
        delete: remove,
      });
      editor = null;
      return result;
    });
  }
  async function deleteEventAtPointer(event: MouseEvent, item: Event) {
    event.preventDefault();
    if (pending || !confirm(`Delete ${item.rrule ? "this occurrence of " : ""}“${item.title}”?`)) return;
    openEvent(item);
    await saveEvent(true);
  }
  async function deleteTaskAtPointer(event: MouseEvent, item: Task) {
    event.preventDefault();
    if (pending || !confirm(`Delete task “${item.title}”?`)) return;
    taskEditor = {...item};
    await tick();
    await saveTask(true);
  }
  function beginRange(value: string, allDay: boolean, event: PointerEvent) {
    if (!isActive || dragEvent || Date.now() < suppressClickUntil) return;
    if (event.button !== 0) return;
    selectedDate = value.slice(0, 10);
    rangeStart = value;
    rangeEnd = value;
    rangeAllDay = allDay;
  }
  function extendRange(value: string) {
    if (rangeStart) rangeEnd = value;
  }
  function finishRange() {
    if (!isActive) { rangeStart = null; rangeEnd = null; return; }
    if (!rangeStart || !rangeEnd) return;
    const [a, b] = [rangeStart, rangeEnd].sort();
    if (rangeAllDay) {
      selectedDate = a;
      if (a !== b) void openQuick(a, addDays(b, 1));
    } else void openQuick(a, a === b ? undefined : shiftWall(b, 30));
    rangeStart = null;
    rangeEnd = null;
  }
  function slotKey(day: string, halfHour: number, event: KeyboardEvent) {
    const shifts: Record<string, number> = {
      ArrowUp: -1,
      ArrowDown: 1,
      ArrowLeft: -48,
      ArrowRight: 48,
      Home: -halfHour,
      End: 47 - halfHour,
    };
    if (event.key in shifts) {
      event.preventDefault();
      const index = days.indexOf(day) * 48 + halfHour + shifts[event.key];
      if (index < 0 || index >= days.length * 48) return;
      const nextDay = days[Math.floor(index / 48)];
      focusedSlot = index % 48;
      selectedDate = nextDay;
      const grid = (event.currentTarget as HTMLElement).closest(".time-grid");
      const target = grid?.querySelector<HTMLButtonElement>(
        `[data-day="${nextDay}"][data-slot="${focusedSlot}"]`,
      );
      target?.focus({ preventScroll: true });
      target?.scrollIntoView({ block: "nearest", inline: "nearest" });
      if (event.shiftKey && rangeStart) extendRange(slot(nextDay, focusedSlot));
      return;
    }
    keyboardRange(slot(day, halfHour), false, event);
  }
  function keyboardRange(value: string, allDay: boolean, event: KeyboardEvent) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (event.shiftKey && rangeStart) {
        rangeEnd = value;
        finishRange();
      } else if (event.shiftKey) {
        rangeStart = value;
        rangeEnd = value;
        rangeAllDay = allDay;
      } else {
        rangeStart = value;
        rangeEnd = value;
        rangeAllDay = allDay;
        finishRange();
      }
    }
  }
  async function dropOn(value: string, allDay: boolean, event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    const selected = dragEvent;
    const mode = dragMode;
    const taskId = event.dataTransfer?.getData("application/kitty-task");
    const task = taskId && data.tasks.find((item) => item.id === taskId);
    const valid = isActive && !pending && (!!task || (!!selected && allDay === selected.all_day));
    finishDrag();
    if (!valid) return;
    selectedDate = value.slice(0, 10);
    if (task) {
      await changed(async () => {
        const due = allDay ? value : await resolveTime(value, tz);
        const result = await api<Task>("/calendar/tasks", {
          id: task.id, revision: task.revision, due,
        });
        return result;
      });
      return;
    }
    if (!selected) return;
    await changed(async () => {
      let start = selected.start;
      let end: string;
      if (allDay) {
        const duration = Math.round((dateAt(selected.end).getTime() - dateAt(selected.start).getTime()) / 86400000);
        if (mode === "resize") end = addDays(value, 1);
        else { start = value; end = addDays(value, duration); }
      } else {
        // Slots are wall-clock times in the displayed timezone, not the event's timezone.
        const target = await resolveTime(value, tz);
        if (mode === "resize") end = target;
        else {
          start = target;
          end = new Date(new Date(start).getTime() +
            new Date(selected.end).getTime() - new Date(selected.start).getTime()).toISOString();
        }
      }
      if (end <= start) throw new Error("Drop the end after the event's start.");
      const result = await api<Event>(`/calendar/events/${selected.id}`, {
        revision: selected.revision,
        scope: selected.rrule ? "this" : "all",
        occurrence: selected.occurrence,
        changes: { start, end },
      });
      return result;
    });
  }
  function drag(e: Event, event: DragEvent, mode = "move") {
    if (!isActive || pending) { event.preventDefault(); return; }
    rangeStart = null;
    rangeEnd = null;
    suppressClickUntil = Number.POSITIVE_INFINITY;
    dragEvent = e;
    dragMode = mode;
    event.dataTransfer?.setData("application/kitty-event", e.id);
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
  }
  function allowDrop(value: string, allDay: boolean, event: DragEvent) {
    event.stopPropagation();
    const task = event.dataTransfer?.types.includes("application/kitty-task");
    if (!dragEvent && !task) return;
    const valid = isActive && !pending && (task || dragEvent?.all_day === allDay);
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = valid ? "move" : "none";
    const target = `${allDay ? "day" : "time"}:${value}`;
    dragTarget = valid ? target : "";
    invalidDropTarget = valid ? "" : target;
    dragMessage = valid
      ? `Drop to ${dragMode === "resize" && !task ? "resize" : "schedule"} at ${value.replace("T", " ")} (${tz})`
      : pending ? "Wait for the current save to finish." : "Keep timed events in timed slots and all-day events in the all-day row.";
  }
  function leaveDrop(event: DragEvent) {
    if (event.relatedTarget instanceof Node && (event.currentTarget as HTMLElement).contains(event.relatedTarget)) return;
    dragTarget = "";
    invalidDropTarget = "";
    dragMessage = "";
  }
  function finishDrag() {
    dragTarget = "";
    invalidDropTarget = "";
    dragMessage = "";
    dragEvent = null;
    suppressClickUntil = Date.now() + 350;
  }
  function dragTask(task: Task, event: DragEvent) {
    if (!isActive || pending || !task.id) { event.preventDefault(); return; }
    rangeStart = null;
    rangeEnd = null;
    suppressClickUntil = Number.POSITIVE_INFINITY;
    dragEvent = null;
    dragMode = "move";
    event.dataTransfer?.setData("application/kitty-task", task.id);
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
  }
  function dayDropSlot(day: string, event: DragEvent) {
    const bounds = (event.currentTarget as HTMLElement).getBoundingClientRect();
    return slot(day, Math.max(0, Math.min(47, Math.floor((event.clientY - bounds.top) / bounds.height * 48))));
  }
  function blockDragClick(event: MouseEvent) {
    if (isActive && Date.now() < suppressClickUntil && event.target instanceof Element &&
        event.target.closest('[draggable="true"]')) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }
  async function saveTask(remove = false) {
    if (!taskEditor) return;
    const {
      id,
      revision,
      title,
      description,
      due,
      timezone,
      priority,
      status,
      tags,
      project,
      subject_id,
      calendar_id,
      repeat_frequency,
      repeat_interval,
      repeat_until,
    } = taskEditor;
    await changed(async () => {
      const result = await api<Task>("/calendar/tasks", {
        id,
        revision,
        title,
        description,
        due:
          taskDueTimed && taskDueInput
            ? due &&
              due.length > 10 &&
              taskDueInput === localInput(due, false, timezone)
              ? due
              : await resolveTime(taskDueInput, timezone)
            : taskDueInput,
        timezone,
        priority,
        status,
        tags,
        project,
        subject_id: subject_id ?? null,
        calendar_id,
        repeat_frequency: repeat_frequency || "",
        repeat_interval: repeat_frequency ? repeat_interval : 1,
        repeat_until: repeat_frequency ? repeat_until || "" : "",
        delete: remove,
      });
      taskEditor = null;
      return result;
    });
  }
  const repeatUnits: Record<string, string> = {
    DAILY: "day",
    WEEKLY: "week",
    MONTHLY: "month",
    YEARLY: "year",
  };
  const editingCompletedTask = $derived(
    !!taskEditor?.id &&
      data.tasks.some((t) => t.id === taskEditor?.id && t.status === "done"),
  );
  function repeatLabel(task: Task) {
    const unit = repeatUnits[task.repeat_frequency] || "period";
    const every =
      task.repeat_interval === 1
        ? `repeats every ${unit}`
        : `repeats every ${task.repeat_interval} ${unit}s`;
    return task.repeat_until ? `${every} until ${task.repeat_until}` : every;
  }
  async function taskStatus(task: Task, status: string) {
    await changed(async () => {
      const result = await api<Task>("/calendar/tasks", {
        id: task.id,
        revision: task.revision,
        status,
      });
      return result;
    });
  }
  async function toggleCalendar(c: Cal) {
    await run(async () => {
      await api("/calendar/calendars", {
        id: c.id,
        revision: c.revision,
        visible: !c.visible,
      });
      await load();
    });
  }
  async function saveCalendar(remove = false) {
    if (!calendarEditor) return;
    const body = { ...calendarEditor, visible: !!calendarEditor.visible };
    await run(async () => {
      await api(
        remove ? "/calendar/calendars/delete" : "/calendar/calendars",
        body,
      );
      calendarEditor = null;
      await load();
    });
  }
  $effect(() => {
    revision;
    anchor;
    view;
    tz;
    search;
    void load();
  });
  $effect(() => {
    const task = taskEditor;
    untrack(() => {
      if (!task) return;
      taskDueTimed = !!task.due && task.due.length > 10;
      taskDueInput = task.due
        ? localInput(task.due, !taskDueTimed, task.timezone)
        : "";
    });
  });
  /** Mind maps holding each calendar task, keyed by task ID. */
  let taskMaps = $state<Record<string, { id: number; title: string }[]>>({});
  async function loadTaskMaps() {
    try {
      const net = await api<{
        maps: { id: number; title: string }[];
        nodes: { id: string; maps: number[] }[];
      }>("/schema/network");
      const titles = new Map(net.maps.map((m) => [m.id, m]));
      const index: Record<string, { id: number; title: string }[]> = {};
      for (const node of net.nodes)
        if (node.id.startsWith("calendar_task:"))
          index[node.id.slice("calendar_task:".length)] = node.maps
            .map((id) => titles.get(id))
            .filter((m): m is { id: number; title: string } => !!m);
      taskMaps = index;
    } catch (e) {
      error = `Mind-map links unavailable: ${e instanceof Error ? e.message : String(e)}`;
    }
  }
  function mappedTask(taskId: string, map: { id: number; title: string }) {
    taskMaps[taskId] = [...(taskMaps[taskId] || []), map];
    notice = `Added to ${map.title}.`;
  }
  $effect(() => {
    if (!isActive) return;
    const labels: Record<string, string> = {
      tasks: "Task list",
      activity: "Activity",
      availability: "Availability",
      integrations: "Imports & Google",
    };
    pane.setTrail("calendar", [
      ...(section === "calendar" ? [] : [{ label: labels[section], onclick: () => (section = "calendar") }]),
      { label: dateTitle(selectedDate, { month: "short", day: "numeric", year: "numeric" }) },
    ], () => {
      section = "calendar";
      view = "week";
      chooseDate(parts(new Date().toISOString()).date);
    });
  });
  $effect(() => {
    if (!isActive) {
      rangeStart = null;
      rangeEnd = null;
      finishDrag();
    }
  });
  onMount(() => {
    window.addEventListener("click", blockDragClick, true);
    const syncTheme = () => (theme = getTheme(currentTheme()));
    window.addEventListener(THEME_EVENT, syncTheme);
    void loadTaskMaps();
    const clockTimer = setInterval(() => (clock = new Date().toISOString()), 60000);
    return () => {
      window.removeEventListener("click", blockDragClick, true);
      window.removeEventListener(THEME_EVENT, syncTheme);
      clearInterval(clockTimer);
      clearTimeout(flashTimer);
      pane.clearTrail("calendar");
    };
  });
</script>

<svelte:window
  onpointerup={finishRange}
  onpointercancel={() => { rangeStart = null; rangeEnd = null; }}
  onkeydown={(e) => {
    if (!isActive) return;
    if (e.key === "Escape") {
      editor = null;
      taskEditor = null;
      calendarEditor = null;
      rangeStart = null;
    }
    if (
      (e.ctrlKey || e.metaKey) &&
      e.key === "z" &&
      undoId &&
      !(
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      )
    ) {
      e.preventDefault();
      void run(async () => {
        await api("/calendar/undo", { id: undoId });
        undoId = null;
        await load();
      });
    }
  }}
/>

<section class="calendar-workspace" class:drawer-open={!!editor} aria-label="Calendar workspace">
  <header class="toolbar">
    <h1 title={`Times in ${tz}`}>Calendar</h1>
    <div class="header-actions">
      {#if undoId}<Button size="sm" disabled={pending > 0}
        onclick={() =>
          run(async () => {
            await api("/calendar/undo", { id: undoId });
            undoId = null;
            await load();
          })}>Undo last change</Button>{/if}
    </div>
  </header>
  <nav class="tabs" aria-label="Calendar sections">
    {#each [["calendar", "Calendar"], ["tasks", "Tasks"], ["activity", "Activity"], ["availability", "Availability"], ["integrations", "Imports & Google"]] as [key, title]}
      <Button size="sm" intent={section === key ? "primary" : "neutral"}
        aria-pressed={section === key}
        onclick={() => (section = key)}>{title}</Button>
    {/each}
  </nav>
  {#if error}<div role="alert" class="error">
      {error}<button aria-label="Dismiss error" onclick={() => (error = "")}
        >×</button
      >
    </div>{/if}
  {#if notice}<p role="status" class="notice">{notice}</p>{/if}
  <div class="search-toolbar">
    <label class="calendar-search"><span>Search</span><input type="search" bind:value={search}
      placeholder="Search events, tasks or tags" /></label>
    <Button size="sm" onclick={load} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</Button>
  </div>
  {#if dragMessage}<p class="drop-status" role="status">{dragMessage}</p>{/if}
  <details class="display-options">
    <summary>Calendars & display settings</summary>
    <div class="controls">
      <label>Display timezone<Select kind="timezone" label="Display timezone" bind:value={tz} /></label>
    </div>
  <div class="calendars">
    {#each data.calendars as c}<label style={`--calendar-color:${calendarColor(c)}`}
        ><input
          type="checkbox"
          checked={!!c.visible}
          onchange={() => toggleCalendar(c)}
        /><span class="dot"></span>{c.name}<button
          aria-label={`Edit ${c.name}`}
          onclick={() => (calendarEditor = { ...c })}>⚙</button
        ></label
      >{/each}<button
      onclick={() =>
        (calendarEditor = {
          name: "",
          color: nextSlot(data.calendars.length),
          subject_id: null,
          timezone: tz,
          visible: 1,
        })}>＋ Calendar</button
    >
  </div>
  </details>
  {#if section === "calendar"}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="calendar-layout" bind:this={calendarLayout} tabindex="-1" onkeydown={calendarKey}>
    <div class="calendar-main">
    {#if view === "month"}
      <div class="month-layout">
      <div class="month-grid" role="grid" aria-label="Month calendar">
        {#each days.slice(0, 7) as day}
          <div class="weekday-label" role="columnheader">{dateTitle(day, { weekday: "short" })}</div>
        {/each}
        {#each days as day}{@const dayEvents = eventsOn(day)}<div
            class="day-cell"
            class:selected={day === selectedDate}
            class:today={day === today}
            aria-label={`${dateTitle(day, { weekday: "long", month: "long", day: "numeric" })}, ${dayEvents.length === 1 ? "1 event" : `${dayEvents.length} events`}`}
            aria-selected={day === selectedDate}
            class:outside={day.slice(0, 7) !== anchor.slice(0, 7)}
            class:selecting={!!rangeStart &&
              rangeAllDay &&
              day >= [rangeStart, rangeEnd || rangeStart].sort()[0] &&
              day <= [rangeStart, rangeEnd || rangeStart].sort()[1]}
            role="gridcell"
            tabindex="0"
            onkeydown={(e) => {
              if (e.target === e.currentTarget) keyboardRange(day, true, e);
            }}
            onpointerdown={(e) => { if (e.target === e.currentTarget) beginRange(day, true, e); }}
            onpointerenter={() => extendRange(day)}
            class:drop-target={dragTarget === `day:${day}`}
            class:invalid-drop={invalidDropTarget === `day:${day}`}
            ondragleave={leaveDrop}
            ondragover={(e) => allowDrop(day, true, e)}
            ondrop={(e) => dropOn(day, true, e)}
          >
            <button class="day-head" onclick={() => (selectedDate = day)}
              aria-label={`Select ${day}`} aria-pressed={day === selectedDate}>{dateTitle(day, { day: "numeric" })}</button>
            <span class="dots" aria-hidden="true">{#each dayEvents.slice(0, 4) as e (e.id + e.occurrence)}<i style:background={paint(e)}></i>{/each}</span>
          </div>{/each}
      </div>
      <DayAgenda
        eyebrow={`${selectedDate === today ? "Today · " : ""}${dateTitle(selectedDate, { weekday: "short", month: "short", day: "numeric" })}`}
        items={[
          ...eventsOn(selectedDate).map((e): AgendaItem => ({
            key: `event:${e.id}:${e.occurrence}`,
            start: e.all_day ? "All day" : parts(e.start).time,
            end: e.all_day ? "" : parts(e.end).time,
            title: e.title + (e.rrule ? " ↻" : ""),
            place: e.location || data.calendars.find((c) => c.id === e.calendar_id)?.name || "",
            color: paint(e),
            open: () => openEvent(e),
            drag: (event) => drag(e, event),
            dragend: finishDrag,
          })),
          ...tasksOn(selectedDate).map((t): AgendaItem => ({
            key: `task:${t.id}`,
            start: t.due.length > 10 ? parts(t.due).time : "Task",
            end: "",
            title: t.title,
            place: t.status === "done" ? "Completed" : t.priority,
            color: taskColor(t) ?? "var(--muted)",
            task: true,
            open: () => (taskEditor = { ...t }),
            drag: (event) => dragTask(t, event),
            dragend: finishDrag,
          })),
        ]} />
      </div>
    {:else if view === "week" || view === "day"}
      <div class="time-scroll" use:scrollToMorning role="region" aria-label={view === "week" ? "Week calendar" : "Day calendar"}>
        <div class="time-grid" style={`--days:${days.length}`}>
          <div class="time-header">
            <div class="time-zone" title={tz}>{tz}</div>
            {#each days as day}
              <div class="time-heading" class:today={day === today} class:selected={day === selectedDate}>
                <button class="time-date" aria-pressed={day === selectedDate}
                  aria-current={day === today ? "date" : undefined}
                  aria-label={`Select ${dateTitle(day, { weekday: "long", month: "long", day: "numeric" })}`}
                  onclick={() => (selectedDate = day)}>
                  <span>{dateTitle(day, { weekday: "short" })}</span>
                  <strong>{dateTitle(day, { day: "numeric" })}</strong>
                  {#if day === today}<small>Today</small>{/if}
                </button>
              </div>
            {/each}
            <div class="all-day-label">All day<br /><span>& tasks</span></div>
            {#each days as day}
              <div class="all-day-cell" class:selected={day === selectedDate}
                class:drop-target={dragTarget === `day:${day}`}
                class:invalid-drop={invalidDropTarget === `day:${day}`}
                ondragleave={leaveDrop}
                role="group" aria-label={`All-day events and tasks on ${day}`}
                ondragover={(event) => allowDrop(day, true, event)}
                ondrop={(event) => dropOn(day, true, event)}>
                <button class="all-day-add" onclick={() => void openQuick(day, addDays(day, 1))}
                  aria-label={`Create all-day event on ${day}`}>＋</button>
                <div class="all-day-items">
                  {#each eventsOn(day).filter((e) => e.all_day) as e}
                    <button class="event-chip all-day-event" style={`--calendar-color:${paint(e)}`}
                      draggable="true" ondragstart={(event) => drag(e, event)} ondragend={finishDrag}
                      oncontextmenu={(event) => void deleteEventAtPointer(event, e)}
                      onclick={() => { selectedDate = day; openEvent(e); }} title={e.title}>{e.title}</button>
                  {/each}
                  {#each tasksOn(day).filter((task) => task.due.length === 10) as t}
                    <button class="task-chip" class:completed={t.status === "done"} style:--task-color={taskColor(t)}
                      draggable="true" ondragstart={(event) => dragTask(t, event)} ondragend={finishDrag}
                      oncontextmenu={(event) => void deleteTaskAtPointer(event, t)}
                      onclick={() => { selectedDate = day; taskEditor = { ...t }; }}
                      title={t.title}>{t.status === "done" ? "✓" : "☐"} {t.title}</button>
                  {/each}
                </div>
              </div>
            {/each}
          </div>
          <div class="time-axis" aria-hidden="true">
            {#each hours as halfHour}
              <div class="time-label">{halfHour % 2 === 0 ? `${String(halfHour / 2).padStart(2, "0")}:00` : ""}</div>
            {/each}
          </div>
          {#each days as day}
            <div class="time-day" class:today={day === today} class:selected={day === selectedDate}
              aria-label={dateTitle(day, { weekday: "long", month: "long", day: "numeric" })} role="group"
              ondragover={(event) => allowDrop(dayDropSlot(day, event), false, event)}
              ondragleave={leaveDrop}
              ondrop={(event) => dropOn(dayDropSlot(day, event), false, event)}>
              {#each hours as halfHour}
                <div class="time-cell" class:hour-start={halfHour % 2 === 0}
                  role="group" aria-label={`${day} ${slot(day, halfHour).slice(-5)}`}
                  class:drop-target={dragTarget === `time:${slot(day, halfHour)}`}
                  class:invalid-drop={invalidDropTarget === `time:${slot(day, halfHour)}`}
                  ondragleave={leaveDrop}
                  ondragover={(e) => allowDrop(slot(day, halfHour), false, e)}
                  ondrop={(e) => dropOn(slot(day, halfHour), false, e)}>
                  <button class="slot-button" data-day={day} data-slot={halfHour}
                    tabindex={day === selectedDate && halfHour === focusedSlot ? 0 : -1}
                    class:selecting={!!rangeStart && !rangeAllDay &&
                      slot(day, halfHour) >= [rangeStart, rangeEnd || rangeStart].sort()[0] &&
                      slot(day, halfHour) <= [rangeStart, rangeEnd || rangeStart].sort()[1]}
                    aria-label={`Create event ${day} ${slot(day, halfHour).slice(-5)}`}
                    onfocus={() => { selectedDate = day; focusedSlot = halfHour; }}
                    onpointerdown={(e) => beginRange(slot(day, halfHour), false, e)}
                    onpointerenter={() => extendRange(slot(day, halfHour))}
                    onkeydown={(e) => slotKey(day, halfHour, e)}
                    onclick={(e) => {
                      if (e.detail === 0 && !editor) void openQuick(slot(day, halfHour));
                    }}></button>
                  {#each weekTasks[day][halfHour] as task}
                    <button class="task-chip timed-task" class:completed={task.status === "done"} style:--task-color={taskColor(task)}
                      style={`top:${parts(task.due).minute % 30 / 30 * 100}%`}
                      draggable="true" ondragstart={(event) => dragTask(task, event)} ondragend={finishDrag}
                      onclick={() => (taskEditor = { ...task })}
                      oncontextmenu={(event) => void deleteTaskAtPointer(event, task)}
                      title={`${task.title} · ${parts(task.due).time}`}>
                      {parts(task.due).time} · ☐ {task.title}
                    </button>
                  {/each}
                </div>
              {/each}
              {#if day === today}
                <div class="now-line" style={`top:${currentMinute / 1440 * 100}%`} role="img"
                  aria-label={`Now ${parts(clock).time}`}><span class="bloom" aria-hidden="true">✿</span></div>
              {/if}
              {#each weekEvents[day] as placement}
                {@const e = placement.event}
                <div class="event-chip timed" draggable="true" role="group" aria-label={e.title}
                  style={`${blockStyle(e)};top:${placement.start / 1440 * 100}%;height:${Math.min(1440 - placement.start, Math.max(30, placement.end - placement.start)) / 1440 * 100}%;left:${placement.column / placement.columns * 100}%;width:${100 / placement.columns}%`}
                  ondragstart={(event) => drag(e, event)} ondragend={finishDrag}>
                  <button class="timed-content" title={`${e.title} · ${label(e.start)} – ${label(e.end)}`}
                    oncontextmenu={(event) => void deleteEventAtPointer(event, e)}
                    aria-label={`Edit ${e.title}, ${label(e.start)} – ${label(e.end)}`}
                    onclick={() => { selectedDate = day; openEvent(e); }}>
                    <strong>{e.title}</strong>
                    <span>{parts(e.start).time}–{parts(e.end).time}</span>
                  </button>
                  <button class="resize" draggable="true"
                    ondragstart={(event) => { event.stopPropagation(); drag(e, event, "resize"); }}
                    aria-label={`Resize ${e.title}`}>↔</button>
                </div>
              {/each}
            </div>
          {/each}
        </div>
      </div>
    {:else if view === "year"}
      <YearHeatmap year={Number(anchor.slice(0, 4))} busy={yearMinutes} {today}
        onopen={(day) => { chooseDate(day); view = "day"; }} />
    {:else if view === "plan"}
      <DayPlan
        eyebrow={`${anchor === today ? "Today · " : ""}${dateTitle(anchor, { weekday: "short", month: "short", day: "numeric" })}`}
        now={anchor === today ? currentMinute : null}
        busy={pending > 0}
        events={timedEvents(anchor).map((p) => ({ key: `${p.event.id}:${p.event.occurrence}`, title: p.event.title,
          start: p.start, end: p.end, column: p.column, columns: p.columns, style: blockStyle(p.event), open: () => openEvent(p.event) }))}
        tasks={planTasks.map(({ task, at }) => ({ id: task.id!, title: task.title, priority: task.priority,
          done: task.status === "done", at, color: taskColor(task), open: () => (taskEditor = { ...task }) }))}
        onplan={(id) => void planTask(id)}
        onunplan={(id) => void unplanTask(id)}
        ontoggle={(id) => { const task = data.tasks.find((t) => t.id === id); if (task) void taskStatus(task, task.status === "done" ? "todo" : "done"); }} />
    {:else}
      <div class="agenda" role="region" aria-label="Agenda">
        {#each days as day}{#if eventsOn(day).length || tasksOn(day).length}<section
            >
              <h2><button class="agenda-date" onclick={() => (selectedDate = day)}>{dateTitle(day, { weekday: "long", month: "short", day: "numeric" })}</button></h2>
              {#each eventsOn(day) as e}<button
                  class="agenda-event"
                  style={`--calendar-color:${paint(e)}`}
                  onclick={() => openEvent(e)}
                  ><span
                    >{e.all_day
                      ? "All day"
                      : `${label(e.start)} → ${label(e.end)}`}</span
                  ><strong>{e.title}</strong><span>{e.location}</span></button
                >{/each}{#each tasksOn(day) as t}<button
                  class="agenda-event"
                  style:--calendar-color={taskColor(t)}
                  onclick={() => (taskEditor = { ...t })}
                  ><span>Task · {t.priority} · {t.status}</span><strong
                    >{t.title}</strong
                  ></button
                >{/each}
            </section>{/if}{/each}{#if !data.events.length && !data.tasks.some((t) => t.due)}<p
          >
            Nothing scheduled.
          </p>{/if}
      </div>
    {/if}
    <RangePill heading={rangeHeading} {view} date={anchor} flash={shortcutFlash}
      onview={setView} onstep={navigate} ontoday={() => chooseDate(today)} ondate={chooseDate}>
      {#snippet add()}
        <CalendarQuickAdd label="Add event or task" round {today} at={quickSlot} bind:kind={quickKind} bind:open={quickOpen} onsave={quickSave} />
      {/snippet}
    </RangePill>
    </div>
    <aside class="selected-plan" aria-label="Selected date plan">
      <MiniMonth selected={selectedDate} {today} refresh={loadVersion} {loadBusy} onpick={chooseDate} />
      <Button size="sm" aria-expanded={taskTrayOpen} aria-controls={`${pane.id}-calendar-task-tray`}
        onclick={() => (taskTrayOpen = !taskTrayOpen)}>{taskTrayOpen ? "Hide task tray" : "Open task tray"} · {trayTasks.length}</Button>
      {#if taskTrayOpen}
        <section id={`${pane.id}-calendar-task-tray`} class="task-tray" aria-label="Tasks to schedule"
          title="Drag a task onto a time slot to schedule it">
          <h3>Tasks to schedule</h3>
          <div class="tray-list">
            {#each trayTasks as task}
              <button class="tray-task" draggable="true" style:--task-color={taskColor(task)}
                ondragstart={(event) => dragTask(task, event)} ondragend={finishDrag}
                onclick={() => (taskEditor = { ...task })}>
                <strong>{task.title}</strong>
                <span>{task.due ? label(task.due, task.due.length === 10) : "Unscheduled"} · {task.priority}</span>
              </button>
            {:else}<p class="hint">No unscheduled or selected-day tasks.</p>{/each}
          </div>
        </section>
      {/if}
      <Panel title={dateTitle(selectedDate, { weekday: "long", month: "short", day: "numeric" })}
        readout="DAY PLAN">
        {#if view !== "month"}
        {#each eventsOn(selectedDate) as e}
          <button class="plan-event" style={`--calendar-color:${paint(e)}`} onclick={() => openEvent(e)}
            oncontextmenu={(event) => void deleteEventAtPointer(event, e)}>
            <span>{e.all_day ? "All day" : `${parts(e.start).time} – ${parts(e.end).time}`}</span>
            <strong>{e.title}</strong>
            {#if e.location}<span>{e.location}</span>{/if}
          </button>
        {/each}
        {#each tasksOn(selectedDate) as t}
          <div class="plan-task" class:completed={t.status === "done"}>
            <input type="checkbox" checked={t.status === "done"} disabled={pending > 0}
              aria-label={`${t.status === "done" ? "Reopen" : "Complete"} ${t.title}`}
              onchange={() => taskStatus(t, t.status === "done" ? "todo" : "done")} />
            <button oncontextmenu={(event) => void deleteTaskAtPointer(event, t)} onclick={() => (taskEditor = { ...t })}>
              <strong>{t.title}</strong><span>{t.due.length > 10 ? `${parts(t.due).time} · ` : ""}{t.status === "done" ? "Completed" : t.status === "doing" ? "In progress" : t.status === "cancelled" ? "Cancelled" : "To do"}{t.repeat_frequency ? " · Repeating" : ""}</span>
            </button>
            {#if t.id}{@const id = t.id}<div class="plan-task-map"><TaskMapAction task={{ id, title: t.title }} inMaps={taskMaps[id]}
              placement="left" onadded={(m) => mappedTask(id, m)} /></div>{/if}
          </div>
        {/each}
        {#if !eventsOn(selectedDate).length && !tasksOn(selectedDate).length}
          <div class="day-empty"><p class="hint">Nothing planned.</p></div>
        {/if}
        {/if}
      </Panel>
    </aside>
    </div>
  {:else if section === "tasks"}
    <div class="task-columns">
      {#each ["all"] as status}<section
          class="task-column"
          aria-label={status === "all" ? "All tasks" : `${status} tasks`}
          ondragover={(e) => e.preventDefault()}
          ondrop={(e) => {
            e.preventDefault();
            const id = e.dataTransfer?.getData("application/kitty-task");
            const t = data.tasks.find((t) => t.id === id);
            if (t && status !== "all") void taskStatus(t, status);
          }}
        >
          <h2>{status === "all" ? "All tasks" : status}</h2>
          {#each data.tasks.filter((t) => status === "all" || t.status === status) as t}<article
              class="task-card"
              draggable="true"
              ondragstart={(e) =>
                e.dataTransfer?.setData("application/kitty-task", t.id || "")}
            >
              <div>
                <input type="checkbox" checked={t.status === "done"} disabled={pending > 0}
                  aria-label={`${t.status === "done" ? "Reopen" : "Complete"} ${t.title}`}
                  onchange={() => taskStatus(t, t.status === "done" ? "todo" : "done")} />
                <button
                  class="task-title"
                  oncontextmenu={(event) => void deleteTaskAtPointer(event, t)}
                  onclick={() => (taskEditor = { ...t })}>{t.title}</button
                ><Select kind="list" label={`Status for ${t.title}`} disabled={pending > 0} value={t.status}
                  options={statusOptions} onchange={(value) => taskStatus(t, value)} />
              </div>
              <p>
                {t.due ? label(t.due, t.due.length === 10) : "No due date"} · {t.priority}
                {t.project ? "· " + t.project : ""}
                {t.repeat_frequency ? "· " + repeatLabel(t) : ""}
              </p>
              {#if t.id}{@const id = t.id}<TaskMapAction task={{ id, title: t.title }} inMaps={taskMaps[id]}
                onadded={(m) => mappedTask(id, m)} />{/if}
              <div class="tags">
                {#each t.tags as tag}<span>{tag}</span>{/each}
              </div>
            </article>{/each}
          <CalendarQuickAdd label="Add task" kind="task" {today} at={{ start: selectedDate }} onsave={quickSave} />
        </section>{/each}
    </div>
  {:else if section === "activity"}
    <h2 title="From saved records only">Activity</h2>
    <div class="activity-graph">
      {#each data.activity as a}<div class="activity-row">
          <span>{a.date}</span>
          <div>
            {#each Object.entries(a).filter(([key]) => key !== "date") as [key, value]}<span
                class="activity-bar"
                style={`width:${Math.min(Number(value) * 15, 500)}px`}
                title={`${key}: ${value}`}
                >{key.replaceAll("_", " ")} {value}</span
              >{/each}
          </div>
        </div>{/each}{#if !data.activity.length}<p>
          No recorded activity in this range.
        </p>{/if}
    </div>
  {:else if section === "availability"}
    <h2 title="Visible calendars; free events excluded">Free / busy</h2>
    <h3>Overlaps</h3>
    {#each data.conflicts as c}<p class="conflict">
        {data.events.find((e) => e.id === c.event_id)?.title} overlaps {c.with
          .map((id) => data.events.find((e) => e.id === id)?.title)
          .join(", ")} · {label(c.start)} → {label(c.end)}
      </p>{/each}{#if !data.conflicts.length}<p>No busy-event overlaps.</p>{/if}
    <h3>Free intervals</h3>
    {#each data.free as free}<div class="free-row">
        <span>{label(free.start)} → {label(free.end)}</span><button
          onclick={() => void openQuick(localInput(free.start, false, tz), localInput(free.end, false, tz))}>Schedule here</button
        >
      </div>{/each}
  {/if}
  <CalendarIntegrations visible={section === "integrations"} active={isActive} {demo} {tz} {anchor} {theme} {paint}
    {run} {load} onerror={(message) => (error = message)} onnotice={(message) => (notice = message)}
    onundo={(id) => (undoId = id)} />
</section>

{#if editor && isActive}
  <EditDrawer title="Event details" accent={paint(editor)} closeLabel="Close event editor"
    meta={`${data.calendars.find((c) => c.id === editor?.calendar_id)?.name ?? "Calendar"} · ${startInput ? dateTitle(startInput.slice(0, 10), { weekday: "short", month: "short", day: "numeric" }) : ""}`}
    onclose={() => (editor = null)}>
      {#if error}<p class="error" role="alert">{error}</p>{/if}
      <form class="drawer-form"
        onsubmit={(e) => {
          e.preventDefault();
          void saveEvent();
        }}
      >
        <input class="drawer-title" aria-label="Title" required maxlength="500" value={titleDraft}
          placeholder="lunch with Mira fri 12:30" spellcheck="false" oninput={(e) => typeTitle(e.currentTarget.value)} />
        {#if quick}
          <QuickUnderstood text={titleDraft} spans={quick.spans} title={editor.title}
            when={`${dateTitle(startInput.slice(0, 10), { weekday: "short", month: "short", day: "numeric" })}${editor.all_day ? " · all day" : ` · ${startInput.slice(11, 16)}–${endInput.slice(11, 16)}`}`}
            where={quick.where} repeat={quick.repeat} />
        {/if}
        {#if editor.rrule}
          <label title="Choose before editing; changing it reloads saved values">Edit recurrence scope<Select label="Edit recurrence scope"
            bind:value={editScope} onchange={changeScope}
            options={[{ value: "this", label: "This one" }, { value: "future", label: "This and future" }, { value: "all", label: "Entire series" }]} /></label>
        {/if}
        {#if !editor.all_day}
          <div class="steppers" role="group" aria-label="Time">
            <span class="stepper"><button type="button" aria-label="Earlier start" onclick={() => shiftTime(-15, "start")}>−</button><b>{startInput.slice(11, 16)}</b><button type="button" aria-label="Later start" onclick={() => shiftTime(15, "start")}>+</button></span>
            <span class="arrow" aria-hidden="true">→</span>
            <span class="stepper"><button type="button" aria-label="Earlier end" onclick={() => shiftTime(-15, "end")}>−</button><b>{endInput.slice(11, 16)}</b><button type="button" aria-label="Later end" onclick={() => shiftTime(15, "end")}>+</button></span>
          </div>
        {/if}
        <div class="drawer-dates">
          <CalendarDateTime label="Start" timed={!editor.all_day} bind:value={startInput} required />
          <CalendarDateTime label={editor.all_day ? "End (following)" : "End"} timed={!editor.all_day} bind:value={endInput} required />
          <label class="inline"><input type="checkbox" bind:checked={editor.all_day}
            onchange={() => {
              if (editor) {
                startInput = editor.all_day ? startInput.slice(0, 10) : startInput + "T09:00";
                const endDay = endInput.slice(0, 10);
                endInput = editor.all_day
                  ? endInput.slice(11, 16) === "00:00" ? endDay : addDays(endDay, 1)
                  : addDays(endDay, -1) + "T10:00";
              }
            }} />All-day / multi-day</label>
        </div>
        <fieldset class="drawer-field" disabled={!!editor.rrule && editScope === "this"}>
          <legend class="eyebrow">calendar</legend>
          <div class="swatches">
            {#each data.calendars as c (c.id)}
              <button type="button" class="swatch" style:--sw={calendarColor(c)} title={c.name} aria-label={c.name}
                aria-pressed={editor.calendar_id === c.id} onclick={() => editor && (editor.calendar_id = c.id)}></button>
            {/each}
          </div>
        </fieldset>
        <ColorField subjects={data.subjects} bind:subject={editor.subject_id} bind:color={editor.color} inherited={calendarDefault} />
        <label class="drawer-field"><span class="eyebrow">place</span><input bind:value={editor.location} placeholder="Add a place" /></label>
        <div class="drawer-field" role="group" aria-label="Remind me">
          <span class="eyebrow">remind me</span>
          <div class="chips">
            {#each reminderChips as [minutes, text] (minutes)}
              <button type="button" aria-pressed={reminderList.includes(minutes)} aria-label={text}
                onclick={() => toggleReminder(minutes)}>{text.replace(" minutes", "m").replace(" hour", "h").replace(" day", "d")}</button>
            {/each}
          </div>
        </div>
        <details class="editor-options">
          <summary>Repeat & time settings{editor.rrule ? " · Repeating event" : ""}</summary>
          <div class="form-grid">
            <label>Timezone<input bind:value={editor.timezone} required /></label>
            <label title="For a fall-back time that happens twice; DST gaps are rejected">DST repeated time<Select label="DST repeated time"
              value={String(fold)} onchange={(value) => { fold = Number(value); foldChanged = true; }}
              options={[{ value: "0", label: "First" }, { value: "1", label: "Second" }]} /></label>
          </div>
          <label title="Empty for one event. UNTIL in UTC, e.g. UNTIL=20271231T235959Z">Repeat rule (RRULE)<input bind:value={editor.rrule}
            disabled={editScope === "this"} placeholder="FREQ=WEEKLY;BYDAY=MO,WE;COUNT=12" /></label>
          <label>Excluded starts, one per line (date or aware ISO timestamp)<textarea rows="2" bind:value={exdatesText}
            disabled={editScope === "this"}></textarea></label>
        </details>
        <details class="editor-options">
          <summary>Notes, privacy & custom reminders</summary>
          <label>Notes<textarea rows="3" bind:value={editor.description}></textarea></label>
          <div class="form-grid">
            <label>URL<input type="url" bind:value={editor.url} /></label>
            <label>Privacy<Select label="Privacy" kind="list" bind:value={editor.privacy}
              options={[{ value: "default", label: "Default" }, { value: "public", label: "Public" }, { value: "private", label: "Private" }, { value: "confidential", label: "Confidential" }]} /></label>
            <label><input type="checkbox" bind:checked={editor.busy} />Blocks availability (busy)</label>
          </div>
          <label>Reminder minutes before start<input bind:value={remindersText} placeholder="10,60,1440" /></label>
        </details>
        <details class="editor-options">
          <summary>Attendees, attachments & Google properties</summary>
        <label title="RSVP: needsAction, accepted, declined or tentative. Local edits never email anyone."
          >Attendees: email,name,RSVP,optional (true/false) per line<textarea
            rows="3"
            bind:value={attendeesText}
            placeholder="person@example.com,Person,needsAction"
          ></textarea></label
        >
        <label
          >Attachment links: title | https://URL per line<textarea
            rows="2"
            bind:value={attachmentsText}
          ></textarea></label
        ><label
          title="Special types need an eligible Google account">Google special event type<Select
            label="Google special event type"
            kind="list"
            bind:value={editor.event_type}
            options={[{ value: "default", label: "Normal" }, { value: "focusTime", label: "Focus time" }, { value: "outOfOffice", label: "Out of office" }, { value: "workingLocation", label: "Working location" }]}
            onchange={() => {
              const defaults: Record<string, unknown> = {
                default: {},
                focusTime: {
                  focusTimeProperties: {
                    autoDeclineMode: "declineNone",
                    chatStatus: "doNotDisturb",
                  },
                },
                outOfOffice: {
                  outOfOfficeProperties: { autoDeclineMode: "declineNone" },
                },
                workingLocation: {
                  workingLocationProperties: {
                    type: "homeOffice",
                    homeOffice: {},
                  },
                },
              };
              googleOptionsText = JSON.stringify(
                defaults[editor?.event_type || "default"],
                null,
                2,
              );
            }}
          /></label
        >
        <label
          >Google properties (exact JSON; account eligibility applies)<textarea
            rows="4"
            bind:value={googleOptionsText}
            spellcheck="false"
          ></textarea></label
        >
        </details>
        {#if editor.source_key}<p>
            Linked source: <code>{editor.source_key}</code>. Manual changes
            protect this copy from source refresh; they never rewrite original
            dates.{#if editor.source_url && safeUrl(editor.source_url)}
              <a
                href={safeUrl(editor.source_url)}
                target="_blank"
                rel="noreferrer">Open source</a
              >{/if}
          </p>{/if}
        <footer>
          <Button type="submit" intent="primary" disabled={pending > 0}>{pending > 0 ? "Saving…" : "Save changes"}</Button>
          <Button type="button" disabled={pending > 0} onclick={() => (editor = null)}>Cancel</Button>
          <Button type="button" disabled={pending > 0} onclick={() => {
            if (window.confirm("Delete the selected occurrence/scope? You can undo if it has not changed.")) void saveEvent(true);
          }}>Delete selected scope</Button>
        </footer>
      </form>
  </EditDrawer>{/if}
{#if taskEditor?.id && isActive}{@const id = taskEditor.id}
  <EditDrawer title="Task details" accent={taskColor(taskEditor) ?? ""} closeLabel="Close task editor"
    meta={`${data.calendars.find((c) => c.id === taskEditor?.calendar_id)?.name ?? "Calendar"}${taskDueInput ? ` · ${dateTitle(taskDueInput.slice(0, 10), { weekday: "short", month: "short", day: "numeric" })}` : ""}`}
    onclose={() => (taskEditor = null)}>
      <div class="task-map-row">
        <TaskMapAction task={{ id, title: taskEditor.title }} inMaps={taskMaps[id]} onadded={(m) => mappedTask(id, m)} />
      </div>
      {#if error}<p class="error" role="alert">{error}</p>{/if}
      <form class="drawer-form"
        onsubmit={(e) => {
          e.preventDefault();
          void saveTask();
        }}
      >
        <input class="drawer-title" aria-label="Task title" required bind:value={taskEditor.title} />
        <label class="inline"><input type="checkbox" bind:checked={taskDueTimed} onchange={() => {
          taskDueInput = taskDueInput ? taskDueTimed ? taskDueInput + "T09:00" : taskDueInput.slice(0, 10) : "";
        }} />Include due time</label>
        <div class="drawer-dates">
          <CalendarDateTime label="Due" timed={taskDueTimed} bind:value={taskDueInput} />
        </div>
        <div class="form-grid">
          <label>Calendar<Select label="Calendar" kind="list" bind:value={taskEditor.calendar_id}
            options={data.calendars.map((c) => ({ value: c.id, label: c.name, color: calendarColor(c) }))} /></label>
          {#if data.subjects.length}<label>Study subject<Select label="Study subject" kind="list" value={String(taskEditor.subject_id ?? "")}
            onchange={(value) => taskEditor && (taskEditor.subject_id = value ? Number(value) : null)}
            options={[{ value: "", label: "No subject" }, ...data.subjects.map((s) => ({ value: String(s.id), label: s.name, color: s.color }))]} /></label>{/if}
        </div>
        <details class="editor-options">
          <summary>Notes, priority & repeat</summary>
          <label>Notes<textarea rows="3" bind:value={taskEditor.description}></textarea></label>
          <div class="form-grid">
            <label>Timezone<Select kind="timezone" label="Timezone" bind:value={taskEditor.timezone} /></label>
            <label>Priority<Select label="Priority" bind:value={taskEditor.priority}
              options={[{ value: "low", label: "Low" }, { value: "normal", label: "Normal" }, { value: "high", label: "High" }, { value: "urgent", label: "Urgent" }]} /></label>
            <label>Status<Select label="Status" kind="list" bind:value={taskEditor.status} options={statusOptions} /></label>
            <label>Project<input bind:value={taskEditor.project} /></label>
          </div>
          <div class="form-grid">
            <label title={editingCompletedTask ? "Completed history: change repetition on the next pending task"
              : "Needs a due date. Completing creates the next one; repetition stays local"}
              >Repeat<Select label="Repeat" kind="list" bind:value={taskEditor.repeat_frequency} disabled={editingCompletedTask}
                options={[{ value: "", label: "One-off task" }, { value: "DAILY", label: "Daily" }, { value: "WEEKLY", label: "Weekly" }, { value: "MONTHLY", label: "Monthly" }, { value: "YEARLY", label: "Yearly" }]} /></label>
            {#if taskEditor.repeat_frequency}<label
                >Every ({repeatUnits[taskEditor.repeat_frequency]}s)<input
                  type="number"
                  min="1"
                  step="1"
                  bind:value={taskEditor.repeat_interval}
                  required
                  disabled={editingCompletedTask}
                /></label
              >
              <label
                >Repeat until (inclusive; optional)<input
                  type="date"
                  bind:value={taskEditor.repeat_until}
                  disabled={editingCompletedTask}
                /></label
              >{/if}
          </div>
          <label
            >Tags (comma-separated)<input
              value={taskEditor.tags.join(", ")}
              onchange={(e) => {
                if (taskEditor)
                  taskEditor.tags = e.currentTarget.value
                    .split(",")
                    .map((t) => t.trim())
                    .filter(Boolean);
              }}
            /></label
          >
        </details>
        <footer>
          <Button type="submit" intent="primary" disabled={pending > 0}>{pending > 0 ? "Saving…" : "Save changes"}</Button>
          <Button type="button" disabled={pending > 0} onclick={() => (taskEditor = null)}>Cancel</Button>
          <Button type="button" disabled={pending > 0} onclick={() => {
            if (taskEditor) { taskEditor.status = taskEditor.status === "done" ? "todo" : "done"; void saveTask(); }
          }}>{taskEditor.status === "done" ? "Reopen task" : "Complete task"}</Button>
          <Button type="button" disabled={pending > 0} onclick={() => saveTask(true)}>Delete task</Button>
        </footer>
      </form>
  </EditDrawer>{/if}
{#if calendarEditor && isActive}<div class="modal-backdrop" role="presentation">
    <dialog
      use:modal
      oncancel={() => (calendarEditor = null)}
      class="dialog small"
      aria-modal="true"
      aria-labelledby={`${pane.id}-calendar-editor-title`}
      tabindex="-1"
    >
      <header>
        <h2 id={`${pane.id}-calendar-editor-title`}>Calendar settings</h2>
        <button
          aria-label="Close calendar settings"
          onclick={() => (calendarEditor = null)}>×</button
        >
      </header>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          if (calendarEditor) void saveCalendar();
        }}
      >
        <label>Name<input bind:value={calendarEditor.name} required /></label
        ><ColorField plain subjects={data.subjects} bind:subject={calendarEditor.subject_id}
          bind:color={() => calendarEditor?.color, (value) => calendarEditor && (calendarEditor.color = value ?? undefined)} />
        <label
          >Timezone<input
            bind:value={calendarEditor.timezone}
            required
          /></label
        ><label
          ><input
            type="checkbox"
            checked={!!calendarEditor.visible}
            onchange={(e) => {
              if (calendarEditor)
                calendarEditor.visible = e.currentTarget.checked ? 1 : 0;
            }}
          />Visible</label
        >
        <footer>
          <button
            type="submit"
            onclick={() => {
              if (calendarEditor)
                calendarEditor = {
                  ...calendarEditor,
                  visible: !!calendarEditor.visible as unknown as number,
                };
            }}>Save calendar</button
          >{#if calendarEditor.id && calendarEditor.id !== "personal"}<button
              type="button"
              class="danger"
              onclick={() => saveCalendar(true)}
              >Delete empty calendar locally</button
            >{/if}<button type="button" onclick={() => (calendarEditor = null)}
            >Cancel</button
          >
        </footer>
      </form>
    </dialog>
  </div>{/if}

<style>
  .calendar-workspace {
    display: flex;
    flex-direction: column;
    gap: 1rem;
    min-width: 0;
    color: var(--ink);
  }
  h1,
  h2,
  h3,
  p {
    margin: 0 0 0.65rem;
  }
  h1 {
    font-size: 1.6rem;
  }
  h2 {
    font-size: 1.1rem;
  }
  h3 {
    margin-top: 1.2rem;
  }
  button,
  input,
  textarea {
    font: inherit;
    color: var(--ink);
  }
  button {
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 0.45rem;
    padding: 0.45rem 0.65rem;
    cursor: pointer;
  }
  button:disabled {
    opacity: 0.45;
    cursor: not-allowed;
  }
  button:hover:not(:disabled) {
    border-color: var(--accent);
  }
  button:focus-visible,
  input:focus-visible,
  textarea:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  input,
  textarea {
    border: 1px solid var(--border);
    background: var(--surface);
    border-radius: 0.4rem;
    padding: 0.5rem;
    max-width: 100%;
    box-sizing: border-box;
  }
  input[type="checkbox"] {
    accent-color: var(--accent);
  }
  label {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }
  form > label {
    display: flex;
    align-items: stretch;
    flex-direction: column;
    margin: 0.75rem 0;
  }
  textarea {
    resize: vertical;
  }
  .toolbar,
  .controls,
  .tabs,
  .calendars,
  footer {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    flex-wrap: wrap;
  }
  .toolbar h1 {
    margin-right: auto;
  }
  .hint {
    color: var(--muted);
    font-size: 0.85rem;
  }
  .tabs {
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.6rem;
  }
  .header-actions,
  .search-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.55rem;
  }
  .header-actions {
    margin-left: auto;
  }
  .calendar-search {
    flex: 1;
    min-width: 220px;
    max-width: 430px;
    font-size: 0.85rem;
  }
  .calendar-search input {
    width: 100%;
    min-width: 0;
  }
  .display-options {
    margin: 0;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.75rem;
    font-size: 0.85rem;
  }
  .display-options > .controls,
  .display-options > .calendars {
    margin-top: 0.8rem;
  }
  .calendar-layout {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 260px;
    align-items: start;
    gap: 1rem;
  }
  .calendar-main,
  .selected-plan {
    min-width: 0;
  }
  .calendar-main {
    border-radius: 0.7rem;
    border: 1px solid var(--border);
  }
  .plan-event {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    text-align: left;
    width: 100%;
    margin: 0.5rem 0;
    padding: 0.75rem;
    border-left: 3px solid var(--calendar-color);
  }
  .plan-event span,
  .plan-task span {
    color: var(--muted);
    font-size: 0.75rem;
  }
  .plan-task {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: start;
    gap: 0.5rem;
    margin-top: 0.75rem;
  }
  .plan-task-map { grid-column: 2; justify-self: start; }
  .plan-task input {
    margin-top: 0.4rem;
  }
  .plan-task > button {
    display: flex;
    flex: 1;
    flex-direction: column;
    align-items: start;
    gap: 0.3rem;
    min-width: 0;
    text-align: left;
    overflow-wrap: anywhere;
  }
  .completed strong,
  .task-chip.completed {
    text-decoration: line-through;
    color: var(--muted);
  }
  .day-empty {
    padding: 1.2rem 0 0.4rem;
    font-size: 0.85rem;
  }
  .day-empty p {
    margin-top: 0.4rem;
    line-height: 1.6;
  }
  .time-date,
  .agenda-date {
    display: block;
    border: 0;
    background: transparent;
    text-align: left;
    padding: 0.25rem 0;
    font-weight: 600;
  }
  .dot {
    width: 0.65rem;
    height: 0.65rem;
    border-radius: 50%;
    background: var(--calendar-color);
  }
  .calendars label {
    background: var(--surface);
    padding: 0.2rem 0.4rem;
    border: 1px solid var(--border);
    border-radius: 0.4rem;
  }
  .calendars button {
    padding: 0.2rem 0.4rem;
  }
  .error,
  .notice {
    padding: 1rem;
    border-radius: 0.65rem;
    border: 1px solid var(--border);
    background: var(--surface);
  }
  .error {
    color: var(--warning);
    display: flex;
    justify-content: space-between;
  }
  .month-layout {
    display: grid;
    grid-template-columns: minmax(0, 1.25fr) minmax(220px, 1fr);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
  }
  .month-grid {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    align-content: start;
    gap: 2px;
    padding: 4px 8px 8px;
  }
  .day-cell {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    min-height: 56px;
    min-width: 0;
    border-radius: 10px;
    cursor: pointer;
  }
  .day-cell:hover {
    background: color-mix(in srgb, var(--ink) 5%, transparent);
  }
  .day-cell.selected {
    background: var(--ink);
  }
  .day-cell.selected .day-head {
    color: var(--solid-void);
  }
  .day-cell.today .day-head {
    color: var(--accent);
    font-weight: 700;
  }
  .day-cell.selected.today .day-head {
    color: var(--solid-void);
  }
  .day-head {
    padding: 0.15rem 0.4rem;
    border: 0;
    background: transparent;
    color: var(--ink);
    font: 500 0.82rem var(--font-ui);
  }
  .dots {
    display: flex;
    gap: 3px;
    height: 5px;
    pointer-events: none;
  }
  .dots i {
    width: 5px;
    height: 5px;
    border-radius: 999px;
  }
  .outside {
    opacity: 0.45;
  }
  .weekday-label {
    padding: 0.55rem 0.4rem 0.3rem;
    text-align: center;
    font: 600 0.7rem var(--font-mono);
    color: var(--subtle);
  }
  .selecting {
    background: color-mix(
      in srgb,
      var(--accent) 22%,
      var(--surface)
    ) !important;
  }
  .event-chip {
    display: flex;
    align-items: center;
    gap: 0.2rem;
    background: color-mix(in srgb, var(--calendar-color) 14%, var(--surface));
    border-left: 3px solid var(--calendar-color);
    margin: 0.25rem 0;
    border-radius: 0.3rem;
    min-width: 0;
  }
  .event-chip > button:first-child {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    background: transparent;
    border: 0;
    text-align: left;
    padding: 0.25rem;
    font-size: 0.78rem;
  }
  .resize {
    padding: 0.2rem;
    border: 0;
    background: transparent;
    font-size: 0.75rem;
  }
  .task-chip {
    display: block;
    text-align: left;
    width: 100%;
    font-size: 0.75rem;
    margin: 0.25rem 0;
    color: var(--muted);
    /* A subject-linked task carries its subject colour as a left rule. */
    box-shadow: inset 3px 0 0 var(--task-color, transparent);
  }
  .time-scroll {
    overflow: auto;
    max-height: 70vh;
    min-height: 380px;
    overscroll-behavior: contain;
  }
  .time-grid {
    --slot-height: 36px;
    --time-columns: 64px repeat(var(--days), minmax(110px, 1fr));
    display: grid;
    grid-template-columns: var(--time-columns);
    min-width: calc(var(--days) * 110px + 64px);
    background: var(--surface);
  }
  .time-header {
    display: grid;
    grid-template-columns: var(--time-columns);
    grid-column: 1 / -1;
    position: sticky;
    top: 0;
    z-index: 4;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    box-shadow: 0 2px 4px color-mix(in srgb, var(--ink) 8%, transparent);
  }
  .time-zone,
  .all-day-label {
    padding: 0.5rem 0.4rem;
    color: var(--muted);
    font-size: 0.65rem;
    text-align: right;
    overflow-wrap: anywhere;
    background: var(--canvas);
  }
  .time-zone {
    align-content: center;
  }
  .all-day-label span {
    font-size: 0.6rem;
  }
  .time-heading {
    border-left: 1px solid var(--border);
    padding: 0.35rem;
    min-width: 0;
  }
  .time-heading .time-date {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    width: 100%;
    padding: 0.25rem;
    border: 1px solid transparent;
    border-radius: 0.5rem;
    line-height: 1.2;
  }
  .time-date span,
  .time-date small {
    font-size: 0.7rem;
    font-weight: 400;
    color: var(--muted);
  }
  .time-date strong {
    display: grid;
    place-items: center;
    width: 1.9rem;
    height: 1.9rem;
    font-size: 1.15rem;
    border-radius: 50%;
  }
  .time-heading.today .time-date strong {
    background: var(--accent);
    color: var(--surface);
  }
  .time-heading.selected .time-date {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 9%, var(--surface));
  }
  .all-day-cell {
    display: flex;
    align-items: flex-start;
    min-width: 0;
    border-left: 1px solid var(--border);
    border-top: 1px solid var(--border);
    padding: 0.2rem;
    gap: 0.2rem;
  }
  .all-day-cell.selected {
    background: color-mix(in srgb, var(--accent) 5%, var(--surface));
  }
  .all-day-add {
    padding: 0.15rem;
    font-size: 0.75rem;
    line-height: 1.3;
    border-color: transparent;
  }
  .all-day-items {
    flex: 1;
    min-width: 0;
    max-height: 100px;
    overflow: auto;
  }
  .all-day-event,
  .all-day-items .task-chip {
    display: block;
    width: 100%;
    text-align: left;
    padding: 0.25rem;
    margin: 0 0 0.2rem;
    font-size: 0.72rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .time-axis {
    background: var(--canvas);
  }
  .time-label {
    height: var(--slot-height);
    box-sizing: border-box;
    text-align: right;
    padding: 0.15rem 0.45rem;
    color: var(--muted);
    font-size: 0.68rem;
  }
  .time-day {
    position: relative;
    height: calc(48 * var(--slot-height));
    min-width: 0;
    border-left: 1px solid var(--border);
  }
  .time-cell {
    box-sizing: border-box;
    height: var(--slot-height);
    position: relative;
    border-top: 1px dashed color-mix(in srgb, var(--border) 55%, transparent);
  }
  .drop-target {
    background: color-mix(in srgb, var(--accent) 20%, var(--surface));
    box-shadow: inset 0 0 0 2px var(--accent);
  }
  .invalid-drop {
    background: color-mix(in srgb, var(--danger, #d14b5a) 16%, var(--surface));
    box-shadow: inset 0 0 0 2px var(--danger, #d14b5a);
  }
  .drop-status { position: fixed; bottom: 1rem; left: 50%; transform: translateX(-50%); z-index: 40; max-width: calc(100vw - 2rem); margin: 0; padding: 0.6rem 0.9rem; pointer-events: none; color: var(--ink); background: var(--surface-raised); border: 1px solid var(--accent); border-radius: 6px; box-shadow: var(--overlay-shadow); }
  .task-tray { margin: 0.7rem 0 1rem; padding: 0.8rem; border: 1px solid var(--border); border-radius: 8px; }
  .task-tray h3 { margin: 0; }
  .tray-list { display: grid; gap: 0.4rem; max-height: 320px; overflow-y: auto; margin-top: 0.6rem; }
  .tray-task { display: grid; gap: 0.25rem; text-align: left; width: 100%; padding: 0.6rem; box-shadow: inset 3px 0 0 var(--task-color, transparent); }
  .tray-task span { font-size: 0.75rem; color: var(--muted); }
  .timed-task { position: absolute; z-index: 4; left: 2px; right: 2px; max-width: calc(100% - 4px); text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 0.7rem; }
  [draggable="true"] { cursor: grab; }
  [draggable="true"]:active { cursor: grabbing; }
  .time-cell.hour-start {
    border-top: 1px solid var(--border);
  }
  .time-day.today {
    background: color-mix(in srgb, var(--accent) 3%, var(--surface));
  }
  .slot-button {
    position: absolute;
    inset: 0;
    width: 100%;
    border: 0;
    border-radius: 0;
    background: transparent;
    touch-action: none;
  }
  .slot-button:hover,
  .slot-button:focus-visible {
    background: color-mix(in srgb, var(--accent) 10%, transparent);
  }
  .slot-button:focus-visible {
    outline-offset: -2px;
    z-index: 2;
  }
  .timed {
    position: absolute;
    z-index: 1;
    margin: 0;
    box-sizing: border-box;
    border: 0;
    border-radius: 9px;
    background: var(--block);
    color: var(--block-ink);
    box-shadow: 0 1px 0 color-mix(in srgb, var(--ink) 25%, transparent) inset;
    overflow: hidden;
    align-items: stretch;
  }
  .timed > .timed-content {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    white-space: normal;
    min-width: 0;
    padding: 0.15rem 0.25rem;
    line-height: 1.15;
    overflow: hidden;
    color: inherit;
  }
  .timed-content strong,
  .timed-content span {
    display: block;
    width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.72rem;
  }
  .timed-content span {
    font-size: 0.65rem;
    margin-top: 0.1rem;
    opacity: 0.72;
  }
  .timed .resize {
    align-self: flex-end;
    font-size: 0.6rem;
    padding: 0.1rem;
    color: inherit;
  }
  .timed:hover {
    box-shadow: 0 6px 16px color-mix(in srgb, var(--block) 35%, transparent);
  }
  .now-line {
    position: absolute;
    left: 0;
    right: 0;
    border-top: 1.5px dashed var(--c1);
    z-index: 3;
    pointer-events: none;
  }
  .now-line .bloom {
    position: absolute;
    left: -6px;
    top: -9px;
    color: var(--c1);
    font-size: 13px;
    line-height: 1;
    text-shadow: 0 0 6px var(--c1);
  }
  .agenda section {
    padding: 0.8rem 0;
    border-bottom: 1px solid var(--border);
  }
  .agenda-event {
    display: flex;
    gap: 0.8rem;
    text-align: left;
    align-items: center;
    width: 100%;
    margin: 0.4rem 0;
    border-left: 3px solid var(--calendar-color, var(--accent));
  }
  .agenda-event span {
    font-size: 0.85rem;
    color: var(--muted);
  }
  .task-columns {
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .task-column {
    background: var(--canvas);
    padding: 0.7rem;
    border: 1px solid var(--border);
    border-radius: 0.5rem;
  }
  .task-card {
    padding: 0.7rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.45rem;
    margin: 0.6rem 0;
  }
  .task-card > div:first-child {
    display: flex;
    gap: 0.5rem;
    align-items: start;
  }
  .task-title {
    border: 0;
    padding: 0;
    background: transparent;
    font-weight: 600;
    text-align: left;
    margin-right: auto;
  }
  .task-card p {
    font-size: 0.78rem;
    color: var(--muted);
    margin: 0.5rem 0;
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem;
  }
  .tags span {
    font-size: 0.7rem;
    padding: 0.15rem 0.3rem;
    background: var(--surface-raised);
    border-radius: 0.3rem;
  }
  .activity-row {
    display: flex;
    gap: 1rem;
    align-items: center;
    margin: 0.6rem 0;
  }
  .activity-row > div {
    display: flex;
    gap: 0.4rem;
    flex-wrap: wrap;
  }
  .activity-bar {
    min-width: 8rem;
    background: var(--accent);
    color: var(--surface);
    font-size: 0.75rem;
    padding: 0.35rem;
    white-space: nowrap;
  }
  .conflict {
    padding: 0.8rem;
    border: 1px solid var(--warning);
    border-radius: 0.5rem;
    margin: 0.6rem 0;
  }
  small {
    display: block;
    color: var(--muted);
    margin-top: 0.3rem;
  }
  details {
    margin: 0.8rem 0;
  }
  summary {
    cursor: pointer;
    font-weight: 600;
  }
  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 100;
    background: #0009;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
  }
  .dialog {
    width: min(660px, 100%);
    max-height: 92vh;
    overflow: auto;
    background: var(--surface);
    color: var(--ink);
    padding: 1.2rem;
    border: 1px solid var(--border);
    border-radius: 0.8rem;
    box-shadow: 0 20px 80px #0005;
  }
  .dialog::backdrop {
    background: #0009;
  }
  .dialog.small {
    width: min(450px, 100%);
  }
  .dialog header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .dialog header button {
    font-size: 1.3rem;
    padding: 0.1rem 0.5rem;
  }
  .editor-options {
    border: 1px solid var(--border);
    border-radius: 0.6rem;
    padding: 0.85rem;
    margin: 0.7rem 0;
  }
  .editor-options summary {
    font-size: 0.85rem;
    color: var(--muted);
  }
  .editor-options[open] summary {
    margin-bottom: 0.8rem;
    color: var(--ink);
  }
  .editor-options > label {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    margin: 0.8rem 0;
  }
  .dialog footer {
    margin-top: 1rem;
    position: sticky;
    bottom: -1.2rem;
    background: var(--surface);
    border-top: 1px solid var(--border);
    padding: 1rem 0 0.25rem;
    z-index: 2;
  }
  .danger {
    color: var(--warning);
  }
  @media (min-width: 900px) {
    .calendar-workspace.drawer-open {
      padding-right: calc(min(400px, 100vw - 1.5rem) + 0.75rem);
    }
    /* The drawer stands in for the side rail while it is open. */
    .drawer-open .calendar-layout {
      grid-template-columns: minmax(0, 1fr);
    }
    .drawer-open .selected-plan {
      display: none;
    }
  }
  .drawer-form {
    display: grid;
    gap: 10px;
  }
  .drawer-form .editor-options {
    margin: 0;
  }
  .drawer-form footer {
    position: sticky;
    bottom: -16px;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    padding: 0.7rem 0 0.2rem;
    border-top: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--solid-raised) 92%, transparent);
  }
  .drawer-title {
    padding: 0 0 4px;
    border: 0;
    border-bottom: 1px solid var(--hairline);
    border-radius: 0;
    background: transparent;
    color: var(--ink);
    font: 700 1.2rem var(--font-ui);
  }
  .drawer-title:focus-visible {
    outline: none;
    border-bottom-color: var(--accent);
  }
  .steppers {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .stepper {
    display: inline-flex;
    align-items: center;
    border: 1px solid var(--hairline);
    border-radius: 999px;
  }
  .stepper button {
    width: 26px;
    height: 26px;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
  }
  .stepper button:hover {
    color: var(--ink);
  }
  .stepper b {
    font: 600 0.8rem var(--font-mono);
  }
  .steppers .arrow {
    color: var(--subtle);
  }
  .drawer-dates {
    display: grid;
    gap: 6px;
    font-size: 0.8rem;
  }
  .drawer-dates .inline {
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }
  .drawer-field {
    display: grid;
    gap: 4px;
    margin: 0;
    padding: 0;
    border: 0;
  }
  .drawer-field .eyebrow {
    color: var(--muted);
    padding: 0;
  }
  .swatches,
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .swatch {
    width: 22px;
    height: 22px;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background: var(--sw);
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--solid-void) 30%, transparent);
  }
  .swatch[aria-pressed="true"] {
    outline: 2px solid var(--ink);
    outline-offset: 2px;
  }
  .chips button {
    padding: 2px 9px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font-size: 0.75rem;
  }
  .chips button[aria-pressed="true"] {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 18%, transparent);
    color: var(--ink);
  }
  @media (max-width: 1200px) {
    .calendar-layout {
      grid-template-columns: minmax(0, 1fr);
    }
  }
  @media (max-width: 760px) {
    .month-layout {
      grid-template-columns: minmax(0, 1fr);
    }
    .controls label {
      flex-wrap: wrap;
    }
    .agenda-event {
      align-items: start;
      flex-direction: column;
      gap: 0.2rem;
    }
    .toolbar {
      align-items: start;
    }
    .header-actions {
      margin-left: 0;
    }
    .day-cell {
      min-height: 44px;
    }
    .modal-backdrop {
      padding: 0.3rem;
    }
    .dialog {
      padding: 0.8rem;
    }
    .activity-row {
      align-items: start;
      flex-direction: column;
    }
  }
</style>
