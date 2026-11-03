<script lang="ts">
  import AIReview from "../AIReview.svelte";
  import { useWorkspaceContext } from "../workspaceContext.svelte";
  import type { Id } from "./api";
  import MathText from "./MathText.svelte";

  /**
   * "Explain my mistake" (design Q6 C via Q7 B): only after a wrong answer. You type your
   * working; the request goes through the reviewed, approved and budgeted AI flow; the
   * reply points at the first faulty step and never gives the answer. The engine grades.
   */
  let { problemId, demo = false }: { problemId: Id; demo?: boolean } = $props();
  const pane = useWorkspaceContext();
  const uid = $props.id();
  const id = `${pane.id}-math-explain-${uid}`;
  let working = $state("");
  const request = $derived({ path: "/math/explain", body: { problem_id: problemId, working } });
</script>

<section class="explain" aria-label="Explain my mistake">
  <label for={id}>Your working</label>
  <textarea {id} rows={4} maxlength={4000} bind:value={working}
    placeholder="One step per line, as you did it. LaTeX like \frac{1}{6} is fine."></textarea>
  <p class="note" title="The problem, your answer and this working go to OpenRouter after you approve the cost. The reply points at the first wrong step and never gives the final answer; the checker still decides what is right.">
    Sends this problem and your working to OpenRouter.
  </p>
  <AIReview purpose="math-explain" text={working} preview={request} {demo} active={pane.active} previewLabel="Review the explanation request">
    {#snippet draft(reply)}
      <div class="bubble" role="note" aria-label="Explanation"><MathText text={reply} /></div>
    {/snippet}
  </AIReview>
</section>

<style>
  .explain {
    display: grid;
    gap: 8px;
    width: 100%;
    padding-top: 12px;
    border-top: 1px solid var(--hairline);
    text-align: left;
  }
  label {
    color: var(--muted);
    font: 500 11px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  textarea {
    width: 100%;
    box-sizing: border-box;
    resize: vertical;
    font: 14px/1.5 var(--font-mono);
  }
  .note {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
  .bubble {
    position: relative;
    padding: 12px 14px;
    border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent);
    border-radius: 14px 14px 14px 4px;
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    color: var(--ink);
    font-size: 14px;
    line-height: 1.55;
  }
</style>
