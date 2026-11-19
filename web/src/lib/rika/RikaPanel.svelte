<script lang="ts">
  import { untrack, type Snippet } from "svelte";
  import HomeWidget from "../HomeWidget.svelte";
  import RikaConversation from "./RikaConversation.svelte";
  import RikaInput from "./RikaInput.svelte";
  import { rika } from "./session.svelte";

  /**
   * Rika on home: a whole column (Home.svelte places it), the conversation above and the field at its
   * foot. While it is on screen (`active`: its tab is the one shown) it stands in for the
   * drawer and the reply toasts: Ctrl + J, Ctrl K and anything that would open the drawer
   * focus its field or switch its section instead. `lead` is home's own line above the
   * conversation (the template offer); `tail` puts controls at the right of its title row
   * (the side panel's Close, rika/RikaDock.svelte).
   */
  let { id, active, lead, tail }: { id: string; active: boolean; lead?: Snippet; tail?: Snippet } = $props();
  let field = $state<RikaInput>();
  const waiting = $derived(rika.pending.length + rika.pendingPlans.length);

  $effect(() => {
    if (active) return untrack(() => rika.showPanel());
  });
  let seen = rika.focusRequest;
  $effect(() => {
    const request = rika.focusRequest;
    if (!active || request === seen) return;
    seen = request;
    field?.focus();
  });
</script>

<div class="rika-panel" data-tutorial="rika">
  <HomeWidget {id} title="Rika" icon="actor-rika" eyebrow={waiting ? `${waiting} waiting` : ""} fill {tail}>
    <!-- Home's line stays in view; only the conversation scrolls beneath it. -->
    {#if lead}<div class="lead">{@render lead()}</div>{/if}
    <div class="scroll">
      <RikaConversation brief={active} />
    </div>
    <div class="foot"><RikaInput bind:this={field} /></div>
  </HomeWidget>
</div>

<style>
  .rika-panel {
    min-width: 0;
    min-height: 0;
    height: 100%;
  }
  .scroll {
    flex: 1 1 auto;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding-right: 4px;
    overflow-y: auto;
    overscroll-behavior: contain;
    scrollbar-width: thin;
    scrollbar-color: var(--hairline) transparent;
  }
  .lead {
    flex: none;
  }
  .foot {
    flex: none;
    padding: 12px 0 2px;
  }
  .rika-panel :global(.home-widget > header .eyebrow) {
    color: var(--warning);
  }
</style>
