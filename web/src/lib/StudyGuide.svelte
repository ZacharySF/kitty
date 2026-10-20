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
</script>

<section class="study-guide" aria-label="Fourteen-step study guide">
  <div class="guide-heading">
    <div><h3>14-step study guide</h3><p>Prepare → connect → test → reflect → return. Your checklist and reflections are saved with this study session.</p></div>
    <span role="status">{value.checked.filter(Boolean).length}/14 checked{dirty ? " · unsaved" : ""}</span>
    {#if !active}<Button size="sm" onclick={onnew} disabled={busy}>New preparation</Button>{/if}
  </div>
  {#each groups as group, groupIndex}
    <details open={stage === groupIndex}>
      <summary>{group.label} · steps {group.start + 1}–{group.end}</summary>
      <ol start={group.start + 1}>
        {#each steps.slice(group.start, group.end) as step, offset}
          {@const index = group.start + offset}
          <li>
            <label class="guide-check"><input type="checkbox" bind:checked={value.checked[index]} onchange={onchange} /><strong>{step.label}</strong></label>
            <p>{step.hint}</p>
            <label><span class="sr-only">Step {index + 1}: {step.label}</span><textarea rows="2" maxlength="4000" bind:value={value.responses[index]} oninput={onchange} placeholder={step.prompt}></textarea></label>
          </li>
        {/each}
      </ol>
    </details>
  {/each}
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
  .guide-heading { display: flex; align-items: start; flex-wrap: wrap; gap: .8rem; }
  .guide-heading > div { flex: 1 1 280px; }
  h3 { margin: 0; }
  p, small { color: var(--muted); line-height: 1.5; }
  p { margin: .4rem 0 .65rem; }
  summary { cursor: pointer; font-weight: 600; padding: .5rem 0; }
  ol { margin: .5rem 0; padding-left: 1.8rem; }
  li { padding: .4rem 0 .8rem; }
  label { display: grid; gap: .4rem; margin: .45rem 0; }
  .guide-check { display: flex; gap: .65rem; align-items: baseline; }
  .guide-check input { width: auto; }
  input, textarea { box-sizing: border-box; max-width: 100%; border: 1px solid var(--border); border-radius: 6px; background: var(--surface-raised); color: var(--ink); padding: .6rem; font: inherit; }
  textarea { width: 100%; resize: vertical; }
  span[role="status"] { font-size: .8rem; color: var(--muted); }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
</style>
