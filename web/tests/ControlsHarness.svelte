<!-- Browser fixture for tests/controls.browser.test.mjs: every shared control on synthetic data. -->
<script>
  import { Button } from "@nunera/bracket";
  import Select from "/src/lib/ui/Select.svelte";
  import OverflowMenu from "/src/lib/ui/OverflowMenu.svelte";
  import Popover from "/src/lib/ui/Popover.svelte";
  import StatusDot from "/src/lib/ui/StatusDot.svelte";
  import QuickAdd from "/src/lib/ui/QuickAdd.svelte";
  import EditDrawer from "/src/lib/ui/EditDrawer.svelte";

  const log = window.controlsLog;
  let mode = $state("review");
  let deck = $state("bio");
  let notebook = $state("");
  let zone = $state("UTC");
  let popover = $state(false);
  let drawer = $state(false);
  let quick = $state("");
  const decks = [
    { value: "all", label: "All decks", count: 41 },
    { value: "bio", label: "Biology", color: "var(--c1)", count: 12 },
    { value: "chem", label: "Organic chemistry", color: "var(--c2)", count: 7 },
    { value: "kanji", label: "Kanji · week 5", color: "var(--c3)", count: 9 },
    { value: "hash", label: "Hashing", color: "var(--c4)", count: 4 },
    { value: "alpha", label: "Alphabet", color: "var(--c5)" },
    { value: "none", label: "Unassigned", disabled: true },
  ];
  const modes = [
    { value: "review", label: "Scheduled" },
    { value: "practice", label: "Practice" },
    { value: "cram", label: "Cram" },
  ];
</script>

<div class="harness">
  <section class="surface" aria-label="Region">
    <div class="surface-section">
      <div class="section-head"><span class="eyebrow">Eyebrow</span></div>
      <h1>Page title</h1>
      <h2>Section heading</h2>
      <span class="br-section-title">Section title</span>
      <label class="br-field__label" for="probe-input">Field label</label>
      <div class="panel nested" aria-label="Nested panel">Nested panel</div>
    </div>
    <div class="surface-section">
      <details class="fold"><summary>Fold-out label</summary><p>Inside</p></details>
    </div>
  </section>

  <div class="row" aria-label="Controls">
    <Button>Default</Button>
    <Button size="sm">Small</Button>
    <Button variant="ghost">Ghost</Button>
    <Button size="lg" variant="solid">Primary page action</Button>
    <button class="pill primary page-action">Page action</button>
    <button class="pill">Pill</button>
    <button class="pill primary">Pill primary</button>
    <button class="button">Button</button>
    <button class="button" aria-pressed="true">Toggle</button>
    <button class="icon-button" aria-label="Icon">×</button>
    <input id="probe-input" placeholder="Input" />
    <input type="search" placeholder="Search" />
    <input type="date" aria-label="Date" />
  </div>

  <div class="row">
    <Select label="Study mode" options={modes} bind:value={mode} onchange={(v) => log.push(["mode", v])} />
    <Select label="Deck" options={decks} bind:value={deck} onchange={(v) => log.push(["deck", v])} />
    <Select label="Notebook" kind="search" placeholder="Choose notebook" options={decks.slice(1, 6)} bind:value={notebook}
      oncreate={(q) => log.push(["create", q])} createLabel={(q) => `New notebook “${q}”`} />
    <Select label="Time zone" kind="timezone" bind:value={zone} />
    <span class="out" data-testid="values">{mode}|{deck}|{notebook}|{zone}</span>
  </div>

  <div class="row clipped">
    <span class="eyebrow">Header</span>
    <OverflowMenu items={[
      { label: "Import…", onselect: () => log.push(["menu", "import"]) },
      { label: "Export", kbd: "Ctrl E", onselect: () => log.push(["menu", "export"]) },
      { separator: true },
      { label: "Archived", disabled: true, onselect: () => log.push(["menu", "archived"]) },
      { label: "Delete", danger: true, onselect: () => log.push(["menu", "delete"]) },
    ]} />
    <Popover bind:open={popover} label="Reminder">
      {#snippet trigger(props)}<button class="pill" {...props}>Reminder</button>{/snippet}
      <label class="br-field__label">When<input data-autofocus placeholder="tomorrow 9am" /></label>
      <Select label="Repeat" options={[{ value: "no", label: "Once" }, { value: "day", label: "Daily" }, { value: "week", label: "Weekly" }, { value: "month", label: "Monthly" }, { value: "year", label: "Yearly" }]} value="no" />
    </Popover>
  </div>

  <div class="row">
    <StatusDot state="saved" />
    <StatusDot state="unsaved" />
    <StatusDot state="failed" detail="Couldn't save: offline" />
  </div>

  <div class="row">
    <QuickAdd label="Add exam" placeholder="Orgo quiz Fri 9am" bind:value={quick}
      onsave={(text) => { log.push(["save", text]); }} onmore={(text) => { log.push(["more", text]); drawer = true; }}>
      {#snippet chips()}
        <span class="chip">Fri, Nov 6 · 09:00</span>
        <Select label="Subject" kind="search" options={decks.slice(1, 5)} value="chem" />
      {/snippet}
    </QuickAdd>
    <button class="button" onclick={() => (drawer = true)}>Open exam</button>
  </div>
</div>

{#if drawer}
  <EditDrawer title="Orgo quiz" meta="Organic chemistry · Fri, Nov 6" accent="var(--c2)" onclose={() => (drawer = false)}>
    <label class="br-field__label">Title<input value="Orgo quiz" /></label>
    {#snippet footer()}
      <button class="button" onclick={() => (drawer = false)}>Cancel</button>
      <button class="button primary" onclick={() => { log.push(["drawer", "save"]); drawer = false; }}>Save</button>
    {/snippet}
  </EditDrawer>
{/if}

<style>
  .harness {
    display: grid;
    gap: 16px;
    padding: 16px;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
  }
  /* A clipping, glass ancestor: menus and popovers must still escape it. */
  .clipped {
    overflow: hidden;
    height: 40px;
    backdrop-filter: blur(2px);
  }
  .chip {
    font-size: 12px;
  }
</style>
