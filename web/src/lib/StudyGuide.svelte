<script lang="ts">
  import OverflowMenu from "./ui/OverflowMenu.svelte";
  type GuideState = { checked: boolean[]; responses: string[]; reflection: string; next_review: string };
  /**
   * The 14-step guide. `phase` mode (the session drawer) shows only the current
   * phase's steps with arrows to browse the others and a Full guide link; `full`
   * mode (its own view) shows every step as phase columns or a rail, the step
   * notes and the return review.
   */
  let { value = $bindable(), mode = "phase", phase = "priming", active = false, busy = false, onchange, onnew, onfull }: {
    value: GuideState; mode?: "phase" | "full"; phase?: string; active?: boolean; busy?: boolean;
    onchange: () => void; onnew?: () => void; onfull?: () => void;
  } = $props();
  // The user's image supplies the decision flow; the requested routine names
  // its final practice actions explicitly as spacing and interleaving.
  const steps = [
    { label: "Measure an outcome, not just grades", hint: "Name what you will be able to do, explain or solve. Grades, speed and time spent can track progress, but they are not the learning outcome.", prompt: "The outcome I will demonstrate…" },
    { label: "Do you have a clear purpose + plan?", hint: "If not: get one. Choose why this matters and how you will approach it.", prompt: "Purpose and plan…" },
    { label: "Do you have a schedule?", hint: "If not: make one. Reserve preparation, focused work, breaks and a later return.", prompt: "When I will study and return…" },
    { label: "Did you plan for priming?", hint: "Plan for it. These first five minutes are for a broad preview; priming never counts as focus time.", prompt: "Headings, prior knowledge and questions to preview…" },
    { label: "What are you focusing on in those sessions?", hint: "Gist, not details. Set a session goal around the main idea and its relationships.", prompt: "The gist / session goal…" },
    { label: "Environment", hint: "Focused, not distractible. Set it up beforehand: clear distractions and have the source material ready.", prompt: "My distraction-free setup…" },
    { label: "Priority", hint: "Connecting, rather than only remember / understand. Relate new ideas to what you already know; use cards to test those connections.", prompt: "Connections I am trying to make…" },
    { label: "Note-taking", hint: "Relational rather than only linear. Draw or write how ideas depend on, contrast with or explain each other.", prompt: "Relationships and evidence…" },
    { label: "Order", hint: "Individualised rather than default. Follow your gaps and prerequisites, not just the page order.", prompt: "The order that fits my gaps…" },
    { label: "Explore confusion", hint: "Identify a precise gap, try an example and ask a focused question. Avoid over-explaining until the gist is lost or under-explaining until the important relationship disappears; do not escape by oversimplifying or stop questioning.", prompt: "What is confusing, and what example or question will I explore?" },
    { label: "Challenge", hint: "How do you test your knowledge? Match style, level and cue to the task you need to perform; attempt the answer before revealing it.", prompt: "Test style, challenge level and available cues…" },
    { label: "Feedback before, during and after", hint: "Before: define success criteria and make a diagnostic attempt, without looking at the answer. During: use focused feedback to correct your approach. After: compare your completed answer with the source and explain the correction.", prompt: "Before: criteria / diagnostic. During: adjustment. After: correction…" },
    { label: "Spacing", hint: "Return after a deliberate gap, when retrieval takes effort but remains possible. Adjust the next return from what you can retrieve, not from familiarity alone; use the spaced-review date below or scheduled flashcard reviews.", prompt: "What I could retrieve and when I will return…" },
    { label: "Interleaving", hint: "Mix related topics or problem types so you must decide which approach applies, rather than repeat one familiar procedure. Keep the challenge and conditions relevant to the real task.", prompt: "Which related topics will I alternate, and how will I choose an approach?" },
  ];
  const groups = [
    { label: "Prepare & prime", start: 0, end: 6 },
    { label: "During the session", start: 6, end: 10 },
    { label: "Test, review & return", start: 10, end: 14 },
  ];
  const stage = $derived(!active || phase === "priming" ? 0 : phase === "focus" ? 1 : 2);
  const done = $derived(value.checked.filter(Boolean).length);
  const range = (start: number, end: number) => Array.from({ length: end - start }, (_, offset) => start + offset);
  /** The phase the drawer shows: the timer's, until the arrows browse another. */
  let browsed = $state<number | null>(null);
  const shown = $derived(browsed ?? stage);
  $effect.pre(() => {
    stage;
    browsed = null;
  });

  const VIEW_KEY = "kitty.study.guide-view";
  let view = $state<"columns" | "rail">("columns");
  try { if (localStorage.getItem(VIEW_KEY) === "rail") view = "rail"; } catch { /* Columns by default. */ }
  function setView(next: "columns" | "rail") {
    view = next;
    try { localStorage.setItem(VIEW_KEY, next); } catch { /* The choice still applies in this session. */ }
  }
  /** Step cards whose note field is open. */
  let notes = $state<Set<number>>(new Set());
  const toggle = (set: Set<number>, index: number) => { const next = new Set(set); if (!next.delete(index)) next.add(index); return next; };
  /** In the rail, the current phase is open; others open on click. */
  let railOpen = $state<number | null>(null);
  const railShown = $derived(railOpen ?? stage);
</script>

{#snippet step(index: number, withNotes: boolean)}
  {@const item = steps[index]}
  <li class="step" class:done={value.checked[index]}>
    <div class="step-head">
      <input type="checkbox" id={`guide-${mode}-step-${index}`} bind:checked={value.checked[index]} onchange={onchange} />
      <label for={`guide-${mode}-step-${index}`} title={item.hint}><span class="number">{index + 1}</span>{item.label}</label>
      {#if withNotes}<button type="button" class="mini" aria-label={`Note for step ${index + 1}`} title="Note" aria-pressed={notes.has(index) || !!value.responses[index]} onclick={() => (notes = toggle(notes, index))}>✎</button>{/if}
    </div>
    {#if withNotes && (notes.has(index) || value.responses[index])}
      <label class="note"><span class="sr-only">Step {index + 1}: {item.label}</span><textarea rows="2" maxlength="4000" bind:value={value.responses[index]} oninput={onchange} placeholder={item.prompt}></textarea></label>
    {/if}
  </li>
{/snippet}

{#if mode === "phase"}
  {@const group = groups[shown]}
  <section class="current-phase" aria-label="Current phase">
    <div class="head">
      <span class="phase-dot" class:on={shown === stage} aria-hidden="true"></span>
      <span class="eyebrow">{group.label} · {done}/14</span>
      <span class="arrows">
        <button type="button" class="mini" aria-label="Previous phase" disabled={shown === 0} onclick={() => (browsed = shown - 1)}>‹</button>
        <button type="button" class="mini" aria-label="Next phase" disabled={shown === groups.length - 1} onclick={() => (browsed = shown + 1)}>›</button>
      </span>
    </div>
    <ol>{#each range(group.start, group.end) as index (index)}{@render step(index, false)}{/each}</ol>
    <button type="button" class="more" onclick={onfull}>Full guide ›</button>
  </section>
{:else}
  <section class="study-guide" aria-label="Fourteen-step study guide">
    <div class="guide-heading">
      <h3>Study guide</h3>
      <span class="count">{done}/14</span>
      <span class="grow"></span>
      <div class="view-switch" role="radiogroup" aria-label="Guide layout">
        <button type="button" role="radio" aria-checked={view === "columns"} onclick={() => setView("columns")}>Columns</button>
        <button type="button" role="radio" aria-checked={view === "rail"} onclick={() => setView("rail")}>Rail</button>
      </div>
      {#if !active && onnew}<OverflowMenu label="Guide actions" items={[{ label: "New preparation", disabled: busy, onselect: onnew }]} />{/if}
    </div>
    {#if view === "columns"}
      <div class="columns" data-guide-view="columns">
        {#each groups as group, groupIndex}
          <section class:current={stage === groupIndex} aria-label={group.label}>
            <h4>{group.label}</h4>
            <span class="count">{value.checked.slice(group.start, group.end).filter(Boolean).length}/{group.end - group.start}</span>
            <ol>{#each range(group.start, group.end) as index (index)}{@render step(index, true)}{/each}</ol>
          </section>
        {/each}
      </div>
    {:else}
      <ol class="rail" data-guide-view="rail">
        {#each groups as group, groupIndex}
          <li class:current={stage === groupIndex} class:open={railShown === groupIndex}>
            <button type="button" class="rail-head" aria-expanded={railShown === groupIndex} onclick={() => (railOpen = railShown === groupIndex ? -1 : groupIndex)}>
              <span class="dot" aria-hidden="true"></span>{group.label}
              <span class="count">{value.checked.slice(group.start, group.end).filter(Boolean).length}/{group.end - group.start}</span>
            </button>
            {#if railShown === groupIndex}
              <ol>{#each range(group.start, group.end) as index (index)}{@render step(index, true)}{/each}</ol>
            {/if}
          </li>
        {/each}
      </ol>
    {/if}
    <hr class="rule" />
    <section class="review" aria-label="Return review">
      <label class="reflection"><span class="eyebrow">What changed in your understanding?</span><textarea rows="3" maxlength="10000" bind:value={value.reflection} oninput={onchange}></textarea></label>
      <label class="next"><span class="eyebrow">Next spaced review</span><input type="date" bind:value={value.next_review} onchange={onchange} /></label>
    </section>
  </section>
{/if}

<style>
  .study-guide { container-type: inline-size; display: grid; gap: .85rem; color: var(--ink); }
  .guide-heading { display: flex; align-items: center; flex-wrap: wrap; gap: .6rem; }
  .grow { flex: 1; }
  h3, h4 { margin: 0; }
  label { display: grid; gap: .4rem; }
  input, textarea { box-sizing: border-box; max-width: 100%; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-raised); color: var(--ink); padding: .6rem; font: inherit; }
  textarea { width: 100%; resize: vertical; }
  .count { font-size: .8rem; color: var(--muted); font-variant-numeric: tabular-nums; }
  .view-switch { display: inline-flex; gap: 2px; padding: 2px; border: 1px solid var(--hairline, var(--border)); border-radius: 999px; }
  .view-switch button { padding: 3px 10px; border: 0; border-radius: 999px; background: transparent; color: var(--muted); font-size: 12px; }
  .view-switch button[aria-checked="true"] { background: color-mix(in srgb, var(--ink) 12%, transparent); color: var(--ink); }
  .columns { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .columns > section { display: grid; align-content: start; grid-template-columns: 1fr auto; gap: .5rem .4rem; padding: 2px 16px 0; min-width: 0; }
  .columns > section + section { border-left: 1px solid var(--hairline, var(--border)); }
  .columns > section.current h4 { color: var(--accent); }
  .columns ol, .rail ol, .current-phase ol { grid-column: 1 / -1; display: grid; gap: 1px; margin: 0; padding: 0; list-style: none; }
  .step { padding: 3px 6px; border-radius: 8px; }
  .step:hover { background: color-mix(in srgb, var(--ink) 5%, transparent); }
  .step.done .step-head label { color: var(--muted); text-decoration: line-through; text-decoration-color: color-mix(in srgb, var(--muted) 60%, transparent); }
  .step-head { display: flex; align-items: center; gap: .5rem; min-height: 24px; }
  .step-head input { width: auto; margin: 0; padding: 0; accent-color: var(--accent); }
  .step-head label { flex: 1; display: flex; gap: .5rem; align-items: baseline; margin: 0; font-size: 12.5px; line-height: 1.35; cursor: pointer; }
  .number { width: 14px; flex: none; color: var(--subtle, var(--muted)); font: 500 10px var(--font-mono); text-align: right; }
  .mini { flex: none; width: 22px; height: 22px; padding: 0; border: 1px solid transparent; border-radius: 999px; background: transparent; color: var(--muted); font-size: 13px; }
  .mini:hover:not(:disabled), .mini[aria-pressed="true"] { border-color: var(--hairline, var(--border)); color: var(--ink); }
  .mini:disabled { opacity: .35; }
  .note { margin: .3rem 0 .2rem; }
  .current-phase { display: grid; gap: 4px; }
  .head { display: flex; align-items: center; gap: 8px; height: 24px; }
  .arrows { display: flex; margin-left: auto; }
  .phase-dot { width: 7px; height: 7px; border-radius: 999px; background: var(--subtle, var(--muted)); }
  .phase-dot.on { background: var(--accent); }
  .more { justify-self: start; padding: 0 6px; border: 0; background: transparent; color: var(--muted); font: 500 12px/24px var(--font-ui); cursor: pointer; }
  .more:hover { color: var(--ink); }
  .review { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 12px; align-items: start; }
  .rail { display: grid; gap: .4rem; margin: 0; padding: 0 0 0 .4rem; list-style: none; border-left: 2px solid var(--hairline, var(--border)); }
  .rail > li { display: grid; gap: .45rem; padding-left: .8rem; }
  .rail-head { display: flex; align-items: center; gap: .55rem; width: 100%; padding: .35rem 0; border: 0; background: transparent; color: var(--ink); font-weight: 600; text-align: left; }
  .rail-head .count { margin-left: auto; }
  .dot { width: 10px; height: 10px; margin-left: -1.32rem; border: 2px solid var(--hairline, var(--border)); border-radius: 999px; background: var(--surface); }
  .rail > li.current .dot { border-color: var(--accent); background: var(--accent); }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
  @container (max-width: 640px) {
    .columns { grid-template-columns: 1fr; }
    .columns > section + section { border-left: 0; border-top: 1px solid var(--hairline, var(--border)); padding-top: 10px; }
    .review { grid-template-columns: 1fr; }
  }
</style>
