<!-- Browser fixture for tests/themes.browser.test.mjs: one page region of shared controls, every sample synthetic. -->
<script>
  import { Button, ToggleGroup } from "@nunera/bracket";
  import EmptyState from "/src/lib/EmptyState.svelte";
  import Loader from "/src/lib/Loader.svelte";
  import ThemeLayer from "/src/lib/themes/ThemeLayer.svelte";

  let view = $state("week");
  let done = $state(true);
  // `?long`: a long scrolling list of panel rows, for the performance budget (themes.browser.test.mjs).
  const long = new URLSearchParams(location.search).has("long");
  const ROWS = Array.from({ length: 400 }, (_, index) => index);
</script>

<ThemeLayer />
<div class="harness" id="specimen">
  <nav class="workspace-groups" aria-label="Groups">
    <button type="button" aria-current="page" data-sample="current group">Plan</button>
    <button type="button" data-sample="group">Study</button>
  </nav>
  <section class="panel" data-surface-sample>
    <h1 data-sample="title">Pipelines &amp; hazards</h1>
    <p data-sample="body">Re-read the hazard chapter before Thursday.</p>
    <p class="muted" data-sample="muted">Next review at 18:00 · 14 cards</p>
    <small class="dim" data-sample="dim">Updated 4 min ago</small>
    <div class="surface-section">
      <ul class="tasks">
        <li><span class="dot" style:background="var(--c2)"></span><label><input type="checkbox" bind:checked={done} data-tick /> Draw SN1 vs SN2 comparison</label></li>
        <li><span class="dot" style:background="var(--c3)"></span><label><input type="checkbox" /> Finish eigenvalue problem set</label></li>
      </ul>
    </div>
    <hr class="rule" />
    <div class="surface-section">
      <div class="row">
        <ToggleGroup label="Range" items={[{ id: "day", label: "Day" }, { id: "week", label: "Week" }, { id: "month", label: "Month" }]} bind:value={view} />
        <button type="button" class="button" aria-pressed="true" data-sample="pressed toggle">Focus</button>
        <button type="button" class="pill primary" data-sample="primary">Start focus</button>
        <Button data-focus-target>Study ahead</Button>
      </div>
      <div role="tablist" aria-label="Views" class="row">
        <button type="button" role="tab" aria-selected="true" data-sample="selected tab">Session</button>
        <button type="button" role="tab" aria-selected="false">Progress</button>
      </div>
      <div role="listbox" aria-label="Decks" class="listbox">
        <div role="option" aria-selected="true" tabindex="-1" data-sample="selected option">Eigen everything</div>
        <div role="option" aria-selected="false" tabindex="-1">Reaction mechanisms</div>
      </div>
    </div>
  </section>
  <section class="panel">
    <EmptyState level={3} title="Nothing due"><p>Next review at 18:00.</p></EmptyState>
    <Loader kind="view" label="Waking the brain…" />
  </section>
  {#if long}
    <section class="panel long-list" data-long-list>
      {#each ROWS as row (row)}
        <div class="surface-section long-row"><span class="dot" style:background={`var(--c${(row % 6) + 1})`}></span> Review card {row + 1} · <span class="muted">due in {row % 9} days</span></div>
      {/each}
    </section>
  {/if}
</div>

<style>
  .harness {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 320px;
    gap: 16px;
    padding: 24px;
  }
  .harness > nav {
    grid-column: 1 / -1;
    width: fit-content;
  }
  .panel {
    position: relative;
    display: grid;
    gap: 10px;
    padding: 16px;
    border: 1px solid;
  }
  .muted { color: var(--muted); }
  .dim { color: var(--subtle); }
  p { margin: 0; }
  .tasks { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
  .tasks li { display: flex; align-items: center; gap: 8px; }
  .dot { width: 7px; height: 7px; border-radius: 999px; }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 8px; }
  [role="tab"], [role="option"] { padding: 5px 10px; border: 0; border-radius: 8px; color: var(--ink); }
  [role="tab"][aria-selected="false"] { background: transparent; }
  .listbox { display: grid; gap: 2px; margin-top: 8px; padding: 4px; border: 1px solid var(--hairline); border-radius: 12px; }
  .long-list { grid-column: 1 / -1; display: block; height: 320px; overflow: auto; }
  .long-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; }
</style>
