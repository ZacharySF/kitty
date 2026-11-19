<script lang="ts">
  import { buildStore } from "../build.svelte";
  import HomeWidget from "../home/HomeWidget.svelte";
  import { spareWidgets } from "../home/layout";
  import { activeModules } from "../modules.svelte";
  import type { View } from "../workspace";
  import { pageDraft } from "./draft.svelte";
  import { widgetsFor } from "./widgets";

  /**
   * The home widgets placed on a page (the build's `ui.pages.widgets`, put there in page
   * edit mode): the last child of the page's view panel, a layer without a size whose panes
   * stand at their saved place over the page. Home has its own board; safe mode draws none,
   * and a widget whose modules are off waits in the build.
   */
  let { view, idPrefix, revision, editing, onview }: {
    view: View;
    idPrefix: string;
    revision: number;
    /** Page edit mode is arranging this page: the widgets hold still. */
    editing: boolean;
    onview: (view: View) => unknown;
  } = $props();

  const drawable = $derived(spareWidgets([], activeModules.enabled));
  const placed = $derived(view === "today" || buildStore.safe ? []
    : widgetsFor(pageDraft.widgets ?? buildStore.build.ui.pages.widgets, view).flatMap((item) => {
      const widget = drawable.find((known) => known.id === item.widget);
      return widget ? [{ ...item, id: widget.id }] : [];
    }));
</script>

<div class="page-widgets" class:editing>
  {#each placed as item (item.id)}
    <div class="page-widget" data-page-widget={item.id} style:left={`${item.x}px`} style:top={`${item.y}px`} style:width={`${item.w}px`}>
      <div class="content" inert={editing}>
        <HomeWidget widget={item.id} {idPrefix} {revision} {onview} placeholder={editing} />
      </div>
    </div>
  {/each}
</div>

<style>
  .page-widgets {
    position: absolute;
    top: 0;
    left: 0;
    width: 0;
    height: 0;
  }
  /* A pane of the page's own colour with a hairline round it; the widget's top rule goes, the pane is its frame. */
  .page-widget {
    position: absolute;
    z-index: 5;
    box-sizing: border-box;
    padding: 2px 20px 16px;
    border: 1px solid var(--hairline);
    border-radius: 16px;
    background: var(--solid-void, #000);
  }
  .page-widget :global(.home-widget) {
    border-top-color: transparent;
  }
  /* A widget with nothing to show right now (Agents with no session) keeps no empty pane. */
  .page-widgets:not(.editing) .page-widget:not(:has(:global(.home-widget))) {
    display: none;
  }
</style>
