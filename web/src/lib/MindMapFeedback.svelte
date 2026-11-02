<script lang="ts">
  import AIReview from "./AIReview.svelte";
  import {
    mindMapFeedbackRequest,
    type MindMapFeedbackGraph,
  } from "./mindMapFeedback";
  import { useWorkspaceContext } from "./workspaceContext.svelte";

  let {
    title,
    graph,
    demo = false,
  }: {
    title: string;
    graph: MindMapFeedbackGraph;
    demo?: boolean;
  } = $props();
  const pane = useWorkspaceContext();
  const instanceId = $props.id();
  const questionId = `${pane.id}-mind-map-feedback-${instanceId}-question`;
  let question = $state("Does this map make sense?");
  const request = $derived(mindMapFeedbackRequest(title, question, graph));
  const text = $derived(JSON.stringify(request, null, 2));
  const PRIVACY = "Only the map title, your question, topic IDs, labels and notes, and link ends and labels are sent. " +
    "Positions, ink, linked records, URLs and sources are not. Remove anything private first; " +
    "you inspect the exact request and approve the paid send. Feedback never changes your map.";
</script>

<section class="mind-map-feedback" aria-label="Conceptual mind-map feedback">
  <h3>Ask about this map</h3>
  <label for={questionId}>
    Your question
    <textarea
      id={questionId}
      rows={3}
      maxlength={4000}
      bind:value={question}
      placeholder="Does this map make sense? Which concepts or relations need clarification?"
    ></textarea>
  </label>
  {#if graph.nodes.length === 0}
    <p role="status">Add labelled topics to ask about this map.</p>
  {/if}
  <!-- Q9: one line before the paid send; the full privacy detail is its tooltip. -->
  <p class="muted" title={PRIVACY}>Sends this map's labels, notes and your question to OpenRouter.</p>
  <AIReview
    purpose="mind-map-feedback"
    {text}
    {demo}
    mindMap={request}
    active={pane.active}
    previewLabel="Review map feedback request"
  />
</section>

<style>
  .mind-map-feedback {
    display: grid;
    gap: 0.75rem;
    min-width: 0;
  }
  h3,
  p {
    margin: 0;
  }
  .muted {
    color: var(--muted);
    font-size: 12px;
  }
  label {
    display: grid;
    gap: 0.4rem;
  }
  textarea {
    width: 100%;
    min-width: 0;
    resize: vertical;
  }
</style>
