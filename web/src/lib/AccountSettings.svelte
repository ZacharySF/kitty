<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "./api";
  import Icon from "./Icon.svelte";
  import type { AccountState } from "./onboarding";
  const GUIDE = "https://github.com/ZacharySF/kitty/blob/main/docs/GOOGLE_ONBOARDING.md";
  let account = $state<AccountState | null>(null);
  let error = $state("");
  let busy = $state(false);
  async function refresh() {
    try { account = await api<AccountState>("/account"); error = ""; }
    catch (cause) { error = (cause as Error).message; }
  }
  onMount(() => {
    void refresh();
    window.addEventListener("kitty-account-changed", refresh);
    return () => window.removeEventListener("kitty-account-changed", refresh);
  });
  function open(mode: "account" | "tutorial") {
    window.dispatchEvent(new CustomEvent("kitty-onboarding", { detail: mode }));
  }
  async function disconnect() {
    busy = true; error = "";
    try { account = await api<AccountState>("/account/google/disconnect", {}); }
    catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
</script>

<Panel title="Account" aria-label="Account">
  <div class="rows">
    <div class="set-row">
      <span class="set-label">Google</span>
      {#if account?.identity}
        <span class="set-desc identity"><span class="dot" aria-hidden="true"></span>Connected as <strong>{account.identity.email}</strong></span>
      {:else}
        <span class="set-desc">Optional sign-in. Your data stays local.</span>
      {/if}
      <span class="set-end">
        <a class="guide" href={GUIDE} target="_blank" rel="noreferrer">Setup guide<Icon name="external" size={12} /></a>
        {#if account?.identity}
          <Button size="sm" variant="ghost" disabled={busy} onclick={disconnect}>Disconnect</Button>
        {:else}
          <Button size="sm" onclick={() => open("account")} title={account && !account.configured ? "Google is not configured yet; see the setup guide" : undefined}>Connect Google</Button>
        {/if}
      </span>
    </div>
    <div class="set-row">
      <span class="set-label">Guided tour</span>
      <span class="set-end"><Button size="sm" onclick={() => open("tutorial")}>Take the tour</Button></span>
    </div>
  </div>
  {#if error}<p class="set-note" role="alert">{error} <button class="text-button" onclick={refresh}>Retry</button></p>{/if}
</Panel>

<style>
  .identity {
    overflow-wrap: anywhere;
  }
  .dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    margin-right: 7px;
    border-radius: 999px;
    background: var(--success);
    vertical-align: 1px;
  }
  .identity strong {
    color: var(--ink);
    font-weight: 500;
  }
  .guide {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--muted);
    font-size: 12.5px;
    text-decoration: none;
    white-space: nowrap;
  }
  .guide:hover,
  .guide:focus-visible {
    color: var(--ink);
  }
</style>
