<script lang="ts">
  import HomeAgents from "./HomeAgents.svelte";
  import HomeQuickNote from "./HomeQuickNote.svelte";
  import HomeUpNext from "./HomeUpNext.svelte";
  import HomeWidget from "./HomeWidget.svelte";
  import TodayAgenda from "./TodayAgenda.svelte";
  import HomeBrief from "./goals/HomeBrief.svelte";
  import { morningBrief } from "./goals/brief.svelte";
  import { activeModules } from "./modules.svelte";
  import RikaPanel from "./rika/RikaPanel.svelte";
  import { rika } from "./rika/session.svelte";
  import { navigation, viewEnabled, visibleGroups, type View } from "./workspace";
  import { useWorkspaceContext } from "./workspaceContext.svelte";

  /**
   * Home: the day's widgets on the left, Rika filling the right-hand column. Sections lists
   * every page the active modules provide. With every module off, one quiet line says so.
   */
  let { revision, onview }: { revision: number; onview: (view: View) => unknown } = $props();
  const pane = useWorkspaceContext();
  const homeId = `home-${pane.id}`;

  const enabled = $derived(activeModules.enabled);
  const workspaceTools = $derived(enabled.includes("workspace"));
  const terminal = $derived(enabled.includes("terminal"));
  const upNext = $derived((["calendar", "flashcards", "reminders"] as View[]).some((view) => viewEnabled(view, enabled)));
  $effect(() => morningBrief.start());
  const day = $derived(workspaceTools || !!morningBrief.brief);
  /** Modules on whose pack is not installed (read defensively until every bootstrap carries it). */
  const missing = $derived((activeModules as { missing?: readonly string[] }).missing ?? []);
  const sections = $derived(
    visibleGroups(enabled).map((group) => ({
      label: group.label,
      items: group.views
        .filter((view) => view !== "today" && !(view === "brain" && missing.includes("brain")))
        .map((view) => navigation.find((item) => item.id === view)!),
    })),
  );

  /** "/" asks Rika. */
  export function focusSearch() {
    if (pane.active) rika.showDrawer();
  }
  /** "Open module settings" lands on Settings' Modules page (`#settings/modules`). */
  function openModuleSettings() {
    onview("settings");
    if (location.hash === "#settings") history.replaceState(history.state, "", "#settings/modules");
  }
  function open(event: MouseEvent, view: View) {
    if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onview(view);
  }
</script>

<div class="home" data-tutorial="home">
  <div class="board" role="region" aria-label="Home widgets">
    {#if day}
      <div class="column">
        <HomeBrief id={`${homeId}-brief`} />
        {#if workspaceTools}
          <TodayAgenda id={`${homeId}-agenda`} {revision} {onview} />
          <HomeQuickNote id={`${homeId}-note`} {onview} />
        {/if}
      </div>
    {/if}
    <div class="column">
      {#if upNext}<HomeUpNext id={`${homeId}-next`} {revision} {onview} />{/if}
      {#if terminal}<HomeAgents id={`${homeId}-agents`} {onview} />{/if}
      <div data-tutorial="sections">
        <HomeWidget id={`${homeId}-sections`} title="Sections">
          <nav class="sections" aria-label="Workspace sections">
            {#each sections as group (group.label)}
              <div class="group">
                <h4>{group.label}</h4>
                <ul>
                  {#each group.items as item (item.id)}
                    <li><a href={`#${item.id}`} onclick={(event) => open(event, item.id)}>{item.label}</a></li>
                  {/each}
                </ul>
              </div>
            {/each}
          </nav>
          {#if !enabled.length}
            <p class="quiet">Modules are off. <button type="button" class="text-button" onclick={openModuleSettings}>Turn some on</button></p>
          {/if}
        </HomeWidget>
      </div>
    </div>
  </div>
  <RikaPanel id={`${homeId}-rika`} active={pane.active} />
</div>

<style>
  /* Fills the room App gives it (zero basis: a widget never sizes the row); the board and
     Rika scroll on their own, so the page itself never does. */
  .home {
    --rika-column: clamp(320px, 30vw, 440px);
    flex: 1 1 0px;
    min-height: 120px;
    width: 100%;
    max-width: 1680px;
    margin-inline: auto;
    display: grid;
    grid-template-columns: minmax(0, 1fr) var(--rika-column);
    grid-template-rows: minmax(0, 1fr);
    gap: 28px;
  }
  .board {
    min-width: 0;
    min-height: 0;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr));
    align-content: start;
    gap: 14px 28px;
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
    gap: 14px;
  }
  .sections {
    display: grid;
    gap: 10px;
  }
  .group {
    display: grid;
    gap: 3px;
  }
  h4 {
    margin: 0;
    color: var(--muted);
    font: 500 12px var(--font-ui);
  }
  ul {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 14px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  a {
    color: var(--ink);
    font-size: 13px;
    line-height: 1.6;
    text-decoration: none;
  }
  a:hover {
    color: var(--accent);
  }
  a:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: 3px;
  }
  .quiet {
    margin: 12px 0 0;
    color: var(--muted);
    font-size: 12px;
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
