<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { CLIENTS_EVENT, decide, kindLabel, loadClients, revoke, scopeLabel, type ClientsState, type PairedClient } from "./clients";

  let connected = $state<ClientsState | null>(null);
  let error = $state("");
  let busy = $state(false);

  async function refresh() {
    try { connected = await loadClients(); error = ""; }
    catch (cause) { error = (cause as Error).message; }
  }
  onMount(() => {
    void refresh();
    // Waiting requests appear while Settings is open; they expire after a few minutes.
    const timer = setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 4000);
    const adopt = (event: Event) => { connected = (event as CustomEvent<ClientsState>).detail; };
    window.addEventListener(CLIENTS_EVENT, adopt);
    window.addEventListener("focus", refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener(CLIENTS_EVENT, adopt);
      window.removeEventListener("focus", refresh);
    };
  });
  async function run(action: () => Promise<ClientsState>) {
    if (busy) return;
    busy = true; error = "";
    try { connected = await action(); }
    catch (cause) { error = (cause as Error).message; await refresh(); }
    finally { busy = false; }
  }
  function remove(client: PairedClient) {
    if (!window.confirm(`Revoke ${client.name}? It loses access to kitty! at once and has to pair again.`)) return;
    void run(() => revoke(client.id));
  }
  const when = (value: string | null) => (value ? new Date(value).toLocaleString() : "Never");
</script>

<section aria-label="Connected apps">
  <Panel title="Connected apps">
    <div class="connected">
      <p class="muted">Programs such as the kitty Tern plugin or the kitty browser extension can capture notes and read titles once you approve them here. Each one gets its own token, limited to what it asked for; revoking it ends its access.</p>
      {#if connected?.requests.length}
        <h3>Waiting for your approval</h3>
        <ul class="rows">
          {#each connected.requests as request (request.id)}
            <li class="row request">
              <span class="who">
                <strong>{request.name}</strong>
                <small>{kindLabel(request.kind)}{#if request.origin} · {request.origin}{/if}</small>
                <small>Wants to {scopeLabel(request.scopes)}</small>
              </span>
              <span class="code" aria-label={`Pairing code ${request.code}`}>{request.code}</span>
              <span class="actions">
                <Button size="sm" intent="primary" disabled={busy} onclick={() => run(() => decide(request.id, true))}>Approve</Button>
                <Button size="sm" variant="ghost" disabled={busy} onclick={() => run(() => decide(request.id, false))}>Deny</Button>
              </span>
            </li>
          {/each}
        </ul>
        <p class="muted">Approve only if the app shows the same code. Requests you ignore expire after five minutes.</p>
      {/if}
      {#if connected}
        {#if connected.clients.length}
          <ul class="rows">
            {#each connected.clients as client (client.id)}
              <li class="row">
                <span class="who">
                  <strong>{client.name}</strong>
                  <small>{kindLabel(client.kind)}{#if client.origin} · {client.origin}{/if}</small>
                  <small>Can {scopeLabel(client.scopes)}</small>
                  <small>Connected {when(client.created_at)} · last used {when(client.last_used_at)}</small>
                </span>
                <span class="actions"><Button size="sm" disabled={busy} onclick={() => remove(client)}>Revoke</Button></span>
              </li>
            {/each}
          </ul>
        {:else}<p class="muted">No connected apps.</p>{/if}
        {#if connected.address}
          <p class="muted address">Browser extension: paste <code>{connected.address}</code> into the kitty! side panel (an extension cannot read <code>.private/endpoint.json</code>).
            <Button size="sm" variant="ghost" onclick={() => connected?.address && void navigator.clipboard.writeText(connected.address).catch(() => {})}>Copy address</Button></p>
        {/if}
      {:else if !error}<p class="muted" role="status">Loading connected apps…</p>{/if}
      <details><summary>How apps connect</summary>
        <p>While kitty! runs, it writes its current address to the owner-only <code>.private/endpoint.json</code> in your workspace (never a token). An app reads it, asks to pair and shows a code; you approve here. A browser extension cannot read files, so you paste the address above into it once. Browser extensions connect from their side panel, sidebar or background page, not from scripts inside web pages.</p>
      </details>
      {#if error}<p role="alert">{error}</p>{/if}
    </div>
  </Panel>
</section>

<style>
  .connected { display: grid; gap: .8rem; }
  .connected p { margin: 0; }
  h3 { margin: .25rem 0 0; font-size: .9rem; }
  .muted { color: var(--muted); font-size: .8rem; line-height: 1.5; }
  .rows { list-style: none; margin: 0; padding: 0; display: grid; gap: .5rem; }
  .row { display: flex; align-items: center; gap: .75rem; padding: .65rem .75rem; border: 1px solid var(--border-fine, var(--border)); border-radius: 8px; }
  .request { border-color: var(--warning); }
  .who { display: grid; gap: .15rem; flex: 1; min-width: 0; }
  .who small { color: var(--muted); line-height: 1.5; overflow-wrap: anywhere; }
  .code { font-family: var(--font-mono, monospace); font-size: 1.05rem; letter-spacing: .08em; font-variant-numeric: tabular-nums; }
  .actions { display: flex; gap: .5rem; flex-wrap: wrap; }
  details { font-size: 12px; color: var(--muted); border-top: 1px solid var(--border); padding-top: .85rem; line-height: 1.7; }
  summary { cursor: pointer; color: var(--ink); }
  code { overflow-wrap: anywhere; font-size: 11px; }
</style>
