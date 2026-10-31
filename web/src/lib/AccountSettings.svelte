<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "./api";
  import type { AccountState } from "./onboarding";
  let account = $state<AccountState | null>(null);
  let error = $state("");
  let busy = $state(false);
  async function refresh() {
    try { account = await api<AccountState>("/account"); }
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
<Panel title="Welcome & account">
  <div class="account-settings">
    <p class="muted">Your workspace works without an account. A Google connection identifies you here; it does not sync your data or protect access to the local API.</p>
    {#if account?.identity}
      <p class="identity"><span aria-hidden="true">●</span> Connected as <strong>{account.identity.email}</strong></p>
    {:else}<p class="muted">Local mode · {account?.configured ? "Google connection available" : "Google not configured"}</p>{/if}
    <div class="actions">
      <Button size="sm" onclick={() => open("account")}>{account?.identity ? "Welcome & Google connection" : "Connect Google"}</Button>
      {#if account?.identity}<Button size="sm" variant="ghost" disabled={busy} onclick={disconnect}>Disconnect Google</Button>{/if}
      <Button size="sm" onclick={() => open("tutorial")}>Take the guided tour</Button>
    </div>
    <details><summary>Google connection setup</summary>
      <ol>
        <li>In your own Google Cloud project, configure Google Auth Platform branding and audience. Add yourself as a test user while the app is in Testing.</li>
        <li>Create an OAuth client with application type <strong>Desktop app</strong>. Use identity scopes only: <code>openid email profile</code>.</li>
        <li>Put <code>KITTY_GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com</code> and that Desktop client's <code>KITTY_GOOGLE_CLIENT_SECRET</code> in the workspace's private <code>.env</code>, or set them in the backend environment. Google requires both for Desktop clients. Restart kitty to load them.</li>
        <li>Reconnect here. Open the Google sign-in link in your system browser, complete consent yourself, and return to kitty.</li>
      </ol>
      <p>No hosted service or Gmail/Drive permission is required. Never reuse another project's client, commit the private <code>.env</code>, or paste the secret into this page. Full setup and verification limits are in <code>docs/GOOGLE_ONBOARDING.md</code> in the source repository.</p>
    </details>
    {#if error}<p class="error-banner" role="alert">{error}<button class="text-button" onclick={refresh}>Retry</button></p>{/if}
  </div>
</Panel>
<style>
  .account-settings { display: grid; gap: .8rem; }.account-settings p { margin: 0; font-size: 12px; line-height: 1.7; }.muted { color: var(--muted); }.actions { display: flex; flex-wrap: wrap; gap: .65rem; }.identity { overflow-wrap: anywhere; }.identity span { color: var(--success); margin-right: .4rem; }details { font-size: 12px; color: var(--muted); border-top: 1px solid var(--border); padding-top: .85rem; }summary { cursor: pointer; color: var(--ink); }li { margin: .65rem 0; line-height: 1.7; }code { overflow-wrap: anywhere; font-size: 11px; }
</style>
