<script lang="ts">
  import Popover from "./ui/Popover.svelte";
  import type { View } from "./workspace";

  /**
   * Job search in four steps, the same on every one of its pages: find a job, save it,
   * apply, track the application. Everything else (resumes, sources, email, questions,
   * reports) is one menu away.
   */
  let { view, onview }: { view: View; onview: (next: View) => void } = $props();
  const STEPS: [View, string][] = [["jobs", "Find"], ["favorites", "Saved"], ["queue", "Apply"], ["pipeline", "Track"]];
  const MORE: [View, string][] = [["inbox", "New jobs"], ["applications", "All applications"], ["resumes", "Resumes"], ["questions", "Questions"],
    ["sources", "Job sources"], ["finder", "Find companies"], ["triage", "Rank jobs"], ["mail", "Email updates"], ["reports", "Reports"]];
  let open = $state(false);
  const elsewhere = $derived(MORE.find(([id]) => id === view));
</script>

<nav class="jobs-nav" aria-label="Job search">
  <div class="steps">
    {#each STEPS as [id, label] (id)}
      <button type="button" aria-current={view === id ? "page" : undefined} onclick={() => onview(id)}>{label}</button>
    {/each}
  </div>
  <Popover label="More job search pages" bind:open placement="bottom-end" class="jobs-more">
    {#snippet trigger(props)}
      <button type="button" class="more" aria-current={elsewhere ? "page" : undefined} {...props}>{elsewhere ? elsewhere[1] : "More"}</button>
    {/snippet}
    <ul>
      {#each MORE as [id, label] (id)}
        <li><button type="button" aria-current={view === id ? "page" : undefined} onclick={() => { open = false; onview(id); }}>{label}</button></li>
      {/each}
    </ul>
  </Popover>
</nav>

<style>
  .jobs-nav {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 2px 0 14px;
  }
  .steps {
    display: flex;
    gap: 4px;
  }
  :global(:root body) .jobs-nav button {
    height: 34px;
    min-height: 0;
    margin: 0;
    padding: 0 16px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: 400 16px/1 var(--font-ui);
    white-space: nowrap;
    cursor: pointer;
  }
  :global(:root body) .jobs-nav button:hover {
    color: var(--ink);
  }
  :global(:root body) .jobs-nav button[aria-current="page"] {
    background: var(--press-fill, var(--accent));
    color: var(--press-ink, var(--on-accent));
  }
  :global(:root body) .jobs-nav button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  ul {
    display: grid;
    gap: 2px;
    min-width: 190px;
    margin: 0;
    padding: 6px;
    list-style: none;
  }
  :global(:root body) .jobs-nav ul button,
  :global(:root body .jobs-more) ul button {
    width: 100%;
    height: 34px;
    padding: 0 12px;
    border: 0;
    border-radius: 8px;
    background: transparent;
    color: var(--ink);
    font: 400 15px/1 var(--font-ui);
    text-align: left;
    cursor: pointer;
  }
  :global(:root body .jobs-more) ul button:is(:hover, [aria-current="page"]) {
    background: var(--wash);
  }
</style>
