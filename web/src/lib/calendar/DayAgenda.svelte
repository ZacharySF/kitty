<script module lang="ts">
  /** One agenda row: an event (timed or all-day) or a dated task. */
  export type AgendaItem = {
    key: string;
    start: string;
    end: string;
    title: string;
    place: string;
    color: string;
    task?: boolean;
    open: () => void;
    drag?: (event: DragEvent) => void;
    dragend?: () => void;
  };
</script>

<script lang="ts">
  let { eyebrow, items }: { eyebrow: string; items: AgendaItem[] } = $props();
  const events = $derived(items.filter((item) => !item.task).length);
</script>

<aside class="day-agenda" aria-label="Day agenda" aria-live="polite">
  <span class="eyebrow">{eyebrow}</span>
  <h3>{events === 1 ? "1 event" : `${events} events`}</h3>
  {#each items as item (item.key)}
    <button class="item" style:--cal={item.color} draggable={!!item.drag}
      ondragstart={item.drag} ondragend={item.dragend} onclick={item.open}>
      <span class="time">{item.start}{#if item.end}<br /><small>{item.end}</small>{/if}</span>
      <span class="what"><strong>{item.task ? "☐ " : ""}{item.title}</strong>{#if item.place}<small>{item.place}</small>{/if}</span>
    </button>
  {:else}
    <p class="empty">Free day. Nothing booked.</p>
  {/each}
</aside>

<style>
  .day-agenda {
    display: grid;
    align-content: start;
    gap: 6px;
    min-width: 0;
    padding: 12px 14px;
    border-left: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--solid-panel) 50%, transparent);
  }
  .eyebrow {
    color: var(--violet);
  }
  h3 {
    margin: 0 0 4px;
    font: 700 1.1rem var(--font-ui);
  }
  .item {
    display: grid;
    grid-template-columns: 3.2rem minmax(0, 1fr);
    gap: 8px;
    width: 100%;
    padding: 5px 0 5px 8px;
    border: 0;
    border-left: 2px solid var(--cal);
    border-radius: 0;
    background: transparent;
    color: var(--ink);
    text-align: left;
    cursor: pointer;
  }
  .item:hover,
  .item:focus-visible {
    background: color-mix(in srgb, var(--cal) 12%, transparent);
  }
  .time {
    font: 500 0.7rem/1.35 var(--font-mono);
  }
  .time small {
    color: var(--subtle);
  }
  .what {
    display: grid;
    min-width: 0;
  }
  .what strong,
  .what small {
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  .what strong {
    font-size: 0.8rem;
    font-weight: 600;
  }
  .what small {
    color: var(--muted);
    font-size: 0.72rem;
  }
  .empty {
    margin: 0;
    color: var(--muted);
    font-size: 0.8rem;
  }
</style>
