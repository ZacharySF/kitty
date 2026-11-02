<script lang="ts">
  import { onMount } from "svelte";
  import { fly } from "svelte/transition";
  import { Button } from "@nunera/bracket";
  import { api } from "./api";
  import Icon from "./Icon.svelte";
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
  import SubjectsColumn from "./study/SubjectsColumn.svelte";
  import TaskList from "./study/TaskList.svelte";
  import TimerPanel from "./study/TimerPanel.svelte";
  import type { Collection, Guide, GuideState, Item, Session, Settings, Workspace } from "./study/types";
  import Select from "./ui/Select.svelte";

  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  const pane = useWorkspaceContext();
  function confirm(message: string) {
    return pane.active && window.confirm(message);
  }
  /** The hub's tabs; "session" is the focus-first timer with its two drawers. */
  const TABS = [
    { id: "session", label: "Session" },
    { id: "overview", label: "Progress" },
    { id: "exams", label: "Exams & plans" },
    { id: "map", label: "Mind map" },
    { id: "tools", label: "Tools" },
  ] as const;
  /** "guide" is the full 14-step guide, reached from the guide drawer, not a tab. */
  type Tab = (typeof TABS)[number]["id"] | "guide";
  const TOOLS = [{ value: "history", label: "History" }, { value: "helpers", label: "Helpers" }];
  /** Drawer open state, remembered per browser. */
  const DRAWERS_KEY = "kitty.study.drawers";
  let drawers = $state({ left: false, right: false });
  try {
    const saved = JSON.parse(localStorage.getItem(DRAWERS_KEY) || "{}");
    drawers = { left: saved.left === true, right: saved.right === true };
  } catch { /* Both closed by default. */ }
  function toggleDrawer(side: "left" | "right", open = !drawers[side]) {
    drawers = { ...drawers, [side]: open };
    try { localStorage.setItem(DRAWERS_KEY, JSON.stringify(drawers)); } catch { /* The state still applies in this session. */ }
  }
  let data = $state<Workspace | null>(null);
  let error = $state("");
  let notice = $state("");
  let online = $state(true);
  let busy = $state(false);
  let now = $state(Date.now());
  let serverOffset = $state(0);
  let loaded = $state(false);
  let tab = $state<Tab>("session");
  let tool = $state("history");
  let historyPeriod = $state<"week" | "month">("week");
  let historyDate = $state("");
  let subjectId = $state("");
  let projectId = $state("");
  let targetId = $state("");
  let notes = $state("");
  let distractions = $state("");
  let notesDirty = $state(false);
  let noteRevision = $state(0);
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
    tab = "guide";
  }
  /** Saves the guide to its session; a draft without a session is saved when one starts. */
  async function saveGuide() {
    if (!guideId || !guideDirty) return;
    if (busy) { window.setTimeout(() => void saveGuide(), 800); return; }
    const sent = JSON.stringify($state.snapshot(guideState));
    if (await change("/study/guide", {session_id: guideId, revision: guideRevision, state: guideState})) {
      const saved = data?.guides.find(guide => guide.id === guideId);
      if (saved) guideRevision = saved.revision;
      // Keystrokes made while saving stay dirty and save next.
      if (JSON.stringify($state.snapshot(guideState)) === sent) {
        guideDirty = false; newPreparation = false;
        if (saved) guideState = structuredClone($state.snapshot(saved.state));
      }
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
    if (action === "start") tab = tab === "guide" ? "guide" : "session";
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
    if (event.defaultPrevented || target?.closest("input,textarea,select,[contenteditable=true],.schema,[role=dialog],[role=menu],[role=listbox]"))
      return false;
    // [ and ] work from anywhere on the session page, a drawer's buttons included.
    if (tab === "session" && (event.key === "[" || event.key === "]")) {
      event.preventDefault();
      toggleDrawer(event.key === "[" ? "left" : "right");
      return true;
    }
    if (event.key === "Escape" && tab === "session" && (drawers.left || drawers.right) && target?.closest(".drawer")) {
      event.preventDefault();
      toggleDrawer(target.closest(".drawer.left") ? "left" : "right", false);
      return true;
    }
    if (target?.closest("button")) return false;
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
        tab = "tools";
        tool = "helpers";
      }
    };
    const focusNode = async (event: Event) => {
      if (!pane.active) return;
      const detail = (event as CustomEvent<{subject_id?: number; concept?: string}>).detail;
      await ready;
      if (!pane.active || !mounted) return;
      if (!data) {error = "The study workspace must reconnect before starting this branch."; return;}
      if (!data || data.timer.status !== "idle") {error = "Stop the current cycle before changing focus."; return;}
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
    const label = tab === "guide" ? "Guide" : TABS.find((item) => item.id === tab)?.label;
    if (tab !== "session" && label) pane.setTrail("study", [{label, onclick: () => {}}], () => {if (pane.active) tab = "session";});
  });
  // The guide saves itself a moment after each change.
  $effect(() => {
    if (!guideDirty || !guideId) return;
    JSON.stringify(guideState);
    const timer = window.setTimeout(() => void saveGuide(), 1200);
    return () => window.clearTimeout(timer);
  });
  // Q3: save state as a dot on the workspace tab instead of status text.
  $effect(() => {
    // The mind map reports its own save state.
    if (tab === "map") return;
    pane.setStatus(!online ? { state: "failed", detail: "Offline · showing your last snapshot" }
      : error ? { state: "failed", detail: error }
      : notesDirty ? { state: "unsaved", detail: "Unsaved session notes" }
      : guideDirty ? { state: "unsaved", detail: guideId ? "Saving the guide…" : "The guide saves when the session starts" }
      : { state: "saved" });
    return () => pane.setStatus(null);
  });

  /** Sizes the session page to the window above the dock, so it never scrolls. */
  function fill(node: HTMLElement) {
    let frame = 0;
    const size = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const main = node.closest("main");
        if (!main || !node.offsetParent) return;
        // Grow into the slack below, or shrink by what overflows the window; never collapse
        // first, so a resize-sensitive child (the mind map) sees one size change.
        const box = node.getBoundingClientRect();
        const slack = main.getBoundingClientRect().bottom - parseFloat(getComputedStyle(main).borderBottomWidth || "0") - box.bottom;
        const over = document.documentElement.scrollHeight - innerHeight;
        const next = Math.floor(box.height + (over > 0 ? -over : slack));
        if (Math.abs(next - box.height) < 1) return;
        const height = `${next}px`;
        node.style.height = height;
        node.style.setProperty("--map-height", height);
      });
    };
    size();
    const observer = new ResizeObserver(size);
    observer.observe(document.querySelector(".workbench-top") ?? document.body);
    const styles = new MutationObserver(size);
    styles.observe(document.documentElement, { attributes: true, attributeFilter: ["style"] });
    window.addEventListener("resize", size);
    return { destroy() { cancelAnimationFrame(frame); observer.disconnect(); styles.disconnect(); window.removeEventListener("resize", size); } };
  }

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
  /** Timer settings save as they change and apply from the next phase. */
  function saveSettings(next: Settings) {
    return change("/study/settings", next);
  }
  async function createItem(kind: Collection, fields: Record<string, unknown>) {
    return change(`/study/${kind}`, { action: "save", ...fields });
  }
</script>

<section class="study" aria-label="Study workspace">
  <header class="section-header">
    <h2>Study</h2>
    {#if data}
      <nav class="tabs" aria-label="Study tools">
        {#each TABS as item (item.id)}<Button size="sm" aria-pressed={tab === item.id} onclick={() => (tab = item.id)}>{item.label}</Button>{/each}
      </nav>
    {/if}
  </header>
  {#if error}<p class="error" role="alert">{error}<button class="dismiss-message" aria-label="Dismiss message" onclick={() => (error = "")}>×</button></p>{/if}
  {#if notice}<p class="notice" role="status">{notice}<button class="dismiss-message" aria-label="Dismiss message" onclick={() => (notice = "")}>×</button></p>{/if}
  {#if !online}<p class="offline" role="status">Offline · showing your last snapshot. <Button size="sm" onclick={() => load()}>Reconnect</Button></p>{/if}
  {#if data}
    {#if tab === "session"}
      {@const open = data.tasks.filter((item) => !item.done).length}
      <div class="focus-first" class:left-open={drawers.left} class:right-open={drawers.right} use:fill>
        <section class="solo" aria-label="Focus">
          <TimerPanel {data} bind:targetId {subjectId} {projectId} {remaining} {clockLabel} {phaseTotal} disabled={busy || !online} ontimer={timerAction} onsettings={saveSettings} />
        </section>
        <button type="button" class="edge left" aria-label="Subjects & tasks" aria-controls="study-drawer-left" aria-expanded={drawers.left}
          title="Subjects & tasks ([)" onclick={() => toggleDrawer("left")}>
          <Icon name="study" size={14} /><span>Subjects & tasks</span>{#if open}<span class="count">{open}</span>{/if}
        </button>
        <button type="button" class="edge right" aria-label="Guide & notes" aria-controls="study-drawer-right" aria-expanded={drawers.right}
          title="Guide & notes (])" onclick={() => toggleDrawer("right")}>
          <Icon name="notes" size={14} /><span>Guide & notes</span>
        </button>
        {#if drawers.left}
          <aside id="study-drawer-left" class="drawer left" aria-label="Subjects & tasks" transition:fly={{ x: -40, duration: 180 }}>
            <SubjectsColumn {data} bind:subjectId bind:projectId locked={data.timer.status !== "idle"} disabled={busy || !online}
              onedit={openEditor} oncreate={createItem} ontoggle={toggleTask} onchecklist={toggleChecklist} />
          </aside>
        {/if}
        {#if drawers.right}
          <aside id="study-drawer-right" class="drawer right" aria-label="Guide & notes" transition:fly={{ x: 40, duration: 180 }}>
            <StudyGuide bind:value={guideState} phase={data.timer.phase} active={data.timer.status !== "idle"} busy={busy || !online}
              onchange={() => (guideDirty = true)} onfull={() => (tab = "guide")} />
            <hr class="rule" />
            <SessionNotes timer={data.timer} bind:notes bind:distractions bind:notesDirty bind:noteRevision disabled={busy || !online} {confirm} onannotate={() => timerAction("annotate")} />
            <Reflections show="due" guides={data.guides} subjects={data.subjects} today={data.stats.today} onopen={openGuide} />
          </aside>
        {/if}
      </div>
    {:else if tab === "guide"}
      <div class="surface page">
        <StudyGuide mode="full" bind:value={guideState} phase={data.timer.phase} active={data.timer.status !== "idle"} busy={busy || !online}
          onchange={() => (guideDirty = true)} onnew={resetGuide} />
        <Reflections show="saved" guides={data.guides} subjects={data.subjects} today={data.stats.today} onopen={openGuide} />
      </div>
    {:else if tab === "map"}
      <div class="map-host" use:fill><SchemaMaps {revision} {demo} section="study" /></div>
    {:else if tab === "overview"}
      <HistoryNav stats={data.stats} period={historyPeriod} date={historyDate} label={historyLabel} onnavigate={navigateHistory} />
      <ProgressView {data} period={historyPeriod} onedit={openEditor} onlog={() => (sessionEditing = { session: null })} />
    {:else if tab === "exams"}
      <PlansView {data} disabled={busy || !online} {change} {confirm} notify={(message) => (notice = message)} fail={(message) => (error = message)}
        onedit={openEditor} onopen={openGuide} onstudy={(exam) => {
          if (data?.timer.status === "idle") { subjectId = exam.subject_id ? String(exam.subject_id) : ""; projectId = ""; }
          else error = "Stop the current cycle before changing focus.";
          tab = "session";
        }} />
    {:else if tab === "tools"}
      <div class="tool-switch"><Select label="Tool" options={TOOLS} bind:value={tool} /></div>
      {#if tool === "history"}
        <HistoryNav stats={data.stats} period={historyPeriod} date={historyDate} label={historyLabel} onnavigate={navigateHistory} />
        <HistoryView {data} period={historyPeriod} label={historyLabel} onopen={(session) => (sessionEditing = { session: session ?? null })} ondelete={deleteSession} />
      {:else}
        <HelpersView bind:text={aiText} {demo} active={pane.active}
          ontemplate={(template) => { setNoteDraft(template); tab = "session"; toggleDrawer("right", true); }}
          onresult={(text) => { setNoteDraft(text); tab = "session"; toggleDrawer("right", true); }} />
      {/if}
    {/if}
  {:else}<p role="status">Loading…</p>{/if}
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
    gap: 12px;
    min-width: 0;
  }
  .section-header {
    display: flex;
    align-items: center;
    gap: 18px;
    flex-wrap: wrap;
    min-height: var(--control-h-lg, 32px);
  }
  h2 {
    margin: 0;
  }
  .tabs {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }
  /* Focus-first: the timer alone, centred; the drawers slide over it from the edges. */
  .focus-first {
    position: relative;
    display: grid;
    min-height: 420px;
    overflow: hidden;
  }
  .solo {
    display: grid;
    place-items: center;
    min-width: 0;
  }
  .solo :global(.timer-card) {
    width: min(100%, 420px);
  }
  .edge {
    position: absolute;
    top: 50%;
    z-index: 3;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    width: 30px;
    height: 200px;
    padding: 0;
    border: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 62%, transparent);
    color: var(--muted);
    font: 500 12px/1 var(--font-ui);
    writing-mode: vertical-rl;
    cursor: pointer;
    transform: translateY(-50%);
    transition: left 180ms ease, right 180ms ease, color 120ms;
  }
  .edge:hover,
  .edge[aria-expanded="true"] {
    color: var(--ink);
  }
  .edge.left {
    left: 0;
    border-left: 0;
    border-radius: 0 12px 12px 0;
    transform: translateY(-50%) rotate(180deg);
  }
  .edge.right {
    right: 0;
    border-right: 0;
    border-radius: 12px 0 0 12px;
  }
  .edge :global(svg) {
    transform: rotate(90deg);
  }
  .edge .count {
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
  }
  .left-open .edge.left {
    left: var(--drawer-w);
  }
  .right-open .edge.right {
    right: var(--drawer-w);
  }
  .focus-first {
    --drawer-w: min(320px, calc(100% - 48px));
  }
  .drawer {
    position: absolute;
    top: 0;
    bottom: 0;
    z-index: 2;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    align-content: start;
    gap: 12px;
    width: var(--drawer-w);
    box-sizing: border-box;
    padding: 14px 16px;
    overflow-y: auto;
    overscroll-behavior: contain;
    border: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 90%, transparent);
    box-shadow: var(--overlay-shadow);
    backdrop-filter: blur(24px) saturate(140%);
    -webkit-backdrop-filter: blur(24px) saturate(140%);
  }
  .drawer.left {
    left: 0;
    border-left: 0;
    border-radius: 0 14px 14px 0;
  }
  .drawer.right {
    right: 0;
    border-right: 0;
    border-radius: 14px 0 0 14px;
  }
  .drawer hr {
    width: 100%;
    margin: 0;
  }
  @media (prefers-reduced-motion: reduce) {
    .edge {
      transition: none;
    }
  }
  .page {
    display: grid;
    gap: 16px;
    padding: 16px 18px;
  }
  .map-host {
    min-height: 360px;
  }
  .tool-switch {
    justify-self: start;
  }
  .error {
    color: var(--warning);
  }
  .notice {
    color: var(--success);
  }
  .offline {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--warning);
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
