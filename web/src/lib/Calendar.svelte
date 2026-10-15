<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { api, safeUrl, type TaskRecord } from "./api";
  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  type Cal = {
    id: string;
    name: string;
    color: string;
    timezone: string;
    visible: number;
    revision: number;
    google_id?: string;
  };
  type Attendee = {
    email: string;
    name: string;
    response: string;
    optional?: boolean;
  };
  type Event = {
    id: string;
    revision: number;
    title: string;
    calendar_id: string;
    start: string;
    end: string;
    timezone: string;
    all_day: boolean;
    occurrence: string;
    series_start?: string;
    series_end?: string;
    rrule: string;
    exdates: string[];
    description: string;
    location: string;
    url: string;
    source_key?: string;
    source_url?: string;
    busy: boolean;
    privacy: string;
    reminders: number[];
    attendees: Attendee[];
    attachments: { title: string; url: string }[];
    event_type: string;
    google_options: Record<string, unknown>;
    undo_id?: number;
  };
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
    activity: Record<string, number | string>[];
    reminders: {
      event_id: string;
      occurrence: string;
      minutes: number;
      title: string;
      due: string;
    }[];
    conflicts: {
      event_id: string;
      with: string[];
      start: string;
      end: string;
    }[];
    free: { start: string; end: string }[];
  };
  type GoogleStatus = {
    client_present: boolean;
    authorized: boolean;
    tasks_authorized: boolean;
    steps: string[];
    selected: Cal[];
    notice: string;
    limits: string[];
    public_operations: Record<string, string[]>;
    calendar_conflicts: {
      calendar_id: string;
      local: {
        name: string;
        timezone: string;
        color: string;
        revision: number;
      };
      remote: { values: { name: string; timezone: string; color: string } };
    }[];
    capabilities: {
      google: string[];
      account_dependent: string[];
      unsupported: Record<string, string>;
    };
  };
  type Preview = {
    row?: number;
    event?: Record<string, unknown>;
    exceptions?: unknown[];
    duplicate?: boolean;
    error?: string;
    selected?: boolean;
  };
  let data = $state<Snapshot>({
    calendars: [],
    events: [],
    tasks: [],
    activity: [],
    reminders: [],
    conflicts: [],
    free: [],
  });
  let view = $state("month");
  let section = $state("calendar");
  let anchor = $state(new Date().toISOString().slice(0, 10));
  let tz = $state(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  let search = $state("");
  let error = $state("");
  let notice = $state("");
  let loading = $state(false);
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
  let exdatesText = $state("");
  let calendarEditor = $state<Partial<Cal> | null>(null);
  let rangeStart = $state<string | null>(null);
  let rangeEnd = $state<string | null>(null);
  let rangeAllDay = $state(false);
  let dragEvent: Event | null = null;
  let dragMode = "move";
  let importRows = $state<Preview[]>([]);
  let importNotice = $state("");
  let googleStatus = $state<GoogleStatus | null>(null);
  let googleResult = $state<unknown>(null);
  let googleTask = $state<TaskRecord | null>(null);
  let linked = $state<string[]>([]);
  let includeTasks = $state(false);
  let syncDirection = $state("pull");
  let sendUpdates = $state("none");
  let syncApproved = $state(false);
  let syncPreview = $state<Record<string, unknown> | null>(null);
  let pendingGoogle = $state<{
    resource?: string;
    action?: string;
    calendar_id?: string;
    id?: string;
  }>({});
  let pendingPath = $state("");
  let remoteCalendars = $state<Record<string, unknown>[]>([]);
  let googleConflicts = $state<
    {
      id: number;
      local_revision: number;
      local: Event;
      remote_json: Record<string, unknown>;
    }[]
  >([]);
  let resource = $state("calendarList");
  let action = $state("list");
  let remoteCalendarId = $state("primary");
  let remoteId = $state("");
  let remoteEtag = $state("");
  let remoteData = $state("{}");
  let remoteParams = $state("{}");
  let meet = $state(false);
  let notifySharing = $state(false);
  let webhookConfirmed = $state(false);
  type OperationReview = {
    resource: string;
    action: string;
    method: string;
    url: string;
    params: Record<string, unknown>;
    body: unknown;
    if_match: string | null;
    current: Record<string, unknown> | null;
    recipients: string[];
    items: Record<string, unknown>[];
    notes: string[];
  };
  let operationRequest = $state<Record<string, unknown> | null>(null);
  let operationReview = $state<OperationReview | null>(null);
  let operationApproved = $state(false);
  let clearConfirmed = $state(false);
  const resourceActions: Record<string, string[]> = $derived(
    googleStatus?.public_operations || {
      calendarList: ["list", "get", "create", "update", "delete"],
      calendars: ["get", "create", "update", "delete"],
      events: [
        "list",
        "get",
        "create",
        "update",
        "delete",
        "rsvp",
        "instances",
      ],
      colors: ["list", "get"],
      settings: ["list", "get"],
      acl: ["list", "get", "create", "update", "delete"],
      freebusy: ["freebusy"],
      tasklists: ["list", "get", "create", "update", "delete"],
      tasks: ["list", "get", "create", "update", "delete"],
    },
  );
  const actionLabels: Record<string, string> = {
    list: "List resources",
    get: "Read resource + ETag",
    create: "Create resource",
    update: "Patch selected fields",
    replace: "Replace full resource (PUT)",
    delete: "Delete resource",
    rsvp: "Update reviewed RSVP responses",
    instances: "List recurring instances",
    freebusy: "Check provider availability",
    move: "Move event/task (explicit source & target)",
    import: "Import a private event copy (iCalUID)",
    quickAdd: "Create event from Google-parsed text",
    watch: "Register a public HTTPS change channel",
    clear: "Clear primary events / completed tasks",
    stop: "Stop a registered change channel",
  };
  let taskDueInput = $state("");
  let taskDueTimed = $state(false);
  function modal(node: HTMLDialogElement) {
    const previous = document.activeElement as HTMLElement | null;
    node.showModal();
    node.querySelector<HTMLInputElement>("input:not([type=checkbox])")?.focus();
    return { destroy: () => previous?.focus() };
  }
  let notifications = $state(false);
  const statuses = ["todo", "doing", "done", "cancelled"];
  const hours = Array.from({ length: 48 }, (_, i) => i);
  let loadCounter = 0;
  const dateAt = (value: string) => new Date(`${value}T12:00:00Z`);
  function addDays(value: string, count: number) {
    const d = dateAt(value);
    d.setUTCDate(d.getUTCDate() + count);
    return d.toISOString().slice(0, 10);
  }
  function weekStart(value: string) {
    const d = dateAt(value);
    return addDays(value, -((d.getUTCDay() + 6) % 7));
  }
  let firstDay = $derived(
    view === "month"
      ? weekStart(anchor.slice(0, 8) + "01")
      : view === "week"
        ? weekStart(anchor)
        : anchor,
  );
  let days = $derived(
    Array.from(
      {
        length:
          view === "month"
            ? 42
            : view === "week"
              ? 7
              : view === "agenda"
                ? 31
                : 1,
      },
      (_, i) => addDays(firstDay, i),
    ),
  );
  function parts(value: string, zoneName = tz) {
    const values = new Intl.DateTimeFormat("en-CA", {
      timeZone: zoneName,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date(value));
    const get = (key: string) =>
      values.find((p) => p.type === key)?.value || "";
    return {
      date: `${get("year")}-${get("month")}-${get("day")}`,
      time: `${get("hour")}:${get("minute")}`,
      hour: Number(get("hour")),
      minute: Number(get("minute")),
    };
  }
  function localInput(value: string, allDay: boolean, zoneName: string) {
    if (allDay) return value;
    const p = parts(value, zoneName);
    return `${p.date}T${p.time}`;
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
    return data.events.filter((e) =>
      e.all_day
        ? e.start <= day && e.end > day
        : parts(e.start).date <= day &&
          parts(e.end).date >= day &&
          !(parts(e.end).date === day && parts(e.end).time === "00:00"),
    );
  }
  function tasksOn(day: string) {
    return data.tasks.filter(
      (t) =>
        t.due &&
        t.status !== "cancelled" &&
        (t.due.length === 10 ? t.due : parts(t.due).date) === day,
    );
  }
  function timedOn(day: string, hour: number) {
    return eventsOn(day).filter(
      (e) =>
        !e.all_day &&
        ((parts(e.start).date === day &&
          Math.floor(
            (parts(e.start).hour * 60 + parts(e.start).minute) / 30,
          ) === hour) ||
          (parts(e.start).date < day && hour === 0)),
    );
  }
  function color(id: string) {
    return data.calendars.find((c) => c.id === id)?.color || "var(--accent)";
  }
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
      if (serial === loadCounter) data = result;
    } catch (e) {
      if (serial === loadCounter) error = String(e);
    } finally {
      if (serial === loadCounter) loading = false;
    }
  }
  async function run(work: () => Promise<unknown>) {
    error = "";
    notice = "";
    try {
      return await work();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
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
    if (view === "month") {
      const d = dateAt(anchor);
      d.setUTCMonth(d.getUTCMonth() + step, 1);
      anchor = d.toISOString().slice(0, 10);
    } else
      anchor = addDays(
        anchor,
        step * (view === "week" ? 7 : view === "agenda" ? 31 : 1),
      );
  }
  function newEvent(start: string, end?: string, allDay = false) {
    const cal = data.calendars.find((c) => c.visible) || data.calendars[0];
    editor = {
      id: "",
      revision: 0,
      title: "",
      calendar_id: cal?.id || "personal",
      start: "",
      end: "",
      timezone: tz,
      all_day: allDay,
      occurrence: "",
      rrule: "",
      exdates: [],
      description: "",
      location: "",
      url: "",
      busy: true,
      privacy: "default",
      reminders: [10],
      attendees: [],
      attachments: [],
      event_type: "default",
      google_options: {},
    };
    startInput = start;
    endInput =
      end ||
      (allDay
        ? addDays(start, 1)
        : new Date(new Date(start + "Z").getTime() + 3600000)
            .toISOString()
            .slice(0, 16));
    editScope = "all";
    attendeesText = "";
    attachmentsText = "";
    exdatesText = "";
    remindersText = "10";
    fold = 0;
    foldChanged = false;
    googleOptionsText = "{}";
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
    foldChanged = false;
    googleOptionsText = JSON.stringify(e.google_options, null, 2);
  }
  function openEvent(e: Event) {
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
      };
      let patch: Record<string, unknown> = changes;
      if (e.id) {
        const base =
          editScope === "all"
            ? await api<Event>(`/calendar/events/${e.id}`)
            : editBase;
        patch = Object.fromEntries(
          Object.entries(changes).filter(
            ([key, value]) =>
              JSON.stringify(value) !==
              JSON.stringify(base?.[key as keyof Event]),
          ),
        );
      }
      const result = e.id
        ? await api<Event>(`/calendar/events/${e.id}`, {
            revision: e.revision,
            scope: editScope,
            occurrence: e.occurrence,
            changes: patch,
            delete: remove,
          })
        : await api<Event>("/calendar/events", changes);
      editor = null;
      return result;
    });
  }
  function beginRange(value: string, allDay: boolean, event: PointerEvent) {
    if (event.button !== 0) return;
    rangeStart = value;
    rangeEnd = value;
    rangeAllDay = allDay;
  }
  function extendRange(value: string) {
    if (rangeStart) rangeEnd = value;
  }
  function finishRange() {
    if (!rangeStart || !rangeEnd) return;
    const [a, b] = [rangeStart, rangeEnd].sort();
    if (rangeAllDay) newEvent(a, addDays(b, 1), true);
    else {
      const d = new Date(b + "Z");
      d.setUTCMinutes(d.getUTCMinutes() + 30);
      newEvent(a, d.toISOString().slice(0, 16));
    }
    rangeStart = null;
    rangeEnd = null;
  }
  function slot(day: string, hour: number) {
    return `${day}T${String(Math.floor(hour / 2)).padStart(2, "0")}:${hour % 2 ? "30" : "00"}`;
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
    if (!dragEvent) return;
    const selected = dragEvent;
    dragEvent = null;
    openEvent(selected);
    if (!editor) return;
    if (dragMode === "resize") {
      endInput = allDay ? addDays(value, 1) : value;
      return;
    }
    if (allDay && selected.all_day) {
      const span = Math.round(
        (dateAt(selected.end).getTime() - dateAt(selected.start).getTime()) /
          86400000,
      );
      startInput = value;
      endInput = addDays(value, span);
    } else if (!allDay && !selected.all_day) {
      await run(async () => {
        const start = await resolveTime(value, selected.timezone);
        const duration =
          new Date(selected.end).getTime() - new Date(selected.start).getTime();
        const end = new Date(
          new Date(start).getTime() + duration,
        ).toISOString();
        startInput = value;
        endInput = localInput(end, false, selected.timezone);
      });
    } else
      notice =
        "Use the event editor to change all-day/timed type. Drag opened the event without changing its type.";
  }
  function drag(e: Event, event: DragEvent, mode = "move") {
    dragEvent = e;
    dragMode = mode;
    event.dataTransfer?.setData("text/plain", e.id);
    if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
  }
  function newTask() {
    taskEditor = {
      title: "",
      description: "",
      due: anchor,
      timezone: tz,
      priority: "normal",
      status: "todo",
      tags: [],
      project: "",
      calendar_id: "personal",
      repeat_frequency: "",
      repeat_interval: 1,
      repeat_until: "",
    };
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
    await changed(() =>
      api<Task>("/calendar/tasks", {
        id: task.id,
        revision: task.revision,
        status,
      }),
    );
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
  async function exportFile() {
    await run(async () => {
      const result = await api<{ content: string; filename: string }>(
        "/calendar/export",
      );
      const url = URL.createObjectURL(
        new Blob([result.content], { type: "text/calendar" }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = result.filename;
      a.click();
      URL.revokeObjectURL(url);
    });
  }
  async function importFile(event: EventTarget | null, format: string) {
    const input = event as HTMLInputElement;
    if (!input.files?.[0]) return;
    await run(async () => {
      const result = await api<{ items: Preview[]; notice: string }>(
        "/calendar/import/preview",
        { content: await input.files![0].text(), format, timezone: tz },
      );
      importRows = result.items.map((v) => ({
        ...v,
        selected: !!v.event && !v.duplicate && !v.error,
      }));
      importNotice = result.notice;
    });
    input.value = "";
  }
  async function applyImport() {
    await run(async () => {
      const result = await api<{
        created: string[];
        duplicates: string[];
        undo_ids: number[];
      }>("/calendar/import", {
        confirmed: true,
        items: importRows.filter((v) => v.selected),
      });
      notice = `Imported ${result.created.length}; skipped ${result.duplicates.length} duplicates.`;
      undoId = result.undo_ids.at(-1) || null;
      importRows = [];
      await load();
    });
  }
  async function refreshSources() {
    await run(async () => {
      const result = await api<{ changed: number; review_errors: unknown[] }>(
        "/calendar/import-jobs",
        {},
      );
      notice = `Refreshed ${result.changed} linked dates. ${result.review_errors.length} source dates need review.`;
      if (result.review_errors.length) googleResult = result.review_errors;
      await load();
    });
  }
  async function dismissReminder(r: Snapshot["reminders"][number]) {
    await run(async () => {
      await api("/calendar/reminders/dismiss", r);
      await load();
    });
  }
  async function enableNotifications() {
    if (!("Notification" in window)) {
      error =
        "Notifications are not supported by this browser; in-app reminders remain available.";
      return;
    }
    const permission = await Notification.requestPermission();
    notifications = permission === "granted";
    localStorage.setItem("kitty-calendar-notifications", String(notifications));
    if (!notifications)
      notice =
        "Notification permission not granted. In-app reminders stay enabled.";
  }
  async function loadGoogle() {
    await run(async () => {
      googleStatus = await api<GoogleStatus>("/calendar/google/status");
      googleConflicts = (
        await api<{ items: typeof googleConflicts }>(
          "/calendar/google/conflicts",
        )
      ).items;
      linked = googleStatus.selected.map((c) => c.id);
    });
  }
  async function googleWork(path: string, body: unknown) {
    pendingGoogle =
      body && typeof body === "object" ? (body as typeof pendingGoogle) : {};
    pendingPath = path;
    await run(async () => {
      const result = await api<{ task: TaskRecord }>(path, body);
      googleTask = result.task;
      syncApproved = false;
      syncPreview = null;
      googleResult = null;
    });
  }
  async function pollTask() {
    if (!googleTask || googleTask.status !== "running") return;
    await run(async () => {
      const result = await api<TaskRecord>(`/tasks/${googleTask!.id}`);
      googleTask = result;
      if (
        result.status === "completed" &&
        pendingPath === "/calendar/google/operation/preview"
      ) {
        operationReview = result.result as OperationReview;
        operationApproved = false;
        clearConfirmed = false;
      } else if (result.status === "completed") {
        googleResult = result.result;
        const items = (result.result as { items?: Record<string, unknown>[] })
          ?.items;
        if (
          pendingGoogle.resource === "calendarList" &&
          pendingGoogle.action === "list" &&
          items
        )
          remoteCalendars = items;
        await loadGoogle();
        await load();
      }
      if (result.status === "failed")
        error = result.error || "Google operation failed";
    });
  }
  async function uploadClient(target: EventTarget | null) {
    const input = target as HTMLInputElement;
    if (!input.files?.[0]) return;
    await run(async () => {
      await api("/calendar/google/client", {
        confirmed: true,
        client: await input.files![0].text(),
      });
      await loadGoogle();
    });
    input.value = "";
  }
  function template() {
    const allowed = resourceActions[resource];
    if (!allowed.includes(action)) action = allowed[0];
    meet = false;
    notifySharing = false;
    if (resource === "calendars" && !remoteId) remoteId = "primary";
    if (resource === "settings" && action === "get" && !remoteId)
      remoteId = "timezone";
    if (resource === "tasks" && remoteCalendarId === "primary")
      remoteCalendarId = "@default";
    const values: Record<string, unknown> = {
      calendarList:
        action === "create"
          ? { id: "calendar-id@example.com" }
          : action === "update"
            ? { selected: true }
            : {},
      calendars:
        action === "create" || action === "update"
          ? { summary: "My calendar", timeZone: tz }
          : {},
      events:
        action === "create" || action === "import"
          ? {
              summary: "New event",
              start: { date: anchor },
              end: { date: addDays(anchor, 1) },
              ...(action === "import"
                ? {
                    iCalUID: `${crypto.randomUUID()}@kitty.local`,
                    eventType: "default",
                  }
                : {}),
            }
          : action === "rsvp"
            ? {
                attendees: [
                  { email: "you@example.com", responseStatus: "accepted" },
                ],
              }
            : {},
      acl:
        action === "create" || action === "update"
          ? {
              scope: { type: "user", value: "person@example.com" },
              role: "reader",
            }
          : {},
      freebusy: {
        timeMin: new Date().toISOString(),
        timeMax: new Date(Date.now() + 86400000).toISOString(),
        timeZone: tz,
        items: [{ id: "primary" }],
      },
      tasks:
        action === "create"
          ? { title: "New task", due: anchor + "T00:00:00Z" }
          : {},
      tasklists: action === "create" ? { title: "New task list" } : {},
    };
    remoteData = JSON.stringify(values[resource] || {}, null, 2);
    if (action === "watch")
      remoteData = JSON.stringify(
        {
          id: crypto.randomUUID(),
          type: "web_hook",
          address: "",
          params: { ttl: "604800" },
        },
        null,
        2,
      );
    if (resource === "events" && action === "quickAdd") {
      remoteData = JSON.stringify(
        { text: `Study block on ${anchor} from 9am to 10am` },
        null,
        2,
      );
      sendUpdates = "none";
    }
    if (resource === "channels")
      remoteData = JSON.stringify({ id: "", resourceId: "" }, null, 2);
    remoteParams = JSON.stringify(
      action === "move"
        ? resource === "events"
          ? { destination: "" }
          : { destinationTasklist: remoteCalendarId }
        : {},
      null,
      2,
    );
    operationRequest = null;
    operationReview = null;
    operationApproved = false;
    clearConfirmed = false;
    webhookConfirmed = false;
  }
  async function reviewOperation() {
    let request: Record<string, unknown>;
    try {
      const data = JSON.parse(remoteData);
      const params = JSON.parse(remoteParams);
      if (
        !data ||
        typeof data !== "object" ||
        Array.isArray(data) ||
        !params ||
        typeof params !== "object" ||
        Array.isArray(params)
      )
        throw new Error("Expected JSON objects");
      if (action === "watch" && !webhookConfirmed)
        throw new Error(
          "Configure and confirm your own public HTTPS receiver before preparing a watch.",
        );
      if (resource === "events" && action === "move" && !params.destination)
        throw new Error(
          "Enter the exact target calendar ID in params.destination.",
        );
      if (
        resource === "events" &&
        action === "quickAdd" &&
        sendUpdates !== "none"
      )
        throw new Error(
          "Safe quick add requires notification policy none; use structured create for reviewed attendees.",
        );
      request = {
        resource,
        action,
        calendar_id: remoteCalendarId,
        id: remoteId,
        etag: remoteEtag || undefined,
        data,
        params: {
          ...params,
          ...(resource === "events" &&
          [
            "create",
            "update",
            "replace",
            "delete",
            "rsvp",
            "move",
            "quickAdd",
          ].includes(action)
            ? { sendUpdates }
            : {}),
        },
        meet,
        notify: notifySharing,
        webhook_confirmed: webhookConfirmed,
      };
    } catch (e) {
      error =
        e instanceof SyntaxError
          ? "Resource data and query parameters must be valid JSON objects."
          : e instanceof Error
            ? e.message
            : String(e);
      return;
    }
    operationReview = null;
    operationApproved = false;
    clearConfirmed = false;
    if (["list", "get", "instances", "freebusy"].includes(action)) {
      operationRequest = null;
      await googleWork("/calendar/google/operation", request);
      return;
    }
    operationRequest = request;
    // The server reads the live target and returns the exact frozen request to approve.
    await googleWork("/calendar/google/operation/preview", request);
  }
  async function sendReviewedOperation() {
    if (!operationRequest || !operationReview) return;
    const body = {
      ...operationRequest,
      review: operationReview,
      confirmed: true,
      external_approved: true,
      clear_confirmed: clearConfirmed,
    };
    operationRequest = null;
    operationReview = null;
    operationApproved = false;
    clearConfirmed = false;
    await googleWork("/calendar/google/operation", body);
  }
  function useGoogleResult() {
    if (
      !googleResult ||
      typeof googleResult !== "object" ||
      Array.isArray(googleResult) ||
      resource !== pendingGoogle.resource
    )
      return;
    const result = googleResult as Record<string, unknown>;
    if (typeof result.id !== "string" || typeof result.etag !== "string")
      return;
    remoteId = result.id;
    remoteEtag = result.etag;
    if (pendingGoogle.calendar_id) remoteCalendarId = pendingGoogle.calendar_id;
    if (resource === "calendars" || resource === "tasklists")
      remoteCalendarId = result.id;
    operationRequest = null;
    operationReview = null;
    operationApproved = false;
    notice =
      "Copied the returned ID and ETag. Prepare the operation to review the exact request and live recipients.";
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
  $effect(() => {
    syncDirection;
    sendUpdates;
    linked;
    untrack(() => {
      syncApproved = false;
      syncPreview = null;
    });
  });
  onMount(() => {
    void loadGoogle();
    notifications =
      localStorage.getItem("kitty-calendar-notifications") === "true" &&
      "Notification" in window &&
      Notification.permission === "granted";
    const timer = setInterval(() => void pollTask(), 1200);
    return () => clearInterval(timer);
  });
</script>

<svelte:window
  onpointerup={finishRange}
  onkeydown={(e) => {
    if (e.key === "Escape") {
      editor = null;
      taskEditor = null;
      calendarEditor = null;
      rangeStart = null;
      operationReview = null;
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

<section class="calendar-workspace" aria-label="Calendar workspace">
  <header class="toolbar">
    <div>
      <h1>Calendar</h1>
      <p>Local time, intentional plans. <span>{tz}</span></p>
    </div>
    <button onclick={() => newEvent(anchor + "T09:00", anchor + "T10:00")}
      >＋ Event</button
    ><button onclick={newTask}>＋ Task</button>{#if undoId}<button
        onclick={() =>
          run(async () => {
            await api("/calendar/undo", { id: undoId });
            undoId = null;
            await load();
          })}>Undo last change</button
      >{/if}
  </header>
  <nav class="tabs" aria-label="Calendar sections">
    {#each [["calendar", "Calendar"], ["tasks", "Task list"], ["board", "Task board"], ["activity", "Activity"], ["availability", "Availability"], ["integrations", "Imports & Google"]] as [key, title]}<button
        class:active={section === key}
        onclick={() => (section = key)}>{title}</button
      >{/each}
  </nav>
  {#if error}<div role="alert" class="error">
      {error}<button aria-label="Dismiss error" onclick={() => (error = "")}
        >×</button
      >
    </div>{/if}
  {#if notice}<p role="status" class="notice">{notice}</p>{/if}
  <div class="controls">
    <label
      >Search <input
        type="search"
        bind:value={search}
        placeholder="Events, tasks, notes, tags"
      /></label
    ><label
      >Display timezone <input
        bind:value={tz}
        list="calendar-zones"
        placeholder="America/Chicago"
      /></label
    ><datalist id="calendar-zones"
      ><option value="UTC"></option><option value="America/Chicago"
      ></option><option value="America/New_York"></option><option
        value="America/Los_Angeles"
      ></option><option value="Europe/London"></option><option
        value="Asia/Tokyo"
      ></option></datalist
    ><button onclick={load} disabled={loading}
      >{loading ? "Loading…" : "Refresh"}</button
    >
  </div>
  <div class="calendars">
    {#each data.calendars as c}<label style={`--calendar-color:${c.color}`}
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
          color: "#6d5dfc",
          timezone: tz,
          visible: 1,
        })}>＋ Calendar</button
    >
  </div>
  {#if data.reminders.length}<aside
      class="reminders"
      aria-label="Due reminders"
    >
      <h2>Reminders</h2>
      {#each data.reminders as r}<div>
          <strong>{r.title}</strong><span>{label(r.due)}</span><button
            onclick={() => dismissReminder(r)}>Dismiss</button
          >
        </div>{/each}
    </aside>{/if}
  {#if section === "calendar"}
    <div class="controls">
      <button aria-label="Previous period" onclick={() => navigate(-1)}
        >←</button
      ><button onclick={() => (anchor = parts(new Date().toISOString()).date)}
        >Today</button
      ><button aria-label="Next period" onclick={() => navigate(1)}>→</button
      ><input
        type="date"
        aria-label="Calendar anchor date"
        bind:value={anchor}
      />{#each ["day", "week", "month", "agenda"] as v}<button
          class:active={view === v}
          onclick={() => (view = v)}>{v}</button
        >{/each}
    </div>
    <p class="hint">
      Drag across empty dates or time cells to create a range. Drag an event to
      move; drag ↔ to set its end. Keyboard: Shift+Enter starts a range,
      Shift+Enter on its end finishes it. Touch: tap a cell or edit exact
      start/end below. No drag change is saved until reviewed.
    </p>
    {#if view === "month"}
      <div class="month-grid" role="grid" aria-label="Month calendar">
        {#each days as day}<div
            class="day-cell"
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
            ondragover={(e) => e.preventDefault()}
            ondrop={(e) => dropOn(day, true, e)}
          >
            <button
              class="day-head"
              onpointerdown={(e) => beginRange(day, true, e)}
              onpointerenter={() => extendRange(day)}
              onkeydown={(e) => keyboardRange(day, true, e)}
              >{new Intl.DateTimeFormat(undefined, {
                weekday: "short",
                day: "numeric",
                timeZone: "UTC",
              }).format(dateAt(day))}</button
            >{#each eventsOn(day) as e}<div
                class="event-chip"
                style={`--calendar-color:${color(e.calendar_id)}`}
                draggable="true"
                ondragstart={(event) => drag(e, event)}
                role="group"
                aria-label={e.title}
              >
                <button onclick={() => openEvent(e)}
                  >{!e.all_day
                    ? parts(e.start).time + " "
                    : ""}{e.title}{e.rrule ? " ↻" : ""}</button
                ><button
                  class="resize"
                  draggable="true"
                  ondragstart={(event) => {
                    event.stopPropagation();
                    drag(e, event, "resize");
                  }}
                  aria-label={`Resize ${e.title}`}>↔</button
                >
              </div>{/each}{#each tasksOn(day) as t}<button
                class="task-chip"
                onclick={() => (taskEditor = { ...t })}>☐ {t.title}</button
              >{/each}
          </div>{/each}
      </div>
    {:else if view === "week" || view === "day"}
      <div class="time-scroll">
        <div class="time-grid" style={`--days:${days.length}`}>
          <div class="time-label">{tz}</div>
          {#each days as day}<div class="time-heading">
              <strong>{day}</strong><button
                onpointerdown={(e) => beginRange(day, true, e)}
                onpointerenter={() => extendRange(day)}
                onkeydown={(e) => keyboardRange(day, true, e)}
                aria-label={`Create all-day event on ${day}`}>All-day ＋</button
              >{#each eventsOn(day).filter((e) => e.all_day) as e}<button
                  class="event-chip"
                  style={`--calendar-color:${color(e.calendar_id)}`}
                  onclick={() => openEvent(e)}>{e.title}</button
                >{/each}{#each tasksOn(day) as t}<button
                  class="task-chip"
                  onclick={() => (taskEditor = { ...t })}>☐ {t.title}</button
                >{/each}
            </div>{/each}{#each hours as hour}<div class="time-label">
              {hour % 2 === 0 ? `${String(hour / 2).padStart(2, "0")}:00` : ""}
            </div>
            {#each days as day}<div
                class="time-cell"
                role="group"
                aria-label={`${day} ${slot(day, hour).slice(-5)}`}
                ondragover={(e) => e.preventDefault()}
                ondrop={(e) => dropOn(slot(day, hour), false, e)}
              >
                <button
                  class="slot-button"
                  class:selecting={!!rangeStart &&
                    !rangeAllDay &&
                    slot(day, hour) >=
                      [rangeStart, rangeEnd || rangeStart].sort()[0] &&
                    slot(day, hour) <=
                      [rangeStart, rangeEnd || rangeStart].sort()[1]}
                  aria-label={`Create event ${day} ${slot(day, hour).slice(-5)}`}
                  onpointerdown={(e) => beginRange(slot(day, hour), false, e)}
                  onpointerenter={() => extendRange(slot(day, hour))}
                  onkeydown={(e) => keyboardRange(slot(day, hour), false, e)}
                ></button>{#each timedOn(day, hour) as e}<div
                    class="event-chip timed"
                    style={`--calendar-color:${color(e.calendar_id)}`}
                    draggable="true"
                    ondragstart={(event) => drag(e, event)}
                    role="group"
                    aria-label={e.title}
                  >
                    <button onclick={() => openEvent(e)}
                      >{parts(e.start).time}–{parts(e.end).time}
                      {e.title}</button
                    ><button
                      class="resize"
                      draggable="true"
                      ondragstart={(event) => {
                        event.stopPropagation();
                        drag(e, event, "resize");
                      }}
                      aria-label={`Resize ${e.title}`}>↔</button
                    >
                  </div>{/each}
              </div>{/each}{/each}
        </div>
      </div>
    {:else}
      <div class="agenda">
        {#each days as day}{#if eventsOn(day).length || tasksOn(day).length}<section
            >
              <h2>{day}</h2>
              {#each eventsOn(day) as e}<button
                  class="agenda-event"
                  style={`--calendar-color:${color(e.calendar_id)}`}
                  onclick={() => openEvent(e)}
                  ><span
                    >{e.all_day
                      ? "All day"
                      : `${label(e.start)} → ${label(e.end)}`}</span
                  ><strong>{e.title}</strong><span>{e.location}</span></button
                >{/each}{#each tasksOn(day) as t}<button
                  class="agenda-event"
                  onclick={() => (taskEditor = { ...t })}
                  ><span>Task · {t.priority} · {t.status}</span><strong
                    >{t.title}</strong
                  ></button
                >{/each}
            </section>{/if}{/each}{#if !data.events.length && !data.tasks.some((t) => t.due)}<p
          >
            No events or due tasks in this range. Create a plan or import linked
            application dates.
          </p>{/if}
      </div>
    {/if}
  {:else if section === "tasks" || section === "board"}
    <div class:board={section === "board"} class="task-columns">
      {#each section === "board" ? statuses : ["all"] as status}<section
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
                <button
                  class="task-title"
                  onclick={() => (taskEditor = { ...t })}>{t.title}</button
                ><select
                  aria-label={`Status for ${t.title}`}
                  value={t.status}
                  onchange={(e) => taskStatus(t, e.currentTarget.value)}
                  >{#each statuses as s}<option value={s}>{s}</option
                    >{/each}</select
                >
              </div>
              <p>
                {t.due ? label(t.due, t.due.length === 10) : "No due date"} · {t.priority}
                {t.project ? "· " + t.project : ""}
                {t.repeat_frequency ? "· " + repeatLabel(t) : ""}
              </p>
              <div class="tags">
                {#each t.tags as tag}<span>{tag}</span>{/each}
              </div>
            </article>{/each}<button
            onclick={() => {
              newTask();
              if (taskEditor && status !== "all") taskEditor.status = status;
            }}>＋ Task</button
          >
        </section>{/each}
    </div>
  {:else if section === "activity"}
    <h2>Actual activity in the displayed range</h2>
    <p class="hint">
      Counts come from saved calendar edits, confirmed application/status
      records, and completed task records—not simulated productivity.
    </p>
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
    <h2>Free / busy in displayed range</h2>
    <p>
      All visible calendars; free events are excluded. Hide calendars to change
      the view, or query Google free/busy in Integrations.
    </p>
    <h3>Overlaps</h3>
    {#each data.conflicts as c}<p class="conflict">
        {data.events.find((e) => e.id === c.event_id)?.title} overlaps {c.with
          .map((id) => data.events.find((e) => e.id === id)?.title)
          .join(", ")} · {label(c.start)} → {label(c.end)}
      </p>{/each}{#if !data.conflicts.length}<p>No busy-event overlaps.</p>{/if}
    <h3>Free intervals</h3>
    {#each data.free as free}<div class="free-row">
        <span>{label(free.start)} → {label(free.end)}</span><button
          onclick={() =>
            newEvent(
              localInput(free.start, false, tz),
              localInput(free.end, false, tz),
            )}>Schedule here</button
        >
      </div>{/each}
  {:else if section === "integrations"}
    <section class="panel">
      <h2>Import / export</h2>
      <p>
        ICS imports preserve recurring masters, exclusions and exceptions. CSV
        headers: <code>title,date</code>; optional
        <code>job_id,kind,timezone,url,description</code>. Application imports
        create local copies only.
      </p>
      <div class="controls">
        <label class="file"
          >Preview ICS<input
            type="file"
            accept=".ics,text/calendar"
            onchange={(e) => importFile(e.target, "ics")}
          /></label
        ><label class="file"
          >Preview CSV<input
            type="file"
            accept=".csv,text/csv"
            onchange={(e) => importFile(e.target, "csv")}
          /></label
        ><button onclick={exportFile}>Export ICS</button><button
          onclick={refreshSources}
          >Refresh linked application / opportunity dates</button
        >
      </div>
      {#if importRows.length}<p>{importNotice}</p>
        <div class="import-review">
          {#each importRows as row}<label
              ><input
                type="checkbox"
                bind:checked={row.selected}
                disabled={!!row.error || !!row.duplicate}
              /><span
                >{row.row ? `Row ${row.row}: ` : ""}{row.event?.title ||
                  row.error}{row.duplicate
                  ? " · existing UID/source, skipped"
                  : ""}<small
                  >{String(row.event?.start || "")} → {String(
                    row.event?.end || "",
                  )}
                  {String(row.event?.timezone || "")}
                  {String(row.event?.rrule || "")}</small
                ></span
              ></label
            >{/each}
        </div>
        <button
          onclick={applyImport}
          disabled={!importRows.some((v) => v.selected)}
          >Confirm selected import</button
        ><button onclick={() => (importRows = [])}>Cancel</button>{/if}
    </section>
    <section class="panel">
      <h2>Reminder notifications</h2>
      <p>
        In-app reminders are always shown while kitty is open. System
        notifications are opt-in and require this workspace to remain open; this
        is not a background alarm service.
      </p>
      <label
        ><input
          type="checkbox"
          checked={notifications}
          onchange={() => {
            if (!notifications) void enableNotifications();
            else {
              notifications = false;
              localStorage.removeItem("kitty-calendar-notifications");
            }
          }}
        />Enable system notifications</label
      >
    </section>
    <section class="panel">
      <h2>Google Calendar · optional</h2>
      {#if demo}<p class="notice">
          Demo supports every local action. Real Google authorization and API
          calls are blocked.
        </p>{/if}{#if googleStatus}<p>{googleStatus.notice}</p>
        <p>
          Client: {googleStatus.client_present
            ? "stored privately"
            : "not configured"} · Account: {googleStatus.authorized
            ? "authorized"
            : "not authorized"} · Tasks: {googleStatus.tasks_authorized
            ? "authorized"
            : "not authorized"}
        </p>
        <ol>
          {#each googleStatus.steps as step}<li>{step}</li>{/each}
        </ol>
        <div class="controls">
          <label class="file"
            >Upload Desktop client JSON<input
              type="file"
              accept=".json,application/json"
              disabled={demo || googleStatus.authorized}
              onchange={(e) => uploadClient(e.target)}
            /></label
          ><label
            ><input type="checkbox" bind:checked={includeTasks} />Also request
            Google Tasks scope</label
          ><button
            disabled={demo ||
              !googleStatus.client_present ||
              googleTask?.status === "running"}
            onclick={() =>
              googleWork("/calendar/google/authorize", {
                confirmed: true,
                include_tasks: includeTasks,
              })}>Review browser consent & authorize</button
          ><button
            disabled={demo || !googleStatus.authorized}
            onclick={() =>
              run(async () => {
                if (
                  window.confirm(
                    "Remove the separate Calendar token? Local data and Gmail token stay unchanged.",
                  )
                ) {
                  await api("/calendar/google/disconnect", {
                    confirmed: true,
                    revoke: false,
                  });
                  await loadGoogle();
                }
              })}>Disconnect locally</button
          ><button
            disabled={demo || !googleStatus.authorized}
            onclick={() =>
              run(async () => {
                if (
                  window.confirm(
                    "Revoke this separate Calendar authorization at Google and remove its token?",
                  )
                ) {
                  await api("/calendar/google/disconnect", {
                    confirmed: true,
                    revoke: true,
                  });
                  await loadGoogle();
                }
              })}>Revoke access</button
          >
        </div>
        <details>
          <summary>Capability inventory & account blockers</summary>
          <p>
            Available API resources: {googleStatus.capabilities.google.join(
              ", ",
            )}.
          </p>
          <p>
            Account/permission dependent: {googleStatus.capabilities.account_dependent.join(
              ", ",
            )}. Google denial is reported, never simulated.
          </p>
          {#each Object.entries(googleStatus.capabilities.unsupported) as [name, reason]}<p
            >
              <strong>{name}:</strong>
              {reason}
            </p>{/each}
          {#each googleStatus.limits as limit}<p>{limit}</p>{/each}
        </details>{/if}
      <h3>Select calendars & synchronize</h3>
      <button
        disabled={demo ||
          !googleStatus?.authorized ||
          googleTask?.status === "running"}
        onclick={() =>
          googleWork("/calendar/google/operation", {
            resource: "calendarList",
            action: "list",
          })}>List Google calendars</button
      >{#each remoteCalendars as c}<div class="free-row">
          <span>{String(c.summary)} · {String(c.accessRole || "")}</span><button
            disabled={demo ||
              !googleStatus?.authorized ||
              googleTask?.status === "running" ||
              googleStatus?.selected.some(
                (selected) => selected.google_id === c.id,
              )}
            onclick={() =>
              googleWork("/calendar/google/select", {
                google_id: c.id,
                confirmed: true,
              })}>Link locally</button
          >
        </div>{/each}
      <div class="controls">
        {#each googleStatus?.selected || [] as c}<label
            ><input
              type="checkbox"
              checked={linked.includes(c.id)}
              onchange={() =>
                (linked = linked.includes(c.id)
                  ? linked.filter((id) => id !== c.id)
                  : [...linked, c.id])}
            />{c.name}</label
          ><button
            disabled={demo || googleTask?.status === "running"}
            onclick={() =>
              run(async () => {
                if (
                  !window.confirm(
                    `Unlink ${c.name}? Local events remain; no Google deletion is made.`,
                  )
                )
                  return;
                await api("/calendar/google/unselect", {
                  calendar_id: c.id,
                  revision: c.revision,
                  confirmed: true,
                });
                await loadGoogle();
                await load();
              })}>Unlink {c.name}</button
          >{/each}
      </div>
      <div class="controls">
        <label
          >Direction<select bind:value={syncDirection}
            ><option value="pull"
              >Pull from Google (local conflicts reviewed)</option
            ><option value="both">Pull + push local changes</option></select
          ></label
        ><label
          >Attendee email updates<select bind:value={sendUpdates}
            ><option value="none">none (Google may still notify)</option><option
              value="all">all attendees</option
            ><option value="externalOnly">external attendees only</option
            ></select
          ></label
        >
      </div>
      <button
        disabled={demo ||
          !googleStatus?.authorized ||
          !linked.length ||
          googleTask?.status === "running"}
        onclick={() =>
          run(async () => {
            syncPreview = await api<Record<string, unknown>>(
              "/calendar/google/preview",
              { calendar_ids: linked },
            );
            syncApproved = false;
          })}>Prepare all pending writes & recipients for review</button
      >
      {#if syncPreview}<details open>
          <summary
            >Frozen sync preview · all dates, including deleted events</summary
          >
          <pre class="results">{JSON.stringify(syncPreview, null, 2)}</pre>
        </details>{/if}
      <label
        ><input
          type="checkbox"
          bind:checked={syncApproved}
          disabled={syncDirection === "both" && !syncPreview}
        />{syncDirection === "both"
          ? "I reviewed every pending write/recipient above and approve this notification policy."
          : "I approve fetching Google data. Local conflicts require review; no external changes are made."}</label
      >
      <div class="controls">
        <button
          disabled={demo ||
            !googleStatus?.authorized ||
            (syncDirection === "both" && !syncPreview) ||
            !linked.length ||
            !syncApproved ||
            googleTask?.status === "running"}
          onclick={() =>
            googleWork("/calendar/google/sync", {
              calendar_ids: linked,
              direction: syncDirection,
              send_updates: sendUpdates,
              confirmed: true,
              external_approved: syncApproved,
              review: syncPreview,
            })}>Run reviewed sync</button
        ><button
          disabled={demo ||
            !googleStatus?.authorized ||
            (syncDirection === "both" && !syncPreview) ||
            !linked.length ||
            !syncApproved ||
            googleTask?.status === "running"}
          onclick={() =>
            googleWork("/calendar/google/sync", {
              calendar_ids: linked,
              direction: syncDirection,
              send_updates: sendUpdates,
              confirmed: true,
              external_approved: syncApproved,
              review: syncPreview,
              full: true,
            })}>Full refresh</button
        >
      </div>
      {#each googleStatus?.calendar_conflicts || [] as conflict}<article
          class="conflict"
        >
          <h3>Calendar settings conflict</h3>
          <div class="comparison">
            <pre>{JSON.stringify(conflict.local, null, 2)}</pre>
            <pre>{JSON.stringify(conflict.remote.values, null, 2)}</pre>
          </div>
          {#each [["local", "Keep local settings (push later)"], ["remote", "Use Google settings"]] as [choice, title]}
            <button
              disabled={demo || googleTask?.status === "running"}
              onclick={() => {
                if (window.confirm(`${title}?`))
                  void googleWork("/calendar/google/resolve-calendar", {
                    calendar_id: conflict.calendar_id,
                    revision: conflict.local.revision,
                    choice,
                    confirmed: true,
                  });
              }}>{title}</button
            >{/each}
        </article>{/each}
      {#if googleConflicts.length}<h3>
          Conflicts · never silently overwritten
        </h3>
        {#each googleConflicts as c}<article class="conflict">
            <h4>{c.local.title}</h4>
            <div class="comparison">
              <pre>{JSON.stringify(c.local, null, 2)}</pre>
              <pre>{JSON.stringify(c.remote_json, null, 2)}</pre>
            </div>
            <div class="controls">
              {#each [["local", "Keep local (push later)"], ["remote", "Use Google version"], ["duplicate", "Save local copy + use Google"]] as [choice, title]}<button
                  disabled={demo || googleTask?.status === "running"}
                  onclick={() => {
                    if (window.confirm(`Confirm: ${title}?`))
                      void googleWork("/calendar/google/resolve", {
                        id: c.id,
                        revision: c.local.revision,
                        choice,
                        confirmed: true,
                      });
                  }}>{title}</button
                >{/each}
            </div>
          </article>{/each}{/if}
      <details class="google-tools">
        <summary>Google resource editor · exact reviewed API operations</summary
        >
        <p>
          List/get first, copy the returned ID and ETag, edit exact JSON, then
          review. Resources include calendar
          creation/timezone/color/subscription, event recurrence/RSVP/attendee
          updates, Meet, attachment links, reminders, sharing ACL, free/busy and
          optional Google Tasks. Primary settings are read-only; primary
          calendar timezone is edited via calendars/update. Focus
          time/OOO/working location may require eligible accounts. Appointment
          schedules are not exposed by Google.
        </p>
        <div class="controls">
          <label
            >Resource<select bind:value={resource} onchange={template}
              >{#each Object.keys(resourceActions) as r}<option>{r}</option
                >{/each}</select
            ></label
          ><label
            >Action<select bind:value={action} onchange={template}
              >{#each resourceActions[resource] as a}<option value={a}
                  >{actionLabels[a] || a}</option
                >{/each}</select
            ></label
          ><button onclick={template}>Fill example for resource</button>
        </div>
        <div class="form-grid">
          <label
            >Source calendar ID / source Tasklist ID<input
              bind:value={remoteCalendarId}
            /></label
          ><label
            >Exact resource / event / task / ACL ID<input
              bind:value={remoteId}
            /></label
          ><label>ETag from latest read<input bind:value={remoteEtag} /></label
          ><label
            ><input
              type="checkbox"
              bind:checked={meet}
              disabled={resource !== "events" ||
                !["create", "update", "replace", "import"].includes(action)}
            />Generate Google Meet (event create/update/replace/import)</label
          ><label
            ><input
              type="checkbox"
              bind:checked={notifySharing}
              disabled={resource !== "acl" || ["list", "get"].includes(action)}
            />Send sharing notifications (ACL changes)</label
          >
        </div>
        {#if googleResult && typeof googleResult === "object" && !Array.isArray(googleResult) && "etag" in googleResult && pendingGoogle.resource === resource}
          <button onclick={useGoogleResult}
            >Copy returned ID & ETag into this editor</button
          >{/if}
        <p class="hint">
          Move: set params.destination (Calendar) or
          destinationTasklist/parent/previous (Tasks). Import requires
          iCalUID/start/end and a normal event. Quick add sends exact data.text
          to Google's parser. Clear hides completed Tasks, or deletes every
          event in the primary Calendar; this is not undoable locally. Every
          change is previewed by kitty's server against live Google state (exact
          URL, body, ETag, target, recipients, clear listing) and is sent only
          with that frozen preview after your approval. Resource operations
          change Google directly; pull again to refresh linked local copies.
        </p>
        {#if action === "watch"}<p class="hint">
            A channel requires your own publicly reachable HTTPS webhook with
            valid TLS and a verified/authorized receiver. kitty's loopback
            server is not that receiver. Configure it separately before
            approving; registrations expire and are not auto-renewed. Use
            channels/stop with the returned id and resourceId.
          </p>
          <label
            ><input type="checkbox" bind:checked={webhookConfirmed} />
            I control and configured the public HTTPS receiver in data.address and
            reviewed what Google may deliver.</label
          >{/if}
        {#if resource === "events" && action === "quickAdd"}<p class="hint">
            Google has no text-parser dry run. This safe quick-add path uses
            sendUpdates=none and no attendee addresses. Use structured create
            for invitations, with every recipient explicitly reviewed.
          </p>{/if}
        <label
          >Exact resource data (JSON)<textarea
            rows="10"
            bind:value={remoteData}
            spellcheck="false"
          ></textarea></label
        ><label
          >Query parameters (JSON)<textarea
            rows="3"
            bind:value={remoteParams}
            spellcheck="false"
          ></textarea></label
        ><button
          disabled={demo ||
            !googleStatus?.authorized ||
            googleTask?.status === "running"}
          onclick={reviewOperation}
          >{["list", "get", "instances", "freebusy"].includes(action)
            ? "Run read-only request"
            : "Prepare exact change for review"}</button
        >{#if !googleStatus?.authorized}<p class="hint">
            {demo
              ? "External Google calls are disabled in demo mode."
              : "Not connected: upload a Desktop OAuth client and authorize Calendar above before using Google resources."}
          </p>{/if}{#if operationReview && operationRequest}<div
            class="review"
            role="group"
            aria-labelledby="operation-review-title"
          >
            <h4 id="operation-review-title">
              Frozen request prepared from live Google state
            </h4>
            <dl>
              <dt>Request</dt>
              <dd>
                <code>{operationReview.method} {operationReview.url}</code>
              </dd>
              <dt>Query parameters</dt>
              <dd><code>{JSON.stringify(operationReview.params)}</code></dd>
              <dt>If-Match ETag</dt>
              <dd>
                {operationReview.if_match ||
                  "none (not an ETag-checked change)"}
              </dd>
              <dt>Target</dt>
              <dd>
                {operationReview.current
                  ? JSON.stringify(operationReview.current)
                  : "new resource"}
              </dd>
              <dt>
                Recipients Google may notify ({operationReview.recipients
                  .length})
              </dt>
              <dd>
                {#if operationReview.recipients.length}<ul>
                    {#each operationReview.recipients as email}<li>
                        {email}
                      </li>{/each}
                  </ul>{:else}none listed{/if}
              </dd>
            </dl>
            <p>Exact request body:</p>
            <pre>{operationReview.body === null
                ? "(empty body)"
                : JSON.stringify(operationReview.body, null, 2)}</pre>
            {#each operationReview.notes as note}<p class="hint">
                {note}
              </p>{/each}
            {#if operationReview.action === "clear"}<p>
                {operationReview.items.length}
                {operationReview.resource === "calendars"
                  ? "events"
                  : "completed tasks"} affected:
              </p>
              {#if operationReview.items.length}<ol class="results">
                  {#each operationReview.items as item}<li>
                      {String(item.summary ?? item.title ?? "(untitled)")} ·
                      <code>{String(item.id)}</code>
                    </li>{/each}
                </ol>
                <label
                  ><input type="checkbox" bind:checked={clearConfirmed} />I
                  reviewed the full list above and confirm clearing all {operationReview
                    .items.length} items.</label
                >{/if}{/if}
            <label
              ><input type="checkbox" bind:checked={operationApproved} />I
              approve this exact external request, recipients, sharing
              role/scope and possible Google emails. Paid services/attachment
              permissions remain provider-controlled.</label
            ><button
              disabled={!operationApproved ||
                googleTask?.status === "running" ||
                (operationReview.items.length > 0 && !clearConfirmed)}
              onclick={() => void sendReviewedOperation()}
              >Send reviewed request</button
            >
          </div>{/if}
      </details>
      {#if googleTask}<p role="status">
          Google operation: {googleTask.kind} · {googleTask.status}{googleTask.status ===
          "running"
            ? " · waiting for Google / browser consent"
            : ""}
        </p>{/if}{#if googleResult}<details open>
          <summary>Google / import operation result</summary>
          <pre class="results">{JSON.stringify(googleResult, null, 2)}</pre>
        </details>{/if}
      <a
        href="https://calendar.google.com/calendar/u/0/r"
        target="_blank"
        rel="noreferrer"
      >
        Open Google Calendar for consumer-only features such as appointment
        schedules</a
      >
    </section>
  {/if}
</section>

{#if editor}<div class="modal-backdrop" role="presentation">
    <dialog
      use:modal
      oncancel={() => (editor = null)}
      class="dialog"
      aria-modal="true"
      aria-labelledby="event-editor-title"
      tabindex="-1"
    >
      <header>
        <h2 id="event-editor-title">
          {editor.id ? "Event details" : "New event"}
        </h2>
        <button onclick={() => (editor = null)} aria-label="Close event editor"
          >×</button
        >
      </header>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void saveEvent();
        }}
      >
        <label
          >Title<input
            required
            bind:value={editor.title}
            maxlength="500"
          /></label
        >
        <div class="form-grid">
          <label
            >Calendar<select
              bind:value={editor.calendar_id}
              disabled={!!editor.id && !!editor.rrule && editScope === "this"}
              >{#each data.calendars as c}<option value={c.id}>{c.name}</option
                >{/each}</select
            ></label
          ><label>Timezone<input bind:value={editor.timezone} required /></label
          ><label
            ><input
              type="checkbox"
              bind:checked={editor.all_day}
              onchange={() => {
                if (editor) {
                  startInput = editor.all_day
                    ? startInput.slice(0, 10)
                    : startInput + "T09:00";
                  const endDay = endInput.slice(0, 10);
                  endInput = editor.all_day
                    ? endInput.slice(11, 16) === "00:00"
                      ? endDay
                      : addDays(endDay, 1)
                    : addDays(endDay, -1) + "T10:00";
                }
              }}
            />All-day / multi-day</label
          ><label
            >DST ambiguous-time occurrence<select
              bind:value={fold}
              onchange={() => (foldChanged = true)}
              ><option value={0}>First occurrence</option><option value={1}
                >Second occurrence</option
              ></select
            ></label
          ><label
            >Start<input
              type={editor.all_day ? "date" : "datetime-local"}
              bind:value={startInput}
              required
            /></label
          ><label
            >Exclusive end<input
              type={editor.all_day ? "date" : "datetime-local"}
              bind:value={endInput}
              required
            /></label
          >
        </div>
        <p class="hint">
          Timezone is explicit. DST gaps are rejected; choose the first/second
          occurrence for repeated fall-back times. All-day end is the next date
          after the last included day.
        </p>
        {#if editor.id && editor.rrule}<label
            >Edit recurrence scope<select
              bind:value={editScope}
              onchange={changeScope}
              ><option value="this">This occurrence only</option><option
                value="future">This and future occurrences</option
              ><option value="all"
                >Entire series (date/time anchors remain original)</option
              ></select
            ></label
          >{/if}<label
          >RRULE (daily / weekly / monthly / yearly)<input
            bind:value={editor.rrule}
            disabled={!!editor.id && editScope === "this"}
            placeholder="FREQ=WEEKLY;BYDAY=MO,WE;COUNT=12"
          /></label
        >
        <p class="hint">
          Empty RRULE means one event. Use UTC UNTIL, e.g.
          UNTIL=20271231T235959Z. This/future selection uses the original
          occurrence, even after moving it.
        </p>
        <label
          >Excluded starts, one per line (date or aware ISO timestamp)<textarea
            rows="2"
            bind:value={exdatesText}
            disabled={!!editor.id && editScope === "this"}
          ></textarea></label
        ><label
          >Notes<textarea rows="4" bind:value={editor.description}
          ></textarea></label
        >
        <div class="form-grid">
          <label>Location<input bind:value={editor.location} /></label><label
            >URL<input type="url" bind:value={editor.url} /></label
          ><label
            >Privacy<select bind:value={editor.privacy}
              >{#each ["default", "public", "private", "confidential"] as p}<option
                  >{p}</option
                >{/each}</select
            ></label
          ><label
            ><input type="checkbox" bind:checked={editor.busy} />Blocks
            availability (busy)</label
          >
        </div>
        <label
          >Reminders before start, minutes (comma-separated; empty disables)<input
            bind:value={remindersText}
            placeholder="10,60,1440"
          /></label
        ><label
          >Attendees: email,name,RSVP,optional (true/false) per line<textarea
            rows="3"
            bind:value={attendeesText}
            placeholder="person@example.com,Person,needsAction"
          ></textarea></label
        >
        <p class="hint">
          RSVP: needsAction, accepted, declined, tentative. Local edits never
          email anyone. Google updates require separately approved sync or
          resource operation.
        </p>
        <label
          >Attachment links: title | https://URL per line<textarea
            rows="2"
            bind:value={attachmentsText}
          ></textarea></label
        ><label
          >Google special event type<select
            bind:value={editor.event_type}
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
            ><option value="default">Normal</option><option value="focusTime"
              >Focus time (eligible Google account)</option
            ><option value="outOfOffice"
              >Out of office (eligible Google account)</option
            ><option value="workingLocation"
              >Working location (eligible Google account)</option
            ></select
          ></label
        >
        <label
          >Google properties (exact JSON; account eligibility applies)<textarea
            rows="4"
            bind:value={googleOptionsText}
            spellcheck="false"
          ></textarea></label
        >
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
        <p class="hint">
          Changing recurrence scope reloads saved values: choose scope before
          editing.
        </p>
        <footer>
          <button type="submit">Save locally</button>{#if editor.id}<button
              type="button"
              class="danger"
              onclick={() => {
                if (
                  window.confirm(
                    "Delete the selected occurrence/scope? You can undo if it has not changed.",
                  )
                )
                  void saveEvent(true);
              }}>Delete selected scope</button
            >{/if}<button type="button" onclick={() => (editor = null)}
            >Cancel</button
          >
        </footer>
      </form>
    </dialog>
  </div>{/if}
{#if taskEditor}<div class="modal-backdrop" role="presentation">
    <dialog
      use:modal
      oncancel={() => (taskEditor = null)}
      class="dialog"
      aria-modal="true"
      aria-labelledby="task-editor-title"
      tabindex="-1"
    >
      <header>
        <h2 id="task-editor-title">
          {taskEditor.id ? "Edit task" : "New task"}
        </h2>
        <button
          onclick={() => (taskEditor = null)}
          aria-label="Close task editor">×</button
        >
      </header>
      <form
        onsubmit={(e) => {
          e.preventDefault();
          void saveTask();
        }}
      >
        <label>Task title<input required bind:value={taskEditor.title} /></label
        ><label
          >Notes<textarea rows="4" bind:value={taskEditor.description}
          ></textarea></label
        >
        <div class="form-grid">
          <label
            >Due date<input
              type={taskDueTimed ? "datetime-local" : "date"}
              bind:value={taskDueInput}
            /></label
          ><label
            ><input
              type="checkbox"
              bind:checked={taskDueTimed}
              onchange={() => {
                taskDueInput = taskDueInput
                  ? taskDueTimed
                    ? taskDueInput + "T09:00"
                    : taskDueInput.slice(0, 10)
                  : "";
              }}
            />Include due time</label
          ><label>Timezone<input bind:value={taskEditor.timezone} /></label
          ><label
            >Priority<select bind:value={taskEditor.priority}
              >{#each ["low", "normal", "high", "urgent"] as p}<option
                  >{p}</option
                >{/each}</select
            ></label
          ><label
            >Status<select bind:value={taskEditor.status}
              >{#each statuses as s}<option>{s}</option>{/each}</select
            ></label
          ><label>Project<input bind:value={taskEditor.project} /></label><label
            >Calendar<select bind:value={taskEditor.calendar_id}
              >{#each data.calendars as c}<option value={c.id}>{c.name}</option
                >{/each}</select
            ></label
          >
        </div>
        <div class="form-grid">
          <label
            >Repeat<select
              bind:value={taskEditor.repeat_frequency}
              disabled={editingCompletedTask}
            >
              <option value="">One-off task</option><option value="DAILY"
                >Daily</option
              >
              <option value="WEEKLY">Weekly</option><option value="MONTHLY"
                >Monthly</option
              >
              <option value="YEARLY">Yearly</option></select
            ></label
          >
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
        <p class="hint">
          Repeating tasks need a due date. Completing one keeps its actual
          completion and creates just the next future due instance; missed dates
          are not marked completed. Undo right after completing removes that
          untouched next instance. This recurrence stays local: Google's Tasks
          REST API cannot encode repeat rules, so kitty never sends them to
          Google.
        </p>
        {#if editingCompletedTask && taskEditor.repeat_frequency}<p
            class="hint"
          >
            This is completed history; change repetition on the next pending
            task instead.
          </p>{/if}
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
        <footer>
          <button type="submit">Save task</button>{#if taskEditor.id}<button
              type="button"
              class="danger"
              onclick={() => saveTask(true)}>Delete task</button
            >{/if}<button type="button" onclick={() => (taskEditor = null)}
            >Cancel</button
          >
        </footer>
      </form>
    </dialog>
  </div>{/if}
{#if calendarEditor}<div class="modal-backdrop" role="presentation">
    <dialog
      use:modal
      oncancel={() => (calendarEditor = null)}
      class="dialog small"
      aria-modal="true"
      aria-labelledby="calendar-editor-title"
      tabindex="-1"
    >
      <header>
        <h2 id="calendar-editor-title">Calendar settings</h2>
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
        ><label
          >Color<input type="color" bind:value={calendarEditor.color} /></label
        ><label
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
  h4,
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
  select,
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
  select:focus-visible,
  textarea:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .active {
    background: var(--accent);
    color: var(--surface);
  }
  input,
  textarea,
  select {
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
  form > label,
  .google-tools > label {
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
  .toolbar > div {
    margin-right: auto;
  }
  .toolbar p,
  .hint,
  .toolbar span {
    color: var(--muted);
    font-size: 0.85rem;
  }
  .tabs {
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.6rem;
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
  .notice,
  .reminders,
  .panel {
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
  .reminders > div {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    flex-wrap: wrap;
    margin: 0.4rem 0;
  }
  .reminders button {
    margin-left: auto;
  }
  .month-grid {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    border-left: 1px solid var(--border);
    border-top: 1px solid var(--border);
  }
  .day-cell {
    min-height: 120px;
    padding: 0.35rem;
    border-right: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    background: var(--surface);
    min-width: 0;
  }
  .outside {
    opacity: 0.55;
  }
  .day-head {
    width: 100%;
    border: 0;
    background: transparent;
    text-align: left;
    font-size: 0.8rem;
    touch-action: none;
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
  }
  .time-scroll {
    overflow: auto;
    max-height: 70vh;
  }
  .time-grid {
    display: grid;
    grid-template-columns: 55px repeat(var(--days), minmax(110px, 1fr));
    border-top: 1px solid var(--border);
    min-width: calc(var(--days) * 110px + 55px);
  }
  .time-heading {
    position: sticky;
    top: 0;
    z-index: 3;
    background: var(--surface);
    padding: 0.5rem;
    border: 1px solid var(--border);
    font-size: 0.8rem;
  }
  .time-heading > button {
    font-size: 0.75rem;
  }
  .time-label {
    text-align: right;
    padding: 0.2rem 0.4rem;
    color: var(--muted);
    font-size: 0.72rem;
    background: var(--canvas);
  }
  .time-cell {
    min-height: 34px;
    position: relative;
    border-right: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
    background: var(--surface);
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
  .timed {
    position: relative;
    z-index: 1;
    margin: 0.15rem;
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
  .board {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
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
  .task-card select {
    max-width: 7rem;
    font-size: 0.8rem;
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
  .free-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 0.7rem;
    padding: 0.55rem 0;
    border-bottom: 1px solid var(--border);
  }
  .conflict {
    padding: 0.8rem;
    border: 1px solid var(--warning);
    border-radius: 0.5rem;
    margin: 0.6rem 0;
  }
  .file {
    border: 1px solid var(--border);
    padding: 0.5rem;
    border-radius: 0.4rem;
    cursor: pointer;
    flex-wrap: wrap;
  }
  .file input {
    max-width: 15rem;
    font-size: 0.8rem;
  }
  .import-review {
    max-height: 24rem;
    overflow: auto;
  }
  .import-review label {
    padding: 0.5rem;
    border-bottom: 1px solid var(--border);
  }
  small {
    display: block;
    color: var(--muted);
    margin-top: 0.3rem;
  }
  .panel ol {
    font-size: 0.9rem;
    padding-left: 1.3rem;
  }
  .panel li {
    margin: 0.45rem 0;
  }
  details {
    margin: 0.8rem 0;
  }
  summary {
    cursor: pointer;
    font-weight: 600;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    background: var(--canvas);
    padding: 0.8rem;
    border-radius: 0.4rem;
    font-size: 0.75rem;
    max-height: 28rem;
    overflow: auto;
  }
  .comparison {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.7rem;
  }
  .review {
    border: 1px solid var(--accent);
    padding: 1rem;
    margin-top: 1rem;
    border-radius: 0.5rem;
  }
  .review button {
    margin-top: 0.8rem;
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
    width: min(760px, 100%);
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
  .form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.7rem;
  }
  .form-grid label {
    flex-direction: column;
    align-items: stretch;
  }
  .form-grid label:has(input[type="checkbox"]) {
    flex-direction: row;
    align-items: center;
  }
  .dialog footer {
    margin-top: 1rem;
  }
  .danger {
    color: var(--warning);
  }
  a {
    color: var(--accent);
  }
  @media (max-width: 760px) {
    .board {
      grid-template-columns: 1fr 1fr;
    }
    .month-grid {
      overflow: auto;
      grid-template-columns: repeat(7, minmax(100px, 1fr));
    }
    .form-grid,
    .comparison {
      grid-template-columns: 1fr;
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
