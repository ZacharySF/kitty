<script lang="ts">
  import { onMount } from "svelte";
  import EditDrawer from "../ui/EditDrawer.svelte";
  import RikaConversation from "./RikaConversation.svelte";
  import RikaInput from "./RikaInput.svelte";
  import { rika } from "./session.svelte";

  /**
   * Rika away from home: the drawer on the right (Ctrl + J, Ctrl K), the same conversation
   * as the home panel with the field at its foot. Opened by hand it focuses the field; one
   * that opened by itself (an approval, a plan, a brain dump) leaves focus where it was.
   */
  const waiting = $derived(rika.pending.length + rika.pendingPlans.length);
  // Read before the drawer mounts and focuses its field.
  const previous = document.activeElement as HTMLElement | null;
  onMount(() => {
    if (!rika.drawer.auto) return;
    if (previous?.isConnected && previous !== document.body) previous.focus({ preventScroll: true });
    else (document.activeElement as HTMLElement | null)?.blur();
  });
  // While open, the drawer's room on the right keeps the toasts beside it (Q2 D).
  $effect(() => {
    document.documentElement.style.setProperty("--rika-drawer", "calc(min(400px, 100vw - 1.5rem) + 0.75rem)");
    return () => document.documentElement.style.removeProperty("--rika-drawer");
  });
</script>

<EditDrawer title="Rika" meta={waiting ? `${waiting} waiting` : ""} closeLabel="Close Rika" onclose={() => rika.closeDrawer()}>
  <RikaConversation brief />
  {#snippet footer()}<div class="foot"><RikaInput /></div>{/snippet}
</EditDrawer>

<style>
  .foot {
    flex: 1;
    min-width: 0;
  }
</style>
