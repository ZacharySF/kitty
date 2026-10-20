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
</script>

<section class="mind-map-feedback" aria-label="Conceptual mind-map feedback">
  <h3>Ask about this map</h3>
  <p class="muted">
    Get a draft critique of your organizing question, named concepts, relations,
    assumptions, and possible improvements—not a score based on shapes or counts.
    Feedback never changes your map.
  </p>
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
  <p class="muted">
    Only the map title, this question, concept IDs, labels and notes, and connector
    endpoints and labels are included. Positions, freehand strokes, linked records,
    URLs stored as metadata, and source metadata are not included. Text you wrote
    in labels or notes is included; remove anything private before reviewing.
    Inspect the exact request before separately approving a paid send to OpenRouter.
  </p>
  {#if graph.nodes.length === 0}
    <p role="status">Add labeled concepts to request conceptual feedback. Freehand strokes alone are not a concept map.</p>
  {/if}
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
