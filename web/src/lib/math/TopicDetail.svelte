<script lang="ts">
  import { Button } from "@nunera/bracket";
  import { isDue, reason, strandLabel, type Topic } from "./graph";

  /** The selected topic: why it is open or locked, its review, and what it needs and unlocks. */
  let { topic, topics, onlesson, onreview }: { topic: Topic; topics: Topic[]; onlesson: (id: string) => void; onreview: (id: string) => void } = $props();
  const byId = $derived(Object.fromEntries(topics.map((item) => [item.id, item])));
  const unlocks = $derived(topics.filter((item) => item.prereqs.includes(topic.id)));
  const due = $derived(isDue(topic, Date.now()));
  const STATE = { locked: "Locked", ready: "Ready", learning: "Learning", mastered: "Mastered" };
  const strand = $derived(strandLabel(topic.strand));

  function when(at: string) {
    const days = Math.round((Date.parse(at) - Date.now()) / 86_400_000);
    return days <= 0 ? "due now" : days === 1 ? "in 1 day" : `in ${days} days`;
  }
</script>

<section class="detail" aria-label={`${topic.title} details`}>
  <span class="eyebrow">{strand}</span>
  <div class="title">
    <h4>{topic.title}</h4>
    <span class={`state s-${topic.state}`}>{STATE[topic.state]}</span>
  </div>
  <p class="why" data-why>{reason(topic, byId)}</p>
  {#if topic.state === "learning"}
    <div class="bar" role="img" aria-label={`Learning ${Math.round((topic.progress ?? 0) * 100)}%`}><span style:width={`${(topic.progress ?? 0) * 100}%`}></span></div>
  {:else if topic.state === "mastered" && topic.due_at}
    <p class="meta" class:warn={due}>Review {when(topic.due_at)} {#if topic.stability_days}· remembered for about {Math.round(topic.stability_days)} d{/if}</p>
  {/if}
  {#if topic.prereqs.length}
    <h5>Needs</h5>
    <ul>
      {#each topic.prereqs as id (id)}<li class:done={byId[id]?.state === "mastered"}><span class="mark">{byId[id]?.state === "mastered" ? "✓" : "○"}</span>{byId[id]?.title ?? id}</li>{/each}
    </ul>
  {/if}
  {#if unlocks.length}
    <h5>Unlocks</h5>
    <ul>
      {#each unlocks as item (item.id)}<li class:done={item.state !== "locked"}><span class="mark">{item.state === "locked" ? "○" : "→"}</span>{item.title}</li>{/each}
    </ul>
  {/if}
  <div class="act">
    {#if topic.state === "locked"}
      <Button size="sm" disabled>Locked</Button>
    {:else if topic.state === "mastered"}
      <Button size="sm" variant="solid" intent="primary" onclick={() => onreview(topic.id)}>Review {topic.title}</Button>
      <Button size="sm" onclick={() => onlesson(topic.id)}>Lesson</Button>
    {:else}
      <Button size="sm" variant="solid" intent="primary" onclick={() => onlesson(topic.id)}>{topic.state === "learning" ? "Continue" : "Start"} the lesson</Button>
    {/if}
  </div>
</section>

<style>
  /* Stays at the drawer's foot while the graph above it scrolls. */
  .detail {
    position: sticky;
    bottom: -14px;
    display: grid;
    gap: 8px;
    margin: 0 -16px -14px;
    padding: 12px 16px 14px;
    border-top: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 97%, transparent);
  }
  .eyebrow {
    color: var(--subtle);
    font: 500 10px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .title {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  h4 {
    margin: 0;
    font-size: 15px;
  }
  h5 {
    margin: 4px 0 0;
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .state {
    padding: 2px 8px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
  }
  .s-mastered {
    border-color: var(--accent);
    color: var(--accent);
  }
  .s-learning,
  .s-ready {
    border-color: color-mix(in srgb, var(--accent) 50%, var(--hairline));
    color: var(--ink);
  }
  .why,
  .meta {
    margin: 0;
    color: var(--muted);
    font-size: 12.5px;
  }
  .meta.warn {
    color: var(--warning);
  }
  .bar {
    height: 6px;
    overflow: hidden;
    border-radius: 999px;
    background: color-mix(in srgb, var(--accent) 16%, transparent);
  }
  .bar span {
    display: block;
    height: 100%;
    background: var(--accent);
  }
  ul {
    display: grid;
    gap: 3px;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: 12.5px;
  }
  li {
    display: flex;
    gap: 6px;
    color: var(--muted);
  }
  li.done {
    color: var(--ink);
  }
  .mark {
    width: 12px;
    color: var(--accent);
  }
  .act {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 4px;
  }
</style>
