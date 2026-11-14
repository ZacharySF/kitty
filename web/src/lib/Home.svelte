<script lang="ts">
  import HomeAgents from "./HomeAgents.svelte";
  import HomeQuickNote from "./HomeQuickNote.svelte";
  import HomeUpNext from "./HomeUpNext.svelte";
  import TodayAgenda from "./TodayAgenda.svelte";
  import type { Bootstrap } from "./api";
  import { buildStore } from "./build.svelte";
  import { resolveHome } from "./home/layout";
  import TemplateSuggestion from "./home/TemplateSuggestion.svelte";
  import { activeModules } from "./modules.svelte";
  import RikaPanel from "./rika/RikaPanel.svelte";
  import { rika } from "./rika/session.svelte";
  import type { View } from "./workspace";
  import { useWorkspaceContext } from "./workspaceContext.svelte";

  /**
   * Home (design review): less going on. Today and a quick note on the left, what is up next
   * beside them, Rika filling the right-hand column. The morning brief lives in Rika's panel,
   * sections in search (Ctrl K); agents show only while a session runs. With every module off,
   * one quiet line says so. Which widgets show, and where, is the build's `ui.home`
   * (home/layout.ts); a template (home/templates.ts) rearranges it from Rika's panel.
   */
  let { revision, counts, onview }: { revision: number; counts?: Bootstrap["counts"]; onview: (view: View) => unknown } = $props();
  const pane = useWorkspaceContext();
  const homeId = `home-${pane.id}`;

  const enabled = $derived(activeModules.enabled);
  const columns = $derived(resolveHome(buildStore.build.ui.home, enabled));

  /** "/" asks Rika. */
  export function focusSearch() {
    if (pane.active) rika.showDrawer();
  }
  /** "Open module settings" lands on Settings' Modules page (`#settings/modules`). */
  function openModuleSettings() {
    onview("settings");
    if (location.hash === "#settings") history.replaceState(history.state, "", "#settings/modules");
  }
</script>

<div class="home" data-tutorial="home">
  <div class="board" role="region" aria-label="Home widgets">
    {#each columns as column, index (index)}
      <div class="column">
        {#each column as widget (widget)}
          {#if widget === "agenda"}<TodayAgenda id={`${homeId}-agenda`} {revision} {onview} />
          {:else if widget === "note"}<HomeQuickNote id={`${homeId}-note`} {onview} />
          {:else if widget === "next"}<HomeUpNext id={`${homeId}-next`} {revision} {onview} />
          {:else}<HomeAgents id={`${homeId}-agents`} {onview} />{/if}
        {/each}
      </div>
    {/each}
    {#if !enabled.length}
      <p class="quiet">Modules are off. <button type="button" class="text-button" onclick={openModuleSettings}>Turn some on</button></p>
    {/if}
  </div>
  <RikaPanel id={`${homeId}-rika`} active={pane.active}>
    {#snippet lead()}<TemplateSuggestion {counts} />{/snippet}
  </RikaPanel>
</div>

<style>
  /* Fills the room App gives it (zero basis: a widget never sizes the row); the board and
     Rika scroll on their own, so the page itself never does. Side margins match main's. */
  .home {
    --rika-column: clamp(340px, 30vw, 460px);
    flex: 1 1 0px;
    min-height: 120px;
    width: 100%;
    max-width: 1560px;
    margin-inline: auto;
    padding: 12px 26px 0;
    box-sizing: border-box;
    display: grid;
    grid-template-columns: minmax(0, 1fr) var(--rika-column);
    grid-template-rows: minmax(0, 1fr);
    gap: 48px;
  }
  .board {
    min-width: 0;
    min-height: 0;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr));
    align-content: start;
    gap: 20px 48px;
    padding-right: 4px;
    overflow-y: auto;
    overscroll-behavior: contain;
    scrollbar-width: thin;
    scrollbar-color: var(--hairline) transparent;
  }
  .column {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 28px;
  }
  .quiet {
    margin: 12px 0 0;
    color: var(--muted);
    font-size: 16px;
  }
  /* Phone widths stack, Rika first; the page scrolls. The desktop minimum window (760×500)
     stays side by side even when display scaling lays it out narrower in CSS pixels. */
  @media (max-width: 599.98px) {
    .home {
      flex: none;
      grid-template-columns: minmax(0, 1fr);
      grid-template-rows: none;
    }
    .home > :global(.rika-panel) {
      order: -1;
      height: 70vh;
      min-height: 360px;
    }
    .board {
      overflow: visible;
    }
  }
</style>
