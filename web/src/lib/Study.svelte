<script lang="ts">
  import { onMount } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api } from "./api";
  import SchemaMaps from "./SchemaMaps.svelte";
  import StudyGuide from "./StudyGuide.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  import HelpersView from "./study/HelpersView.svelte";
  import HistoryNav from "./study/HistoryNav.svelte";
  import HistoryView from "./study/HistoryView.svelte";
  import ItemEditor from "./study/ItemEditor.svelte";
  import PlansView from "./study/PlansView.svelte";
  import ProgressView from "./study/ProgressView.svelte";
  import Reflections from "./study/Reflections.svelte";
  import SessionEditor from "./study/SessionEditor.svelte";
  import SessionNotes from "./study/SessionNotes.svelte";
  import SettingsForm from "./study/SettingsForm.svelte";
  import SubjectsColumn from "./study/SubjectsColumn.svelte";
  import TaskList from "./study/TaskList.svelte";
  import TimerPanel from "./study/TimerPanel.svelte";
  import type { Collection, Guide, GuideState, Item, Session, Settings, Workspace } from "./study/types";

  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  const pane = useWorkspaceContext();
  function confirm(message: string) {
    return pane.active && window.confirm(message);
  }
  /** The hub's tabs; "session" is the three-column cockpit. */
  const TABS = [
    { id: "session", label: "Study session" },
    { id: "overview", label: "Progress" },
    { id: "sessions", label: "History" },
    { id: "exams", label: "Exams & plans" },
    { id: "map", label: "Mind map" },
    { id: "helpers", label: "Helpers" },
    { id: "settings", label: "Timer settings" },
  ] as const;
  type Tab = (typeof TABS)[number]["id"];
  let data = $state<Workspace | null>(null);
  let error = $state("");
  let notice = $state("");
  let online = $state(true);
  let busy = $state(false);
  let now = $state(Date.now());
  let serverOffset = $state(0);
  let loaded = $state(false);
  let focusMode = $state(false);
  let tab = $state<Tab>("session");
  let historyPeriod = $state<"week" | "month">("week");
  let historyDate = $state("");
  let subjectId = $state("");
  let projectId = $state("");
  let targetId = $state("");
  let notes = $state("");
  let distractions = $state("");
  let notesDirty = $state(false);
  let noteRevision = $state(0);
  let settings = $state<Settings | null>(null);
  let settingsDirty = $state(false);
  let editing = $state<{ kind: Collection; item: Item } | null>(null);
  let sessionEditing = $state<{ session: Session | null } | null>(null);
  let aiText = $state("");
  const blankGuide = (): GuideState => ({checked: Array(14).fill(false), responses: Array(14).fill(""), reflection: "", next_review: ""});
  let guideState = $state<GuideState>(blankGuide());
  let guideId = $state("");
  let guideRevision = $state(0);
  let guideDirty = $state(false);
  let newPreparation = $state(false);
  function resetGuide() {
    if (guideDirty && !confirm("Discard the unsaved guide draft?")) return;
    guideState = blankGuide(); guideId = ""; guideRevision = 0; guideDirty = true; newPreparation = true;
  }
  function openGuide(guide: Guide) {
    if (guideDirty && !confirm("Replace the unsaved guide draft?")) return;
    guideId = guide.id; guideRevision = guide.revision; guideState = structuredClone($state.snapshot(guide.state)); guideDirty = false; newPreparation = true;
    tab = "session";
  }
  async function saveGuide() {
    if (!guideId) { notice = "Preparation draft kept on this device. Start the session to save it with your timer."; return; }
    if (await change("/study/guide", {session_id: guideId, revision: guideRevision, state: guideState})) {
      guideDirty = false; newPreparation = false;
      const saved = data?.guides.find(guide => guide.id === guideId);
      if (saved) { guideRevision = saved.revision; guideState = structuredClone($state.snapshot(saved.state)); }
      notice = "Guide, reflection and next review saved.";
    }
  }
  let mounted = false;
  let inFlight = false;
  let notePhase: string | null = null;
  const snapshotKey = "kitty-study-snapshot";
  const selectedId = (value: string) => (value ? Number(value) : null);
  const historyLabel = $derived(data
    ? data.stats.history.period === "month"
      ? new Date(`${data.stats.history.start}T12:00:00`).toLocaleDateString(undefined, { month: "long", year: "numeric" })
      : `Week of ${new Date(`${data.stats.history.start}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}`
    : "");
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
  /** Length of the current phase; older timers without `duration_seconds` fall back to settings. */
  const phaseTotal = $derived(
    !data ? 0
      : data.timer.duration_seconds
        || (data.timer.phase === "priming" ? 300 : data.timer.phase === "focus" ? data.settings.focus_seconds : data.settings.short_break_seconds),
  );

  function accept(result: Workspace) {
    data = result;
    serverOffset = Date.parse(result.server_now) - Date.now();
    online = true;
    if (!settingsDirty) settings = { ...result.settings };
    if (result.guide && !guideDirty && !newPreparation) {
      guideId = result.guide.id;
      guideRevision = result.guide.revision;
      guideState = structuredClone(result.guide.state);
    }
    if (notePhase !== result.timer.phase_id && !notesDirty) {
      notePhase = result.timer.phase_id;
      notes = result.timer.notes;
      distractions = result.timer.distractions;
      noteRevision = result.timer.revision;
    } else if (!notesDirty) {
      notes = result.timer.notes;
      distractions = result.timer.distractions;
      noteRevision = result.timer.revision;
    }
    if (!loaded || result.timer.status !== "idle") {
      subjectId = result.timer.subject_id?.toString() || "";
      projectId = result.timer.project_id?.toString() || "";
      targetId = result.timer.target_id?.toString() || "";
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
    const query = new URLSearchParams({ period: historyPeriod, ...(historyDate ? { date: historyDate } : {}) }).toString();
    try {
      const result = await api<Workspace>(`/study?${query}`);
      accept(result);
      if (!silent) error = "";
    } catch (cause) {
      online = false;
      error = (cause as Error).message;
    } finally {
      inFlight = false;
      const current = new URLSearchParams({ period: historyPeriod, ...(historyDate ? { date: historyDate } : {}) }).toString();
      if (query !== current) void load(true);
    }
  }
  function navigateHistory(anchor: string, period = historyPeriod) {
    historyPeriod = period;
    historyDate = anchor;
    void load();
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
    if (!pane.active || !data || busy || !online) return;
    if (action === "start" || action === "resume")
      window.dispatchEvent(new CustomEvent("kitty-study-unlock-sound"));
    if (
      (action === "stop" || action === "reset") &&
      data.timer.status !== "idle" &&
      !confirm(
        "Stop this study session? Unfinished focus time is not counted. Completed focus, your guide and reflection stay saved.",
      )
    )
      return;
    if (action === "start") tab = "session";
    if (action === "start" && guideId && !newPreparation && !guideDirty) {
      guideState = blankGuide(); guideId = ""; guideRevision = 0;
    }
    if (action === "stop" && guideDirty && guideId) {
      await saveGuide();
      if (guideDirty) return;
    }
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
            guide: guideState,
          }
        : {}),
      ...(includeNotes ? { notes, distractions } : {}),
    });
    if (saved && action === "start") {
      guideDirty = false; newPreparation = false;
      if (data?.guide) { guideId = data.guide.id; guideRevision = data.guide.revision; }
      notesDirty = false; noteRevision = data.timer.revision; notePhase = data.timer.phase_id;
    }
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
    if (!pane.active || event.ctrlKey || event.metaKey || event.altKey || event.repeat)
      return false;
    const target = event.target as HTMLElement | null;
    if (event.defaultPrevented || target?.closest("input,textarea,select,[contenteditable=true],button,.schema"))
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
    if (key === "b" && data?.timer.phase === "short_break") {
      event.preventDefault();
      await timerAction("skip_break");
      return true;
    }
    if (key === "escape" && focusMode) {
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
        const snapshot = JSON.parse(cached) as Workspace;
        if (snapshot.stats?.history) {
          snapshot.guides ??= [];
          snapshot.guide ??= null;
          data = snapshot;
          settings = { ...snapshot.settings };
          online = false;
        }
      }
      const draft = localStorage.getItem("kitty-study-draft");
      if (draft) {
        const value = JSON.parse(draft);
        aiText = typeof value.text === "string" ? value.text : "";
        if (value.guide?.checked?.length === 14 && value.guide?.responses?.length === 14) {
          guideState = value.guide; guideId = value.guideId || ""; guideRevision = value.guideRevision || 0;
          guideDirty = value.guideDirty === true; newPreparation = value.newPreparation === true;
        }
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
    const ready = load();
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
      if (!pane.active) return;
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
    const focusNode = async (event: Event) => {
      if (!pane.active) return;
      const detail = (event as CustomEvent<{subject_id?: number; concept?: string}>).detail;
      await ready;
      if (!pane.active || !mounted) return;
      if (!data) {error = "The study workspace must reconnect before starting this branch."; return;}
      if (!data || data.timer.status !== "idle") {notice = "Stop the current cycle before changing focus."; return;}
      subjectId = detail.subject_id ? String(detail.subject_id) : "";
      projectId = ""; targetId = "";
      setNoteDraft(detail.concept ?? "");
      tab = "session";
      if (guideId && !guideDirty) resetGuide();
      guideState.responses[4] = detail.concept ?? "";
      guideDirty = true;
      await timerAction("start");
    };
    window.addEventListener("kitty-study-focus", focusNode);
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
      window.removeEventListener("kitty-study-focus", focusNode);
      document.removeEventListener("visibilitychange", wake);
      pane.clearTrail("study");
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
            guide: guideState, guideId, guideRevision, guideDirty, newPreparation,
          }),
        );
      } catch {
        /* Optional local draft cache. */
      }
    }
  });

  $effect(() => {
    const current = TABS.find((item) => item.id === tab);
    if (tab !== "session" && current) pane.setTrail("study", [{label: current.label, onclick: () => {}}], () => {if (pane.active) tab = "session";});
  });

  function openEditor(kind: Collection, item?: Item, defaults: Partial<Item> = {}) {
    editing = {
      kind,
      item: item ?? {
        id: 0, revision: 0, name: "", title: "", color: "#7c6fdb", subject_id: null, project_id: null,
        period: "weekly", minutes: 300, priority: "normal", due_date: "", done: false, checklist: [],
        exam_date: data?.stats.today, timezone: data?.settings.timezone || "UTC", notes: "", ...defaults,
      },
    };
  }
  async function deleteItem(kind: Collection, item: Item): Promise<boolean> {
    if (!confirm(`Delete ${item.name || item.title}? Completed session history stays; deleting an exam removes its local plan.`)) return false;
    return change(`/study/${kind}`, { action: "delete", id: item.id, revision: item.revision });
  }
  async function toggleTask(task: Item) {
    await change("/study/tasks", { id: task.id, revision: task.revision, done: !task.done });
  }
  async function toggleChecklist(task: Item, checklistId: string) {
    await change("/study/tasks", {
      id: task.id,
      revision: task.revision,
      checklist: (task.checklist || []).map((item) => item.id === checklistId ? { ...item, done: !item.done } : item),
    });
  }
  async function deleteSession(session: Session) {
    if (confirm("Delete this completed session? Its study time and streak contribution will be removed."))
      await change("/study/sessions", { action: "delete", id: session.id, revision: session.revision, request_id: crypto.randomUUID() });
  }
  async function saveSettings(event: SubmitEvent) {
    event.preventDefault();
    if (!settings) return;
    if (settings.notifications && "Notification" in window && Notification.permission !== "granted") {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        error = "Desktop notifications were not authorized; visual phase alerts remain available.";
        settings.notifications = false;
      }
    }
    if (await change("/study/settings", settings)) {
      settingsDirty = false;
      await load(true);
      notice = "Preferences saved. Active phase durations are unchanged.";
    }
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
    </div>
    <div class="row">
      {#if data?.timer.status === "idle" && tab !== "session"}<Button intent="primary" disabled={busy || !online} onclick={() => timerAction("start")}>Start session · 5 min priming</Button>{/if}
      <Button onclick={() => { focusMode = !focusMode; if (focusMode) tab = "session"; }}
        >{focusMode ? "Exit focus mode" : "Focus mode"}</Button>
    </div>
  </header>
  {#if demo}<p class="muted">
      Demo study records stay local; external AI and Google calls are disabled.
    </p>{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if notice}<p class="notice" role="status">{notice}<button class="dismiss-message" aria-label="Dismiss message" onclick={() => notice = ""}>×</button></p>{/if}
  {#if !online}<p class="offline" role="status">
      Showing your last saved snapshot. Timer display is an estimate until the
      local server reconnects. Changes are not queued silently. <Button
        onclick={() => load()}>Reconnect</Button
      >
    </p>{/if}
  {#if data}
    {#if !focusMode}
      <nav class="tabs" aria-label="Study tools">
        {#each TABS as item (item.id)}<Button aria-pressed={tab === item.id} onclick={() => (tab = item.id)}>{item.label}</Button>{/each}
      </nav>
    {/if}
    {#if tab === "session"}
      <div class="cockpit">
        {#if !focusMode}
          <SubjectsColumn {data} bind:subjectId bind:projectId locked={data.timer.status !== "idle"} disabled={busy || !online}
            onedit={openEditor} ontoggle={toggleTask} onchecklist={toggleChecklist} />
        {/if}
        <TimerPanel {data} bind:targetId {subjectId} {projectId} {remaining} {clockLabel} {phaseTotal} disabled={busy || !online} ontimer={timerAction} />
        {#if focusMode}
          <section class="column tasks-focus" aria-label="Next tasks">
            <span class="eyebrow">Next tasks</span>
            {#if data.tasks.some((item) => !item.done)}
              <TaskList tasks={data.tasks.filter((item) => !item.done)} subjects={data.subjects} limit={5} disabled={busy || !online} ontoggle={toggleTask} />
            {:else}<p class="muted">Add a task in the task list, or focus on one thing you choose.</p>{/if}
          </section>
        {:else}
          <section class="guide-column" aria-label="Guide & notes">
            <StudyGuide bind:value={guideState} phase={data.timer.phase} active={data.timer.status !== "idle"} dirty={guideDirty} busy={busy || !online} onchange={() => guideDirty = true} onsave={saveGuide} onnew={resetGuide} />
            <div class="column">
              <SessionNotes timer={data.timer} bind:notes bind:distractions bind:notesDirty bind:noteRevision disabled={busy || !online} {confirm} onannotate={() => timerAction("annotate")} />
            </div>
            <div class="column">
              <Reflections guides={data.guides} subjects={data.subjects} today={data.stats.today} onopen={openGuide} />
            </div>
          </section>
        {/if}
      </div>
    {:else if tab === "map"}
      <SchemaMaps {revision} {demo} section="study" />
    {:else}
      {#if tab === "overview" || tab === "sessions"}
        <HistoryNav stats={data.stats} period={historyPeriod} date={historyDate} label={historyLabel} onnavigate={navigateHistory} />
      {/if}
      {#if tab === "overview"}
        <ProgressView {data} period={historyPeriod} onedit={openEditor} onlog={() => (sessionEditing = { session: null })} />
      {:else if tab === "sessions"}
        <HistoryView {data} period={historyPeriod} label={historyLabel} onopen={(session) => (sessionEditing = { session: session ?? null })} ondelete={deleteSession} />
      {:else if tab === "exams"}
        <PlansView {data} disabled={busy || !online} {change} {confirm} notify={(message) => (notice = message)} fail={(message) => (error = message)}
          onedit={openEditor} onopen={openGuide} onstudy={(exam) => {
            if (data?.timer.status === "idle") { subjectId = exam.subject_id ? String(exam.subject_id) : ""; projectId = ""; }
            else notice = "Stop the current cycle before changing focus.";
            tab = "session";
          }} />
      {:else if tab === "helpers"}
        <HelpersView bind:text={aiText} {demo} active={pane.active}
          ontemplate={(template) => { setNoteDraft(template); tab = "session"; }}
          onresult={(text) => { setNoteDraft(text); notice = "Approved suggestions copied into your unsaved note draft. Review them, then save explicitly."; }} />
      {:else if tab === "settings" && settings}
        <SettingsForm bind:settings bind:dirty={settingsDirty} disabled={busy || !online} onsave={saveSettings} />
      {/if}
    {/if}
  {:else}<p role="status">Loading local study records…</p>{/if}
  {#if editing && data}
    {#key editing}<ItemEditor kind={editing.kind} item={editing.item} {data} disabled={busy || !online} {change} ondelete={deleteItem} onclose={() => (editing = null)} />{/key}
  {/if}
  {#if sessionEditing && data}
    {#key sessionEditing}<SessionEditor session={sessionEditing.session} {data} {subjectId} {projectId} disabled={busy || !online} {change} onclose={() => (sessionEditing = null)} />{/key}
  {/if}
</section>

<style>
  .study {
    container: study / inline-size;
    display: grid;
    gap: 1rem;
    min-width: 0;
  }
  .section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    flex-wrap: wrap;
  }
  h2 {
    margin: 0;
  }
  .tabs {
    display: flex;
    gap: 0.45rem;
    flex-wrap: wrap;
  }
  /* Cockpit: what to study | the live timer | how to study it. */
  .cockpit {
    display: grid;
    grid-template-columns: minmax(200px, 250px) minmax(0, 1fr) minmax(280px, 360px);
    gap: 12px;
    align-items: start;
  }
  .focus-mode .cockpit {
    grid-template-columns: minmax(0, 1fr) minmax(220px, 300px);
  }
  .focus-mode .cockpit :global(.timer-card) {
    min-height: 45vh;
  }
  .guide-column {
    display: grid;
    gap: 12px;
    min-width: 0;
  }
  .tasks-focus {
    display: grid;
    gap: 8px;
  }
  @container study (max-width: 900px) {
    .cockpit,
    .focus-mode .cockpit {
      grid-template-columns: minmax(0, 1fr);
    }
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
  /* Shared by the hub's section components. */
  .study :global {
    h3,
    h4,
    p {
      margin: 0 0 0.65rem;
    }
    .row {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    .muted {
      color: var(--muted);
    }
    .column,
    .panel {
      min-width: 0;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--br-radius-lg, 0.8rem);
    }
    .column {
      padding: 14px;
    }
    .panel {
      padding: clamp(1rem, 2.3vw, 1.6rem);
      margin-bottom: 0.6rem;
    }
    .form-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr));
      gap: 0.85rem;
      margin: 0.85rem 0;
    }
    .form-grid > label,
    label.field {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      text-align: left;
    }
    :is(.form-grid, .field, .history-date) :is(input, select, textarea) {
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
      display: flex;
      align-items: center;
      gap: 0.6rem;
      margin: 0.6rem 0;
    }
    .check-row input {
      width: auto;
    }
    .preserve {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .editor {
      border: 2px solid var(--accent);
      position: sticky;
      bottom: 0;
      max-height: 80vh;
      overflow-y: auto;
      z-index: 2;
      box-shadow: 0 -8px 30px color-mix(in srgb, var(--shadow-tint) 13%, transparent);
    }
    summary {
      cursor: pointer;
      color: var(--muted);
      font-size: 0.85rem;
    }
  }
</style>
