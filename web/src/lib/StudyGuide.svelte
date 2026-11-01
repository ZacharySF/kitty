<script lang="ts">
  import { Button } from "@nunera/bracket";
  type GuideState = { checked: boolean[]; responses: string[]; reflection: string; next_review: string };
  let { value = $bindable(), phase = "priming", active = false, dirty = false, busy = false, onchange, onsave, onnew }: {
    value: GuideState; phase?: string; active?: boolean; dirty?: boolean; busy?: boolean;
    onchange: () => void; onsave: () => void; onnew: () => void;
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
  const VIEW_KEY = "kitty.study.guide-view";
  let view = $state<"columns" | "rail">("columns");
  try { if (localStorage.getItem(VIEW_KEY) === "rail") view = "rail"; } catch { /* Columns by default. */ }
  function setView(next: "columns" | "rail") {
    view = next;
    try { localStorage.setItem(VIEW_KEY, next); } catch { /* The choice still applies in this session. */ }
  }
  /** Step cards whose hint or note field is open. */
  let hints = $state<Set<number>>(new Set());
  let notes = $state<Set<number>>(new Set());
  const toggle = (set: Set<number>, index: number) => { const next = new Set(set); if (!next.delete(index)) next.add(index); return next; };
  /** In the rail, the current phase is open; others open on click. */
  let railOpen = $state<number | null>(null);
  const railShown = $derived(railOpen ?? stage);
</script>

{#snippet step(index: number, compact: boolean)}
  {@const item = steps[index]}
  <li class="step" class:done={value.checked[index]}>
    <div class="step-head">
      <input type="checkbox" id={`guide-step-${index}`} bind:checked={value.checked[index]} onchange={onchange} />
      <label for={`guide-step-${index}`}><span class="number">{index + 1}</span>{item.label}</label>
      <button type="button" class="mini" aria-label={`Hint for step ${index + 1}`} aria-pressed={hints.has(index)} onclick={() => (hints = toggle(hints, index))}>?</button>
      {#if !compact}<button type="button" class="mini" aria-label={`Note for step ${index + 1}`} aria-pressed={notes.has(index) || !!value.responses[index]} onclick={() => (notes = toggle(notes, index))}>✎</button>{/if}
    </div>
    {#if hints.has(index)}<p class="hint">{item.hint}</p>{/if}
    {#if !compact && (notes.has(index) || value.responses[index])}
      <label class="note"><span class="sr-only">Step {index + 1}: {item.label}</span><textarea rows="2" maxlength="4000" bind:value={value.responses[index]} oninput={onchange} placeholder={item.prompt}></textarea></label>
    {/if}
  </li>
{/snippet}

<section class="study-guide" aria-label="Fourteen-step study guide">
  <div class="guide-heading">
    <div><h3>14-step study guide</h3><p>Prepare → connect → test → reflect → return. Your checklist and reflections are saved with this study session.</p></div>
    <span role="status">{value.checked.filter(Boolean).length}/14 checked{dirty ? " · unsaved" : ""}</span>
    <div class="view-switch" role="radiogroup" aria-label="Guide layout">
      <button type="button" role="radio" aria-checked={view === "columns"} onclick={() => setView("columns")}>Columns</button>
      <button type="button" role="radio" aria-checked={view === "rail"} onclick={() => setView("rail")}>Rail</button>
    </div>
    {#if !active}<Button size="sm" onclick={onnew} disabled={busy}>New preparation</Button>{/if}
  </div>
  {#if view === "columns"}
    <div class="columns" data-guide-view="columns">
      {#each groups as group, groupIndex}
        <section class:current={stage === groupIndex} aria-label={group.label}>
          <h4>{group.label}</h4>
          <span class="count">{value.checked.slice(group.start, group.end).filter(Boolean).length}/{group.end - group.start}</span>
          <ol>{#each Array.from({ length: group.end - group.start }, (_, offset) => group.start + offset) as index (index)}{@render step(index, false)}{/each}</ol>
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
            <ol>{#each Array.from({ length: group.end - group.start }, (_, offset) => group.start + offset) as index (index)}{@render step(index, true)}{/each}</ol>
          {/if}
        </li>
      {/each}
    </ol>
  {/if}
  <details open={active && phase === "short_break"}>
    <summary>Return review & reflection</summary>
    <label>What changed in your understanding?<textarea rows="3" maxlength="10000" bind:value={value.reflection} oninput={onchange} placeholder="Outcome versus goal; connections made; confusion explored; test attempt and feedback; one change for next time."></textarea></label>
    <label>Next spaced review<input type="date" bind:value={value.next_review} onchange={onchange} /></label>
    <p>Return to the gist and relationships, retrieve before looking, then compare with your source. Interleave related tasks while keeping assessment-like conditions.</p>
  </details>
  <Button intent="primary" disabled={busy || !dirty} onclick={onsave}>{dirty ? "Save guide & reflection" : "Guide saved"}</Button>
  <small>Adapted from your handwritten decision flow and requested fourteen-step routine. The image names its last two checks “Frequency” and “Conditions”; this guide makes spacing and interleaving explicit.</small>
</section>

<style>
  .study-guide { display: grid; gap: .85rem; padding: 1rem; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); color: var(--ink); }
  .guide-heading { display: flex; align-items: center; flex-wrap: wrap; gap: .8rem; }
  .guide-heading > div:first-child { flex: 1 1 280px; }
  h3, h4 { margin: 0; }
  p, small { color: var(--muted); line-height: 1.5; }
  p { margin: .4rem 0 .65rem; }
  summary { cursor: pointer; font-weight: 600; padding: .5rem 0; }
  label { display: grid; gap: .4rem; margin: .45rem 0; }
  input, textarea { box-sizing: border-box; max-width: 100%; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-raised); color: var(--ink); padding: .6rem; font: inherit; }
  textarea { width: 100%; resize: vertical; }
  span[role="status"], .count { font-size: .8rem; color: var(--muted); font-variant-numeric: tabular-nums; }
  .view-switch { display: inline-flex; gap: 2px; padding: 2px; border: 1px solid var(--hairline, var(--border)); border-radius: 999px; }
  .view-switch button { padding: 3px 10px; border: 0; border-radius: 999px; background: transparent; color: var(--muted); font-size: 12px; }
  .view-switch button[aria-checked="true"] { background: color-mix(in srgb, var(--ink) 12%, transparent); color: var(--ink); }
  .columns { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .75rem; }
  .columns > section { display: grid; align-content: start; grid-template-columns: 1fr auto; gap: .5rem .4rem; padding: .75rem; border: 1px solid var(--hairline, var(--border)); border-radius: 12px; background: color-mix(in srgb, var(--surface-raised) 50%, transparent); }
  .columns > section.current { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); box-shadow: inset 0 2px 0 var(--accent); }
  .columns ol, .rail ol { grid-column: 1 / -1; display: grid; gap: .4rem; margin: 0; padding: 0; list-style: none; }
  .step { padding: .45rem .55rem; border: 1px solid var(--hairline, var(--border)); border-radius: 9px; background: var(--surface); }
  .step.done { opacity: .7; }
  .step.done .step-head label { text-decoration: line-through; text-decoration-color: color-mix(in srgb, var(--muted) 60%, transparent); }
  .step-head { display: flex; align-items: center; gap: .45rem; }
  .step-head input { width: auto; margin: 0; padding: 0; accent-color: var(--accent); }
  .step-head label { flex: 1; display: flex; gap: .4rem; align-items: baseline; margin: 0; font-size: 13px; line-height: 1.35; cursor: pointer; }
  .number { color: var(--subtle, var(--muted)); font: 500 10.5px var(--font-mono); }
  .mini { flex: none; width: 22px; height: 22px; padding: 0; border: 1px solid transparent; border-radius: 999px; background: transparent; color: var(--muted); font-size: 12px; }
  .mini:hover, .mini[aria-pressed="true"] { border-color: var(--hairline, var(--border)); color: var(--ink); }
  .hint { margin: .4rem 0 .1rem; font-size: 12.5px; }
  .note { margin: .4rem 0 0; }
  .rail { display: grid; gap: .4rem; margin: 0; padding: 0 0 0 .4rem; list-style: none; border-left: 2px solid var(--hairline, var(--border)); }
  .rail > li { display: grid; gap: .45rem; padding-left: .8rem; }
  .rail-head { display: flex; align-items: center; gap: .55rem; width: 100%; padding: .35rem 0; border: 0; background: transparent; color: var(--ink); font-weight: 600; text-align: left; }
  .rail-head .count { margin-left: auto; }
  .dot { width: 10px; height: 10px; margin-left: -1.32rem; border: 2px solid var(--hairline, var(--border)); border-radius: 999px; background: var(--surface); }
  .rail > li.current .dot { border-color: var(--accent); background: var(--accent); }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
  @media (max-width: 900px) { .columns { grid-template-columns: 1fr; } }
</style>
