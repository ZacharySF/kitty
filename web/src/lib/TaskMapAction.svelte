<script lang="ts">
  import { untrack } from "svelte";
  import { Badge, Button, Popover } from "@nunera/bracket";
  import { api } from "./api";
  type MapRef = { id: number; title: string };
  type MapNode = { id: string; label: string };
  type MapItem = {
    id: number;
    title: string;
    revision: number;
    graph: { nodes: MapNode[] };
  };
  let {
    task,
    inMaps = [],
    onadded,
    placement = "bottom",
  }: {
    task: { id: string; title: string };
    inMaps?: MapRef[];
    onadded?: (map: MapRef) => void;
    placement?: "bottom" | "top" | "left" | "right";
  } = $props();
  let open = $state(false);
  let maps = $state<MapRef[] | null>(null);
  let mapId = $state(0);
  let map = $state<MapItem | null>(null);
  let parentId = $state("");
  let busy = $state(false);
  let error = $state("");
  const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
  const added = (id: number) => inMaps.some((m) => m.id === id);
  async function loadMaps() {
    error = "";
    try {
      maps = (await api<{ maps: MapRef[] }>("/schema")).maps;
      if (!maps.some((m) => m.id === mapId))
        mapId = maps.find((m) => !added(m.id))?.id ?? 0;
      await loadMap();
    } catch (e) {
      error = message(e);
    }
  }
  async function loadMap() {
    map = null;
    parentId = "";
    if (!mapId) return;
    const id = mapId;
    try {
      const item = await api<MapItem>(`/schema/map?id=${id}`);
      if (id === mapId) map = item;
    } catch (e) {
      error = message(e);
    }
  }
  $effect(() => {
    if (open) untrack(() => void loadMaps());
  });
  async function add() {
    if (!map || busy) return;
    busy = true;
    error = "";
    try {
      await api("/schema/add-node", {
        map_id: map.id,
        revision: map.revision,
        label: task.title,
        kind: "task",
        ...(parentId ? { parent_id: parentId } : {}),
        source: { kind: "calendar_task", id: task.id },
      });
      onadded?.({ id: map.id, title: map.title });
      open = false;
    } catch (e) {
      error = message(e);
      // Reload the revision so an explicit retry uses the current map.
      await loadMap();
    } finally {
      busy = false;
    }
  }
</script>

<span class="task-map">
  {#if inMaps.length}<Badge
      size="sm"
      intent="reference"
      label={`In map: ${inMaps.map((m) => m.title).join(", ")}`}
    />{/if}
  <Popover bind:open {placement}>
    {#snippet trigger(props)}
      <Button
        {...props}
        size="xs"
        variant="ghost"
        aria-label={`Add ${task.title} to a mind map`}>＋ Map</Button
      >
    {/snippet}
    <form
      class="map-form"
      onsubmit={(e) => {
        e.preventDefault();
        void add();
      }}
    >
      {#if maps && !maps.length}
        <p>No mind maps yet.</p>
        <Button size="sm" onclick={() => (location.hash = "#study")}
          >Open Study</Button
        >
      {:else if maps}
        <label
          >Map<select
            bind:value={mapId}
            onchange={() => {
              error = "";
              void loadMap();
            }}
            >{#each maps as m}<option value={m.id} disabled={added(m.id)}
                >{m.title}{added(m.id) ? " ✓" : ""}</option
              >{/each}</select
          ></label
        >
        {#if map?.graph.nodes.length}<label
            >Under<select bind:value={parentId}
              ><option value="">Auto</option>{#each map.graph.nodes as n}<option
                  value={n.id}>{n.label || n.id}</option
                >{/each}</select
            ></label
          >{/if}
        <Button
          type="submit"
          size="sm"
          intent="primary"
          disabled={!map || busy || added(mapId)}
          loading={busy}>Add</Button
        >
      {:else if !error}<p>Loading…</p>{/if}
      {#if error}<p class="error" role="alert">{error}</p>{/if}
    </form>
  </Popover>
</span>

<style>
  .task-map {
    display: inline-flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.35rem;
  }
  .map-form {
    display: grid;
    gap: 0.5rem;
    min-width: 14rem;
  }
  label {
    display: grid;
    gap: 0.2rem;
    font-size: 0.78rem;
    color: var(--muted);
  }
  select {
    font: inherit;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.35rem;
    padding: 0.35rem;
  }
  p {
    margin: 0;
    font-size: 0.8rem;
    color: var(--muted);
  }
  .error {
    color: var(--warning);
  }
</style>
