<script lang="ts">
  import { onMount } from "svelte";
  import { api, dateLabel, type Bootstrap } from "./api";
  import JobDetail from "./JobDetail.svelte";
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
  let board = $state<Record<string, Card[]>>({});
  let track = $state("");
  let loading = $state(true);
  let error = $state("");
  let selected = $state<number | null>(null);
  let detail: JobDetail | undefined = $state();
  let pending = $state("");
  let column = $state(0);
  let row = $state(0);
  let container = $state<HTMLElement>();
  async function load() {
    loading = true;
    error = "";
    try {
      const result = await api<{ columns: Record<string, Card[]> }>(
        `/pipeline?track=${encodeURIComponent(track)}`,
      );
      board = result.columns;
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      loading = false;
    }
  }
  async function changed() {
    await load();
    await onchange();
  }
  onMount(() => {
    void load();
  });
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
    if (event.key === "t") {
      event.preventDefault();
      const names = ["", ...(workspace?.tracks.map((t) => t.name) || [])];
      track = names[(names.indexOf(track) + 1) % names.length];
      await load();
      return true;
    }
    const columns = Object.values(board);
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      column = Math.max(
        0,
        Math.min(
          columns.length - 1,
          column + (event.key === "ArrowRight" ? 1 : -1),
        ),
      );
      row = 0;
      container
        ?.querySelector<HTMLButtonElement>(`[data-pipeline="${column}-0"]`)
        ?.focus();
      return true;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      row = Math.max(
        0,
        Math.min(
          (columns[column]?.length || 1) - 1,
          row + (event.key === "ArrowDown" ? 1 : -1),
        ),
      );
      container
        ?.querySelector<HTMLButtonElement>(`[data-pipeline="${column}-${row}"]`)
        ?.focus();
      return true;
    }
    if (
      ["Enter", "s", "u", "n", "o"].includes(event.key) &&
      columns[column]?.[row]
    ) {
      event.preventDefault();
      selected = columns[column][row].job_id;
      pending = event.key === "Enter" ? "" : event.key;
      return true;
    }
    return false;
  }
</script>

<section class="panel pipeline" bind:this={container} aria-label="Application pipeline">
  <div class="panel-heading">
    <h2>Application pipeline</h2>
    <div class="controls">
      <label class="sr-only" for={`${pane.id}-pipeline-track`}>Application track</label
      ><select id={`${pane.id}-pipeline-track`} bind:value={track} onchange={load}
        ><option value="">All application tracks</option
        >{#each workspace?.tracks || [] as item}<option value={item.name}
            >{item.label}</option
          >{/each}</select
      ><button class="button" onclick={onmail}>Review mail proposals</button
      ><button class="button" disabled={loading} onclick={load}>Refresh</button>
    </div>
  </div>
  {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
  {#if loading}<p class="empty-state" role="status">
      Loading pipeline…
    </p>{:else}<div class="columns">
      {#each Object.entries(board) as [status, cards], ci}<section
          class="column"
          aria-label={status}
        >
          <h3>
            {status === "oa" ? "Assessment" : status}<span class="badge"
              >{cards.length}</span
            >
          </h3>
          {#if !cards.length}<p class="muted">
              No applications at this stage.
            </p>{/if}{#each cards as card, ri}<button
              class="pipeline-card"
              data-pipeline={`${ci}-${ri}`}
              onfocus={() => {
                column = ci;
                row = ri;
              }}
              onclick={() => {
                selected = card.job_id;
                pending = "";
              }}
              ><strong>{card.company_name}</strong><span>{card.title}</span
              ><small
                >{card.resume_label || "No resume recorded"} · {card.track ||
                  "Unassigned"}</small
              ><span class="age" class:stale={card.days_since_change >= 21}
                >{card.days_since_change} days since activity</span
              >{#if card.next_action_at}<small
                  >Next: {dateLabel(card.next_action_at)}</small
                >{/if}{#if card.has_proposals}<span class="badge"
                  >Mail proposal waiting</span
                >{/if}</button
            >{/each}
        </section>{/each}
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
    gap: 8px;
  }
  .columns {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 12px;
    padding: 18px;
  }
  .column {
    background: var(--chrome);
    border: 1px solid var(--border);
    border-radius: 9px;
    padding: 14px;
    min-width: 0;
  }
  .column h3 {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    text-transform: capitalize;
    font-size: 14px;
    margin-bottom: 18px;
  }
  .column > p {
    font-size: 12px;
    line-height: 1.7;
  }
  .pipeline-card {
    display: grid;
    gap: 8px;
    width: 100%;
    text-align: left;
    background: var(--surface-raised);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px;
    margin-bottom: 10px;
    color: var(--ink);
  }
  .pipeline-card:hover {
    border-color: var(--accent);
  }
  .pipeline-card strong {
    font-size: 13px;
  }
  .pipeline-card span {
    font-size: 12px;
    line-height: 1.5;
  }
  .pipeline-card small {
    color: var(--muted);
    font-size: 10px;
    line-height: 1.5;
  }
  .age {
    color: var(--muted);
  }
  .age.stale {
    color: var(--warning);
  }
  .pipeline-card .badge {
    justify-self: start;
    font-size: 10px;
  }
  @media (max-width: 1150px) {
    .columns {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .panel-heading {
      flex-wrap: wrap;
    }
  }
  @media (max-width: 550px) {
    .columns {
      grid-template-columns: 1fr;
      padding: 12px;
    }
    .controls select {
      width: 100%;
    }
  }
</style>
