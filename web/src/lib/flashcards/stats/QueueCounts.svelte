<script lang="ts">
  import { Panel } from "@nunera/bracket";
  import type { MemoryStats } from "./types";

  /** Today's queue (Q41 G): the learning / review / new split, parked cards and a time estimate. */
  let { queue }: { queue: MemoryStats["queue"] } = $props();

  let parts = $derived([
    { key: "learning", label: "Learning", value: queue.learning, color: "var(--warning)", note: "steps due now" },
    { key: "review", label: "Review", value: queue.review, color: "var(--violet)", note: "scheduled by FSRS for today" },
    { key: "new", label: "New", value: queue.new, color: "var(--c4)", note: "today's new-card allowance" },
  ]);
  let total = $derived(queue.learning + queue.review + queue.new);
  let hover = $state<string | null>(null);
  let focus = $derived(parts.find((part) => part.key === hover));
  let readout = $derived(
    focus
      ? `${focus.value} ${focus.label.toLowerCase()} · ${focus.note}`
      : !total
        ? "nothing left today"
        : queue.seconds_per_card
          ? `≈ ${Math.max(1, Math.round((total * queue.seconds_per_card) / 60))} min at your pace`
          : "no timing yet",
  );
</script>

<Panel title="Today's queue" {readout} aria-label="Today's queue" class="memory-stat">
  <div class="big"><strong>{total}</strong> <span>cards today</span></div>
  <div class="bar" role="img" aria-label={`${total} cards: ${parts.map((part) => `${part.value} ${part.label}`).join(", ")}`}>
    {#each parts.filter((part) => part.value) as part}
      <span class:off={hover && hover !== part.key} style:flex={part.value} style:background={part.color} role="presentation" onpointerenter={() => (hover = part.key)} onpointerleave={() => (hover = null)}></span>
    {:else}
      <span class="empty"></span>
    {/each}
  </div>
  <ul class="counts">
    {#each parts as part}
      <li class:on={hover === part.key} onpointerenter={() => (hover = part.key)} onpointerleave={() => (hover = null)}>
        <i style:background={part.color}></i><b>{part.value}</b><small>{part.label}</small>
      </li>
    {/each}
  </ul>
  <p class="parked">
    <span>{queue.suspended} suspended</span>
    <span>{queue.leeches} {queue.leeches === 1 ? "leech" : "leeches"}</span>
    <span>{queue.buried} buried until tomorrow</span>
  </p>
</Panel>

<style>
  .big {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .big strong {
    font: 700 34px/1 var(--font-ui);
  }
  .big span,
  .counts small {
    color: var(--muted);
    font: 10px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .bar {
    display: flex;
    gap: 3px;
    height: 12px;
    margin: 12px 0;
  }
  .bar span {
    border-radius: 999px;
    transition: opacity 0.15s ease;
  }
  .bar .off {
    opacity: 0.3;
  }
  .bar .empty {
    flex: 1;
    background: color-mix(in srgb, var(--ink) 8%, transparent);
  }
  .counts {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 6px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .counts li {
    display: grid;
    grid-template-columns: auto 1fr;
    align-items: center;
    column-gap: 6px;
    padding: 6px 8px;
    border: 1px solid var(--hairline);
    border-radius: 10px;
  }
  .counts li.on {
    background: color-mix(in srgb, var(--ink) 5%, transparent);
  }
  .counts i {
    grid-row: span 2;
    width: 4px;
    height: 26px;
    border-radius: 2px;
  }
  .counts b {
    font: 600 16px var(--font-mono);
  }
  .parked {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 12px 0 0;
  }
  .parked span {
    padding: 2px 9px;
    border: 1px dashed var(--hairline);
    border-radius: 999px;
    color: var(--muted);
    font: 10px var(--font-mono);
  }
  @media (prefers-reduced-motion: reduce) {
    .bar span {
      transition: none;
    }
  }
</style>
