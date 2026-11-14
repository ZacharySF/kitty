<script lang="ts">
  import { tick } from "svelte";
  import HomeAgents from "./HomeAgents.svelte";
  import HomeFocus from "./HomeFocus.svelte";
  import HomeQuickNote from "./HomeQuickNote.svelte";
  import HomeUpNext from "./HomeUpNext.svelte";
  import TodayAgenda from "./TodayAgenda.svelte";
  import type { Bootstrap } from "./api";
  import { buildStore, setPreferences } from "./build.svelte";
  import { HOME_WIDGETS, MAX_COLUMNS, placeWidget, resolveHome, savedHome, spareWidgets, type HomeWidgetId } from "./home/layout";
  import { homeEditor } from "./home/templatePicker.svelte";
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
   * Edit (under the widgets, or "Edit home" in search) unlocks them: drag one by its
   * handle (or move it with the arrow keys), Remove it, or add one from the library that
   * takes Rika's place until Done, which saves the arrangement.
   */
  let { revision, counts, onview }: { revision: number; counts?: Bootstrap["counts"]; onview: (view: View) => unknown } = $props();
  const pane = useWorkspaceContext();
  const homeId = `home-${pane.id}`;

  const enabled = $derived(activeModules.enabled);
  const saved = $derived(resolveHome(buildStore.build.ui.home, enabled));
  /** The arrangement while editing; null when home is locked. */
  let draft = $state<HomeWidgetId[][] | null>(null);
  const editing = $derived(draft !== null);
  const columns = $derived(draft ?? saved);
  const spare = $derived(draft ? spareWidgets(draft, enabled) : []);
  let dragged = $state<HomeWidgetId | null>(null);
  let failure = $state("");
  const titleOf = (id: HomeWidgetId) => HOME_WIDGETS.find((widget) => widget.id === id)!.title;

  $effect(() => {
    if (homeEditor.on && !draft) draft = saved.map((column) => [...column]);
  });
  async function done() {
    failure = "";
    // Plain copies: the draft and the build are reactive proxies, which the save cannot clone.
    const next = savedHome($state.snapshot(draft) ?? [], $state.snapshot(buildStore.build.ui.home), enabled);
    if (!(await setPreferences({ ui: { home: next } }))) {
      failure = "Could not save the arrangement.";
      return;
    }
    draft = null;
    homeEditor.on = false;
  }
  function place(id: HomeWidgetId, column: number, before?: HomeWidgetId) {
    if (!draft) return;
    const next = placeWidget(draft, id, column, before);
    if (JSON.stringify(next) !== JSON.stringify(draft)) draft = next;
  }
  /** Dragging over a widget: the dragged one lands above or below it, by which half the pointer is in. */
  function over(event: DragEvent, column: number, index: number) {
    if (!dragged || !draft) return;
    event.preventDefault();
    event.stopPropagation();
    const target = draft[column][index];
    if (target === dragged) return;
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const below = event.clientY > box.top + box.height / 2;
    const others = draft[column].filter((id) => id !== dragged);
    place(dragged, column, below ? others[others.indexOf(target) + 1] : target);
  }
  /** Arrow keys on a handle: up and down within the column, left and right across columns. */
  function nudge(event: KeyboardEvent, id: HomeWidgetId, column: number, index: number) {
    if (!draft || !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const items = draft[column];
    if (event.key === "ArrowUp" && index > 0) place(id, column, items[index - 1]);
    else if (event.key === "ArrowDown" && index < items.length - 1) place(id, column, items[index + 2]);
    else if (event.key === "ArrowLeft" && column > 0) place(id, column - 1);
    else if (event.key === "ArrowRight" && column < MAX_COLUMNS - 1) place(id, column + 1);
    else return;
    // A move across columns redraws the widget: its handle takes the focus back.
    void tick().then(() => document.querySelector<HTMLElement>(`[data-handle="${id}"]`)?.focus());
  }
  /** A new widget joins the shortest column. */
  function add(id: HomeWidgetId) {
    if (!draft) return;
    const shortest = draft.length ? draft.reduce((best, column, index) => (column.length < draft![best].length ? index : best), 0) : 0;
    place(id, shortest);
  }

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
  <div class="board" class:editing role="region" aria-label="Home widgets">
    {#each columns as column, index (index)}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="column" ondragover={(event) => { if (dragged) { event.preventDefault(); place(dragged, index); } }}>
        {#each column as widget, row (widget)}
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <div class="slot" class:dragged={dragged === widget} draggable={editing}
            ondragstart={(event) => { dragged = widget; event.dataTransfer?.setData("text/plain", widget); if (event.dataTransfer) event.dataTransfer.effectAllowed = "move"; }}
            ondragend={() => (dragged = null)} ondragover={(event) => over(event, index, row)} ondrop={(event) => event.preventDefault()}>
            {#if editing}
              <div class="tools">
                <button type="button" class="handle" data-handle={widget} aria-label={`Move ${titleOf(widget)}: drag, or use the arrow keys`}
                  onkeydown={(event) => nudge(event, widget, index, row)}>
                  <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M5 3.5h.01M5 8h.01M5 12.5h.01M11 3.5h.01M11 8h.01M11 12.5h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg>
                </button>
                <button type="button" class="text-button" onclick={() => draft && (draft = draft.map((items) => items.filter((id) => id !== widget)))}>Remove</button>
              </div>
            {/if}
            <div class="content" inert={editing}>
              {#if widget === "agenda"}<TodayAgenda id={`${homeId}-agenda`} {revision} {onview} />
              {:else if widget === "note"}<HomeQuickNote id={`${homeId}-note`} {onview} />
              {:else if widget === "next"}<HomeUpNext id={`${homeId}-next`} {revision} {onview} />
              {:else if widget === "focus"}<HomeFocus id={`${homeId}-focus`} />
              {:else}<HomeAgents id={`${homeId}-agents`} {onview} placeholder={editing} />{/if}
            </div>
          </div>
        {/each}
      </div>
    {/each}
    {#if editing && columns.length < MAX_COLUMNS}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="column spare" ondragover={(event) => { if (dragged) { event.preventDefault(); place(dragged, columns.length); } }}></div>
    {/if}
    {#if !enabled.length}
      <p class="quiet">Modules are off. <button type="button" class="text-button" onclick={openModuleSettings}>Turn some on</button></p>
    {:else if !editing}
      <p class="edit"><button type="button" class="text-button" onclick={() => (homeEditor.on = true)}>Edit</button></p>
    {/if}
  </div>
  {#if editing}
    <aside class="library" aria-label="Widget library">
      <header><h3>Widgets</h3><button type="button" class="pill primary" onclick={done}>Done</button></header>
      {#if spare.length}
        <ul>
          {#each spare as widget (widget.id)}
            <li><span>{widget.title}</span><button type="button" class="text-button" onclick={() => add(widget.id)}>Add</button></li>
          {/each}
        </ul>
      {:else}<p class="quiet">Every widget is on home.</p>{/if}
      {#if failure}<p class="quiet" role="alert">{failure}</p>{/if}
    </aside>
  {:else}
    <RikaPanel id={`${homeId}-rika`} active={pane.active}>
      {#snippet lead()}<TemplateSuggestion {counts} />{/snippet}
    </RikaPanel>
  {/if}
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
  /* Edit mode: each widget gains a handle row and stops taking clicks; a dashed rule marks a free column. */
  .board.editing .column {
    min-height: 120px;
  }
  .column.spare {
    border-top: 1px dashed var(--hairline);
  }
  .slot.dragged {
    opacity: 0.4;
  }
  .board.editing .slot {
    cursor: grab;
  }
  .tools {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: 6px;
  }
  .handle {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    margin-left: -6px;
    padding: 0;
    border: 0;
    border-radius: 8px;
    background: none;
    color: var(--muted);
    cursor: grab;
  }
  .handle:hover,
  .handle:focus-visible {
    color: var(--ink);
  }
  .board.editing .content {
    opacity: 0.55;
    pointer-events: none;
  }
  .edit {
    grid-column: 1 / -1;
    margin: 0;
  }
  .library {
    min-width: 0;
    border-top: 1px solid var(--hairline);
    padding-top: 4px;
  }
  .library header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 0 12px;
  }
  .library h3 {
    margin: 0;
    font: 600 22px/1.2 var(--font-ui);
    letter-spacing: -0.01em;
  }
  .library ul {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .library li {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    padding: 10px 0;
    border-top: 1px solid var(--hairline);
    font-size: 17px;
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
