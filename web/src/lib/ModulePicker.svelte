<script lang="ts">
  import { untrack } from "svelte";
  import { Button } from "@nunera/bracket";
  import type { ModuleId, ModulesState } from "./api";
  import { buildStore } from "./build.svelte";
  import { TEMPLATES, wantedModules } from "./home/templates";
  import { megabytes, moduleObtainable, packsOf, percent, preselect, sameSelection, totalLine, totals, withoutDownload } from "./modulePicker";
  import { moduleStore as store } from "./modulePicker.svelte";

  /**
   * The one module picker: a switch, a name and what you do with it per module, the size on
   * those that download and a total. Saving downloads what the selection lacks, then saves
   * it. `question` is the first run: plain answers preselect modules and only the chosen
   * ones show until "Show all"; `fresh` starts from every module without a download instead
   * of the saved selection. Settings → Modules mounts it bare and adds "Restart now".
   */
  let { question = false, fresh = false, overrides = $bindable({}), onsaved }: {
    question?: boolean;
    fresh?: boolean;
    /** The switches the user set, over the starting selection (kept by the owner across remounts). */
    overrides?: Partial<Record<ModuleId, boolean>>;
    onsaved: (state: ModulesState) => void | Promise<void>;
  } = $props();
  const uid = $props.id();

  let answers = $state<string[]>([]);
  let showAll = $state(false);
  let failure = $state("");
  const available = $derived(store.modules?.available ?? []);
  const configured = $derived(store.modules?.configured ?? []);
  const base = $derived(fresh ? withoutDownload(available, store.packs) : configured);
  const selection = $derived(preselect(available, store.packs, base, wantedModules(answers), overrides));
  const total = $derived(totals(available, selection, store.packs));
  const dirty = $derived(!!store.modules && !sameSelection(selection, configured));
  // A row the user switched stays in sight even once it is off.
  const rows = $derived(question && !showAll ? available.filter((item) => selection.includes(item.id) || item.id in overrides) : available);

  // The build's modules can change from anywhere (an install, a rollback, an outside editor).
  const buildModules = $derived(buildStore.build.modules.join(","));
  $effect(() => {
    void buildModules;
    untrack(() => { if (!store.busy) void store.load(); });
  });
  $effect(() => untrack(() => void store.loadPacks()));
  // A download started elsewhere (another window, before a reload) still shows its progress.
  $effect(() => {
    if (!store.downloading || store.busy) return;
    const timer = setInterval(() => void store.loadPacks(), 500);
    return () => clearInterval(timer);
  });

  function answer(id: string) {
    const template = TEMPLATES.find((item) => item.id === id)!;
    answers = answers.includes(id) ? answers.filter((item) => item !== id) : [...answers, id];
    // An answer speaks for its modules again, whatever their switches said.
    overrides = Object.fromEntries(Object.entries(overrides).filter(([module]) => !template.modules.includes(module as ModuleId)));
  }
  async function save() {
    if (!store.modules || store.busy) return;
    failure = "";
    try {
      const saved = dirty || total.downloads.length ? await store.apply(selection) : store.modules;
      if (!fresh) { overrides = {}; answers = []; }
      await onsaved(saved);
    } catch (cause) {
      failure = `Could not save modules: ${(cause as Error).message}`;
    }
  }
</script>

<div class="module-picker">
  {#if question}
    <div class="uses" role="group" aria-label="What will you use rika! for?">
      {#each TEMPLATES as template (template.id)}
        <button type="button" class="pill" aria-pressed={answers.includes(template.id)} disabled={store.busy} onclick={() => answer(template.id)}>{template.use}</button>
      {/each}
    </div>
  {/if}
  {#if store.modules}
    <fieldset class="rows" disabled={store.busy}>
      <legend class="sr-only">Modules</legend>
      {#each rows as item (item.id)}
        {@const on = selection.includes(item.id)}
        {@const saved = configured.includes(item.id)}
        {@const locked = !on && !moduleObtainable(item, store.packs)}
        <div class="row" data-module={item.id}>
          <button type="button" class="switch" role="switch" id={`${uid}-${item.id}`} aria-checked={on} aria-label={item.label}
            aria-describedby={`${uid}-${item.id}-about`} disabled={locked}
            title={locked ? (store.packs?.index.error ?? `${item.label}'s download is not available`) : undefined}
            onclick={() => (overrides = { ...overrides, [item.id]: !on })}><i></i></button>
          <label class="name" for={`${uid}-${item.id}`}>{item.label}</label>
          <span class="about" id={`${uid}-${item.id}-about`} title={item.description}>{item.description}</span>
          <span class="end">
            {#if !question && saved !== store.modules.enabled.includes(item.id)}
              <span class="pending" title={saved ? "Starts after rika! restarts" : "Stops after rika! restarts"}>Restart</span>
            {/if}
            {#if item.packs.length && !store.packs}<span class="quiet">{store.packsError ? "Not available" : "Checking…"}</span>{/if}
            {#each packsOf(item, store.packs) as pack (pack.id)}
              {#if pack.state === "downloading" && pack.progress}
                {@const done = percent(pack.progress)}
                <span class="bar" role="progressbar" aria-label={`${pack.label} download`} aria-valuemin={0} aria-valuemax={100}
                  aria-valuenow={done} aria-valuetext={`${megabytes(pack.progress.received)} of ${megabytes(pack.progress.total)}`}><span style:width={`${done}%`}></span></span>
                <output>{done}%</output>
              {:else if pack.state === "available" || pack.state === "outdated"}
                <span class="size" title={`${pack.label} downloads when you ${question ? "continue" : "save"}`}>{megabytes(pack.size)}</span>
              {:else if pack.state === "unavailable"}
                <span class="quiet" title={store.packs?.index.error}>Not available</span>
              {:else if pack.state === "installed" && !question}
                <Button size="sm" variant="ghost" disabled={store.busy || on || saved}
                  title={on || saved ? `Turn ${item.label} off and save before removing it` : `Frees ${megabytes(pack.installed_size)}`}
                  aria-label={`Remove ${pack.label} download`} onclick={() => store.remove(pack.id)}>Remove</Button>
              {/if}
            {/each}
          </span>
        </div>
      {/each}
    </fieldset>
    {#if question}
      <button type="button" class="text-button more" aria-expanded={showAll} onclick={() => (showAll = !showAll)}>{showAll ? "Show only chosen" : `Show all ${available.length} modules`}</button>
    {/if}
    {#if failure}<p class="note" role="alert">{failure}</p>{/if}
    {#if store.packsError}<p class="note" role="alert">{store.packsError}</p>
    {:else if store.packs?.index.error && available.some((item) => packsOf(item, store.packs).some((pack) => pack.state !== "built-in" && pack.state !== "installed"))}
      <p class="note" role="status">Downloads: {store.packs.index.error}.</p>
    {/if}
    {#if store.modules.error}<p class="note" role="alert">Saved selection unreadable ({store.modules.error}). Saving replaces it.</p>{/if}
    {#if !question && store.modules.restart_required && !dirty}
      <div class="restart" role="status">
        <span>{store.restarting ? "Restarting…" : "Restart rika! to finish"}</span>
        {#if store.desktop && !store.restarting}<Button size="sm" onclick={() => store.restart()}>Restart now</Button>{/if}
      </div>
    {/if}
    <div class="foot">
      <span class="total" role="status">
        {#if store.progress}Downloading {megabytes(store.progress.received)} of {megabytes(store.progress.total)}
        {:else if store.busy}Saving…
        {:else}{totalLine(total)}{/if}
      </span>
      <span class="actions">
        {#if question}
          <Button variant="solid" intent="primary" disabled={store.busy} onclick={save}>Continue</Button>
        {:else}
          <Button size="sm" variant="ghost" disabled={!dirty || store.busy} onclick={() => (overrides = {})}>Discard</Button>
          <Button intent="primary" size="sm" disabled={!(dirty || total.downloads.length) || store.busy} onclick={save}>Save</Button>
        {/if}
      </span>
    </div>
  {:else if !store.error}<p class="note" role="status">Loading modules…</p>{/if}
  {#if store.error}<p class="note" role="alert">{store.error}</p>{/if}
</div>

<style>
  /* Rows reach the edges of a settings panel; anywhere else the picker has no gutter to cross. */
  .module-picker {
    --gutter: var(--br-panel-p, 0px);
    display: grid;
    min-width: 0;
    font-size: 13px;
  }
  .uses {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 14px;
  }
  /* An answer not chosen is still a control: a hairline outline, no fill. */
  :global(:root body) .uses .pill {
    border: 1px solid var(--border);
    background: transparent;
  }
  :global(:root body) .uses .pill[aria-pressed="true"] {
    border-color: var(--ink);
    background: var(--ink);
    color: var(--solid-void, #141414);
  }
  .rows {
    display: grid;
    min-width: 0;
    margin: 0;
    padding: 0;
    border: 0;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    min-height: 44px;
    margin: 0 calc(-1 * var(--gutter));
    padding: 6px var(--gutter);
    border-top: 1px solid var(--hairline);
  }
  .rows > .row:first-of-type {
    border-top: 0;
  }
  .uses + .rows > .row:first-of-type {
    border-top: 1px solid var(--hairline);
  }
  .name {
    flex-shrink: 0;
    min-width: 118px;
    color: var(--ink);
    font-weight: 500;
    cursor: pointer;
  }
  /* Wraps to a second line rather than cutting a sentence short. */
  .about {
    min-width: 0;
    color: var(--muted);
    line-height: 1.45;
  }
  .end {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    margin-left: auto;
    white-space: nowrap;
  }
  .size,
  .quiet,
  output {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .pending {
    color: var(--warning);
    font-size: 12px;
    cursor: help;
  }
  .switch {
    position: relative;
    flex-shrink: 0;
    width: 32px;
    height: 18px;
    padding: 0;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 8%, transparent);
    cursor: pointer;
    transition: background-color 160ms;
  }
  .switch i {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 12px;
    height: 12px;
    border-radius: 999px;
    background: var(--muted);
    transition: transform 160ms, background-color 160ms;
  }
  .switch[aria-checked="true"] {
    background: color-mix(in srgb, var(--accent) 45%, transparent);
  }
  .switch[aria-checked="true"] i {
    transform: translateX(14px);
    background: var(--ink);
  }
  .switch:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .switch:disabled {
    opacity: 0.55;
    cursor: default;
  }
  /* A pack's download progress: the goals' measure bar. */
  .bar {
    width: 96px;
    height: 5px;
    overflow: hidden;
    border-radius: 999px;
    background: color-mix(in srgb, var(--ink) 10%, transparent);
  }
  .bar span {
    display: block;
    height: 100%;
    border-radius: inherit;
    background: var(--accent);
    transition: width var(--motion-ms, 200ms) ease-out;
  }
  .more {
    justify-self: start;
    margin-top: 6px;
    color: var(--ink);
  }
  .note {
    margin: 8px 0 0;
    color: var(--muted);
    font-size: 12px;
  }
  .restart,
  .foot {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    min-height: 44px;
    margin: 0 calc(-1 * var(--gutter));
    padding: 6px var(--gutter);
    border-top: 1px solid var(--hairline);
  }
  .restart {
    margin-top: 8px;
    color: var(--ink);
  }
  .foot {
    margin-top: 8px;
  }
  .restart + .foot {
    margin-top: 0;
  }
  .total {
    min-width: 0;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .actions {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }
  @media (prefers-reduced-motion: reduce) {
    .switch,
    .switch i,
    .bar span {
      transition: none;
    }
  }
</style>
