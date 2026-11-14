<script lang="ts">
  import type { Bootstrap, ModuleId } from "../api";
  import { buildStore, setPreferences } from "../build.svelte";
  import { megabytes, moduleObtainable, totals } from "../modulePicker";
  import { moduleStore } from "../modulePicker.svelte";
  import { activeModules } from "../modules.svelte";
  import type { HomeLayout } from "./layout";
  import { templatePicker } from "./templatePicker.svelte";
  import { TEMPLATES, arrangement, missingLine, missingModules, suggestTemplate, suggestionLine, type Template } from "./templates";

  /**
   * Rika's template offer, at the top of home's Rika panel: one suggestion, once ("Not now"
   * or using one ends it; `ui.home.template_offered`), the other three behind "Other
   * templates". Using one only arranges home. The modules it lacks are then offered with a
   * button each; nothing downloads or restarts without a click.
   */
  let { counts }: { counts?: Bootstrap["counts"] } = $props();

  const home = $derived(buildStore.build.ui.home);
  const signals = $derived({ enabled: activeModules.enabled, counts });
  const suggestion = $derived(suggestTemplate(signals));
  let applied = $state<Template | null>(null);
  let previous: HomeLayout | null = null;
  let listing = $state(false);
  let turnedOn = $state(false);
  let failure = $state("");
  const visible = $derived(applied !== null || templatePicker.open || (buildStore.loaded && !home.template_offered));

  const available = $derived(moduleStore.modules?.available ?? []);
  const missing = $derived(applied ? missingModules(applied, moduleStore.modules?.configured ?? activeModules.enabled) : []);
  const labelOf = (id: ModuleId) => available.find((item) => item.id === id)?.label ?? id;

  async function use(template: Template) {
    const before = { columns: home.columns, hidden: home.hidden };
    if (!(await setPreferences(arrangement(template)))) return;
    previous = before;
    applied = template;
    listing = false;
    templatePicker.open = false;
    turnedOn = false;
    failure = "";
    void moduleStore.load();
    void moduleStore.loadPacks();
  }
  async function notNow() {
    listing = false;
    templatePicker.open = false;
    if (!home.template_offered) await setPreferences({ ui: { home: { template_offered: true } } });
  }
  async function undo() {
    if (previous && (await setPreferences({ ui: { home: previous } }))) applied = null;
  }
  async function turnOn(id: ModuleId) {
    failure = "";
    try {
      await moduleStore.enable(id);
      turnedOn = true;
    } catch (cause) {
      failure = (cause as Error).message;
    }
  }
</script>

{#if visible}
  <section class="template" aria-label="Home template">
    {#if applied}
      <p>{applied.name} is arranged. {missingLine(missing.map(labelOf))}</p>
      <div class="choices">
        {#each missing as id (id)}
          {@const item = available.find((entry) => entry.id === id)}
          {@const download = item ? totals([item], [id], moduleStore.packs) : null}
          <button type="button" class="pill" disabled={moduleStore.busy || !item || !moduleObtainable(item, moduleStore.packs)}
            title={item && !moduleObtainable(item, moduleStore.packs) ? (moduleStore.packs?.index.error ?? "Its download is not available") : undefined}
            onclick={() => turnOn(id)}>Turn on {labelOf(id)}{#if download?.downloads.length}<span class="size">{megabytes(download.bytes)}</span>{/if}</button>
        {/each}
        {#if turnedOn && moduleStore.modules?.restart_required && !moduleStore.busy}
          {#if moduleStore.restarting}<span class="quiet" role="status">Restarting…</span>
          {:else if moduleStore.desktop}<button type="button" class="pill" onclick={() => moduleStore.restart()}>Restart now</button>
          {:else}<span class="quiet" role="status">Restart rika! to finish</span>{/if}
        {/if}
        <button type="button" class="text-button" disabled={moduleStore.busy} onclick={undo}>Undo</button>
        <button type="button" class="text-button" onclick={() => (applied = null)}>Done</button>
      </div>
      {#if moduleStore.progress}<p class="quiet" role="status">Downloading {megabytes(moduleStore.progress.received)} of {megabytes(moduleStore.progress.total)}</p>{/if}
      {#if failure}<p class="quiet" role="alert">{failure}</p>{/if}
    {:else if listing || home.template_offered}
      <div class="choices">
        {#each TEMPLATES as template (template.id)}
          <button type="button" class="pill" onclick={() => use(template)}>{template.name}</button>
        {/each}
        <button type="button" class="text-button" onclick={notNow}>Not now</button>
      </div>
    {:else}
      <p>{suggestionLine(suggestion, signals)}</p>
      <div class="choices">
        <button type="button" class="pill primary" onclick={() => use(suggestion)}>Use {suggestion.name}</button>
        <button type="button" class="text-button" onclick={notNow}>Not now</button>
        <button type="button" class="text-button" onclick={() => (listing = true)}>Other templates</button>
      </div>
    {/if}
  </section>
{/if}

<style>
  /* Rika speaking: a rule down the left, no box. */
  .template {
    flex: none;
    display: grid;
    gap: 10px;
    padding: 2px 0 2px 14px;
    border-left: 2px solid var(--ink);
  }
  p {
    margin: 0;
    font-size: 13px;
    line-height: 1.45;
    overflow-wrap: anywhere;
  }
  .choices {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 14px;
  }
  .size {
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .quiet {
    color: var(--muted);
    font-size: 12px;
  }
  .text-button {
    color: var(--muted);
  }
</style>
