<script lang="ts">
  import { approveLabel, planGroups, talkClock, type PlanArea, type RikaPlan } from "./rika";
  import { rika } from "./session.svelte";

  /**
   * Q7 A, Q8 A: a brain dump's one plan, grouped by area. Every item starts ticked; untick
   * what was misheard (or a whole area), answer the plan's one question if it has one, then
   * approve all of it or only the ticked items. The items run through Rika's ordinary
   * tools, so in-app changes come with Undo; approving the plan is the approval for its
   * outward items (starting a worker). Once it has run each item shows what it made, and
   * the dump note keeps the transcript and links to all of it.
   */
  let { plan }: { plan: RikaPlan } = $props();
  const LABELS: Record<PlanArea, string> = { notes: "Notes", tasks: "Tasks", calendar: "Calendar", cards: "Cards", jobs: "Jobs", agents: "Agents" };
  const STATUS = { pending: "Needs you", running: "Running", applied: "Done", dismissed: "Dismissed", failed: "Failed" } as const;

  // svelte-ignore state_referenced_locally
  let ticked = $state<Record<string, boolean>>(Object.fromEntries(plan.items.map((item) => [item.id, item.picked ?? true])));
  // svelte-ignore state_referenced_locally
  let choices = $state<Record<string, number>>(Object.fromEntries(plan.items.filter((item) => item.question).map((item) => [item.id, item.choice ?? 0])));
  let busy = $state(false);
  const groups = $derived(planGroups(plan.items));
  const picked = $derived(plan.items.filter((item) => ticked[item.id]).length);
  const pending = $derived(plan.status === "pending");

  function area(area: PlanArea) {
    const items = plan.items.filter((item) => item.area === area);
    const on = items.filter((item) => ticked[item.id]).length;
    return { all: on === items.length, some: on > 0 && on < items.length };
  }
  function toggleArea(name: PlanArea) {
    const next = !area(name).all;
    for (const item of plan.items) if (item.area === name) ticked[item.id] = next;
  }
  async function decide(decision: "approve" | "dismiss") {
    busy = true;
    await rika.decidePlan(plan.id, decision, plan.items.filter((item) => ticked[item.id]).map((item) => item.id), choices);
    busy = false;
  }
  /** A link in the dump note: `[[Page]]`, or `[label](#view)`. */
  function linkText(link: string) {
    const page = link.match(/^\[\[(.+)\]\]$/);
    if (page) return { label: page[1], view: "notes" };
    const other = link.match(/^\[(.+)\]\(#([\w-]+)\)$/);
    return other ? { label: other[1], view: other[2] } : { label: link, view: "" };
  }
</script>

<article class="plan" data-status={plan.status} aria-label="Brain dump plan">
  <header>
    <div>
      <span class="kind">Brain dump · {talkClock(plan.duration)} of talk</span>
      <h3>One plan · {plan.items.length} item{plan.items.length === 1 ? "" : "s"}</h3>
    </div>
    <span class="state">{STATUS[plan.status]}</span>
  </header>
  {#each groups as group (group.area)}
    {@const state = area(group.area)}
    <section class="area" data-area={group.area} aria-label={LABELS[group.area]}>
      <label class="area-head">
        {#if pending}<input type="checkbox" checked={state.all} indeterminate={state.some} onchange={() => toggleArea(group.area)} aria-label={`All of ${LABELS[group.area]}`} />{/if}
        <i class="dot" aria-hidden="true"></i>{LABELS[group.area]}<span class="count">{group.items.length}</span>
      </label>
      {#each group.items as item (item.id)}
        <div class="item" class:off={pending ? !ticked[item.id] : item.result?.status === "skipped"} data-result={item.result?.status}>
          {#if pending}<input type="checkbox" bind:checked={ticked[item.id]} aria-label={item.title} />{:else}<i class="mark" aria-hidden="true"></i>{/if}
          <div class="body">
            <b>{item.title}</b>
            {#if item.detail || item.count > 1}<small>{item.detail}{item.detail && item.count > 1 ? " · " : ""}{item.count > 1 ? `${item.count} changes` : ""}</small>{/if}
            {#if item.outward && pending}<small class="outward">Approving starts it</small>{/if}
            {#if item.question}
              <div class="ask" role="group" aria-label={`Rika asks: ${item.question.text}`}>
                <span>{item.question.text}</span>
                {#each item.question.choices as choice, index (index)}
                  <button class="chip" aria-pressed={choices[item.id] === index} disabled={!pending} onclick={() => (choices[item.id] = index)}>{choice}</button>
                {/each}
              </div>
            {/if}
            {#if item.source}<q class="said">{item.source}</q>{/if}
            {#if item.result && item.result.status !== "skipped"}
              <p class="result" data-status={item.result.status}>
                {#if item.result.status === "failed"}{item.result.summary}{:else}
                  <!-- A page and the block added to it link to the same page once. -->
                  {#each [...new Set(item.result.links)] as link, index (index)}
                    {@const target = linkText(link)}
                    {index ? " · " : ""}{#if target.view}<button class="link" onclick={() => rika.navigate(target.view)}>{target.label}</button>{:else}{target.label}{/if}
                  {/each}
                {/if}
              </p>
            {/if}
          </div>
        </div>
      {/each}
    </section>
  {/each}
  {#if plan.note}
    <p class="note">Dump note: <button class="link" onclick={() => rika.navigate("notes")}>{plan.note.title}</button></p>
  {/if}
  {#if pending}
    <footer>
      <button class="pill" disabled={busy} onclick={() => decide("dismiss")}>Dismiss</button>
      <button class="pill primary" disabled={busy || !picked} onclick={() => decide("approve")}>{approveLabel(picked, plan.items.length)}</button>
    </footer>
  {/if}
</article>

<style>
  .plan {
    --tone: var(--warning);
    display: grid;
    gap: 8px;
    margin-left: 21px;
    padding: 10px 12px;
    border: 1px solid color-mix(in srgb, var(--tone) 45%, var(--hairline));
    border-left: 2px solid var(--tone);
    border-radius: 10px;
    background: color-mix(in srgb, var(--solid-raised) 55%, transparent);
  }
  .plan[data-status="applied"] {
    --tone: var(--success);
  }
  .plan[data-status="dismissed"],
  .plan[data-status="running"] {
    --tone: var(--subtle);
  }
  .plan[data-status="failed"] {
    --tone: var(--danger);
  }
  header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
  }
  h3 {
    margin: 2px 0 0;
    font-size: 14px;
  }
  .kind,
  .area-head {
    color: var(--subtle);
    font: 10px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }
  .state {
    color: var(--tone);
    font: 500 9.5px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .area {
    display: grid;
    gap: 2px;
  }
  .area-head {
    display: flex;
    align-items: center;
    gap: 7px;
    padding: 4px 0 2px;
    color: var(--ink);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 999px;
    background: var(--area);
  }
  .area[data-area="notes"] { --area: var(--c1, var(--accent)); }
  .area[data-area="tasks"] { --area: var(--c2, var(--accent)); }
  .area[data-area="calendar"] { --area: var(--c3, var(--accent)); }
  .area[data-area="cards"] { --area: var(--c4, var(--accent)); }
  .area[data-area="jobs"] { --area: var(--c5, var(--accent)); }
  .area[data-area="agents"] { --area: var(--warning); }
  .count {
    padding: 0 5px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 12%, transparent);
    letter-spacing: 0;
  }
  .item {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 8px;
    padding: 4px 0 4px 2px;
  }
  .item.off {
    opacity: 0.45;
  }
  .item input {
    margin: 2px 0 0;
  }
  .mark {
    width: 8px;
    height: 8px;
    margin-top: 4px;
    border-radius: 999px;
    background: var(--subtle);
  }
  .item[data-result="done"] .mark { background: var(--success); }
  .item[data-result="failed"] .mark { background: var(--danger); }
  .body {
    display: grid;
    gap: 2px;
    min-width: 0;
  }
  b {
    font-size: 12.5px;
    font-weight: 600;
  }
  small {
    color: var(--muted);
    font-size: 11.5px;
  }
  .outward {
    color: var(--warning);
  }
  .said {
    color: var(--subtle);
    font-size: 11.5px;
    font-style: italic;
    overflow-wrap: anywhere;
  }
  .ask {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 5px;
    margin: 2px 0;
    font-size: 11.5px;
  }
  .chip {
    padding: 2px 9px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: inherit;
    cursor: pointer;
  }
  .chip[aria-pressed="true"] {
    border-color: var(--accent);
    color: var(--ink);
  }
  .result,
  .note {
    margin: 0;
    color: var(--muted);
    font-size: 11.5px;
    overflow-wrap: anywhere;
  }
  .result[data-status="failed"] {
    color: var(--danger);
  }
  .link {
    padding: 0;
    border: 0;
    background: none;
    color: var(--accent);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  footer {
    display: flex;
    justify-content: end;
    gap: 6px;
  }
  footer .pill {
    min-height: 26px;
    padding: 3px 12px;
    font-size: 10.5px;
  }
</style>
