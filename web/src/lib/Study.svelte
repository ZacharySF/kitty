<script lang="ts">
  import { onMount } from "svelte";
  import { Button } from "@nunera/bracket";
  import { BarChart } from "@nunera/bracket/charts";
  import { api } from "./api";
  import AIReview from "./AIReview.svelte";

  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  type Item = {
    id: number;
    revision: number;
    name?: string;
    title?: string;
    color?: string;
    subject_id?: number | null;
    project_id?: number | null;
    period?: string;
    minutes?: number;
    priority?: string;
    due_date?: string | null;
    done?: boolean;
    checklist?: { id: string; text: string; done: boolean }[];
    exam_date?: string;
    timezone?: string;
    notes?: string;
    exam_id?: number | null;
    start?: string;
    end?: string;
  };
  type Session = {
    id: number;
    revision: number;
    source: string;
    subject_id: number | null;
    project_id: number | null;
    target_id: number | null;
    started_at: string;
    ended_at: string;
    timezone: string;
    duration_seconds: number;
    notes: string;
    distractions: string;
  };
  type Timer = {
    revision: number;
    status: "idle" | "running" | "paused" | "awaiting_next";
    phase: "focus" | "short_break" | "long_break";
    phase_id: string | null;
    remaining_seconds: number;
    deadline_at: string | null;
    subject_id: number | null;
    project_id: number | null;
    target_id: number | null;
    timezone: string;
    notes: string;
    distractions: string;
    cycle: number;
  };
  type Settings = {
    revision: number;
    timezone: string;
    focus_seconds: number;
    short_break_seconds: number;
    long_break_seconds: number;
    cycle_count: number;
    sound: boolean;
    notifications: boolean;
  };
  type Stats = {
    timezone: string;
    today: string;
    today_seconds: number;
    week_seconds: number;
    total_seconds: number;
    daily_streak: number;
    weekly_streak: number;
    session_count: number;
    heatmap: { date: string; seconds: number }[];
    days: { date: string; seconds: number }[];
    subjects: {
      id: number | null;
      name: string;
      color: string;
      seconds: number;
    }[];
    goals: { id: number; seconds: number; goal_seconds: number }[];
  };
  type Workspace = {
    timer: Timer;
    settings: Settings;
    server_now: string;
    stats: Stats;
    subjects: Item[];
    projects: Item[];
    targets: Item[];
    tasks: Item[];
    exams: Item[];
    plans: Item[];
    sessions: Session[];
  };
  type Collection = "subjects" | "projects" | "targets" | "tasks" | "exams";
  let data = $state<Workspace | null>(null);
  let error = $state("");
  let notice = $state("");
  let online = $state(true);
  let busy = $state(false);
  let now = $state(Date.now());
  let serverOffset = $state(0);
  let loaded = $state(false);
  let focusMode = $state(false);
  let tab = $state("overview");
  let subjectId = $state("");
  let projectId = $state("");
  let targetId = $state("");
  let notes = $state("");
  let distractions = $state("");
  let notesDirty = $state(false);
  let noteRevision = $state(0);
  let settings = $state<Settings | null>(null);
  let settingsDirty = $state(false);
  let editorKind = $state<Collection | "">("");
  let editor = $state<Item>({ id: 0, revision: 0 });
  let checklistText = $state("");
  let sessionOpen = $state(false);
  let sessionId = $state<number | null>(null);
  let sessionRevision = $state(0);
  let sessionStart = $state("");
  let sessionZone = $state("UTC");
  let sessionMinutes = $state(25);
  let sessionSubject = $state("");
  let sessionProject = $state("");
  let sessionNotes = $state("");
  let sessionDistractions = $state("");
  let planExam = $state("");
  let planStart = $state("");
  let planMinutes = $state(180);
  let planBlock = $state(60);
  let planTime = $state("17:00");
  let replacePlan = $state(false);
  let aiText = $state("");
  let mounted = false;
  let inFlight = false;
  let notePhase: string | null = null;
  const snapshotKey = "kitty-study-snapshot";
  const minutesLabel = (seconds: number) => `${Math.round(seconds / 60)} min`;
  const selectedId = (value: string) => (value ? Number(value) : null);
  const subjectName = (id: number | null | undefined) =>
    data?.subjects.find((item) => item.id === id)?.name || "Unassigned";
  const phaseLabel = (phase: string) =>
    phase === "focus"
      ? "Focus"
      : phase === "long_break"
        ? "Long break"
        : "Short break";
  const remaining = $derived(
    data?.timer.status === "running" && data.timer.deadline_at
      ? Math.max(
          0,
          (Date.parse(data.timer.deadline_at) - now - serverOffset) / 1000,
        )
      : data?.timer.remaining_seconds || 0,
  );
  const clockLabel = $derived(
    `${Math.floor(Math.ceil(remaining) / 60)
      .toString()
      .padStart(
        2,
        "0",
      )}:${(Math.ceil(remaining) % 60).toString().padStart(2, "0")}`,
  );
  const daySeries = $derived([
    {
      name: "Study minutes",
      points: (data?.stats.heatmap.slice(-14) || []).map((day, index) => ({
        x: index,
        y: day.seconds / 60,
        label: day.date,
      })),
    },
  ]);
  const subjectSeries = $derived([
    {
      name: "Study minutes by subject",
      points: (data?.stats.subjects || []).map((subject, index) => ({
        x: index,
        y: subject.seconds / 60,
        label: subject.name,
      })),
    },
  ]);

  function accept(result: Workspace) {
    data = result;
    serverOffset = Date.parse(result.server_now) - Date.now();
    online = true;
    if (!settingsDirty) settings = { ...result.settings };
    if (notePhase !== result.timer.phase_id) {
      notePhase = result.timer.phase_id;
      notesDirty = false;
      notes = result.timer.notes;
      distractions = result.timer.distractions;
      noteRevision = result.timer.revision;
    } else if (!notesDirty) {
      notes = result.timer.notes;
      distractions = result.timer.distractions;
      noteRevision = result.timer.revision;
    }
    if (!loaded) {
      subjectId = result.timer.subject_id?.toString() || "";
      projectId = result.timer.project_id?.toString() || "";
      targetId = result.timer.target_id?.toString() || "";
      sessionZone = result.settings.timezone;
      planStart = result.stats.today;
    }
    loaded = true;
    try {
      localStorage.setItem(snapshotKey, JSON.stringify(result));
    } catch {
      /* Storage may be disabled; server persistence remains authoritative. */
    }
  }
  async function load(silent = false) {
    if (inFlight) return;
    inFlight = true;
    try {
      const result = await api<Workspace>("/study");
      accept(result);
      if (!silent) error = "";
    } catch (cause) {
      online = false;
      error = (cause as Error).message;
    } finally {
      inFlight = false;
    }
  }
  async function change(path: string, body: unknown): Promise<boolean> {
    if (busy) return false;
    busy = true;
    error = "";
    notice = "";
    try {
      await api(path, body);
      await load(true);
      window.dispatchEvent(new CustomEvent("kitty-study-changed"));
      return true;
    } catch (cause) {
      error = (cause as Error).message;
      await load(true);
      return false;
    } finally {
      busy = false;
    }
  }
  async function timerAction(action: string) {
    if (!data || busy || !online) return;
    if (
      (action === "stop" || action === "reset") &&
      data.timer.status !== "idle" &&
      !confirm(
        "Discard unfinished focus time? Completed sessions stay in your history; breaks never count.",
      )
    )
      return;
    const includeNotes =
      action === "annotate" ||
      ((action === "pause" || action === "resume") && notesDirty);
    const saved = await change("/study/timer", {
      action,
      revision: includeNotes && notesDirty ? noteRevision : data.timer.revision,
      request_id: crypto.randomUUID(),
      ...(action === "start"
        ? {
            subject_id: selectedId(subjectId),
            project_id: selectedId(projectId),
            target_id: selectedId(targetId),
            timezone: data.settings.timezone,
            notes,
            distractions,
          }
        : {}),
      ...(includeNotes ? { notes, distractions } : {}),
    });
    if (saved && includeNotes) {
      notesDirty = false;
      noteRevision = data.timer.revision;
    }
  }
  function markNotesDirty() {
    if (!notesDirty) noteRevision = data?.timer.revision || 0;
    notesDirty = true;
  }
  function setNoteDraft(value: string) {
    markNotesDirty();
    notes = value;
  }
  export async function handleKey(event: KeyboardEvent): Promise<boolean> {
    if (event.ctrlKey || event.metaKey || event.altKey || event.repeat)
      return false;
    const target = event.target as HTMLElement | null;
    if (target?.closest("input,textarea,select,[contenteditable=true],button"))
      return false;
    const key = event.key.toLowerCase();
    if (key === " " || key === "p") {
      event.preventDefault();
      await timerAction(
        data?.timer.status === "running"
          ? "pause"
          : data?.timer.status === "paused"
            ? "resume"
            : "start",
      );
      return true;
    }
    if (key === "s" || key === "r") {
      event.preventDefault();
      await timerAction(key === "s" ? "stop" : "reset");
      return true;
    }
    if (key === "b" && data?.timer.phase !== "focus") {
      event.preventDefault();
      await timerAction("skip_break");
      return true;
    }
    if (key === "f" || (key === "escape" && focusMode)) {
      event.preventDefault();
      focusMode = !focusMode;
      return true;
    }
    return false;
  }
  onMount(() => {
    mounted = true;
    try {
      const cached = localStorage.getItem(snapshotKey);
      if (cached) {
        data = JSON.parse(cached) as Workspace;
        settings = { ...data.settings };
        online = false;
      }
      const draft = localStorage.getItem("kitty-study-draft");
      if (draft) {
        const value = JSON.parse(draft);
        aiText = typeof value.text === "string" ? value.text : "";
        if (
          value.notePhase === (data?.timer.phase_id || null) &&
          typeof value.notes === "string"
        ) {
          notePhase = value.notePhase;
          notes = value.notes;
          distractions =
            typeof value.distractions === "string" ? value.distractions : "";
          notesDirty = value.notesDirty === true;
          noteRevision = Number.isInteger(value.noteRevision)
            ? value.noteRevision
            : 0;
        }
      }
    } catch {
      /* A malformed or unavailable cache does not replace real server state. */
    }
    void load();
    const tick = window.setInterval(() => {
      now = Date.now();
      if (data?.timer.status === "running" && remaining === 0) void load(true);
    }, 250);
    const refresh = window.setInterval(() => {
      if (!busy) void load(true);
    }, 5000);
    const wake = () => {
      if (document.visibilityState === "visible") void load(true);
    };
    const changed = () => void load(true);
    const textEvent = (event: Event) => {
      const detail = (event as CustomEvent<{ text?: string; purpose?: string }>)
        .detail;
      if (
        detail?.purpose !== "flashcards" &&
        typeof detail?.text === "string"
      ) {
        aiText = detail.text;
        tab = "helpers";
      }
    };
    window.addEventListener("focus", changed);
    window.addEventListener("online", changed);
    window.addEventListener("kitty-study-changed", changed);
    window.addEventListener("kitty-study-text", textEvent);
    document.addEventListener("visibilitychange", wake);
    return () => {
      mounted = false;
      window.clearInterval(tick);
      window.clearInterval(refresh);
      window.removeEventListener("focus", changed);
      window.removeEventListener("online", changed);
      window.removeEventListener("kitty-study-changed", changed);
      window.removeEventListener("kitty-study-text", textEvent);
      document.removeEventListener("visibilitychange", wake);
    };
  });
  $effect(() => {
    revision;
    if (mounted) void load(true);
  });
  $effect(() => {
    if (mounted) {
      try {
        localStorage.setItem(
          "kitty-study-draft",
          JSON.stringify({
            text: aiText,
            notes,
            distractions,
            notesDirty,
            noteRevision,
            notePhase,
          }),
        );
      } catch {
        /* Optional local draft cache. */
      }
    }
  });

  function openEditor(kind: Collection, item?: Item) {
    editorKind = kind;
    editor = item
      ? structuredClone($state.snapshot(item))
      : {
          id: 0,
          revision: 0,
          name: "",
          title: "",
          color: "#7c6fdb",
          subject_id: null,
          project_id: null,
          period: "weekly",
          minutes: 300,
          priority: "normal",
          due_date: "",
          done: false,
          checklist: [],
          exam_date: data?.stats.today,
          timezone: data?.settings.timezone || "UTC",
          notes: "",
        };
    checklistText = "";
  }
  async function saveEditor(event: SubmitEvent) {
    event.preventDefault();
    const kind = editorKind;
    if (!kind) return;
    const identity = editor.id
      ? { id: editor.id, revision: editor.revision }
      : {};
    const common = { subject_id: editor.subject_id || null };
    const fields =
      kind === "subjects"
        ? { name: editor.name, color: editor.color }
        : kind === "projects"
          ? { name: editor.name, ...common }
          : kind === "targets"
            ? {
                title: editor.title,
                ...common,
                project_id: editor.project_id || null,
                period: editor.period,
                minutes: Number(editor.minutes),
              }
            : kind === "tasks"
              ? {
                  title: editor.title,
                  ...common,
                  project_id: editor.project_id || null,
                  priority: editor.priority,
                  due_date: editor.due_date || null,
                  done: editor.done || false,
                  checklist: editor.checklist || [],
                }
              : {
                  title: editor.title,
                  ...common,
                  exam_date: editor.exam_date,
                  timezone: editor.timezone,
                  notes: editor.notes || "",
                };
    if (
      await change(`/study/${kind}`, { action: "save", ...identity, ...fields })
    )
      editorKind = "";
  }
  async function deleteItem(kind: Collection, item: Item) {
    if (
      confirm(
        `Delete ${item.name || item.title}? Completed session history stays; deleting an exam removes its local plan.`,
      )
    )
      await change(`/study/${kind}`, {
        action: "delete",
        id: item.id,
        revision: item.revision,
      });
  }
  async function toggleTask(task: Item) {
    await change("/study/tasks", {
      id: task.id,
      revision: task.revision,
      done: !task.done,
    });
  }
  async function toggleChecklist(task: Item, checklistId: string) {
    await change("/study/tasks", {
      id: task.id,
      revision: task.revision,
      checklist: (task.checklist || []).map((item) =>
        item.id === checklistId ? { ...item, done: !item.done } : item,
      ),
    });
  }
  function addChecklist() {
    if (checklistText.trim()) {
      editor.checklist = [
        ...(editor.checklist || []),
        { id: crypto.randomUUID(), text: checklistText.trim(), done: false },
      ];
      checklistText = "";
    }
  }
  function openSession(session?: Session) {
    sessionOpen = true;
    sessionId = session?.id || null;
    sessionRevision = session?.revision || 0;
    sessionStart =
      session?.started_at || new Date(Date.now() - 25 * 60000).toISOString();
    sessionZone = session?.timezone || data?.settings.timezone || "UTC";
    sessionMinutes = session ? session.duration_seconds / 60 : 25;
    sessionSubject = session?.subject_id?.toString() || subjectId;
    sessionProject = session?.project_id?.toString() || projectId;
    sessionNotes = session?.notes || "";
    sessionDistractions = session?.distractions || "";
  }
  async function saveSession(event: SubmitEvent) {
    event.preventDefault();
    if (
      await change("/study/sessions", {
        action: "save",
        request_id: crypto.randomUUID(),
        ...(sessionId ? { id: sessionId, revision: sessionRevision } : {}),
        started_at: sessionStart,
        timezone: sessionZone,
        duration_seconds: Math.round(sessionMinutes * 60),
        subject_id: selectedId(sessionSubject),
        project_id: selectedId(sessionProject),
        notes: sessionNotes,
        distractions: sessionDistractions,
      })
    )
      sessionOpen = false;
  }
  async function deleteSession(session: Session) {
    if (
      confirm(
        "Delete this completed session? Its study time and streak contribution will be removed.",
      )
    )
      await change("/study/sessions", {
        action: "delete",
        id: session.id,
        revision: session.revision,
        request_id: crypto.randomUUID(),
      });
  }
  async function saveSettings(event: SubmitEvent) {
    event.preventDefault();
    if (!settings) return;
    if (
      settings.notifications &&
      "Notification" in window &&
      Notification.permission !== "granted"
    ) {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        error =
          "Notifications were not authorized; timer and sound still work.";
        settings.notifications = false;
      }
    }
    if (await change("/study/settings", settings)) {
      settingsDirty = false;
      await load(true);
      notice = "Preferences saved. Active phase durations are unchanged.";
    }
  }
  async function generatePlan(event: SubmitEvent) {
    event.preventDefault();
    const exam = data?.exams.find((item) => item.id === Number(planExam));
    if (!exam) {
      error = "Choose an exam.";
      return;
    }
    if (
      await change("/study/generate-plan", {
        exam_id: exam.id,
        exam_revision: exam.revision,
        start_date: planStart,
        minutes: Number(planMinutes),
        block_minutes: Number(planBlock),
        local_time: planTime,
        replace: replacePlan,
      })
    )
      notice =
        "Your dated plan is saved locally. Export blocks to your calendar explicitly.";
  }
  async function exportBlock(block: Item) {
    if (
      !confirm(
        "Add this study block to your local calendar? Google sync remains a separate calendar choice.",
      )
    )
      return;
    if (
      await change("/calendar/events", {
        title: block.title,
        start: block.start,
        end: block.end,
        timezone: block.timezone,
        all_day: false,
        source_key: `study-plan:${block.id}`,
        description: `Study block for ${subjectName(block.subject_id)}. Created from your saved study plan.`,
      })
    )
      notice = "Calendar block saved; repeated export does not duplicate it.";
  }
</script>

<section
  class:focus-mode={focusMode}
  class="study"
  aria-label="Study workspace"
>
  <header class="section-header">
    <div>
      <h2>Study</h2>
      <p>Small focused sessions. Honest time. A plan with actual dates.</p>
    </div>
    <Button onclick={() => (focusMode = !focusMode)}
      >{focusMode ? "Exit focus mode" : "Focus mode"} <kbd>f</kbd></Button
    >
  </header>
  {#if demo}<p class="muted">
      Demo study records stay local; external AI and Google calls are disabled.
    </p>{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if notice}<p class="notice" role="status">{notice}</p>{/if}
  {#if !online}<p class="offline" role="status">
      Showing your last saved snapshot. Timer display is an estimate until the
      local server reconnects. Changes are not queued silently. <Button
        onclick={() => load()}>Reconnect</Button
      >
    </p>{/if}
  {#if data}
    <div class="timer-card">
      <div class="timer-heading">
        <span
          >{phaseLabel(data.timer.phase)} · {data.timer.status.replaceAll(
            "_",
            " ",
          )} · {data.timer.cycle} completed focus sessions</span
        ><span>{data.settings.timezone}</span>
      </div>
      <div
        class="clock"
        aria-label={`${phaseLabel(data.timer.phase)} remaining ${clockLabel}`}
      >
        {clockLabel}
      </div>
      {#if data.timer.status === "awaiting_next"}<p role="status">
          {data.timer.phase === "focus"
            ? "Break finished. Start your next focus session when ready."
            : "Focus completed and saved. Choose to take this break or skip it."}
        </p>{/if}
      <div class="row actions">
        {#if data.timer.status === "running"}<Button
            disabled={busy || !online}
            onclick={() => timerAction("pause")}>Pause <kbd>Space</kbd></Button
          >
        {:else if data.timer.status === "paused"}<Button
            disabled={busy || !online}
            onclick={() => timerAction("resume")}
            >Resume <kbd>Space</kbd></Button
          >
        {:else}<Button
            disabled={busy || !online}
            onclick={() => timerAction("start")}
            >Start {phaseLabel(data.timer.phase).toLowerCase()}
            <kbd>Space</kbd></Button
          >{/if}
        <Button
          disabled={busy || !online || data.timer.status === "idle"}
          onclick={() => timerAction("stop")}>Stop <kbd>s</kbd></Button
        >
        <Button disabled={busy || !online} onclick={() => timerAction("reset")}
          >Reset <kbd>r</kbd></Button
        >
        {#if data.timer.phase !== "focus"}<Button
            disabled={busy || !online}
            onclick={() => timerAction("skip_break")}
            >Skip break <kbd>b</kbd></Button
          >{/if}
      </div>
      <p class="muted">
        Completed focus only. Stop discards unfinished time; paused time and all
        breaks never count. Phases never auto-chain while you're away.
      </p>
      <div class="form-grid">
        <label
          >Subject<select
            bind:value={subjectId}
            disabled={data.timer.status === "running" ||
              data.timer.status === "paused"}
            ><option value="">Unassigned</option
            >{#each data.subjects as item}<option value={item.id.toString()}
                >{item.name}</option
              >{/each}</select
          ></label
        >
        <label
          >Project<select
            bind:value={projectId}
            disabled={data.timer.status === "running" ||
              data.timer.status === "paused"}
            ><option value="">None</option>{#each data.projects as item}<option
                value={item.id.toString()}>{item.name}</option
              >{/each}</select
          ></label
        >
        <label
          >Goal<select
            bind:value={targetId}
            disabled={data.timer.status === "running" ||
              data.timer.status === "paused"}
            ><option value="">None</option>{#each data.targets as item}<option
                value={item.id.toString()}>{item.title}</option
              >{/each}</select
          ></label
        >
      </div>
      <div class="form-grid">
        <label
          >Session notes / reflection<textarea
            rows="2"
            bind:value={notes}
            oninput={markNotesDirty}
            placeholder="What did I recall without looking? What needs another pass?"
          ></textarea></label
        ><label
          >Distractions<textarea
            rows="2"
            bind:value={distractions}
            oninput={markNotesDirty}
            placeholder="Capture it here, then return to the task."
          ></textarea></label
        >
      </div>
      {#if notesDirty}
        <p class="muted">
          Unsaved local note draft. Save during the session or copy it into a
          completed session from the Sessions tab.
        </p>
        <details>
          <summary>Compare with latest saved notes</summary>
          <p class="preserve">{data.timer.notes || "No saved notes."}</p>
          <p class="preserve">
            {data.timer.distractions || "No saved distractions."}
          </p>
        </details>
        <Button
          onclick={() => {
            if (
              confirm(
                "Discard your local note draft and use the latest saved notes?",
              )
            ) {
              notes = data!.timer.notes;
              distractions = data!.timer.distractions;
              noteRevision = data!.timer.revision;
              notesDirty = false;
            }
          }}>Use saved notes</Button
        >
        {#if noteRevision !== data.timer.revision && (data.timer.status === "running" || data.timer.status === "paused")}
          <Button
            onclick={() => {
              if (
                confirm(
                  "You reviewed the latest saved notes. Keep your local draft and explicitly allow replacing them on the next save?",
                )
              )
                noteRevision = data!.timer.revision;
            }}>Keep reviewed local draft</Button
          >
        {/if}
      {/if}
      {#if data.timer.status === "running" || data.timer.status === "paused"}<Button
          disabled={busy || !online}
          onclick={() => timerAction("annotate")}>Save session notes</Button
        >{/if}
    </div>
    <div class="tasks-focus" class:hidden={!focusMode}>
      <h3>Next tasks</h3>
      {#each data.tasks.filter((item) => !item.done).slice(0, 5) as task}<label
          class="check-row"
          ><input
            type="checkbox"
            checked={task.done}
            disabled={busy || !online}
            onchange={() => toggleTask(task)}
          />{task.title}</label
        >{:else}<p>
          Add a task in the task list, or focus on one thing you choose.
        </p>{/each}
    </div>
    <div class:hidden={focusMode}>
      <nav class="tabs" aria-label="Study tools">
        {#each ["overview", "tasks", "subjects", "sessions", "exams", "helpers", "settings"] as name}<Button
            aria-pressed={tab === name}
            onclick={() => (tab = name)}>{name}</Button
          >{/each}
      </nav>
      {#if tab === "overview"}
        <div class="summary">
          <article>
            <strong>{minutesLabel(data.stats.today_seconds)}</strong><span
              >Today</span
            >
          </article>
          <article>
            <strong>{minutesLabel(data.stats.week_seconds)}</strong><span
              >This week</span
            >
          </article>
          <article>
            <strong>{data.stats.daily_streak} days</strong><span
              >Daily completion streak</span
            >
          </article>
          <article>
            <strong>{data.stats.weekly_streak} weeks</strong><span
              >Weekly completion streak</span
            >
          </article>
        </div>
        <p class="muted">
          Reports use {data.stats.timezone}. Time is split across local
          midnights; streaks use completed session dates, never logins. An
          ongoing streak can include yesterday / last week.
        </p>
        <div class="panel">
          <h3>Study activity · last 12 weeks</h3>
          <div class="heatmap" aria-label="Daily study activity">
            {#each data.stats.heatmap as cell}<div
                class="heat-cell"
                style:opacity={cell.seconds
                  ? Math.min(1, 0.3 + cell.seconds / 14400)
                  : 0.12}
                title={`${cell.date}: ${minutesLabel(cell.seconds)}`}
                aria-label={`${cell.date}: ${minutesLabel(cell.seconds)}`}
              ></div>{/each}
          </div>
          <p class="muted">
            Darker means more completed study time. Total: {minutesLabel(
              data.stats.total_seconds,
            )} across {data.stats.session_count} sessions.
          </p>
        </div>
        <div class="chart-grid">
          <article class="panel">
            <h3>Last 14 days · minutes</h3>
            <BarChart
              series={daySeries}
              minY={0}
              label="Study minutes for the last fourteen dates"
              width={520}
              height={220}
            />
            <details>
              <summary>View time data</summary
              >{#each data.stats.heatmap.slice(-14) as day}<p>
                  {day.date}: {minutesLabel(day.seconds)}
                </p>{/each}
            </details>
          </article>
          <article class="panel">
            <h3>Time by subject · minutes</h3>
            <BarChart
              series={subjectSeries}
              minY={0}
              label="Completed study time by subject"
              width={520}
              height={220}
            />
            <ul>
              {#each data.stats.subjects as subject}<li>
                  <span style:color={subject.color}>{subject.name}</span>: {minutesLabel(
                    subject.seconds,
                  )}
                </li>{:else}<li>
                  Your first completed session will appear here.
                </li>{/each}
            </ul>
          </article>
        </div>
        <div class="panel">
          <div class="row">
            <h3>Goals</h3>
            <Button onclick={() => openEditor("targets")}>Add goal</Button>
          </div>
          {#each data.targets as target}{@const goal = data.stats.goals.find(
              (item) => item.id === target.id,
            )}
            <div class="goal">
              <div>
                <strong>{target.title}</strong> · {target.period} · {subjectName(
                  target.subject_id,
                )}
              </div>
              <progress value={goal?.seconds || 0} max={goal?.goal_seconds || 1}
              ></progress><span
                >{minutesLabel(goal?.seconds || 0)} / {target.minutes} min</span
              ><Button onclick={() => openEditor("targets", target)}
                >Edit</Button
              ><Button onclick={() => deleteItem("targets", target)}
                >Delete</Button
              >
            </div>{:else}<p>
              Set a daily or weekly time target for all study, a subject, or a
              project.
            </p>{/each}
        </div>
      {:else if tab === "tasks"}
        <div class="row">
          <h3>Study tasks</h3>
          <Button onclick={() => openEditor("tasks")}>Add task</Button>
        </div>
        {#each [...data.tasks].sort((a, b) => Number(a.done) - Number(b.done) || ({ high: 0, normal: 1, low: 2 }[a.priority as "high" | "normal" | "low"] || 0) - ({ high: 0, normal: 1, low: 2 }[b.priority as "high" | "normal" | "low"] || 0)) as task}<article
            class="panel"
            class:completed={task.done}
          >
            <div class="row">
              <label class="check-row"
                ><input
                  type="checkbox"
                  checked={task.done}
                  disabled={busy || !online}
                  onchange={() => toggleTask(task)}
                /><strong>{task.title}</strong></label
              ><span
                >{task.priority} · {subjectName(task.subject_id)}
                {task.due_date ? `· due ${task.due_date}` : ""}</span
              ><Button onclick={() => openEditor("tasks", task)}>Edit</Button
              ><Button onclick={() => deleteItem("tasks", task)}>Delete</Button>
            </div>
            {#each task.checklist || [] as item}<label
                class="check-row checklist"
                ><input
                  type="checkbox"
                  checked={item.done}
                  disabled={busy || !online}
                  onchange={() => toggleChecklist(task, item.id)}
                />{item.text}</label
              >{/each}
          </article>{:else}<p>
            No tasks yet. Make the next step small and specific.
          </p>{/each}
      {:else if tab === "subjects"}
        {#each ["subjects", "projects"] as kind}<div class="panel">
            <div class="row">
              <h3>{kind}</h3>
              <Button onclick={() => openEditor(kind as Collection)}
                >Add {kind === "subjects" ? "subject" : "project"}</Button
              >
            </div>
            {#each data[kind as "subjects" | "projects"] as item}<div
                class="row list-item"
              >
                <span
                  >{item.name}
                  {kind === "projects"
                    ? `· ${subjectName(item.subject_id)}`
                    : ""}</span
                ><Button onclick={() => openEditor(kind as Collection, item)}
                  >Edit</Button
                ><Button onclick={() => deleteItem(kind as Collection, item)}
                  >Delete</Button
                >
              </div>{:else}<p>No {kind} yet.</p>{/each}
          </div>{/each}
      {:else if tab === "sessions"}
        <div class="row">
          <h3>Completed sessions</h3>
          <Button onclick={() => openSession()}>Log manual session</Button>
        </div>
        <p class="muted">
          Edit or delete real records. Manual logs require an explicit offset,
          IANA timezone, and duration. Showing the latest 500 sessions; graphs
          include all records.
        </p>
        {#each data.sessions as session}<article class="panel">
            <div class="row">
              <strong
                >{minutesLabel(session.duration_seconds)} · {subjectName(
                  session.subject_id,
                )}</strong
              ><span
                >{new Date(session.started_at).toLocaleString(undefined, {
                  timeZone: session.timezone,
                })} ({session.timezone}) · {session.source}</span
              ><Button onclick={() => openSession(session)}>Edit</Button><Button
                onclick={() => deleteSession(session)}>Delete</Button
              >
            </div>
            {#if session.notes}<p class="preserve">
                {session.notes}
              </p>{/if}{#if session.distractions}<p class="muted preserve">
                Distractions: {session.distractions}
              </p>{/if}
          </article>{:else}<p>
            No completed sessions yet. A running or paused timer is not a
            completed session.
          </p>{/each}
      {:else if tab === "exams"}
        <div class="row">
          <h3>Exam schedule</h3>
          <Button onclick={() => openEditor("exams")}>Add exam</Button>
        </div>
        {#each data.exams as exam}<div class="panel row">
            <strong>{exam.title}</strong><span
              >{exam.exam_date} · {exam.timezone} · {subjectName(
                exam.subject_id,
              )}</span
            ><Button
              onclick={() => {
                planExam = exam.id.toString();
              }}>Plan study</Button
            ><Button onclick={() => openEditor("exams", exam)}>Edit</Button
            ><Button onclick={() => deleteItem("exams", exam)}>Delete</Button>
          </div>{:else}<p>
            Add the dates you actually know. No exam dates are guessed.
          </p>{/each}
        <form class="panel" onsubmit={generatePlan}>
          <h3>Make a dated study plan</h3>
          <div class="form-grid">
            <label
              >Exam<select required bind:value={planExam}
                ><option value="">Choose exam</option
                >{#each data.exams as exam}<option value={exam.id.toString()}
                    >{exam.title} · {exam.exam_date}</option
                  >{/each}</select
              ></label
            ><label
              >First date<input
                type="date"
                required
                bind:value={planStart}
              /></label
            ><label
              >Total minutes<input
                type="number"
                min="1"
                max="10080"
                required
                bind:value={planMinutes}
              /></label
            ><label
              >Minutes per block<input
                type="number"
                min="1"
                max="240"
                required
                bind:value={planBlock}
              /></label
            ><label
              >Start time in exam timezone<input
                type="time"
                required
                bind:value={planTime}
              /></label
            >
          </div>
          <label class="check-row"
            ><input type="checkbox" bind:checked={replacePlan} />Explicitly
            replace this exam's existing local plan</label
          >
          <p class="muted">
            One block per day, evenly spaced before the exam. Dates and
            durations come only from your inputs. Calendar exports are separate
            records.
          </p>
          <Button type="submit" disabled={busy || !online}
            >Save dated plan</Button
          >
        </form>
        <h3>Saved study blocks</h3>
        {#each data.plans as block}<article class="panel row">
            <label class="check-row"
              ><input
                type="checkbox"
                checked={block.done}
                disabled={busy || !online}
                onchange={() =>
                  change("/study/plan", {
                    id: block.id,
                    revision: block.revision,
                    done: !block.done,
                  })}
              />{block.title}</label
            ><span
              >{new Date(block.start!).toLocaleString(undefined, {
                timeZone: block.timezone,
              })} — {new Date(block.end!).toLocaleTimeString(undefined, {
                timeZone: block.timezone,
              })} ({block.timezone})</span
            ><Button
              disabled={busy || !online}
              onclick={() => exportBlock(block)}>Add to calendar</Button
            ><Button
              onclick={() =>
                change("/study/plan", {
                  action: "delete",
                  id: block.id,
                  revision: block.revision,
                })}>Delete block</Button
            >
          </article>{:else}<p>No planned blocks yet.</p>{/each}
        <p class="muted">
          Checking a planned block does not log study time. Complete a focus
          session or add a manual session.
        </p>
      {:else if tab === "helpers"}
        <div class="chart-grid">
          <article class="panel">
            <h3>Active recall</h3>
            <ol>
              <li>
                Close the material. Explain the central idea in your own words.
              </li>
              <li>Write three questions you should be able to answer.</li>
              <li>Answer without looking, then compare with your source.</li>
              <li>Mark the gap and revisit it in a later session.</li>
            </ol>
            <p>No facts are generated here; use your actual course sources.</p>
            <Button
              onclick={() => {
                setNoteDraft(
                  "Recall question:\nMy answer without looking:\nSource comparison / gap:\nNext practice step:",
                );
                focusMode = true;
              }}>Use recall note template</Button
            >
          </article>
          <article class="panel">
            <h3>Reflection</h3>
            <p>
              What could I retrieve unaided? What caused friction? What will I
              do differently in the next session?
            </p>
            <Button
              onclick={() => {
                setNoteDraft(
                  "What I retrieved:\nWhat I could not explain yet:\nDistractions / friction:\nOne next action:",
                );
                focusMode = true;
              }}>Use reflection template</Button
            >
          </article>
        </div>
        <article class="panel">
          <h3>Optional AI study suggestions</h3>
          <p>
            Select only material you want to send. Preview is local; the
            confirmed run sends this text to OpenRouter. Suggestions are not
            facts and never alter plans automatically.
          </p>
          <label
            >Selected text<textarea
              rows="6"
              bind:value={aiText}
              maxlength="100000"
              placeholder="Paste a passage or requirements you deliberately want to send."
            ></textarea></label
          >
          <AIReview
            purpose="study-plan"
            text={aiText}
            {demo}
            onresult={(text) => {
              setNoteDraft(text);
              notice =
                "Approved suggestions copied into your unsaved note draft. Review them, then save explicitly.";
            }}
          />
        </article>
      {:else if tab === "settings" && settings}
        <form
          class="panel"
          onsubmit={saveSettings}
          oninput={() => (settingsDirty = true)}
        >
          <h3>Pomodoro preferences</h3>
          <div class="form-grid">
            <label
              >Focus duration (seconds)<input
                type="number"
                min="60"
                max="14400"
                required
                bind:value={settings.focus_seconds}
              /></label
            ><label
              >Short break (seconds)<input
                type="number"
                min="60"
                max="7200"
                required
                bind:value={settings.short_break_seconds}
              /></label
            ><label
              >Long break (seconds)<input
                type="number"
                min="60"
                max="7200"
                required
                bind:value={settings.long_break_seconds}
              /></label
            ><label
              >Focus sessions before long break<input
                type="number"
                min="1"
                max="12"
                required
                bind:value={settings.cycle_count}
              /></label
            ><label
              >IANA timezone<input
                required
                bind:value={settings.timezone}
                placeholder="Europe/London"
              /></label
            >
          </div>
          <label class="check-row"
            ><input type="checkbox" bind:checked={settings.sound} />Play a
            phase-completion sound</label
          ><label class="check-row"
            ><input
              type="checkbox"
              bind:checked={settings.notifications}
            />Desktop notification (browser permission required)</label
          >
          <p class="muted">
            Preferences affect the next phase, not a currently running or paused
            phase. Sound may require interacting with the browser first.
          </p>
          <Button type="submit" disabled={busy || !online}
            >Save preferences</Button
          >
        </form>
      {/if}
    </div>
  {:else}<p role="status">Loading local study records…</p>{/if}
  {#if editorKind}
    <div class="editor panel" role="region" aria-label={`Edit ${editorKind}`}>
      <form onsubmit={saveEditor}>
        <h3>{editor.id ? "Edit" : "Add"} {editorKind}</h3>
        <div class="form-grid">
          {#if editorKind === "subjects" || editorKind === "projects"}<label
              >Name<input
                required
                maxlength="250"
                bind:value={editor.name}
              /></label
            >{:else}<label
              >Title<input
                required
                maxlength="500"
                bind:value={editor.title}
              /></label
            >{/if}{#if editorKind === "subjects"}<label
              >Color<input type="color" bind:value={editor.color} /></label
            >{:else}<label
              >Subject<select bind:value={editor.subject_id}
                ><option value={null}>Unassigned</option
                >{#each data?.subjects || [] as item}<option value={item.id}
                    >{item.name}</option
                  >{/each}</select
              ></label
            >{/if}{#if editorKind === "tasks" || editorKind === "targets"}<label
              >Project<select bind:value={editor.project_id}
                ><option value={null}>None</option
                >{#each data?.projects || [] as item}<option value={item.id}
                    >{item.name}</option
                  >{/each}</select
              ></label
            >{/if}{#if editorKind === "targets"}<label
              >Period<select bind:value={editor.period}
                ><option value="daily">Daily</option><option value="weekly"
                  >Weekly</option
                ></select
              ></label
            ><label
              >Minutes<input
                type="number"
                min="1"
                max="10080"
                required
                bind:value={editor.minutes}
              /></label
            >{/if}{#if editorKind === "tasks"}<label
              >Priority<select bind:value={editor.priority}
                ><option value="low">Low</option><option value="normal"
                  >Normal</option
                ><option value="high">High</option></select
              ></label
            ><label
              >Due date<input type="date" bind:value={editor.due_date} /></label
            >{/if}{#if editorKind === "exams"}<label
              >Exam date<input
                type="date"
                required
                bind:value={editor.exam_date}
              /></label
            ><label
              >IANA timezone<input
                required
                bind:value={editor.timezone}
              /></label
            ><label>Notes<textarea bind:value={editor.notes}></textarea></label
            >{/if}
        </div>
        {#if editorKind === "tasks"}<h4>Checklist</h4>
          {#each editor.checklist || [] as item}<div class="row">
              <label class="check-row"
                ><input
                  type="checkbox"
                  bind:checked={item.done}
                />{item.text}</label
              ><Button
                onclick={() =>
                  (editor.checklist = editor.checklist?.filter(
                    (entry) => entry.id !== item.id,
                  ))}>Remove</Button
              >
            </div>{/each}
          <div class="row">
            <label
              >New checklist item<input
                bind:value={checklistText}
                maxlength="500"
              /></label
            ><Button onclick={addChecklist}>Add item</Button>
          </div>{/if}
        <div class="row">
          <Button type="submit" disabled={busy || !online}>Save</Button><Button
            onclick={() => (editorKind = "")}>Cancel</Button
          >
        </div>
      </form>
    </div>
  {/if}
  {#if sessionOpen}<div
      class="editor panel"
      role="region"
      aria-label="Edit completed session"
    >
      <form onsubmit={saveSession}>
        <h3>{sessionId ? "Edit session" : "Log completed session"}</h3>
        <div class="form-grid">
          <label
            >Start timestamp with explicit offset<input
              required
              bind:value={sessionStart}
              placeholder="2026-10-14T09:00:00-04:00"
            /></label
          ><label
            >IANA timezone<input required bind:value={sessionZone} /></label
          ><label
            >Duration in minutes<input
              type="number"
              min="0.016667"
              max="10080"
              step="any"
              required
              bind:value={sessionMinutes}
            /></label
          ><label
            >Subject<select bind:value={sessionSubject}
              ><option value="">Unassigned</option
              >{#each data?.subjects || [] as item}<option
                  value={item.id.toString()}>{item.name}</option
                >{/each}</select
            ></label
          ><label
            >Project<select bind:value={sessionProject}
              ><option value="">None</option
              >{#each data?.projects || [] as item}<option
                  value={item.id.toString()}>{item.name}</option
                >{/each}</select
            ></label
          ><label>Notes<textarea bind:value={sessionNotes}></textarea></label
          ><label
            >Distractions<textarea bind:value={sessionDistractions}
            ></textarea></label
          >
        </div>
        <p class="muted">
          Duration means time actually studied, not time away. Editing the
          start/duration of a paused focus replaces its interval distribution
          with the manual contiguous interval you enter.
        </p>
        <div class="row">
          <Button type="submit" disabled={busy || !online}
            >Save completed session</Button
          ><Button onclick={() => (sessionOpen = false)}>Cancel</Button>
        </div>
      </form>
    </div>{/if}
</section>

<style>
  .study {
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .section-header,
  .row,
  .timer-heading {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    flex-wrap: wrap;
  }
  .section-header,
  .timer-heading {
    justify-content: space-between;
  }
  h2,
  h3,
  h4,
  p {
    margin: 0 0 0.65rem;
  }
  .muted,
  .timer-heading {
    color: var(--muted);
  }
  .timer-card,
  .panel {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.7rem;
    padding: 1.2rem;
    margin-bottom: 0.85rem;
  }
  .timer-card {
    background: var(--surface-raised);
    text-align: center;
  }
  .clock {
    color: var(--accent);
    font-size: clamp(3.5rem, 9vw, 7rem);
    font-variant-numeric: tabular-nums;
    font-weight: 700;
    line-height: 1.2;
  }
  .actions {
    justify-content: center;
    margin: 1rem 0;
  }
  .form-grid,
  .chart-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr));
    gap: 0.85rem;
    margin: 0.85rem 0;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    text-align: left;
  }
  input,
  select,
  textarea {
    color: var(--ink);
    background: var(--canvas);
    border: 1px solid var(--border);
    padding: 0.6rem;
    border-radius: 0.35rem;
    width: 100%;
    box-sizing: border-box;
  }
  textarea {
    resize: vertical;
  }
  .check-row {
    flex-direction: row;
    align-items: center;
    gap: 0.6rem;
    margin: 0.6rem 0;
  }
  .check-row input {
    width: auto;
  }
  .tabs {
    display: flex;
    gap: 0.45rem;
    flex-wrap: wrap;
    margin: 0.4rem 0 1rem;
  }
  .summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 0.75rem;
    margin-bottom: 1rem;
  }
  .summary article {
    display: grid;
    gap: 0.35rem;
    padding: 1rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.5rem;
  }
  .summary strong {
    font-size: 1.5rem;
  }
  .summary span {
    color: var(--muted);
  }
  .heatmap {
    display: grid;
    grid-auto-flow: column;
    grid-template-rows: repeat(7, 1fr);
    grid-template-columns: repeat(12, 1fr);
    gap: 0.35rem;
    max-width: 620px;
    margin-bottom: 0.75rem;
  }
  .heat-cell {
    background: var(--accent);
    aspect-ratio: 1;
    min-height: 14px;
    border-radius: 0.2rem;
  }
  .goal {
    display: flex;
    gap: 0.7rem;
    flex-wrap: wrap;
    align-items: center;
    margin: 1rem 0;
  }
  .goal progress {
    accent-color: var(--accent);
  }
  .list-item {
    padding: 0.6rem 0;
    border-bottom: 1px solid var(--border);
  }
  .checklist {
    margin-left: 1.75rem;
  }
  .completed {
    opacity: 0.7;
  }
  .error {
    color: var(--warning);
  }
  .notice {
    color: var(--success);
  }
  .offline {
    border: 1px solid var(--warning);
    padding: 0.75rem;
  }
  .editor {
    border: 2px solid var(--accent);
    position: sticky;
    bottom: 0;
    max-height: 80vh;
    overflow-y: auto;
    z-index: 2;
    box-shadow: 0 -8px 30px #0002;
  }
  .preserve {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .hidden {
    display: none;
  }
  .focus-mode .timer-card {
    min-height: 45vh;
  }
  kbd {
    font-size: 0.75em;
    color: var(--muted);
  }
  .chart-grid :global(svg) {
    max-width: 100%;
    height: auto;
  }
</style>
