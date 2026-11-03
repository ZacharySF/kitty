<script lang="ts">
  import { onMount, tick } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api } from "../api";
  import { useWorkspaceContext } from "../workspaceContext.svelte";
  import type { Problem, Verdict } from "./api";
  import AnswerInput from "./AnswerInput.svelte";
  import ExplainMistake from "./ExplainMistake.svelte";
  import Feedback, { type FeedbackLine } from "./Feedback.svelte";
  import MathText from "./MathText.svelte";

  /**
   * One generated problem, checked by the engine. The first decided answer is the graded
   * one (the backend counts it); retries are allowed until it is right or revealed.
   * A wrong answer offers "Explain my mistake".
   */
  let {
    topicId,
    eyebrow,
    demo = false,
    nextLabel = "Next problem",
    ongraded,
    onnext,
  }: {
    topicId: string;
    eyebrow: string;
    demo?: boolean;
    nextLabel?: string;
    ongraded?: (correct: boolean, verdict: Verdict) => void;
    onnext?: () => void;
  } = $props();
  const pane = useWorkspaceContext();

  let problem = $state<Problem | null>(null);
  let error = $state("");
  let busy = $state(false);
  let lines = $state<FeedbackLine[]>([]);
  let graded = $state<boolean | null>(null);
  let explaining = $state(false);
  let box = $state<{ mark: (correct: boolean | null, choice?: number) => Promise<void>; focus: () => void }>();
  let shown = 0;
  const finished = $derived(lines.some((line) => line.verdict.correct === true || line.verdict.revealed));

  onMount(async () => {
    try {
      problem = await api<Problem>("/math/problems", { topic_id: topicId });
      shown = performance.now();
      await tick();
      box?.focus();
    } catch (cause) {
      error = (cause as Error).message;
    }
  });

  async function submit(answer: string) {
    if (!problem || busy || finished) return;
    busy = true;
    error = "";
    try {
      const verdict = await api<Verdict>("/math/answers", { problem_id: problem.problem_id, answer, elapsed_ms: Math.round(performance.now() - shown) });
      lines = [{ verdict, answer, kind: problem.answer_kind }, ...lines].slice(0, 3);
      const choice = problem.answer_kind === "choice" ? problem.choices?.indexOf(answer) : undefined;
      void box?.mark(verdict.correct, choice);
      if (graded === null && verdict.correct !== null) {
        graded = verdict.correct;
        ongraded?.(verdict.correct, verdict);
      }
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }

  function onkey(event: KeyboardEvent) {
    if (!finished || !onnext || !pane.active || event.key !== "Enter" || event.defaultPrevented) return;
    if ((event.target as HTMLElement | null)?.closest("textarea,button,[role=dialog]")) return;
    event.preventDefault();
    onnext();
  }
</script>

<svelte:window onkeydown={onkey} />

<div class="problem" data-problem={problem ? String(problem.problem_id) : undefined}>
  <span class="eyebrow">{eyebrow}</span>
  {#if problem}
    <div class="prompt"><MathText text={problem.prompt_md} /></div>
    <AnswerInput bind:this={box} kind={problem.answer_kind} choices={problem.choices} disabled={finished} {busy} onsubmit={submit} />
    {#if lines.length}<Feedback {lines} />{/if}
    <div class="actions">
      {#if graded === false && !explaining}
        <Button size="sm" onclick={() => (explaining = true)}>Explain my mistake</Button>
      {/if}
      {#if finished && onnext}
        <Button size="sm" variant="solid" intent="primary" onclick={onnext}>{nextLabel} <kbd>↵</kbd></Button>
      {/if}
    </div>
    {#if explaining}<ExplainMistake problemId={problem.problem_id} {demo} />{/if}
  {:else if error}
    <p class="error" role="alert">{error}</p>
  {:else}
    <p class="rule" role="status">Dealing a problem…</p>
  {/if}
  {#if problem && error}<p class="error" role="alert">{error}</p>{/if}
</div>

<style>
  .problem {
    display: grid;
    justify-items: center;
    gap: 14px;
    width: min(100%, 560px);
    box-sizing: border-box;
    padding: 26px 30px;
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
  .prompt {
    color: var(--ink);
    font-size: 16px;
    line-height: 1.5;
  }
  .rule {
    margin: 0;
    color: var(--subtle);
    font-size: 12.5px;
  }
  .actions {
    display: flex;
    gap: 8px;
  }
  .actions:empty {
    display: none;
  }
  .actions :global(kbd) {
    color: inherit;
    font: 500 10.5px var(--font-mono);
    opacity: 0.7;
  }
  .error {
    margin: 0;
    color: var(--warning);
    font-size: 13px;
  }
</style>
