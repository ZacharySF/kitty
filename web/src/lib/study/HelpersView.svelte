<script lang="ts">
  import { Button } from "@nunera/bracket";
  import AIReview from "../AIReview.svelte";

  /** Recall and reflection note templates, plus optional AI study suggestions. */
  let { text = $bindable(), demo, active, ontemplate, onresult }: {
    text: string;
    demo: boolean;
    active: boolean;
    ontemplate: (template: string) => void;
    onresult: (text: string) => void;
  } = $props();
</script>

<div class="chart-grid">
  <article class="panel">
    <h3>Active recall</h3>
    <ol>
      <li>Close the material. Explain the central idea in your own words.</li>
      <li>Write three questions you should be able to answer.</li>
      <li>Answer without looking, then compare with your source.</li>
      <li>Mark the gap and revisit it in a later session.</li>
    </ol>
    <p>No facts are generated here; use your actual course sources.</p>
    <Button onclick={() => ontemplate("Recall question:\nMy answer without looking:\nSource comparison / gap:\nNext practice step:")}>Use recall note template</Button>
  </article>
  <article class="panel">
    <h3>Reflection</h3>
    <p>What could I retrieve unaided? What caused friction? What will I do differently in the next session?</p>
    <Button onclick={() => ontemplate("What I retrieved:\nWhat I could not explain yet:\nDistractions / friction:\nOne next action:")}>Use reflection template</Button>
  </article>
</div>
<article class="panel">
  <h3>Optional AI study suggestions</h3>
  <p>
    Select only material you want to send. Preview is local; the confirmed run sends this text to OpenRouter. Suggestions are not facts and never alter plans automatically.
  </p>
  <label class="field">Selected text<textarea rows="6" bind:value={text} maxlength="100000" placeholder="Paste a passage or requirements you deliberately want to send."></textarea></label>
  <AIReview purpose="study-plan" {text} {demo} {active} {onresult} />
</article>

<style>
  .chart-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 230px), 1fr));
    gap: 0.85rem;
    margin: 0 0 0.85rem;
  }
</style>
