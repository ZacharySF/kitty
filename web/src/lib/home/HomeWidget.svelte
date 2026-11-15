<script lang="ts">
  import HomeAgents from "../HomeAgents.svelte";
  import HomeCards from "../HomeCards.svelte";
  import HomeClock from "../HomeClock.svelte";
  import HomeFocus from "../HomeFocus.svelte";
  import HomeGoals from "../HomeGoals.svelte";
  import HomePipeline from "../HomePipeline.svelte";
  import HomeQuickNote from "../HomeQuickNote.svelte";
  import HomeSession from "../HomeSession.svelte";
  import HomeUpNext from "../HomeUpNext.svelte";
  import HomeWeek from "../HomeWeek.svelte";
  import TodayAgenda from "../TodayAgenda.svelte";
  import type { View } from "../workspace";
  import type { HomeWidgetId } from "./layout";

  /** One home widget by its id: on home's board, or placed on another page (pageEdit/PageWidgets.svelte). */
  let { widget, idPrefix, revision, onview, placeholder = false }: {
    widget: HomeWidgetId;
    /** Its heading's id is `<idPrefix>-<widget>`. */
    idPrefix: string;
    revision: number;
    onview: (view: View) => unknown;
    /** Edit mode: a widget with nothing to show right now still shows its title. */
    placeholder?: boolean;
  } = $props();
  const id = $derived(`${idPrefix}-${widget}`);
</script>

{#if widget === "agenda"}<TodayAgenda {id} {revision} {onview} />
{:else if widget === "note"}<HomeQuickNote {id} {onview} />
{:else if widget === "next"}<HomeUpNext {id} {revision} {onview} />
{:else if widget === "focus"}<HomeFocus {id} />
{:else if widget === "goals"}<HomeGoals {id} {revision} {onview} />
{:else if widget === "week"}<HomeWeek {id} {revision} {onview} />
{:else if widget === "cards"}<HomeCards {id} {revision} {onview} />
{:else if widget === "pipeline"}<HomePipeline {id} {revision} {onview} />
{:else if widget === "clock"}<HomeClock {id} />
{:else if widget === "session"}<HomeSession {id} {placeholder} />
{:else}<HomeAgents {id} {onview} {placeholder} />{/if}
