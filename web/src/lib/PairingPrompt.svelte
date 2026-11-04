<script lang="ts">
  // A non-blocking prompt for an app asking to connect; Settings → Connected apps has the full list.
  import { onMount } from "svelte";
  import { CLIENTS_EVENT, decide, kindLabel, loadClients, scopeLabel, type ClientsState, type PairingRequest } from "./clients";

  let { onsettings }: { onsettings: () => void } = $props();
  let requests = $state<PairingRequest[]>([]);
  let busy = $state(false);
  let error = $state("");

  async function refresh() {
    try { requests = (await loadClients()).requests; }
    catch { /* The prompt stays quiet; Settings reports errors. */ }
  }
  onMount(() => {
    void refresh();
    const timer = setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 4000);
    const adopt = (event: Event) => { requests = (event as CustomEvent<ClientsState>).detail.requests; };
    window.addEventListener(CLIENTS_EVENT, adopt);
    window.addEventListener("focus", refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener(CLIENTS_EVENT, adopt);
      window.removeEventListener("focus", refresh);
    };
  });
  async function answer(request: PairingRequest, approve: boolean) {
    if (busy) return;
    busy = true; error = "";
    try { requests = (await decide(request.id, approve)).requests; }
    catch (cause) { error = (cause as Error).message; await refresh(); }
    finally { busy = false; }
  }
</script>

{#each requests as request (request.id)}
  <div class="demo-notice pairing" role="status" aria-label="Connection request">
    <span><strong>{request.name}</strong> ({kindLabel(request.kind)}) wants to {scopeLabel(request.scopes)}. Approve only if it shows code <strong class="code">{request.code}</strong>.</span>
    <button class="text-button" disabled={busy} onclick={() => answer(request, true)}>Approve</button>
    <button class="text-button" disabled={busy} onclick={() => answer(request, false)}>Deny</button>
    <button class="text-button" onclick={onsettings}>Connected apps</button>
  </div>
{/each}
{#if error}<div class="error-banner" role="alert">{error}<button class="icon-button" aria-label="Dismiss connection message" onclick={() => (error = "")}>×</button></div>{/if}

<style>
  .pairing .code { font-family: var(--font-mono, monospace); letter-spacing: .08em; }
</style>
