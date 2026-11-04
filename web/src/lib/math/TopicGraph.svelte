<script lang="ts">
  import { layoutGraph, NODE_R, type Topic, type TopicState } from "./graph";

  /**
   * The topic graph (design Q3 B): prerequisites above what they unlock, each topic
   * coloured by state; learning fills with progress, mastered carries a tick, and a
   * mastered topic whose review is due wears a pulsing ring. Topics in `outside` are
   * another strand's prerequisites, drawn faint for context.
   */
  let { topics, selected, outside = [], onselect }: { topics: Topic[]; selected: string; outside?: string[]; onselect: (id: string) => void } = $props();
  const STATE_LABEL: Record<TopicState, string> = { locked: "Locked", ready: "Ready", learning: "Learning", mastered: "Mastered" };
  const uid = $props.id();
  const graph = $derived(layoutGraph(topics));
  const R = NODE_R;

  /** Two short lines under a node. */
  function lines(title: string): string[] {
    if (title.length <= 14) return [title];
    const words = title.split(" ");
    let first = "";
    while (words.length && (first + " " + words[0]).trim().length <= 14) first = `${first} ${words.shift()}`.trim();
    if (!first) first = words.shift() ?? "";
    const rest = words.join(" ");
    return rest ? [first, rest.length > 16 ? `${rest.slice(0, 15)}…` : rest] : [first];
  }
</script>

<svg class="dag" viewBox={`0 0 ${graph.width} ${graph.height}`} role="group" aria-label="Topic graph" data-topics={graph.nodes.length}>
  <defs>
    <marker id={`${uid}-arrow`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path class="arrow" d="M0 0 L8 4 L0 8 z" />
    </marker>
    <marker id={`${uid}-arrow-live`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path class="arrow live" d="M0 0 L8 4 L0 8 z" />
    </marker>
    {#each graph.nodes as node (node.id)}
      {#if node.state === "learning"}
        {@const fill = Math.max(0.08, Math.min(1, node.progress ?? 0))}
        <clipPath id={`${uid}-fill-${node.id}`}><rect x={node.x - R} y={node.y + R - 2 * R * fill} width={2 * R} height={2 * R * fill} /></clipPath>
      {/if}
    {/each}
  </defs>
  {#each graph.edges as edge (`${edge.from}-${edge.to}`)}
    <path class="edge" class:live={edge.live} d={edge.d} marker-end={`url(#${uid}-arrow${edge.live ? "-live" : ""})`} />
  {/each}
  {#each graph.nodes as node (node.id)}
    <g class={`node ${node.state}`} class:sel={node.id === selected} class:due={node.due} class:outside={outside.includes(node.id)} role="button" tabindex="0"
      data-topic={node.id} data-state={node.state} data-outside={outside.includes(node.id) || undefined}
      aria-label={`${node.title}: ${STATE_LABEL[node.state]}${node.due ? ", review due" : ""}`} aria-pressed={node.id === selected}
      onclick={() => onselect(node.id)} onkeydown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onselect(node.id); } }}>
      <circle class="hit" cx={node.x} cy={node.y} r={R + 12} />
      {#if node.id === selected}<circle class="ring-sel" cx={node.x} cy={node.y} r={R + 6} />{/if}
      {#if node.due}
        <circle class="due-pulse" cx={node.x} cy={node.y} r={R + 4} />
        <circle class="due-ring" cx={node.x} cy={node.y} r={R + 4} />
      {/if}
      <circle class="disc" cx={node.x} cy={node.y} r={R} />
      {#if node.state === "learning"}<circle class="level" cx={node.x} cy={node.y} r={R} clip-path={`url(#${uid}-fill-${node.id})`} />{/if}
      {#if node.state === "mastered"}<path class="tick" d={`M${node.x - 5} ${node.y} l3.5 4 l7 -8`} />{/if}
      {#if node.state === "locked"}
        <rect class="lock" x={node.x - 4.5} y={node.y - 1} width="9" height="7" rx="1.5" />
        <path class="shackle" d={`M${node.x - 2.6} ${node.y - 1} v-2.6 a2.6 2.6 0 0 1 5.2 0 v2.6`} />
      {/if}
      {#each lines(node.title) as line, index (index)}
        <text class="label" x={node.x} y={node.y + R + 13 + index * 12}>{line}</text>
      {/each}
    </g>
  {/each}
</svg>
<ul class="legend" aria-label="Legend">
  <li><span class="key locked"></span>locked</li>
  <li><span class="key ready"></span>ready</li>
  <li><span class="key learning"></span>learning</li>
  <li><span class="key mastered"></span>mastered</li>
  <li><span class="key due"></span>review due</li>
</ul>

<style>
  .dag {
    display: block;
    width: 100%;
    height: auto;
    overflow: visible;
  }
  .edge {
    fill: none;
    stroke: var(--border);
    stroke-width: 1.4;
  }
  .edge.live {
    stroke: color-mix(in srgb, var(--accent) 65%, var(--border));
  }
  .arrow {
    fill: var(--border);
  }
  .arrow.live {
    fill: color-mix(in srgb, var(--accent) 65%, var(--border));
  }
  .node {
    cursor: pointer;
    outline: none;
  }
  .hit {
    fill: transparent;
  }
  .disc {
    stroke-width: 2;
  }
  .node.locked .disc {
    fill: color-mix(in srgb, var(--subtle) 22%, var(--solid-void, var(--surface)));
    stroke: var(--subtle);
    stroke-dasharray: 3 3;
  }
  .node.ready .disc,
  .node.learning .disc {
    fill: var(--solid-void, var(--surface));
    stroke: var(--accent);
  }
  .level {
    fill: var(--accent);
  }
  .node.mastered .disc {
    fill: var(--accent);
    stroke: var(--accent);
  }
  .tick {
    fill: none;
    stroke: var(--on-accent, #fff);
    stroke-width: 2.2;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .lock {
    fill: var(--subtle);
  }
  .shackle {
    fill: none;
    stroke: var(--subtle);
    stroke-width: 1.5;
  }
  .label {
    fill: var(--ink);
    font: 500 10.5px var(--font-ui);
    text-anchor: middle;
  }
  .node.locked .label {
    fill: var(--subtle);
  }
  .node.outside {
    opacity: 0.5;
  }
  .ring-sel {
    fill: none;
    stroke: var(--ink);
    stroke-width: 1.5;
  }
  .node:hover .disc,
  .node:focus-visible .disc {
    stroke-width: 3;
  }
  .node:focus-visible .ring-sel,
  .node:focus-visible .hit {
    stroke: var(--accent);
  }
  .due-ring {
    fill: none;
    stroke: var(--warning);
    stroke-width: 2;
  }
  .due-pulse {
    fill: none;
    stroke: var(--warning);
    stroke-width: 2;
    transform-box: fill-box;
    transform-origin: center;
    animation: pulse 1.6s ease-out infinite;
  }
  @keyframes pulse {
    from { transform: scale(1); opacity: 0.9; }
    to { transform: scale(1.5); opacity: 0; }
  }
  @media (prefers-reduced-motion: reduce) {
    .due-pulse {
      display: none;
    }
  }
  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 14px;
    margin: 0;
    padding: 0;
    color: var(--muted);
    font-size: 11.5px;
    list-style: none;
  }
  .legend li {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .key {
    width: 10px;
    height: 10px;
    box-sizing: border-box;
    border: 1.5px solid var(--accent);
    border-radius: 50%;
  }
  .key.locked {
    border: 1.5px dashed var(--subtle);
    background: color-mix(in srgb, var(--subtle) 22%, transparent);
  }
  .key.learning {
    background: linear-gradient(to top, var(--accent) 50%, transparent 50%);
  }
  .key.mastered {
    background: var(--accent);
  }
  .key.due {
    border: 2px solid var(--warning);
  }
</style>
