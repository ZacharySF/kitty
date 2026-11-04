<script lang="ts">
  import { onMount, tick } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api } from "../api";
  import { BLANK, type Lesson, type TextbookList, type Verdict } from "./api";
  import { echo, readAnswer } from "./answer";
  import MathText from "./MathText.svelte";
  import ProblemCard from "./ProblemCard.svelte";
  import { dueIn } from "./review";
  import { asMath } from "./tex";
  import TextbookCard from "./TextbookCard.svelte";

  /**
   * A topic lesson (design Q2 B): a worked example, the same kind of problem with its later
   * steps faded to blanks you fill, then solo problems. It ends early after a run of misses.
   * Mastery comes from the graded solo answers; guided blanks never count. Its Textbook
   * section lists the topic's Grinstead & Snell exercises to work from the book.
   */
  let {
    topicId,
    demo = false,
    titles = {},
    onchange,
    ondone,
  }: {
    topicId: string;
    demo?: boolean;
    /** Topic titles by id, for a textbook exercise's linked topics. */
    titles?: Record<string, string>;
    /** A graded answer may have moved the topic's mastery. */
    onchange: () => void;
    ondone: () => void;
  } = $props();

  const STAGES = ["Example", "Guided", "Solo"] as const;
  type Stage = (typeof STAGES)[number] | "Done";
  let lesson = $state<Lesson | null>(null);
  let error = $state("");
  let stage = $state<Stage>("Example");
  let reached = $state(0);

  // Guided: one instance, a step at a time.
  let step = $state(0);
  let blank = $state("");
  let miss = $state(false);
  let note = $state("");
  let revealed = $state("");
  let checking = $state(false);
  let blankInput = $state<HTMLInputElement>();
  let shown = 0;

  // Solo.
  let solo = $state(0);
  let results = $state<(boolean | null)[]>([]);
  let streak = $state(0);
  let endedEarly = $state(false);
  let mastery = $state<Verdict["mastery"] | null>(null);

  // Textbook: the topic's exercises, the first few until asked; one is worked in place of the sheet.
  const FEW = 6;
  let textbook = $state<TextbookList | null>(null);
  let textbookError = $state("");
  let working = $state("");
  let allExercises = $state(false);
  const exercises = $derived(allExercises ? (textbook?.exercises ?? []) : (textbook?.exercises ?? []).slice(0, FEW));

  const stageIndex = $derived(stage === "Done" ? 3 : STAGES.indexOf(stage));
  const guided = $derived(lesson?.guided[step]);
  const stopping = $derived(!!lesson && streak >= lesson.stop_after_misses);
  const right = $derived(results.filter((result) => result === true).length);

  onMount(async () => {
    void loadTextbook();
    try {
      lesson = await api<Lesson>(`/math/lessons/${encodeURIComponent(topicId)}`);
      results = Array(lesson.solo_count).fill(null);
    } catch (cause) {
      error = (cause as Error).message;
    }
  });

  async function loadTextbook() {
    try {
      textbook = await api<TextbookList>(`/math/textbook?${new URLSearchParams({ topic_id: topicId })}`);
      textbookError = "";
    } catch (cause) {
      textbookError = (cause as Error).message;
    }
  }

  async function go(next: Stage) {
    stage = next;
    if (next !== "Done") reached = Math.max(reached, STAGES.indexOf(next));
    if (next === "Guided" && lesson && !lesson.guided.length) stage = "Solo";
    if (stage === "Guided") {
      shown = performance.now();
      await tick();
      blankInput?.focus();
    }
  }

  async function checkBlank(event?: Event) {
    event?.preventDefault();
    if (!guided || checking || !blank.trim() || revealed) return;
    checking = true;
    try {
      const verdict = await api<Verdict>("/math/answers", { problem_id: guided.problem_id, answer: blank.trim(), elapsed_ms: Math.round(performance.now() - shown) });
      if (verdict.correct) {
        note = `Right: ${blank.trim()}.`;
        await nextStep();
      } else if (verdict.correct === null) {
        note = verdict.feedback || "Couldn't read that: try a number or a fraction like 3/7.";
      } else {
        miss = true;
        note = verdict.feedback || "Not quite: work the step again.";
        if (verdict.revealed && verdict.canonical) revealed = asMath(verdict.canonical);
      }
    } catch (cause) {
      note = (cause as Error).message;
    } finally {
      checking = false;
    }
  }

  async function nextStep() {
    blank = "";
    miss = false;
    revealed = "";
    if (lesson && step < lesson.guided.length - 1) {
      step += 1;
      shown = performance.now();
      await tick();
      blankInput?.focus();
    } else step = lesson?.guided.length ?? 0;
  }

  function graded(correct: boolean, verdict: Verdict) {
    results[solo] = correct;
    streak = correct ? 0 : streak + 1;
    if (verdict.mastery) mastery = verdict.mastery;
    onchange();
  }

  function nextSolo() {
    if (!lesson) return;
    if (stopping) {
      endedEarly = true;
      stage = "Done";
    } else if (solo < lesson.solo_count - 1) solo += 1;
    else stage = "Done";
  }

  function restart() {
    step = 0;
    solo = 0;
    streak = 0;
    endedEarly = false;
    note = "";
    results = Array(lesson?.solo_count ?? 0).fill(null);
    void go("Example");
  }
</script>

<section class="lesson" aria-label="Lesson">
  {#if error}
    <p class="error" role="alert">{error}</p>
  {:else if !lesson}
    <p class="muted" role="status">Opening the lesson…</p>
  {:else}
    <ol class="strip" aria-label="Lesson stages">
      {#each STAGES as name, index (name)}
        <li>
          <button type="button" aria-current={name === stage ? "step" : undefined} class:past={index < stageIndex}
            disabled={index > reached || stage === "Done"} onclick={() => go(name)}>
            <span class="n">{index < stageIndex ? "✓" : index + 1}</span>
            <span class="t"><b>{name}</b><small>{name === "Example" ? "watch it solved" : name === "Guided" ? "fill the faded steps" : `${lesson.solo_count} on your own`}</small></span>
          </button>
        </li>
      {/each}
    </ol>

    {#if working}
      <div class="sheet working">
        {#key working}<TextbookCard exerciseId={working} {titles} ongraded={() => void loadTextbook()} />{/key}
        <Button size="sm" onclick={() => (working = "")}>Back to the lesson</Button>
      </div>
    {:else}
    <div class="sheet">
      {#if stage === "Example"}
        <span class="eyebrow">Worked example · {lesson.title}</span>
        <div class="q"><MathText text={lesson.example.prompt_md} /></div>
        <ol class="steps">
          {#each lesson.example.steps as line, index (index)}<li><MathText text={line} inline /></li>{/each}
        </ol>
        <div class="foot"><Button variant="solid" intent="primary" onclick={() => go("Guided")}>I follow, try one</Button></div>
      {:else if stage === "Guided"}
        <span class="eyebrow">Guided · {lesson.title}</span>
        {#if guided}
          <div class="q"><MathText text={guided.prompt_md} /></div>
          <ol class="steps">
            {#each guided.steps.slice(0, guided.blank_step) as line, index (index)}<li><MathText text={line} inline /></li>{/each}
            {#if guided.steps[guided.blank_step] !== undefined}
              {@const [before, after = ""] = guided.steps[guided.blank_step].split(BLANK)}
              <li class="active" data-blank>
                <MathText text={before} inline />
                {#if revealed}<span class="got shown"><MathText text={revealed} inline /></span>
                {:else}
                  <form class="blank-form" onsubmit={checkBlank}>
                    <input bind:this={blankInput} class="blank" class:miss bind:value={blank} aria-label="Fill the blank" placeholder="?" autocomplete="off"
                      oninput={() => { miss = false; }} />
                  </form>
                {/if}
                <MathText text={after} inline />
              </li>
            {/if}
            {#each Array(lesson.guided.length - step - 1) as _, index (index)}<li class="faded" aria-hidden="true"><span class="placeholder">…</span></li>{/each}
          </ol>
          <div class="foot">
            {#if revealed}
              <Button variant="solid" intent="primary" onclick={nextStep}>Continue</Button>
            {:else}
              <Button variant="solid" intent="primary" disabled={checking || !blank.trim()} onclick={checkBlank}>Check <kbd>↵</kbd></Button>
            {/if}
            <span class="note" class:bad={miss} aria-live="polite">
              {note || `Step ${guided.blank_step + 1} is yours.`} {#if blank.trim() && readAnswer(blank)}<span class="echo">{echo(readAnswer(blank))}</span>{/if}
            </span>
          </div>
        {:else}
          <p class="note ok">{note} Every step is right.</p>
          <div class="foot"><Button variant="solid" intent="primary" onclick={() => go("Solo")}>Go solo</Button></div>
        {/if}
      {:else if stage === "Solo"}
        <div class="solo-head">
          <span class="eyebrow">Solo · problem {solo + 1} of {lesson.solo_count}</span>
          <span class="dots" aria-label={`${right} of ${lesson.solo_count} right`}>
            {#each results as result, index (index)}<span class="dot" class:cur={index === solo} class:right={result === true} class:wrong={result === false}></span>{/each}
          </span>
          {#if streak > 0}<span class="misses">{streak} of {lesson.stop_after_misses} misses in a row end it</span>{/if}
        </div>
        {#key solo}
          <ProblemCard {topicId} {demo} eyebrow={`Solo · ${lesson.title}`} nextLabel={stopping ? "End the lesson" : solo < lesson.solo_count - 1 ? "Next problem" : "Finish"}
            ongraded={graded} onnext={nextSolo} />
        {/key}
      {:else}
        <span class="eyebrow">{endedEarly ? "Lesson ended early" : "Lesson done"} · {lesson.title}</span>
        <p class="result" data-lesson-result>{right} of {results.filter((result) => result !== null).length} right</p>
        {#if endedEarly}
          <p class="note bad">Three misses in a row end a lesson. Take a break, then retry.</p>
        {/if}
        {#if mastery}
          <p class="note" data-mastery={mastery.state}>
            {lesson.title}: {mastery.state === "learning" ? `learning · ${Math.round((mastery.progress ?? 0) * 100)}%` : mastery.state}
            {#if mastery.state === "mastered" && mastery.due_at} · next review {new Date(mastery.due_at).toLocaleDateString([], { day: "numeric", month: "short" })}{/if}
          </p>
        {/if}
        <div class="foot">
          <Button variant="solid" intent="primary" onclick={ondone}>Next up</Button>
          <Button onclick={restart}>Start this lesson again</Button>
        </div>
      {/if}
    </div>
    {#if (stage === "Example" || stage === "Done") && (textbook?.exercises.length || textbookError)}
      <section class="book" aria-label="Textbook">
        <span class="eyebrow">Textbook · {textbook?.source.short ?? "Grinstead & Snell"}{#if textbook}{` · ${textbook.exercises.length} exercise${textbook.exercises.length === 1 ? "" : "s"}`}{/if}</span>
        {#if textbookError}<p class="note">{textbookError}</p>{/if}
        {#if textbook}
          <ul>
            {#each exercises as exercise (exercise.id)}
              <li data-exercise={exercise.id}>
                <b class="label">{exercise.label}</b>
                <span class="what">{exercise.section_title}{#if exercise.program}{" · a program"}{/if}</span>
                <span class="kind">{exercise.graded === "number" ? "answer checked" : "self-check"}</span>
                {#if exercise.card?.due}<span class="due">due</span>{:else if exercise.card}<span class="kind">next {dueIn(exercise.card.due_at, Date.now())}</span>{/if}
                <Button size="sm" onclick={() => (working = exercise.id)}>Work {exercise.label}</Button>
              </li>
            {/each}
          </ul>
          {#if textbook.exercises.length > FEW}
            <Button size="sm" onclick={() => (allExercises = !allExercises)}>{allExercises ? "Show fewer" : `Show all ${textbook.exercises.length}`}</Button>
          {/if}
          <p class="credit">{textbook.source.title} · {textbook.source.version} · {textbook.source.licence}</p>
        {/if}
      </section>
    {/if}
    {/if}
  {/if}
</section>

<style>
  .lesson {
    display: grid;
    align-content: start;
    gap: 14px;
    width: min(100%, 760px);
  }
  .strip {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .strip button {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    padding: 10px 14px;
    border: 1px solid var(--hairline);
    border-radius: 12px;
    background: color-mix(in srgb, var(--solid-panel, var(--surface)) 45%, transparent);
    color: var(--muted);
    text-align: left;
    cursor: pointer;
  }
  .strip button:disabled {
    cursor: default;
  }
  .strip button[aria-current="step"] {
    border-color: var(--accent);
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    color: var(--ink);
  }
  .n {
    display: grid;
    flex: none;
    place-items: center;
    width: 26px;
    height: 26px;
    border: 1px solid currentColor;
    border-radius: 999px;
    font: 600 12px var(--font-mono);
  }
  .past .n {
    border-color: var(--success);
    color: var(--success);
  }
  .t {
    display: grid;
  }
  .t b {
    font-size: 14px;
  }
  .t small {
    color: var(--subtle);
    font-size: 11.5px;
  }
  .sheet {
    display: grid;
    align-content: start;
    justify-items: start;
    gap: 14px;
    padding: 22px 28px;
    border: 1px solid var(--hairline);
    border-radius: 16px;
    background: color-mix(in srgb, var(--solid-panel, var(--surface)) 55%, transparent);
  }
  .sheet > :global(.problem) {
    justify-self: center;
  }
  .eyebrow {
    color: var(--subtle);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .q {
    max-width: 72ch;
    color: var(--ink);
    font-size: 16px;
  }
  .steps {
    display: grid;
    gap: 8px;
    width: 100%;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: 17px;
  }
  .steps li {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border-radius: 10px;
  }
  .steps li.active {
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    box-shadow: inset 3px 0 0 var(--accent);
  }
  .steps li.faded {
    opacity: 0.45;
  }
  .placeholder {
    color: var(--subtle);
    font: 500 14px var(--font-mono);
  }
  .blank-form {
    display: contents;
  }
  .blank {
    width: 104px;
    padding: 3px 8px;
    border: 2px dashed var(--accent);
    border-radius: 8px;
    background: color-mix(in srgb, var(--accent) 14%, transparent);
    color: var(--ink);
    font: 600 17px var(--font-mono);
    text-align: center;
    outline: none;
  }
  .blank:focus {
    border-style: solid;
  }
  .blank.miss {
    border-color: var(--error);
    background: color-mix(in srgb, var(--error) 12%, transparent);
  }
  .got {
    color: var(--success);
    font: 600 17px var(--font-mono);
  }
  .foot {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
  }
  .foot :global(kbd) {
    color: inherit;
    font: 500 10.5px var(--font-mono);
    opacity: 0.7;
  }
  .note {
    margin: 0;
    color: var(--muted);
    font-size: 13px;
  }
  .note.bad {
    color: var(--error);
  }
  .note.ok {
    color: var(--success);
  }
  .echo {
    color: var(--subtle);
    font-family: var(--font-mono);
  }
  .solo-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 14px;
    width: 100%;
  }
  .dots {
    display: flex;
    gap: 6px;
  }
  .dot {
    width: 12px;
    height: 12px;
    border: 1.5px solid var(--border);
    border-radius: 999px;
  }
  .dot.cur {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent);
  }
  .dot.right {
    border-color: var(--success);
    background: var(--success);
  }
  .dot.wrong {
    border-color: var(--error);
    background: var(--error);
  }
  .misses {
    margin-left: auto;
    color: var(--warning);
    font: 500 11px var(--font-mono);
  }
  .result {
    margin: 0;
    color: var(--ink);
    font: 600 40px/1.1 var(--font-mono);
  }
  .muted {
    color: var(--muted);
  }
  .error {
    color: var(--warning);
  }
  .working {
    justify-items: center;
  }
  .book {
    display: grid;
    gap: 8px;
    padding: 14px 18px;
    border: 1px solid var(--hairline);
    border-radius: 14px;
  }
  .book ul {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .book li {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 0;
    font-size: 13px;
  }
  .book li + li {
    border-top: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
  }
  .book .label {
    min-width: 52px;
    color: var(--ink);
    font: 600 13px var(--font-mono);
  }
  .book .what {
    flex: 1;
    min-width: 0;
    color: var(--ink);
  }
  .book .kind {
    color: var(--subtle);
    font: 500 11px var(--font-mono);
  }
  .book .due {
    color: var(--warning);
    font: 600 11px var(--font-mono);
  }
  .book > :global(.br-btn) {
    justify-self: start;
  }
  .book .credit,
  .book .note {
    margin: 0;
    color: var(--subtle);
    font-size: 11px;
  }
</style>
