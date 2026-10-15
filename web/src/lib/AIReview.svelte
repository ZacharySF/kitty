<script lang="ts">
  import { api, type TaskRecord } from "./api";
  let {
    purpose,
    text,
    demo = false,
    onresult,
  }: {
    purpose: string;
    text: string;
    demo?: boolean;
    onresult?: (text: string) => void;
  } = $props();
  type Review = {
    review_id: string;
    purpose: string;
    model: string;
    text: string;
    system_prompt: string;
    max_output_tokens: number;
    maximum_cost_usd: number;
    expires_at: string;
    privacy_notice: string;
  };
  let review = $state<Review | null>(null);
  let busy = $state(false);
  let approved = $state(false);
  let budget = $state(0.05);
  let error = $state("");
  let output = $state("");
  let cost = $state<number | null>(null);
  let reviewedText = $state("");
  let reviewedPurpose = $state("");
  const stale = $derived(reviewedText !== text || reviewedPurpose !== purpose);
  async function preview() {
    busy = true;
    error = "";
    approved = false;
    try {
      review = await api<Review>("/ai/preview", { purpose, text });
      reviewedText = text;
      reviewedPurpose = purpose;
      budget = Math.max(0.01, Math.ceil(review.maximum_cost_usd * 100) / 100);
    } catch (e) {
      error = String(e instanceof Error ? e.message : e);
    } finally {
      busy = false;
    }
  }
  async function run() {
    if (!review || stale || !approved || busy) return;
    busy = true;
    error = "";
    output = "";
    try {
      const started = await api<{ task: TaskRecord }>("/ai/run", {
        review_id: review.review_id,
        confirmed: true,
        budget_usd: budget,
      });
      let task = started.task;
      while (task.status === "running") {
        await new Promise((r) => setTimeout(r, 600));
        task = await api<TaskRecord>(`/tasks/${task.id}`);
      }
      review = null;
      approved = false;
      if (task.status === "failed")
        throw new Error(task.error || "AI request failed");
      const result = task.result as { text: string; cost_usd: number | null };
      output = result.text;
      cost = result.cost_usd;
    } catch (e) {
      error = String(e instanceof Error ? e.message : e);
    } finally {
      busy = false;
    }
  }
</script>

<section class="ai-review" aria-label="Reviewed AI assistance">
  <p class="muted">
    Only this selection is sent. Nothing changes until you accept a draft.
  </p>
  <button
    class="button"
    onclick={preview}
    disabled={busy || !text.trim() || demo}
    >{busy ? "Working…" : "Review AI request"}</button
  >
  {#if demo}<p class="muted">
      External AI requests are disabled in the disposable demo.
    </p>{/if}
  {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
  {#if review}
    <div class="review-box">
      <strong>{review.model}</strong>
      <p>
        Maximum token cost: ${review.maximum_cost_usd.toFixed(6)} · {review.max_output_tokens}
        output tokens
      </p>
      <p class="muted">{review.privacy_notice}</p>
      <details>
        <summary>Exact text and instructions sent</summary>
        <h4>Selected text</h4>
        <pre>{review.text}</pre>
        <h4>Instructions</h4>
        <pre>{review.system_prompt}</pre>
      </details>
      <label
        >Request spending limit (USD)<input
          type="number"
          bind:value={budget}
          min={0}
          max={100}
          step="0.01"
        /></label
      >
      <label class="approval"
        ><input type="checkbox" bind:checked={approved} /> I approve sending this
        text and the bounded paid request.</label
      >
      {#if stale}<p role="alert">Selection changed. Review again.</p>{/if}
      <button
        class="button primary"
        onclick={run}
        disabled={busy ||
          stale ||
          !approved ||
          budget < review.maximum_cost_usd}>Send approved request</button
      >
    </div>
  {/if}
  {#if output}<div class="review-box">
      <h3>AI draft — check before using</h3>
      <pre class="draft">{output}</pre>
      <small
        >Reported cost: {cost === null
          ? "not provided; maximum remains reserved"
          : `$${cost.toFixed(6)}`}</small
      >{#if onresult}<button class="button" onclick={() => onresult?.(output)}
          >Use this draft for review</button
        >{/if}<button
        class="button"
        onclick={() =>
          navigator.clipboard.writeText(output).catch(() => {
            error = "Clipboard unavailable; select the draft to copy.";
          })}>Copy draft</button
      >
    </div>{/if}
</section>

<style>
  .ai-review {
    display: grid;
    gap: 0.7rem;
  }
  .review-box {
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    display: grid;
    gap: 0.75rem;
    min-width: 0;
  }
  pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 28rem;
    overflow: auto;
    font: inherit;
  }
  label {
    display: grid;
    gap: 0.3rem;
  }
  .approval {
    display: flex;
    align-items: flex-start;
    gap: 0.7rem;
  }
  .approval input {
    width: auto;
  }
  .draft {
    line-height: 1.65;
  }
</style>
