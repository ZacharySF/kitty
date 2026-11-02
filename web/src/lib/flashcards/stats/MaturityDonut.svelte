<script lang="ts">
  import { Panel } from "@nunera/bracket";
  import type { MemoryStats } from "./types";

  /** Maturity donut (Q41 C): new / learning / young (< 21 d) / mature (≥ 21 d) cards. */
  let { maturity }: { maturity: MemoryStats["maturity"] } = $props();

  let parts = $derived([
    { key: "new", label: "New", value: maturity.new, color: "var(--c4)", note: "never studied" },
    { key: "learning", label: "Learning", value: maturity.learning, color: "var(--warning)", note: "in learning steps" },
    { key: "young", label: "Young", value: maturity.young, color: "color-mix(in srgb, var(--violet) 60%, transparent)", note: "interval under 21 days" },
    { key: "mature", label: "Mature", value: maturity.mature, color: "var(--violet)", note: "interval 21 days or more" },
  ]);
  let total = $derived(parts.reduce((sum, part) => sum + part.value, 0));
  const R = 42;
  const C = 2 * Math.PI * R;
  let arcs = $derived.by(() => {
    let offset = 0;
    return parts.filter((part) => part.value).map((part) => {
      const length = (part.value / total) * C;
      const arc = { ...part, dash: `${Math.max(0.5, length - 2)} ${C}`, offset: -offset };
      offset += length;
      return arc;
    });
  });
  let hover = $state<string | null>(null);
  let focus = $derived(parts.find((part) => part.key === hover));
  const share = (value: number) => (total ? `${Math.round((value / total) * 100)}%` : "0%");
</script>

<Panel title="Maturity" readout={focus ? `${focus.label.toLowerCase()} · ${focus.note}` : `${total} cards`} aria-label="Maturity" class="memory-stat">
  <div class="row">
    <svg viewBox="0 0 110 110" role="img" aria-label={`Cards by maturity: ${parts.map((part) => `${part.value} ${part.label.toLowerCase()}`).join(", ")}`}>
      <circle class="track" cx="55" cy="55" r={R} />
      {#each arcs as arc}
        <circle
          class="arc"
          class:on={hover === arc.key}
          class:off={hover && hover !== arc.key}
          cx="55"
          cy="55"
          r={R}
          style:stroke={arc.color}
          stroke-dasharray={arc.dash}
          stroke-dashoffset={arc.offset}
          role="presentation"
          onpointerenter={() => (hover = arc.key)}
          onpointerleave={() => (hover = null)}
        />
      {/each}
      <text class="big" x="55" y="57">{share(focus ? focus.value : maturity.mature)}</text>
      <text class="small" x="55" y="69">{focus ? focus.label : "mature"}</text>
    </svg>
    <ul>
      {#each parts as part}
        <li class:on={hover === part.key} onpointerenter={() => (hover = part.key)} onpointerleave={() => (hover = null)}>
          <i style:background={part.color}></i><span>{part.label}</span><b>{part.value}</b>
        </li>
      {/each}
    </ul>
  </div>
</Panel>

<style>
  .row {
    display: grid;
    grid-template-columns: 140px 1fr;
    gap: 16px;
    align-items: center;
  }
  svg {
    width: 140px;
    height: 140px;
  }
  circle {
    fill: none;
    stroke-width: 13;
  }
  .track {
    stroke: color-mix(in srgb, var(--ink) 6%, transparent);
  }
  .arc {
    transform: rotate(-90deg);
    transform-origin: 55px 55px;
    transition: stroke-width 0.15s ease, opacity 0.15s ease;
  }
  .arc.on {
    stroke-width: 17;
  }
  .arc.off {
    opacity: 0.35;
  }
  .big {
    fill: var(--ink);
    font: 700 19px var(--font-ui);
    text-anchor: middle;
  }
  .small {
    fill: var(--subtle);
    font: 7px var(--font-mono);
    letter-spacing: 0.12em;
    text-anchor: middle;
    text-transform: uppercase;
  }
  ul {
    display: grid;
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: grid;
    grid-template-columns: 10px 1fr auto;
    gap: 8px;
    align-items: center;
    padding: 5px 8px;
    border-radius: 8px;
    font-size: 13px;
  }
  li.on {
    background: color-mix(in srgb, var(--ink) 6%, transparent);
  }
  li i {
    width: 10px;
    height: 10px;
    border-radius: 3px;
  }
  li b {
    font: 500 12px var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  @media (prefers-reduced-motion: reduce) {
    .arc {
      transition: none;
    }
  }
</style>
