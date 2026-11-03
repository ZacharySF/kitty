<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { fly } from "svelte/transition";
  import { Button } from "@nunera/bracket";
  import { api } from "../api";
  import { fitHeight } from "../fitHeight";
  import Icon from "../Icon.svelte";
  import { useWorkspaceContext } from "../workspaceContext.svelte";
  import type { Next, SprintHistory } from "./api";
  import { MATH_SUBJECT, readFocus, startMathFocus, type FocusTimer, type Subject } from "./focus";
  import { isDue, upNext, type Topic } from "./graph";
  import HistoryPanel from "./HistoryPanel.svelte";
  import { takeLaunch, type MathLaunch } from "./launch";
  import LessonPanel from "./LessonPanel.svelte";
  import MathText from "./MathText.svelte";
  import ProblemCard from "./ProblemCard.svelte";
  import { loadSettings, saveSettings, type MathSettings } from "./settings";
  import SettingsPanel from "./SettingsPanel.svelte";
  import SprintPanel from "./SprintPanel.svelte";
  import { clock } from "./sprint";
  import TopicDetail from "./TopicDetail.svelte";
  import TopicGraph from "./TopicGraph.svelte";

  /**
   * rika! math (design Q5 A): focus-first like Study. One centred problem, lesson or
   * sprint; the topic graph slides in from the left, history and settings from the right.
   */
  let { demo = false }: { demo?: boolean } = $props();
  const pane = useWorkspaceContext();

  const MODES = [
    { id: "practice", label: "Practice" },
    { id: "sprint", label: "Sprint" },
    { id: "lessons", label: "Lessons" },
  ] as const;
  type Mode = (typeof MODES)[number]["id"];
  type Right = "" | "history" | "settings";
  const MODE_KEY = "kitty.math.mode";
  const DRAWERS_KEY = "kitty.math.drawers";

  let mode = $state<Mode>("practice");
  let left = $state(false);
  let right = $state<Right>("");
  try {
    const saved = localStorage.getItem(MODE_KEY);
    if (saved === "sprint" || saved === "lessons") mode = saved;
    const drawers = JSON.parse(localStorage.getItem(DRAWERS_KEY) || "{}");
    left = drawers.left === true;
    right = drawers.right === "history" || drawers.right === "settings" ? drawers.right : "";
  } catch { /* Practice with both drawers closed. */ }

  let settings = $state<MathSettings>(loadSettings());
  let topics = $state<Topic[] | null>(null);
  let selected = $state("");
  let next = $state<Next | null>(null);
  let nextLoaded = $state(false);
  let history = $state<SprintHistory | null>(null);
  let error = $state("");
  let historyError = $state("");
  let lessonTopic = $state("");
  let lessonRun = $state(0);
  let reviewTopic = $state("");
  let problemRun = $state(0);
  let focus = $state<{ timer: FocusTimer; offset: number; subject: Subject | null } | null>(null);
  let now = $state(Date.now());

  const byId = $derived(Object.fromEntries((topics ?? []).map((topic) => [topic.id, topic])));
  const due = $derived((topics ?? []).filter((topic) => isDue(topic, now)).length);
  const selectedTopic = $derived(byId[selected]);
  const lessonChoices = $derived((topics ?? []).filter((topic) => topic.state === "learning" || topic.state === "ready"));
  const focusActive = $derived(focus !== null && focus.timer.status !== "idle");
  const focusLeft = $derived(
    focus?.timer.status === "running" && focus.timer.deadline_at
      ? Math.max(0, (Date.parse(focus.timer.deadline_at) - now - focus.offset) / 1000)
      : focus?.timer.remaining_seconds ?? 0,
  );

  function setMode(value: Mode) {
    mode = value;
    try { localStorage.setItem(MODE_KEY, value); } catch { /* Applies in this session. */ }
  }
  function setDrawers(nextLeft: boolean, nextRight: Right) {
    left = nextLeft;
    right = nextRight;
    try { localStorage.setItem(DRAWERS_KEY, JSON.stringify({ left, right })); } catch { /* Applies in this session. */ }
  }
  function changeSettings(value: MathSettings) {
    const reload = value.duration_s !== settings.duration_s;
    settings = value;
    saveSettings(value);
    if (reload) void loadHistory();
  }

  async function loadTopics() {
    try {
      topics = (await api<{ topics: Topic[] }>("/math/topics")).topics;
      if (!byId[selected]) selected = upNext(topics, Date.now())[0]?.topic.id ?? topics[0]?.id ?? "";
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  async function loadNext() {
    try {
      const result = await api<Next | { kind: null }>("/math/next");
      next = result.kind ? (result as Next) : null;
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      nextLoaded = true;
    }
  }
  async function loadHistory() {
    try {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      history = await api<SprintHistory>(`/math/sprints/history?${new URLSearchParams({ duration_s: String(settings.duration_s), timezone })}`);
      historyError = "";
    } catch (cause) {
      historyError = (cause as Error).message;
    }
  }
  async function loadFocus() {
    try { focus = await readFocus(); } catch { focus = null; /* The chip just stays hidden. */ }
  }

  /** Practice can start a Study cycle on Math (Settings), never interrupting one already running. */
  async function practising() {
    if (!settings.focus || focusActive) return;
    try {
      await startMathFocus();
      await loadFocus();
    } catch (cause) {
      error = `Study focus didn't start: ${(cause as Error).message}`;
    }
  }

  function openLesson(id: string) {
    lessonTopic = id;
    lessonRun += 1;
    setMode("lessons");
    void practising();
  }
  function openReview(id: string) {
    reviewTopic = id;
    problemRun += 1;
    setMode("practice");
  }
  async function openNextLesson() {
    if (!nextLoaded) await loadNext();
    if (!topics) await loadTopics();
    const id = next?.kind === "lesson" ? next.topic_id : lessonChoices[0]?.id;
    if (id) openLesson(id);
    else setMode("lessons");
  }
  function launch(kind: MathLaunch | null) {
    if (kind === "sprint") setMode("sprint");
    else if (kind === "lesson") void openNextLesson();
    else if (kind === "practice") setMode("practice");
  }
  async function nextProblem() {
    reviewTopic = "";
    await loadNext();
    problemRun += 1;
  }
  function refresh() {
    void loadTopics();
  }

  export function handleKey(event: KeyboardEvent): boolean {
    if (!pane.active || event.ctrlKey || event.metaKey || event.altKey || event.repeat) return false;
    const target = event.target as HTMLElement | null;
    if (event.defaultPrevented || target?.closest("input,textarea,select,math-field,[contenteditable=true],[role=dialog],[role=menu],[role=listbox]")) return false;
    if (event.key === "[" || event.key === "]") {
      event.preventDefault();
      if (event.key === "[") setDrawers(!left, right);
      else setDrawers(left, right ? "" : "history");
      return true;
    }
    if (event.key === "Escape" && target?.closest(".drawer")) {
      event.preventDefault();
      if (target.closest(".drawer.left")) setDrawers(false, right);
      else setDrawers(left, "");
      return true;
    }
    return false;
  }

  const onStudy = () => void loadFocus();
  let ticker: ReturnType<typeof setInterval> | undefined;
  let poll: ReturnType<typeof setInterval> | undefined;
  onMount(() => {
    // Launchers (Ctrl K, home Up next) leave a request; Math always mounts afresh to take it.
    const pending = takeLaunch();
    if (pending !== "lesson") launch(pending);
    void Promise.all([loadTopics(), loadNext(), loadHistory(), loadFocus()]).then(() => { if (pending === "lesson") launch(pending); });
    window.addEventListener("kitty-study-changed", onStudy);
    ticker = setInterval(() => (now = Date.now()), 1000);
    poll = setInterval(() => void loadFocus(), 30_000);
  });
  onDestroy(() => {
    window.removeEventListener("kitty-study-changed", onStudy);
    clearInterval(ticker);
    clearInterval(poll);
  });
</script>

<section class="math" aria-label="Math workspace">
  <header class="section-header">
    <h2>Math</h2>
    <nav class="tabs" aria-label="Math modes">
      {#each MODES as item (item.id)}
        <Button size="sm" aria-pressed={mode === item.id} onclick={() => (item.id === "lessons" && !lessonTopic ? void openNextLesson() : setMode(item.id))}>{item.label}</Button>
      {/each}
    </nav>
    {#if focusActive && focus}
      <span class="focus-tag" class:math={focus.subject?.name === MATH_SUBJECT} data-focus-subject={focus.subject?.name ?? ""}
        title="This practice is happening inside your Study focus cycle">
        <span class="dot" style:background={focus.subject?.color ?? "var(--accent)"}></span>
        {focus.timer.phase === "focus" ? "Focus" : focus.timer.phase === "priming" ? "Priming" : "Break"} · {focus.subject?.name ?? "no subject"}
        · {clock(focusLeft)}{focus.timer.status === "paused" ? " paused" : ""}
      </span>
    {/if}
  </header>
  {#if error}<p class="error" role="alert">{error}<button class="dismiss" aria-label="Dismiss message" onclick={() => (error = "")}>×</button></p>{/if}

  <div class="focus-first" class:left-open={left} class:right-open={!!right} use:fitHeight>
    <section class="solo" aria-label={mode === "sprint" ? "Sprint" : mode === "lessons" ? "Lessons" : "Problem"}>
      {#if mode === "sprint"}
        <SprintPanel {settings} {history} onrecorded={loadHistory} onsettings={() => setDrawers(left, "settings")} onstart={practising} />
      {:else if mode === "lessons"}
        {#if lessonTopic && byId[lessonTopic]}
          {#key lessonRun}
            <LessonPanel topicId={lessonTopic} {demo} onchange={refresh} ondone={() => { lessonTopic = ""; setMode("practice"); void nextProblem(); }} />
          {/key}
        {:else if topics}
          <div class="card">
            <span class="eyebrow">Lessons</span>
            {#if lessonChoices.length}
              <p class="lead">Pick a topic to learn.</p>
              <div class="choices">
                {#each lessonChoices as topic (topic.id)}<Button size="sm" onclick={() => openLesson(topic.id)}>{topic.title}</Button>{/each}
              </div>
            {:else}
              <p class="lead">Every open topic is mastered.</p>
            {/if}
          </div>
        {/if}
      {:else if reviewTopic && byId[reviewTopic]}
        {#key problemRun}
          <ProblemCard topicId={reviewTopic} {demo} eyebrow={`Review · ${byId[reviewTopic].title}`} ongraded={refresh} onnext={nextProblem} />
        {/key}
      {:else if next?.kind === "review"}
        <div class="stack">
          {#key problemRun}
            <ProblemCard topicId={next.topic_id} {demo} eyebrow={`Review · ${next.title}`} ongraded={refresh} onnext={nextProblem} />
          {/key}
          <p class="why">{next.why}</p>
        </div>
      {:else if next?.kind === "lesson"}
        <div class="card" data-next-lesson={next.topic_id}>
          <span class="eyebrow">Next lesson · {next.title}</span>
          <p class="lead"><MathText text={next.why} inline /></p>
          <Button variant="solid" intent="primary" onclick={() => next && openLesson(next.topic_id)}>Start the lesson</Button>
        </div>
      {:else if nextLoaded}
        <div class="card">
          <span class="eyebrow">Practice</span>
          <p class="lead">Nothing is due. Try a sprint.</p>
          <Button variant="solid" intent="primary" onclick={() => setMode("sprint")}>Mental math sprint</Button>
        </div>
      {:else}
        <p class="why" role="status">Finding what's next…</p>
      {/if}
    </section>

    <button type="button" class="edge left" aria-label="Topics" aria-controls="math-drawer-left" aria-expanded={left} title="Topics ([)" onclick={() => setDrawers(!left, right)}>
      <Icon name="schema" size={14} /><span>Topics</span>{#if due}<span class="count" title={`${due} reviews due`}>{due}</span>{/if}
    </button>
    <button type="button" class="edge right upper" aria-label="History" aria-controls="math-drawer-right" aria-expanded={right === "history"} title="History (])"
      onclick={() => setDrawers(left, right === "history" ? "" : "history")}>
      <Icon name="calendar" size={14} /><span>History</span>
    </button>
    <button type="button" class="edge right lower" aria-label="Settings" aria-controls="math-drawer-right" aria-expanded={right === "settings"} title="Settings"
      onclick={() => setDrawers(left, right === "settings" ? "" : "settings")}>
      <Icon name="settings" size={14} /><span>Settings</span>
    </button>

    {#if left}
      <aside id="math-drawer-left" class="drawer left" aria-label="Topics" transition:fly={{ x: -40, duration: 180 }}>
        <h4>Topics</h4>
        {#if topics?.length}
          <TopicGraph {topics} {selected} onselect={(id) => (selected = id)} />
          <!-- Starting a lesson or review moves to the centre, so the drawer gets out of its way. -->
          {#if selectedTopic}<TopicDetail topic={selectedTopic} {topics} onlesson={(id) => { setDrawers(false, right); openLesson(id); }}
            onreview={(id) => { setDrawers(false, right); openReview(id); }} />{/if}
        {:else if topics}
          <p class="why">No topics yet.</p>
        {:else}
          <p class="why" role="status">Reading the topic graph…</p>
        {/if}
      </aside>
    {/if}
    {#if right === "history"}
      <aside id="math-drawer-right" class="drawer right" aria-label="History" transition:fly={{ x: 40, duration: 180 }}>
        <HistoryPanel {history} {settings} error={historyError} />
      </aside>
    {:else if right === "settings"}
      <aside id="math-drawer-right" class="drawer right" aria-label="Settings" transition:fly={{ x: 40, duration: 180 }}>
        <SettingsPanel {settings} onchange={changeSettings} />
      </aside>
    {/if}
  </div>
</section>

<style>
  .math {
    display: grid;
    gap: 12px;
    min-width: 0;
  }
  .section-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 18px;
    min-height: var(--control-h-lg, 32px);
  }
  h2 {
    margin: 0;
  }
  h4 {
    margin: 0;
    font-size: 14px;
  }
  .tabs {
    display: flex;
    gap: 4px;
  }
  .focus-tag {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-left: auto;
    padding: 3px 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--muted);
    font: 500 11.5px var(--font-mono);
  }
  .focus-tag.math {
    border-color: color-mix(in srgb, var(--accent) 55%, var(--hairline));
    color: var(--ink);
  }
  .focus-tag .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  .error {
    display: flex;
    gap: 8px;
    margin: 0;
    color: var(--warning);
  }
  .dismiss {
    border: 0;
    background: transparent;
    color: inherit;
    cursor: pointer;
  }
  /* Focus-first, as Study: the work alone, centred; drawers slide over it from the edges. */
  .focus-first {
    --drawer-w: min(320px, calc(100% - 48px));
    --left-w: min(400px, calc(100% - 48px));
    position: relative;
    display: grid;
    height: var(--fit-h, auto);
    min-height: 420px;
    overflow: hidden;
  }
  .solo {
    display: grid;
    place-items: center;
    min-width: 0;
    padding: 12px 40px;
    overflow-y: auto;
  }
  .stack {
    display: grid;
    justify-items: center;
    gap: 10px;
    width: min(100%, 560px);
  }
  .card {
    display: grid;
    justify-items: center;
    gap: 12px;
    width: min(100%, 520px);
    box-sizing: border-box;
    padding: 28px 32px;
    border: 1px solid var(--border);
    border-radius: var(--br-radius-lg, 0.8rem);
    background: var(--surface);
    text-align: center;
  }
  .eyebrow {
    color: var(--subtle);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .lead {
    margin: 0;
    color: var(--ink);
    font-size: 15px;
  }
  .choices {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 6px;
  }
  .why {
    margin: 0;
    color: var(--muted);
    font-size: 12.5px;
    text-align: center;
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
    height: 150px;
    border-right: 0;
    border-radius: 12px 0 0 12px;
  }
  .edge.upper {
    top: calc(50% - 80px);
  }
  .edge.lower {
    top: calc(50% + 80px);
  }
  .edge :global(svg) {
    transform: rotate(90deg);
  }
  .edge .count {
    color: var(--warning);
    font: 500 10.5px var(--font-mono);
  }
  .left-open .edge.left {
    left: var(--left-w);
  }
  .right-open .edge.right {
    right: var(--drawer-w);
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
    width: var(--left-w);
    border-left: 0;
    border-radius: 0 14px 14px 0;
  }
  .drawer.right {
    right: 0;
    border-right: 0;
    border-radius: 14px 0 0 14px;
  }
  @media (prefers-reduced-motion: reduce) {
    .edge {
      transition: none;
    }
  }
</style>
