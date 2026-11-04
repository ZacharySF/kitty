<script lang="ts">
  import { onMount, tick } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api, safeUrl } from "../api";
  import { useWorkspaceContext } from "../workspaceContext.svelte";
  import type { Card, Rating, TextbookExercise, TextbookProblem, TextbookVerdict } from "./api";
  import AnswerInput from "./AnswerInput.svelte";
  import Feedback, { type FeedbackLine } from "./Feedback.svelte";
  import MathText from "./MathText.svelte";
  import Ratings from "./Ratings.svelte";
  import { dueIn } from "./review";

  /**
   * A Grinstead & Snell exercise (GNU FDL, bundled). One rika! has an answer for is
   * checked like a generated problem; the rest you work, reveal (the free edition has no
   * answer key) and rate Again … Easy. Its text never goes to AI: no Explain my mistake.
   */
  let {
    exerciseId,
    eyebrow = "Textbook",
    titles = {},
    nextLabel = "Next",
    ongraded,
    onnext,
  }: {
    exerciseId: string;
    eyebrow?: string;
    /** Topic titles by id, for the linked topics. */
    titles?: Record<string, string>;
    nextLabel?: string;
    ongraded?: (card: Card) => void;
    onnext?: () => void;
  } = $props();
  const pane = useWorkspaceContext();

  let exercise = $state<TextbookExercise | null>(null);
  let problem = $state<TextbookProblem | null>(null);
  let error = $state("");
  let busy = $state(false);
  let lines = $state<FeedbackLine[]>([]);
  let revealed = $state(false);
  let card = $state<Card | null>(null);
  let box = $state<{ mark: (correct: boolean | null) => Promise<void>; focus: () => void }>();
  let shown = 0;
  const self = $derived(exercise?.graded === "self");
  const finished = $derived(self ? card !== null : lines.some((line) => line.verdict.correct === true || line.verdict.revealed));
  const link = $derived(exercise ? safeUrl(exercise.source.url) : undefined);
  const topicNames = $derived((exercise?.topics ?? []).map((id) => titles[id] ?? id));

  onMount(async () => {
    try {
      exercise = await api<TextbookExercise>(`/math/textbook/${encodeURIComponent(exerciseId)}`);
      if (exercise.graded === "number") {
        problem = await api<TextbookProblem>("/math/textbook/problems", { exercise_id: exerciseId });
        shown = performance.now();
        await tick();
        box?.focus();
      }
    } catch (cause) {
      error = (cause as Error).message;
    }
  });

  async function submit(answer: string) {
    if (!problem || busy || finished) return;
    busy = true;
    error = "";
    try {
      const verdict = await api<TextbookVerdict>("/math/textbook/answers", { problem_id: problem.problem_id, answer, elapsed_ms: Math.round(performance.now() - shown) });
      lines = [{ verdict, answer, kind: problem.answer_kind }, ...lines].slice(0, 3);
      void box?.mark(verdict.correct);
      if (!card && verdict.correct !== null) {
        card = verdict.card;
        ongraded?.(verdict.card);
      }
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }

  async function rate(rating: Rating) {
    if (!self || !revealed || busy || card) return;
    busy = true;
    error = "";
    try {
      card = (await api<{ card: Card }>("/math/textbook/ratings", { exercise_id: exerciseId, rating })).card;
      ongraded?.(card);
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }

  function onkey(event: KeyboardEvent) {
    if (!pane.active || event.key !== "Enter" || event.defaultPrevented || event.repeat) return;
    if ((event.target as HTMLElement | null)?.closest("input,textarea,button,a,math-field,[role=dialog]")) return;
    if (self && !revealed) revealed = true;
    else if (finished && onnext) onnext();
    else return;
    event.preventDefault();
  }
</script>

<svelte:window onkeydown={onkey} />

<div class="problem textbook" data-exercise={exerciseId} data-graded={exercise?.graded}>
  <span class="eyebrow">{eyebrow}</span>
  {#if exercise}
    <div class="ref">
      <strong>{exercise.source.short} {exercise.label}</strong>{#if exercise.starred}<span class="star" title="Starred in the book: harder">★</span>{/if}
      <span class="section">§{exercise.section} {exercise.section_title}</span>
    </div>
    {#if topicNames.length}
      <ul class="topics" aria-label="Linked topics">{#each topicNames as name (name)}<li>{name}</li>{/each}</ul>
    {/if}
    <div class="prompt"><MathText text={problem?.prompt_md ?? exercise.text_md} /></div>
    {#if exercise.figure}<p class="flag">It refers to a figure in the book that isn't bundled here.</p>{/if}
    {#if exercise.program}<p class="flag">It asks for a program: write and run it, then rate your working.</p>{/if}

    {#if problem}
      <AnswerInput bind:this={box} kind={problem.answer_kind} disabled={finished} {busy} onsubmit={submit} />
      {#if lines.length}<Feedback {lines} />{/if}
    {:else if self && !revealed}
      <Button variant="solid" intent="primary" onclick={() => (revealed = true)}>Reveal <kbd>↵</kbd></Button>
    {:else if self}
      <div class="reveal" data-revealed>
        <p>Check your working; the free edition has no answer key.</p>
        {#if topicNames.length}<p class="uses">It uses {topicNames.join(", ")}.</p>{/if}
      </div>
      {#if !card}<Ratings keys {busy} label="How did it go?" onrate={rate} />{/if}
    {/if}

    {#if card}<p class="due" data-next-review>Next review {dueIn(card.due_at, Date.now())}</p>{/if}
    {#if finished && onnext}
      <Button size="sm" variant="solid" intent="primary" onclick={onnext}>{nextLabel} <kbd>↵</kbd></Button>
    {/if}
    <p class="credit" data-credit>
      {#if link}<a href={link} target="_blank" rel="noopener noreferrer">{exercise.source.title}</a>{:else}{exercise.source.title}{/if}
      · {exercise.source.version} · {exercise.source.licence}
    </p>
  {:else if error}
    <p class="error" role="alert">{error}</p>
  {:else}
    <p class="rule" role="status">Opening the exercise…</p>
  {/if}
  {#if exercise && error}<p class="error" role="alert">{error}</p>{/if}
</div>

<style>
  .problem {
    display: grid;
    justify-items: center;
    gap: 12px;
    width: min(100%, 600px);
    box-sizing: border-box;
    padding: 24px 30px 16px;
    border: 1px solid var(--border);
    border-radius: var(--br-radius-lg, 0.8rem);
    background: var(--surface);
    text-align: center;
  }
  .eyebrow {
    color: var(--subtle);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .ref {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: center;
    gap: 4px 10px;
    color: var(--ink);
  }
  .ref strong {
    font: 600 14px var(--font-ui);
  }
  .star {
    color: var(--warning);
    font-size: 12px;
  }
  .section {
    color: var(--muted);
    font-size: 12.5px;
  }
  .topics {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 6px;
    margin: -4px 0 0;
    padding: 0;
    list-style: none;
  }
  .topics li {
    padding: 2px 9px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--muted);
    font: 500 11px var(--font-ui);
  }
  .prompt {
    max-width: 62ch;
    color: var(--ink);
    font-size: 15.5px;
    line-height: 1.55;
    text-align: left;
  }
  .flag,
  .due,
  .rule {
    margin: 0;
    color: var(--muted);
    font-size: 12.5px;
  }
  .reveal {
    display: grid;
    gap: 4px;
    width: 100%;
    box-sizing: border-box;
    padding: 10px 14px;
    border: 1px solid color-mix(in srgb, var(--accent) 45%, var(--hairline));
    border-radius: 10px;
    background: color-mix(in srgb, var(--accent) 8%, transparent);
  }
  .reveal p {
    margin: 0;
    color: var(--ink);
    font-size: 13.5px;
  }
  .reveal .uses {
    color: var(--muted);
    font-size: 12.5px;
  }
  .problem :global(kbd) {
    color: inherit;
    font: 500 10.5px var(--font-mono);
    opacity: 0.7;
  }
  .credit {
    margin: 2px 0 0;
    padding-top: 10px;
    width: 100%;
    border-top: 1px solid var(--hairline);
    color: var(--subtle);
    font-size: 11px;
  }
  .credit a {
    color: inherit;
  }
  .error {
    margin: 0;
    color: var(--warning);
    font-size: 13px;
  }
</style>
