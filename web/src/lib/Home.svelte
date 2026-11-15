<script lang="ts">
  import { tick } from "svelte";
  import HomeAgents from "./HomeAgents.svelte";
  import HomeCards from "./HomeCards.svelte";
  import Icon from "./Icon.svelte";
  import HomeClock from "./HomeClock.svelte";
  import HomeFocus from "./HomeFocus.svelte";
  import HomeGoals from "./HomeGoals.svelte";
  import HomePipeline from "./HomePipeline.svelte";
  import HomeWeek from "./HomeWeek.svelte";
  import HomeQuickNote from "./HomeQuickNote.svelte";
  import HomeSession from "./HomeSession.svelte";
  import HomeUpNext from "./HomeUpNext.svelte";
  import TodayAgenda from "./TodayAgenda.svelte";
  import type { Bootstrap } from "./api";
  import { buildStore, setPreferences } from "./build.svelte";
  import { HOME_WIDGETS, MAX_COLUMNS, RIKA_LIMITS, WIDTH_LIMITS, columnShares, placeWidget, resolveHome, rikaShare, savedHome, spareWidgets, splitWidths, type HomeWidgetId } from "./home/layout";
  import { homeEditor, templatePicker } from "./home/templatePicker.svelte";
  import { TONES, toneColor, withTone } from "./home/tones";
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
   * takes Rika's place until Done, which saves the arrangement. In edit mode a splitter
   * between the two columns, and one before Rika's column, sets how the width is shared
   * (drag, arrow keys, double-click to reset).
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
  /** The shares while editing (the build's `ui.home.widths` and `ui.home.rika` otherwise). */
  let draftWidths = $state<number[] | null>(null);
  let draftRika = $state<number | null>(null);
  const shares = $derived(columnShares(draftWidths ?? buildStore.build.ui.home.widths, columns.length));
  const rikaWidth = $derived(draftRika ?? buildStore.build.ui.home.rika);
  /** Rika's panel can leave home too: "rika" among the build's hidden widgets. The drawer still opens with Ctrl + J. */
  let draftPanel = $state<boolean | null>(null);
  /** Colour-coding: a tone dragged from the palette onto a widget, or picked there and then pressed onto one. */
  let draftTones = $state<string[] | null>(null);
  const tones = $derived(draftTones ?? buildStore.build.ui.home.tones);
  /** The tone in hand: its id, or "" for the one that clears; null when none is. */
  let toneInHand = $state<string | null>(null);
  let toneOver = $state<string | null>(null);
  function paint(widget: string, tone: string | null = toneInHand) {
    if (tone === null) return;
    draftTones = withTone(tones, widget, tone || null);
  }
  const WIDGET_ICONS: Record<HomeWidgetId, string> = { agenda: "today", note: "quick-notes", focus: "timer", next: "next", agents: "terminal", session: "pulse",
    goals: "goals", week: "week", cards: "flashcards", pipeline: "applications", clock: "clock" };
  const panel = $derived(draftPanel ?? !buildStore.build.ui.home.hidden.includes("rika"));
  let board = $state<HTMLElement>();
  let home = $state<HTMLElement>();
  /** Which splitter the pointer holds. */
  let sizing: "columns" | "rika" | null = null;

  function size(event: PointerEvent) {
    if (sizing === "columns" && board) {
      const box = board.getBoundingClientRect();
      draftWidths = splitWidths((event.clientX - box.left) / box.width);
    } else if (sizing === "rika" && home) {
      const box = home.getBoundingClientRect();
      draftRika = rikaShare((box.right - event.clientX) / box.width);
    }
  }
  function grab(event: PointerEvent, which: "columns" | "rika") {
    sizing = which;
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }
  /** Arrow keys move a splitter one step; Home and End go to its ends. */
  function step(event: KeyboardEvent, which: "columns" | "rika") {
    const direction = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (which === "columns") {
      const now = shares[0];
      const next = event.key === "Home" ? WIDTH_LIMITS.min : event.key === "End" ? WIDTH_LIMITS.max : direction ? now + direction * WIDTH_LIMITS.step : null;
      if (next === null) return;
      draftWidths = splitWidths(next / 2);
    } else {
      const now = rikaWidth || 0.3;
      // Rika's column grows to the left.
      const next = event.key === "Home" ? RIKA_LIMITS.max : event.key === "End" ? RIKA_LIMITS.min : direction ? now - direction * 0.02 : null;
      if (next === null) return;
      draftRika = rikaShare(next);
    }
    event.preventDefault();
  }
  let failure = $state("");
  const titleOf = (id: HomeWidgetId) => HOME_WIDGETS.find((widget) => widget.id === id)!.title;

  $effect(() => {
    if (homeEditor.on && !draft) draft = saved.map((column) => [...column]);
  });
  // Ctrl + Shift + Space (App): the page edit shortcut opens and closes home's own edit mode.
  $effect(() => {
    const toggle = () => {
      if (!pane.active) return;
      if (draft) void done();
      else homeEditor.on = true;
    };
    window.addEventListener("kitty:edit-home", toggle);
    return () => window.removeEventListener("kitty:edit-home", toggle);
  });
  async function done() {
    failure = "";
    // Plain copies: the draft and the build are reactive proxies, which the save cannot clone.
    const next = savedHome($state.snapshot(draft) ?? [], $state.snapshot(buildStore.build.ui.home), enabled);
    const sized = { widths: draftWidths ? $state.snapshot(draftWidths) : $state.snapshot(buildStore.build.ui.home.widths), rika: draftRika ?? buildStore.build.ui.home.rika };
    const hidden = [...next.hidden.filter((id) => id !== "rika"), ...(panel ? [] : ["rika"])];
    if (!(await setPreferences({ ui: { home: { ...next, hidden, ...sized, tones: $state.snapshot(tones) } } }))) {
      failure = "Could not save the arrangement.";
      return;
    }
    draft = null;
    draftWidths = null;
    draftRika = null;
    draftPanel = null;
    draftTones = null;
    toneInHand = null;
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

<div class="home" class:solo={!editing && !panel && !templatePicker.open} data-tutorial="home" bind:this={home} style:--rika-column={rikaWidth ? `${Math.round(rikaWidth * 100)}%` : null}>
  <div class="board" class:editing class:sized={columns.length === 2} bind:this={board} role="region" aria-label="Home widgets"
    style:--board-columns={columns.length === 2 ? `minmax(0, ${shares[0]}fr) minmax(0, ${shares[1]}fr)` : null}>
    {#each columns as column, index (index)}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="column" ondragover={(event) => { if (dragged) { event.preventDefault(); place(dragged, index); } }}>
        {#each column as widget, row (widget)}
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <div class="slot" class:dragged={dragged === widget} class:toned={!!toneColor(tones, widget)} class:tone-over={toneOver === widget} draggable={editing}
            style:--tone={toneColor(tones, widget)}
            ondragstart={(event) => { dragged = widget; event.dataTransfer?.setData("text/plain", widget); if (event.dataTransfer) event.dataTransfer.effectAllowed = "move"; }}
            ondragend={() => (dragged = null)}
            ondragover={(event) => { if (toneInHand !== null && !dragged) { event.preventDefault(); toneOver = widget; } else over(event, index, row); }}
            ondragleave={() => { if (toneOver === widget) toneOver = null; }}
            ondrop={(event) => { event.preventDefault(); if (toneInHand !== null && !dragged) { paint(widget); toneOver = null; toneInHand = null; } }}
            onclick={() => { if (editing && toneInHand !== null) paint(widget); }}>
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
              {:else if widget === "goals"}<HomeGoals id={`${homeId}-goals`} {revision} {onview} />
              {:else if widget === "week"}<HomeWeek id={`${homeId}-week`} {revision} {onview} />
              {:else if widget === "cards"}<HomeCards id={`${homeId}-cards`} {revision} {onview} />
              {:else if widget === "pipeline"}<HomePipeline id={`${homeId}-pipeline`} {revision} {onview} />
              {:else if widget === "clock"}<HomeClock id={`${homeId}-clock`} />
              {:else if widget === "session"}<HomeSession id={`${homeId}-session`} placeholder={editing} />
              {:else}<HomeAgents id={`${homeId}-agents`} {onview} placeholder={editing} />{/if}
            </div>
          </div>
        {/each}
        {#if editing && index === 0 && columns.length === 2}
          <!-- svelte-ignore a11y_no_noninteractive_tabindex a11y_no_noninteractive_element_interactions -->
          <div class="splitter" role="separator" aria-orientation="vertical" aria-label="Width of the two widget columns" tabindex="0"
            aria-valuemin={WIDTH_LIMITS.min * 50} aria-valuemax={WIDTH_LIMITS.max * 50} aria-valuenow={Math.round(shares[0] * 50)}
            onpointerdown={(event) => grab(event, "columns")} onpointermove={size} onpointerup={() => (sizing = null)} onpointercancel={() => (sizing = null)}
            onkeydown={(event) => step(event, "columns")} ondblclick={() => (draftWidths = [1, 1])}></div>
        {/if}
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
    <!-- svelte-ignore a11y_no_noninteractive_tabindex a11y_no_noninteractive_element_interactions -->
    <div class="splitter rika" role="separator" aria-orientation="vertical" aria-label="Width of Rika's column" tabindex="0"
      aria-valuemin={RIKA_LIMITS.min * 100} aria-valuemax={RIKA_LIMITS.max * 100} aria-valuenow={Math.round((rikaWidth || 0.3) * 100)}
      onpointerdown={(event) => grab(event, "rika")} onpointermove={size} onpointerup={() => (sizing = null)} onpointercancel={() => (sizing = null)}
      onkeydown={(event) => step(event, "rika")} ondblclick={() => (draftRika = 0)}></div>
    <aside class="library" aria-label="Widget library">
      <header><h3>Widgets</h3><button type="button" class="pill primary" onclick={done}>Done</button></header>
      {#if spare.length}
        <ul>
          {#each spare as widget (widget.id)}
            <li><span><span class="module-icon"><Icon name={WIDGET_ICONS[widget.id]} size={17} /></span>{widget.title}</span><button type="button" class="text-button" onclick={() => add(widget.id)}>Add</button></li>
          {/each}
        </ul>
      {:else}<p class="quiet">Every widget is on home.</p>{/if}
      <div class="tones" role="group" aria-label="Colours">
        <h4>Colours</h4>
        <div class="swatches">
          {#each [{ id: "", label: "None", color: "" }, ...TONES] as tone (tone.id)}
            <button type="button" class="swatch" class:none={!tone.id} draggable="true" aria-pressed={toneInHand === tone.id} aria-label={tone.label} title={tone.label}
              style:--tone={tone.color || null}
              ondragstart={(event) => { toneInHand = tone.id; event.dataTransfer?.setData("text/plain", tone.label); if (event.dataTransfer) event.dataTransfer.effectAllowed = "copy"; }}
              ondragend={() => { toneInHand = null; toneOver = null; }}
              onclick={() => (toneInHand = toneInHand === tone.id ? null : tone.id)}></button>
          {/each}
        </div>
        <p class="quiet">{toneInHand === null ? "Drag a colour onto a widget." : "Now press a widget."}</p>
      </div>
      <ul>
        <li><span>Rika's panel</span><button type="button" class="text-button" onclick={() => (draftPanel = !panel)}>{panel ? "Remove" : "Add"}</button></li>
      </ul>
      {#if failure}<p class="quiet" role="alert">{failure}</p>{/if}
    </aside>
  {:else if panel || templatePicker.open}
    <!-- With the panel off home, the template offer (the palette's Templates) still shows in it. -->
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
  .home.solo {
    grid-template-columns: minmax(0, 1fr);
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
  /* Two columns share the room as the build says (even unless resized), once both fit side by side. */
  @media (min-width: 1200px) {
    .board.sized {
      grid-template-columns: var(--board-columns);
    }
  }
  /* A splitter: a hairline down the gap with a short grip, wide enough to catch. */
  .home {
    position: relative;
  }
  .board.editing .column {
    position: relative;
  }
  .splitter {
    position: absolute;
    top: 0;
    bottom: 0;
    right: -29px;
    width: 11px;
    cursor: col-resize;
    touch-action: none;
    background:
      linear-gradient(var(--muted), var(--muted)) center / 3px 44px no-repeat,
      linear-gradient(var(--hairline), var(--hairline)) center / 1px 100% no-repeat;
  }
  .splitter.rika {
    top: 12px;
    right: calc(26px + var(--rika-column) + 19px);
  }
  .splitter:hover,
  .splitter:focus-visible {
    outline: none;
    background:
      linear-gradient(var(--ink), var(--ink)) center / 3px 44px no-repeat,
      linear-gradient(var(--muted), var(--muted)) center / 1px 100% no-repeat;
  }
  @media (max-width: 1199.98px) {
    .board .splitter {
      display: none;
    }
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
  /* A colour-coded widget sits on a pane of its tone mixed into the page colour: dark glass
     with a lit top edge, no outline. The widget's own top rule goes; the pane is its frame. */
  /* A widget with nothing to show right now (Agents with no session) keeps no empty pane. */
  .board:not(.editing) .slot.toned:not(:has(:global(.home-widget))) {
    display: none;
  }
  .slot.toned {
    margin: 8px 0;
    padding: 2px 20px 16px;
    border-radius: 22px;
    background:
      linear-gradient(180deg, color-mix(in srgb, var(--ink) 5%, transparent), transparent 42%),
      color-mix(in srgb, var(--tone) 22%, var(--solid-void, #000));
    box-shadow: inset 0 1px 0 color-mix(in srgb, var(--ink) 9%, transparent);
  }
  .slot.toned :global(.home-widget) {
    border-top-color: transparent;
  }
  .slot.tone-over {
    outline: 2px dashed color-mix(in srgb, var(--ink) 55%, transparent);
    outline-offset: 2px;
    border-radius: 18px;
  }
  .tones {
    padding: 14px 0 6px;
    border-top: 1px solid var(--hairline);
  }
  .tones h4 {
    margin: 0 0 12px;
    font: 500 17px/1.2 var(--font-ui);
  }
  .swatches {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }
  :global(:root body) .swatch {
    width: 30px;
    height: 30px;
    min-height: 0;
    padding: 0;
    border: 0;
    border-radius: 999px;
    background:
      linear-gradient(180deg, color-mix(in srgb, var(--ink) 10%, transparent), transparent 55%),
      color-mix(in srgb, var(--tone) 36%, var(--solid-void, #000));
    box-shadow: inset 0 1px 0 color-mix(in srgb, var(--ink) 14%, transparent);
    cursor: grab;
  }
  :global(:root body) .swatch.none {
    background: transparent;
    box-shadow: inset 0 0 0 1.5px color-mix(in srgb, var(--ink) 40%, transparent);
  }
  :global(:root body) .swatch[aria-pressed="true"] {
    outline: 2px solid var(--ink);
    outline-offset: 3px;
  }
  :global(:root body) .swatch:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 3px;
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
