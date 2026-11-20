<script lang="ts">
  import { onMount, tick, untrack } from "svelte";
  import { api, type Bootstrap } from "./api";
  import { careerFocus } from "./career/focus.svelte";
  import Icon from "./Icon.svelte";
  import JobDetail from "./JobDetail.svelte";
  import Loader from "./Loader.svelte";
  import { beginDrag } from "./pointerDrag";
  import RikaPanel from "./rika/RikaPanel.svelte";
  import AskRika from "./rika/AskRika.svelte";
  import TrackFunnel, { type Closed, type FunnelStage } from "./track/TrackFunnel.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";
  const pane = useWorkspaceContext();
  let {
    workspace,
    onchange,
    onmail,
  }: {
    workspace: Bootstrap | null;
    onchange: () => Promise<void>;
    onmail: () => void;
  } = $props();
  type Card = {
    job_id: number;
    company_name: string;
    title: string;
    status: string;
    track: string | null;
    resume_label: string | null;
    has_proposals: boolean;
  };
  type Lane = { name: string | null; label: string; tone: string };
  /** `GET /api/pipeline/funnel`: per track, how far its applications got. */
  type Reach = {
    track: string | null;
    stages: { id: string; label: string; reached: number; now: number }[];
    closed: Closed;
  };
  type Move = {
    job: number;
    company: string;
    stage: { from: string; to: string } | null;
    track: { from: string | null; to: string | null } | null;
  };
  /** Open stages are columns; rejected and ghosted applications share the Closed column. */
  const STAGES = [
    { id: "applied", label: "Applied", tone: "var(--c1)" },
    { id: "oa", label: "Assessment", tone: "var(--c2)" },
    { id: "interview", label: "Interview", tone: "var(--c3)" },
    { id: "offer", label: "Offer", tone: "var(--c4)" },
  ];
  const CLOSED = "closed";
  const CLOSED_LABEL: Record<string, string> = { rejected: "Rejected", ghosted: "No response", withdrawn: "Withdrawn" };
  const NO_TRACK: Lane = { name: null, label: "No track", tone: "var(--subtle)" };
  const stageLabel = (id: string) =>
    STAGES.find((stage) => stage.id === id)?.label ?? CLOSED_LABEL[id] ?? id.charAt(0).toUpperCase() + id.slice(1);
  let cards = $state<Card[]>([]);
  let reach = $state<Reach[]>([]);
  let loading = $state(true);
  let error = $state("");
  let selected = $state<number | null>(null);
  let detail: JobDetail | undefined = $state();
  let pending = $state("");
  let column = $state(0);
  let row = $state(0);
  let container = $state<HTMLElement>();
  let drag = $state<{ job: number; x: number; y: number; w: number; zone: string | null } | null>(null);
  let moved = $state<Move | null>(null);
  /** The track filter; null shows every track. */
  let only = $state<{ name: string | null } | null>(null);
  /** The stage a Career rail link asked for, outlined for a moment. */
  let wanted = $state<string | null>(null);
  let pendingSaves = 0;
  let saving = Promise.resolve();

  const lanes = $derived.by<Lane[]>(() => {
    const configured = (workspace?.tracks ?? []).map((track, index) => ({
      name: track.name,
      label: track.label,
      tone: `var(--c${(index % 6) + 1})`,
    }));
    const known = new Set(configured.map((lane) => lane.name));
    const tracks = [...cards.map((card) => card.track), ...reach.map((entry) => entry.track)];
    return tracks.some((track) => !track || !known.has(track)) ? [...configured, NO_TRACK] : configured;
  });
  const laneOf = (track: string | null) =>
    lanes.find((lane) => lane.name !== null && lane.name === track) ?? NO_TRACK;
  const stageOf = (card: Card) => (STAGES.some((stage) => stage.id === card.status) ? card.status : CLOSED);
  const visible = (track: string | null) => !only || laneOf(track).name === only.name;
  const shown = $derived(cards.filter((card) => visible(card.track)));
  /** Keyboard columns: each stage's shown cards, the same order they are drawn in, then Closed. */
  const columns = $derived(
    [...STAGES.map((stage) => stage.id), CLOSED].map((stage) => shown.filter((card) => stageOf(card) === stage)),
  );
  /** The funnel for the shown tracks: the per-track counts added up. */
  const funnel = $derived.by(() => {
    const mine = reach.filter((entry) => visible(entry.track));
    const sum = (pick: (entry: Reach) => number) => mine.reduce((total, entry) => total + pick(entry), 0);
    const stages: FunnelStage[] = STAGES.map((stage, index) => ({
      ...stage,
      reached: sum((entry) => entry.stages[index]?.reached ?? 0),
      now: sum((entry) => entry.stages[index]?.now ?? 0),
    }));
    const closed: Closed = {
      rejected: sum((entry) => entry.closed.rejected),
      ghosted: sum((entry) => entry.closed.ghosted),
      withdrawn: sum((entry) => entry.closed.withdrawn),
    };
    return { stages, closed };
  });
  const dragged = $derived(drag ? cards.find((card) => card.job_id === drag!.job) : undefined);

  async function load(quiet = false) {
    if (!quiet) loading = true;
    error = "";
    try {
      const [board, counts] = await Promise.all([
        api<{ columns: Record<string, Card[]> }>("/pipeline"),
        api<{ tracks: Reach[] }>("/pipeline/funnel"),
      ]);
      cards = Object.values(board.columns).flat();
      reach = counts.tracks;
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      loading = false;
    }
  }
  async function changed() {
    await load(true);
    await onchange();
  }
  onMount(() => {
    void load();
  });

  /** A Career rail link (Interviews) opens Track on one stage: bring that column into view. */
  $effect(() => {
    const stage = careerFocus.stage;
    if (!stage || loading || !pane.active) return;
    careerFocus.stage = null;
    untrack(() => void reveal(stage));
  });
  async function reveal(stage: string) {
    only = null;
    wanted = stage;
    await tick();
    const node = container?.querySelector<HTMLElement>(`[data-stage="${stage}"]`);
    node?.scrollIntoView({ block: "nearest", inline: "nearest" });
    node?.querySelector<HTMLButtonElement>("[data-pipeline]")?.focus({ preventScroll: true });
    setTimeout(() => {
      if (wanted === stage) wanted = null;
    }, 2400);
  }

  async function focusCard(job: number) {
    await tick();
    container?.querySelector<HTMLButtonElement>(`[data-job="${job}"]`)?.focus();
  }

  /**
   * Saves run one after another so quick keyboard moves are never dropped; the board
   * reloads once the last one lands, keeping earlier optimistic moves on screen meanwhile.
   */
  function save(job: number, work: () => Promise<void>) {
    pendingSaves += 1;
    error = "";
    saving = saving.then(async () => {
      try {
        await work();
      } catch (cause) {
        error = (cause as Error).message;
      }
      pendingSaves -= 1;
      if (pendingSaves) return;
      await changed();
      await focusCard(job);
    });
    return saving;
  }

  /** A filtered board follows a card moved to another track, so it stays in view. */
  function follow(track: string | null) {
    if (only) only = { name: laneOf(track).name };
  }

  /** Moves a card to another open stage and/or track through the status and track actions. */
  async function move(card: Card, stage: string, lane: string | null) {
    const stageChanged = stage !== card.status;
    const trackChanged = lane !== laneOf(card.track).name;
    if (!stageChanged && !trackChanged) return;
    if (stageChanged && stage === "applied") {
      error = "An application cannot move back to Applied; use Undo on the last change instead.";
      return;
    }
    const change: Move = {
      job: card.job_id,
      company: card.company_name,
      stage: stageChanged ? { from: card.status, to: stage } : null,
      track: trackChanged ? { from: card.track, to: lane } : null,
    };
    cards = cards.map((item) =>
      item.job_id === card.job_id ? { ...item, status: stage, track: trackChanged ? lane : item.track } : item,
    );
    if (trackChanged) follow(lane);
    void focusCard(card.job_id);
    await save(card.job_id, async () => {
      if (change.stage) await api(`/jobs/${change.job}`, { action: "status", value: stage });
      if (change.track) await api(`/jobs/${change.job}`, { action: "track", value: lane });
      moved = change;
    });
  }

  async function undo() {
    const change = moved;
    if (!change) return;
    moved = null;
    if (change.track) follow(change.track.from);
    await save(change.job, async () => {
      if (change.track) await api(`/jobs/${change.job}`, { action: "track", value: change.track.from });
      if (change.stage) await api(`/job-tools/${change.job}`, { action: "undo" });
    });
  }

  function grab(event: PointerEvent, card: Card) {
    const node = event.currentTarget as HTMLElement;
    const box = node.getBoundingClientRect();
    const dx = event.clientX - box.left;
    const dy = event.clientY - box.top;
    const place = (x: number, y: number, zone: string | null) => {
      const frame = container!.getBoundingClientRect();
      return { job: card.job_id, x: x - frame.left - dx, y: y - frame.top - dy, w: box.width, zone };
    };
    beginDrag(event, {
      start: (x, y) => (drag = place(x, y, null)),
      move: (x, y, zone) => (drag = place(x, y, zone)),
      drop: (zone) => {
        drag = null;
        if (!zone) return;
        const [kind, ...rest] = zone.split(":");
        const value = rest.join(":");
        if (kind === "stage") void move(card, value, laneOf(card.track).name);
        else void move(card, card.status, value === "" ? null : value);
      },
      cancel: () => (drag = null),
      click: () => open(card.job_id),
    });
  }

  function open(job: number, command = "") {
    selected = job;
    pending = command;
  }

  /** A stage column's drop zone, or undefined where the dragged card may not land (back to Applied). */
  function stageZone(stage: string): string | undefined {
    if (dragged && stage === "applied" && dragged.status !== "applied") return undefined;
    return `stage:${stage}`;
  }
  /** A track chip's drop zone while a card from another track is dragged. */
  function trackZone(lane: Lane): string | undefined {
    if (!dragged || laneOf(dragged.track) === lane) return undefined;
    return `track:${lane.name ?? ""}`;
  }

  export async function handleKey(event: KeyboardEvent): Promise<boolean> {
    if (!pane.active || event.ctrlKey || event.metaKey || event.altKey) return false;
    if (selected !== null) {
      if (event.key === "Escape") {
        selected = null;
        return true;
      }
      return (await detail?.handleKey(event)) ?? false;
    }
    if (event.key === "r") {
      event.preventDefault();
      await load();
      return true;
    }
    if (event.key === "v" || event.key === "G") {
      event.preventDefault();
      onmail();
      return true;
    }
    const current = columns[column]?.[row];
    if (event.shiftKey && event.key.startsWith("Arrow")) {
      event.preventDefault();
      if (!current || stageOf(current) === CLOSED) return true;
      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        const at = STAGES.findIndex((stage) => stage.id === current.status);
        const next = STAGES[at + (event.key === "ArrowRight" ? 1 : -1)];
        if (next) await move(current, next.id, laneOf(current.track).name);
      } else {
        const tracks = lanes.filter((lane) => lane.name !== null);
        const at = tracks.indexOf(laneOf(current.track));
        const next = tracks[at < 0 ? 0 : at + (event.key === "ArrowDown" ? 1 : -1)];
        if (next) await move(current, current.status, next.name);
      }
      return true;
    }
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      column = Math.max(0, Math.min(columns.length - 1, column + (event.key === "ArrowRight" ? 1 : -1)));
      row = 0;
      container?.querySelector<HTMLButtonElement>(`[data-pipeline="${column}-0"]`)?.focus();
      return true;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      row = Math.max(0, Math.min((columns[column]?.length || 1) - 1, row + (event.key === "ArrowDown" ? 1 : -1)));
      container?.querySelector<HTMLButtonElement>(`[data-pipeline="${column}-${row}"]`)?.focus();
      return true;
    }
    if (["Enter", "s", "u", "n", "o"].includes(event.key) && current) {
      event.preventDefault();
      open(current.job_id, event.key === "Enter" ? "" : event.key);
      return true;
    }
    return false;
  }
</script>

{#snippet card(item: Card, ci: number, ri: number)}
  {@const lane = laneOf(item.track)}
  <button
    class="app-card"
    class:lifted={drag?.job === item.job_id}
    style:--tone={lane.tone}
    data-pipeline={`${ci}-${ri}`}
    data-job={item.job_id}
    title={lane.label}
    aria-label={`${item.company_name}, ${item.title}. ${stageLabel(item.status)}, ${lane.label}.${item.resume_label ? ` Sent with the ${item.resume_label} resume.` : ""}${item.has_proposals ? " A status email is waiting for review." : ""} Shift+arrow keys move it between stages and tracks.`}
    onfocus={() => {
      column = ci;
      row = ri;
    }}
    onpointerdown={(event) => grab(event, item)}
    onclick={(event) => {
      // Pointer presses are handled by the drag; this is Space on a focused card.
      if (event.detail === 0) open(item.job_id);
    }}
  >
    <strong class="company">{item.company_name}</strong>
    <span class="role">{item.title}</span>
    {#if item.resume_label || item.has_proposals}<span class="foot"
        >{#if item.resume_label}<span class="resume" title={`Sent with the ${item.resume_label} resume`}
            ><Icon name="resumes" size={12} /><span>{item.resume_label}</span></span
          >{/if}{#if item.has_proposals}<span class="email" title="A status email is waiting for review"
            ><Icon name="mail" size={12} />Email waiting</span
          >{/if}</span
      >{/if}
  </button>
{/snippet}

<!-- Career's landing: the funnel and the board, with home's Rika column at its right (below it when narrow). -->
<div class="track">
<section class="panel pipeline" aria-label="Application pipeline">
  <div class="panel-heading">
    <h2>Track</h2>
    <div class="controls">
      {#if moved}<div class="undo-pill" role="status">
          <span
            ><strong>{moved.company}</strong> moved to {[
              moved.stage && stageLabel(moved.stage.to),
              moved.track && laneOf(moved.track.to).label,
            ]
              .filter(Boolean)
              .join(" · ")}</span
          ><button class="pill" onclick={undo}>Undo</button>
        </div>{/if}
      <AskRika />
      <button class="button" onclick={onmail}>Review mail proposals</button>
      <button class="button" disabled={loading} onclick={() => load()}>Refresh</button>
    </div>
  </div>
  {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
  {#if loading}<Loader label="Loading pipeline…" />{:else}<div class="board-wrap" bind:this={container} class:dragging={!!drag}>
    {#if lanes.length > 1}<div class="tracks" role="group" aria-label="Show track">
          <button class="pill" aria-pressed={!only} onclick={() => (only = null)}
            >All tracks <b>{cards.length}</b></button
          >
          {#each lanes as lane (lane.name)}
            {@const target = trackZone(lane)}
            <button
              class="pill"
              style:--tone={lane.tone}
              aria-pressed={only?.name === lane.name}
              class:over={!!target && drag?.zone === target}
              data-drop={target}
              title={drag ? `Drop to move it to ${lane.label}` : undefined}
              onclick={() => (only = { name: lane.name })}
              ><i></i>{lane.label} <b>{cards.filter((item) => laneOf(item.track) === lane).length}</b></button
            >
          {/each}
      </div>{/if}
    <div class="board">
      <TrackFunnel stages={funnel.stages} closed={funnel.closed} />
      <div class="kanban" role="group" aria-label="Applications by stage">
        {#each STAGES as stage, ci (stage.id)}
          {@const target = stageZone(stage.id)}
          <div
            class="column"
            class:wanted={wanted === stage.id}
            role="group"
            aria-label={stage.label}
            data-stage={stage.id}
            style:--tone={stage.tone}
          >
            <div class="col-head"><i></i><span>{stage.label}</span><b>{columns[ci].length}</b></div>
            <div
              class="col-body"
              class:over={!!target && drag?.zone === target}
              class:refused={!!drag && !target}
              data-drop={target}
            >
              {#each columns[ci] as item, ri (item.job_id)}{@render card(item, ci, ri)}{/each}
              {#if !columns[ci].length}<p class="empty">Nothing here yet</p>{/if}
            </div>
          </div>
        {/each}
        <div class="column closed" role="group" aria-label="Closed" data-stage={CLOSED}>
          <div class="col-head"><i></i><span>Closed</span><b>{columns[STAGES.length].length}</b></div>
          <div class="col-body">
            {#each columns[STAGES.length] as item, ri (item.job_id)}
              <button
                class="closed-row"
                data-pipeline={`${STAGES.length}-${ri}`}
                data-job={item.job_id}
                aria-label={`${item.company_name}, ${item.title}. ${stageLabel(item.status)}, ${laneOf(item.track).label}.`}
                onfocus={() => {
                  column = STAGES.length;
                  row = ri;
                }}
                onclick={() => open(item.job_id)}
                ><span class="who">{item.company_name}</span><span class="why" class:ghost={item.status !== "rejected"}
                  >{stageLabel(item.status)}</span
                ></button
              >
            {/each}
          </div>
        </div>
      </div>
    </div>
    {#if drag && dragged}<div
        class="drag-ghost"
        style:left={`${drag.x}px`}
        style:top={`${drag.y}px`}
        style:width={`${drag.w}px`}
        aria-hidden="true"
      >
        <strong>{dragged.company_name}</strong><span>{dragged.title}</span>
      </div>{/if}
  </div>{/if}
</section>
<aside class="track-rika"><RikaPanel id={`track-${pane.id}-rika`} active={pane.active} /></aside>
</div>
{#if selected !== null}{#key selected}<JobDetail
      bind:this={detail}
      id={selected}
      focus="application"
      initialCommand={pending}
      demo={workspace?.demo ?? true}
      onclose={() => (selected = null)}
      onchange={changed}
    />{/key}{/if}

<style>
  .track {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(280px, 340px);
    gap: 18px;
    align-items: start;
    margin-bottom: 18px;
  }
  .pipeline {
    min-width: 0;
  }
  /* Rika keeps a steady column beside the board and stays in view while the board scrolls. */
  .track-rika {
    position: sticky;
    top: 12px;
    height: min(640px, calc(100vh - 160px));
    min-height: 360px;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .undo-pill {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    padding: 3px 4px 3px 14px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-raised) 80%, transparent);
    color: var(--muted);
    font-size: 12.5px;
  }
  .undo-pill strong {
    color: var(--ink);
  }
  .undo-pill .pill {
    min-height: 26px;
    padding: 2px 10px;
  }
  /* The drag ghost sits on the wrapper, so it is not clipped by the board's sideways scroll. */
  .board-wrap {
    position: relative;
  }
  .board-wrap.dragging {
    cursor: grabbing;
  }
  .board {
    display: grid;
    gap: 14px;
    padding: 12px 18px 18px;
    overflow-x: auto;
  }
  .tracks {
    display: flex;
    flex-wrap: wrap;
    padding: 14px 18px 0;
    gap: 6px;
  }
  .tracks .pill {
    min-height: 26px;
    font-size: 12px;
    color: var(--muted);
  }
  .tracks .pill[aria-pressed="true"] {
    border-color: color-mix(in srgb, var(--ink) 45%, transparent);
    background: color-mix(in srgb, var(--ink) 9%, transparent);
    color: var(--ink);
  }
  .tracks .pill.over {
    border-color: var(--violet);
    background: color-mix(in srgb, var(--violet) 14%, transparent);
    color: var(--ink);
  }
  .tracks i {
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--tone);
  }
  .tracks b {
    color: var(--subtle);
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }
  .kanban {
    display: grid;
    grid-template-columns: repeat(4, minmax(150px, 1fr)) minmax(130px, 0.85fr);
    gap: 10px;
    min-width: 780px;
  }
  .column {
    display: grid;
    grid-template-rows: auto 1fr;
    gap: 8px;
    min-width: 0;
    padding: 8px;
    border: 1px solid color-mix(in srgb, var(--hairline) 70%, transparent);
    border-top: 2px solid var(--tone);
    border-radius: 12px;
    background: color-mix(in srgb, var(--solid-panel) 40%, transparent);
  }
  .column.wanted {
    outline: 2px solid color-mix(in srgb, var(--tone) 70%, transparent);
    outline-offset: 2px;
  }
  .column.closed {
    --tone: var(--subtle);
    border-style: dashed;
    border-top-style: solid;
    background: transparent;
  }
  .col-head {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 2px 4px;
    color: var(--muted);
    font: 500 10.5px var(--font-ui);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .col-head i {
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--tone);
  }
  .col-head b {
    margin-left: auto;
    padding: 0 7px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--tone) 18%, transparent);
    color: var(--ink);
    font: 600 10.5px / 1.7 var(--font-ui);
    letter-spacing: 0;
  }
  .col-body {
    display: grid;
    align-content: start;
    gap: 6px;
    min-height: 140px;
    padding: 2px;
    border: 1px solid transparent;
    border-radius: 10px;
  }
  .col-body.over {
    border-color: var(--violet);
    background: color-mix(in srgb, var(--violet) 12%, transparent);
  }
  .col-body.refused {
    opacity: 0.45;
  }
  .empty {
    margin: 0;
    padding: 14px 6px;
    border: 1px dashed var(--hairline);
    border-radius: 10px;
    color: var(--subtle);
    font-size: 11.5px;
    text-align: center;
  }
  /* Q11: company and role, the resume sent and a waiting-email badge; the track is the edge colour. */
  .app-card {
    display: grid;
    gap: 1px;
    width: 100%;
    min-width: 0;
    padding: 9px 11px 9px 12px;
    border: 1px solid var(--hairline);
    border-radius: 10px;
    background:
      linear-gradient(90deg, color-mix(in srgb, var(--tone) 9%, transparent), transparent 46%),
      color-mix(in srgb, var(--solid-raised) 82%, transparent);
    box-shadow: inset 3px 0 0 var(--tone);
    color: var(--ink);
    text-align: left;
    font: inherit;
    cursor: grab;
    touch-action: none;
    user-select: none;
  }
  .app-card:hover {
    border-color: color-mix(in srgb, var(--violet) 55%, transparent);
  }
  .app-card:focus-visible {
    outline: 2px solid var(--violet);
    outline-offset: 2px;
  }
  .app-card.lifted {
    opacity: 0.32;
    border-style: dashed;
  }
  .company,
  .role {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .company {
    font: 600 13px / 1.3 var(--font-ui);
  }
  .role {
    color: var(--muted);
    font: 12px / 1.35 var(--font-ui);
  }
  /* The resume keeps its name; the badge wraps under it when the card is narrow. */
  .foot {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 6px;
    min-width: 0;
    margin-top: 6px;
  }
  .resume,
  .email {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 1px 7px;
    border-radius: var(--r-chip);
    font: 500 10.5px / 1.6 var(--font-ui);
    white-space: nowrap;
  }
  .resume {
    flex-shrink: 0;
    max-width: 100%;
    border: 1px solid var(--hairline);
    color: var(--muted);
  }
  .resume span {
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .resume :global(svg) {
    flex: none;
  }
  .email {
    flex: none;
    margin-left: auto;
    border: 1px solid color-mix(in srgb, var(--violet) 45%, transparent);
    background: color-mix(in srgb, var(--violet) 16%, transparent);
    color: var(--ink);
  }
  .email :global(svg) {
    color: var(--violet);
  }
  .closed-row {
    display: grid;
    gap: 1px;
    width: 100%;
    padding: 6px 9px;
    border: 1px solid transparent;
    border-radius: 8px;
    background: color-mix(in srgb, var(--ink) 4%, transparent);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .closed-row:hover,
  .closed-row:focus-visible {
    border-color: var(--violet);
    outline: none;
  }
  .closed-row .who {
    overflow: hidden;
    color: var(--muted);
    font: 500 12px var(--font-ui);
    text-decoration: line-through;
    text-decoration-color: color-mix(in srgb, var(--subtle) 50%, transparent);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .closed-row .why {
    color: var(--error);
    font: 10px var(--font-ui);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .closed-row .why.ghost {
    color: var(--subtle);
  }
  .drag-ghost {
    position: absolute;
    z-index: 5;
    display: grid;
    gap: 3px;
    padding: 9px 11px;
    border: 1px solid var(--violet);
    border-radius: 10px;
    background: var(--solid-raised);
    color: var(--ink);
    pointer-events: none;
    box-shadow:
      0 18px 40px color-mix(in srgb, var(--shadow-tint) 50%, transparent),
      0 0 24px color-mix(in srgb, var(--violet) 22%, transparent);
  }
  .drag-ghost strong {
    font: 600 13px var(--font-ui);
  }
  .drag-ghost span {
    color: var(--muted);
    font-size: 12px;
  }
  :global(:root[data-anim="on"]:not([data-motion="reduced"])) .app-card {
    transition:
      transform 140ms,
      border-color 140ms,
      opacity 140ms;
  }
  :global(:root[data-anim="on"]:not([data-motion="reduced"])) .app-card:hover {
    transform: translateY(-1px);
  }
  :global(:root[data-anim="on"]:not([data-motion="reduced"])) .col-body {
    transition:
      border-color 140ms,
      background-color 140ms;
  }
  :global(:root[data-anim="on"]:not([data-motion="reduced"])) .drag-ghost {
    transform: rotate(-2.5deg);
  }
  @media (max-width: 1150px) {
    .panel-heading {
      flex-wrap: wrap;
    }
    /* Narrow, the board takes the width and Rika follows below it. */
    .track {
      grid-template-columns: minmax(0, 1fr);
    }
    .track-rika {
      position: static;
      height: 480px;
    }
  }
</style>
