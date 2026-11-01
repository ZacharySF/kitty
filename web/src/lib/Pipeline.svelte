<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api, dateLabel, type Bootstrap } from "./api";
  import Icon from "./Icon.svelte";
  import JobDetail from "./JobDetail.svelte";
  import Loader from "./Loader.svelte";
  import { beginDrag } from "./pointerDrag";
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
    days_since_change: number;
    next_action_at: string | null;
    has_proposals: boolean;
  };
  type Lane = { name: string | null; label: string; tone: string };
  type Move = {
    job: number;
    company: string;
    stage: { from: string; to: string } | null;
    track: { from: string | null; to: string | null } | null;
  };
  /** Open stages are columns; rejected and ghosted applications share the Closed column. */
  const STAGES = [
    { id: "applied", label: "Applied" },
    { id: "oa", label: "Assessment" },
    { id: "interview", label: "Interview" },
    { id: "offer", label: "Offer" },
  ];
  const CLOSED = "closed";
  const stageLabel = (id: string) =>
    STAGES.find((stage) => stage.id === id)?.label ?? id.charAt(0).toUpperCase() + id.slice(1);
  let cards = $state<Card[]>([]);
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
  let pendingSaves = 0;
  let saving = Promise.resolve();

  const lanes = $derived.by<Lane[]>(() => {
    const configured = (workspace?.tracks ?? []).map((track, index) => ({
      name: track.name,
      label: track.label,
      tone: `var(--c${(index % 6) + 1})`,
    }));
    const known = new Set(configured.map((lane) => lane.name));
    const others = cards.some((card) => !card.track || !known.has(card.track));
    return others ? [...configured, { name: null, label: "No track", tone: "var(--subtle)" }] : configured;
  });
  const laneOf = (card: Card) =>
    lanes.find((lane) => lane.name === card.track) ?? lanes.find((lane) => lane.name === null)!;
  const stageOf = (card: Card) => (STAGES.some((stage) => stage.id === card.status) ? card.status : CLOSED);
  /** Keyboard columns: each stage's cards in lane order, the same order they are drawn in. */
  const columns = $derived(
    [...STAGES.map((stage) => stage.id), CLOSED].map((stage) =>
      lanes.flatMap((lane) => cards.filter((card) => laneOf(card) === lane && stageOf(card) === stage)),
    ),
  );
  const busiest = $derived(Math.max(1, ...lanes.map((lane) => cards.filter((card) => laneOf(card) === lane).length)));
  const dragged = $derived(drag ? cards.find((card) => card.job_id === drag!.job) : undefined);

  async function load(quiet = false) {
    if (!quiet) loading = true;
    error = "";
    try {
      const result = await api<{ columns: Record<string, Card[]> }>("/pipeline");
      cards = Object.values(result.columns).flat();
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

  /** Moves a card to another open stage and/or lane through the status and track actions. */
  async function move(card: Card, stage: string, lane: string | null) {
    const stageChanged = stage !== card.status;
    const trackChanged = lane !== laneOf(card).name;
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
      item.job_id === card.job_id ? { ...item, status: stage, track: lane, days_since_change: 0 } : item,
    );
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
        const [stage, ...name] = zone.split(":");
        const lane = name.join(":");
        void move(card, stage, lane === "" ? null : lane);
      },
      cancel: () => (drag = null),
      click: () => open(card.job_id),
    });
  }

  function open(job: number, command = "") {
    selected = job;
    pending = command;
  }

  /** A drop zone id, or undefined where the dragged card may not land (back to Applied). */
  function zone(stage: string, lane: Lane): string | undefined {
    if (dragged && stage === "applied" && dragged.status !== "applied") return undefined;
    return `${stage}:${lane.name ?? ""}`;
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
        if (next) await move(current, next.id, laneOf(current).name);
      } else {
        const tracks = lanes.filter((lane) => lane.name !== null);
        const at = tracks.indexOf(laneOf(current));
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

{#snippet card(item: Card)}
  {@const ci = STAGES.findIndex((stage) => stage.id === item.status)}
  {@const ri = columns[ci].indexOf(item)}
  <button
    class="lane-card"
    class:lifted={drag?.job === item.job_id}
    style:--tone={laneOf(item).tone}
    data-pipeline={`${ci}-${ri}`}
    data-job={item.job_id}
    aria-label={`${item.company_name}, ${item.title}. ${stageLabel(item.status)}, ${laneOf(item).label}. ${item.days_since_change} days since activity${item.has_proposals ? ". Mail proposal waiting" : ""}. Shift+arrow keys move it between stages and tracks.`}
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
    <span class="card-top"
      ><strong>{item.company_name}</strong>{#if item.has_proposals}<span class="mail" title="Mail proposal waiting"
          ><Icon name="mail" size={13} /></span
        >{/if}</span
    >
    <span class="card-title">{item.title}</span>
    <span class="card-meta"
      ><span class:stale={item.days_since_change >= 21}
        >{item.days_since_change === 0 ? "today" : `${item.days_since_change}d`}</span
      >{#if item.next_action_at}<span class="next">→ {dateLabel(item.next_action_at)}</span>{/if}</span
    >
  </button>
{/snippet}

<section class="panel pipeline" aria-label="Application pipeline">
  <div class="panel-heading">
    <h2>Application pipeline</h2>
    <div class="controls">
      {#if moved}<div class="undo-pill" role="status">
          <span
            ><strong>{moved.company}</strong> moved to {[
              moved.stage && stageLabel(moved.stage.to),
              moved.track && (lanes.find((lane) => lane.name === moved!.track!.to)?.label ?? "No track"),
            ]
              .filter(Boolean)
              .join(" · ")}</span
          ><button class="pill" onclick={undo}>Undo</button>
        </div>{/if}
      <button class="button" onclick={onmail}>Review mail proposals</button>
      <button class="button" disabled={loading} onclick={() => load()}>Refresh</button>
    </div>
  </div>
  {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
  {#if loading}<Loader label="Loading pipeline…" />{:else}<div class="lanes" bind:this={container} class:dragging={!!drag}>
      <div class="lane-grid" role="grid" aria-label="Applications by track and stage" style:--stages={STAGES.length}>
        <div class="lane-row" role="row">
          <span role="columnheader"></span>
          {#each STAGES as stage, index (stage.id)}<span class="col-head" role="columnheader"
              ><i style:background={`var(--c${index + 1})`}></i>{stage.label}</span
            >{/each}
          <span class="col-head" role="columnheader">Closed</span>
        </div>
        {#each lanes as lane (lane.name)}
          {@const mine = cards.filter((item) => laneOf(item) === lane)}
          {@const openCount = mine.filter((item) => stageOf(item) !== CLOSED).length}
          <div class="lane-row" role="row">
            <div class="lane-head" role="rowheader" style:--tone={lane.tone}>
              <strong>{lane.label}</strong>
              <small>{mine.length} sent · {openCount} open</small>
              <span class="lane-bar"><span style:width={`${(mine.length / busiest) * 100}%`}></span></span>
            </div>
            {#each STAGES as stage (stage.id)}
              {@const target = zone(stage.id, lane)}
              <div
                class="cell"
                role="gridcell"
                aria-label={`${lane.label} · ${stage.label}`}
                class:over={!!target && drag?.zone === target}
                class:refused={!!drag && !target}
                data-drop={target}
              >
                {#each mine.filter((item) => item.status === stage.id) as item (item.job_id)}{@render card(item)}{/each}
              </div>
            {/each}
            <div class="cell closed" role="gridcell" aria-label={`${lane.label} · Closed`}>
              {#each mine.filter((item) => stageOf(item) === CLOSED) as item (item.job_id)}
                {@const ri = columns[STAGES.length].indexOf(item)}
                <button
                  class="closed-chip"
                  data-pipeline={`${STAGES.length}-${ri}`}
                  data-job={item.job_id}
                  title={`${item.company_name}: ${stageLabel(item.status)}`}
                  aria-label={`${item.company_name}, ${item.title}. ${stageLabel(item.status)}, ${lane.label}.`}
                  onfocus={() => {
                    column = STAGES.length;
                    row = ri;
                  }}
                  onclick={() => open(item.job_id)}>{item.company_name}</button
                >
              {/each}
            </div>
          </div>
        {/each}
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
{#if selected !== null}{#key selected}<JobDetail
      bind:this={detail}
      id={selected}
      initialCommand={pending}
      demo={workspace?.demo ?? true}
      onclose={() => (selected = null)}
      onchange={changed}
    />{/key}{/if}

<style>
  .pipeline {
    margin-bottom: 18px;
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
  .lanes {
    position: relative;
    padding: 16px 18px 18px;
    overflow-x: auto;
  }
  .lanes.dragging {
    cursor: grabbing;
  }
  .lane-grid {
    display: grid;
    grid-template-columns: 150px repeat(var(--stages), minmax(140px, 1fr)) minmax(110px, 0.8fr);
    gap: 6px;
    min-width: 860px;
  }
  .lane-row {
    display: contents;
  }
  .col-head {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 0 4px 4px;
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .col-head i {
    width: 7px;
    height: 7px;
    border-radius: 999px;
  }
  .lane-head {
    display: grid;
    align-content: center;
    gap: 3px;
    padding: 10px 12px;
    border-left: 2px solid var(--tone);
    border-radius: 0 10px 10px 0;
    background: color-mix(in srgb, var(--tone) 7%, transparent);
  }
  .lane-head strong {
    font: 600 14px var(--heading);
    color: var(--ink);
  }
  .lane-head small {
    color: var(--muted);
    font: 10px var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .lane-bar {
    height: 3px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 10%, transparent);
    overflow: hidden;
  }
  .lane-bar span {
    display: block;
    height: 100%;
    background: var(--tone);
  }
  .cell {
    display: grid;
    align-content: start;
    gap: 6px;
    min-height: 64px;
    padding: 6px;
    border: 1px solid color-mix(in srgb, var(--hairline) 70%, transparent);
    border-radius: 10px;
    background: color-mix(in srgb, var(--solid-panel) 35%, transparent);
    transition:
      border-color 140ms,
      background-color 140ms;
  }
  .cell.over {
    border-color: var(--violet);
    background: color-mix(in srgb, var(--violet) 12%, transparent);
  }
  .cell.refused {
    opacity: 0.45;
  }
  .cell.closed {
    display: flex;
    flex-wrap: wrap;
    align-content: start;
    background: transparent;
    border-style: dashed;
  }
  .lane-card {
    display: grid;
    gap: 3px;
    width: 100%;
    min-width: 0;
    padding: 7px 9px;
    border: 1px solid var(--hairline);
    border-left: 2px solid var(--tone);
    border-radius: 10px;
    background: color-mix(in srgb, var(--solid-raised) 78%, transparent);
    color: var(--ink);
    text-align: left;
    font: inherit;
    cursor: grab;
    touch-action: none;
    user-select: none;
    transition:
      transform 140ms,
      border-color 140ms,
      opacity 140ms;
  }
  .lane-card:hover {
    border-color: color-mix(in srgb, var(--violet) 60%, transparent);
    border-left-color: var(--tone);
    transform: translateY(-1px);
  }
  .lane-card:focus-visible {
    outline: 2px solid var(--violet);
    outline-offset: 2px;
  }
  .lane-card.lifted {
    opacity: 0.32;
    border-style: dashed;
  }
  .card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    min-width: 0;
  }
  .card-top strong {
    font: 600 13px var(--heading);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mail {
    display: inline-flex;
    color: var(--violet);
  }
  .card-title {
    overflow: hidden;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.35;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .card-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 8px;
    color: var(--subtle);
    font: 10px var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
  .card-meta .stale {
    color: var(--warning);
  }
  .card-meta .next {
    color: var(--ink);
  }
  .closed-chip {
    padding: 2px 8px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--subtle);
    font: inherit;
    font-size: 11px;
    text-decoration: line-through;
    text-decoration-color: color-mix(in srgb, var(--subtle) 50%, transparent);
    cursor: pointer;
  }
  .closed-chip:hover,
  .closed-chip:focus-visible {
    color: var(--ink);
    border-color: var(--violet);
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
    transform: rotate(-2.5deg);
    box-shadow:
      0 18px 40px color-mix(in srgb, var(--shadow-tint) 50%, transparent),
      0 0 24px color-mix(in srgb, var(--violet) 22%, transparent);
  }
  .drag-ghost strong {
    font: 600 13px var(--heading);
  }
  .drag-ghost span {
    color: var(--muted);
    font-size: 12px;
  }
  @media (prefers-reduced-motion: reduce) {
    .lane-card,
    .cell {
      transition: none;
    }
    .lane-card:hover {
      transform: none;
    }
    .drag-ghost {
      transform: none;
    }
  }
  @media (max-width: 1150px) {
    .panel-heading {
      flex-wrap: wrap;
    }
  }
</style>
