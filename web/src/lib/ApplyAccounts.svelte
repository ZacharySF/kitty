<script lang="ts">
  /**
   * Settings → Apply → Accounts (proposal-07 Step 4): the owner's one-time ATS-family
   * grants, and the employer account registry. Only states, login emails and where each
   * password is stored are shown; a password never reaches this page.
   */
  import { onMount } from "svelte";
  import { Panel } from "@nunera/bracket";
  import { day, hostOf, loadAccounts, needsOwner, setGrant, stateLabel, storedLabel, type AccountsState, type FamilyGrant } from "./applyAccounts";

  const uid = $props.id();
  let accounts = $state<AccountsState | null>(null);
  let error = $state("");
  let busy = $state(false);

  async function refresh() {
    try { accounts = await loadAccounts(); error = ""; }
    catch (cause) { error = (cause as Error).message; }
  }
  onMount(() => { void refresh(); });

  async function toggle(grant: FamilyGrant) {
    if (busy) return;
    const granting = !grant.granted;
    if (granting && !window.confirm(
      `Let rika! create, verify, sign in to and reset your accounts on ${grant.label} sites (${grant.patterns.join(", ")})? ` +
      "Each employer gets its own generated password in Keys. Submit always stays yours.")) return;
    busy = true; error = "";
    try { accounts = await setGrant(grant.family, granting); }
    catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  const labelOf = (family: string) => accounts?.grants.find((grant) => grant.family === family)?.label ?? family;
</script>

<Panel title="Accounts" aria-label="Accounts">
  <div class="rows">
    <p class="set-note intro">One-time grants per ATS family let rika! make and use your employer accounts while it fills applications. Every employer gets its own generated password, kept in Keys and never shown here.</p>
    {#if accounts?.keyring_locked}
      <p class="set-note warn" role="alert">Your OS keyring is locked. Unlock it so runs can read employer passwords; until then each run hands the job back to you.</p>
    {/if}
    {#each accounts?.warnings ?? [] as warning (warning)}
      <p class="set-note warn" role="note">{warning}</p>
    {/each}

    <h2 class="section">Family grants</h2>
    {#each accounts?.grants ?? [] as grant (grant.family)}
      <div class="set-row" data-family={grant.family}>
        <button type="button" class="set-switch" role="switch" id={`${uid}-grant-${grant.family}`} aria-checked={grant.granted}
          aria-label={`${grant.label} grant`} aria-describedby={`${uid}-grant-${grant.family}-about`} disabled={busy}
          onclick={() => toggle(grant)}><i></i></button>
        <label class="set-label" for={`${uid}-grant-${grant.family}`}>{grant.label}</label>
        <span class="set-desc patterns" id={`${uid}-grant-${grant.family}-about`}>{grant.patterns.join("  ·  ")}</span>
        <span class="set-end muted">{grant.granted ? `Granted ${day(grant.granted_at)}` : "Not granted"}</span>
      </div>
    {/each}
    {#if accounts}
      <p class="set-note">At most {accounts.caps.new_accounts_per_day} new employer accounts a day ({accounts.caps.new_accounts_today} in the last 24 hours) and {accounts.caps.sign_ins_per_tenant_per_day} sign-in tries per employer a day. A refused password is replaced, never retried.</p>
    {/if}

    <h2 class="section">Employer accounts</h2>
    {#if accounts && !accounts.accounts.length}
      <p class="set-note" role="status">No employer accounts yet. They appear here as rika! creates or signs in to them.</p>
    {/if}
    <ul class="accounts" aria-label="Employer accounts">
      {#each accounts?.accounts ?? [] as account (account.origin)}
        <li class="set-row" data-origin={account.origin} data-state={account.state}>
          <span class="set-label who"><strong>{hostOf(account.origin)}</strong><small>{labelOf(account.family)} · {account.login_email}</small></span>
          <span class="set-desc details">
            <span class="state" class:attention={needsOwner(account.state)}><span class="dot" aria-hidden="true"></span>{stateLabel(account.state)}</span>
            <small>Password: {account.password_profile} · {storedLabel(account.password_stored)}</small>
            {#if account.last_sign_in_at}<small>Last sign-in {day(account.last_sign_in_at)}</small>{/if}
          </span>
        </li>
      {/each}
    </ul>
    {#if !accounts && !error}<p class="set-note" role="status">Loading accounts…</p>{/if}
    {#if error}<p class="set-note" role="alert">{error} <button class="text-button" type="button" onclick={refresh}>Retry</button></p>{/if}
  </div>
</Panel>

<style>
  .rows {
    display: grid;
  }
  .intro {
    margin-bottom: 6px;
  }
  .warn {
    color: var(--warning);
  }
  .section {
    margin: 14px 0 2px;
    color: var(--muted);
    font: 600 var(--fs-label) / 1.3 var(--font-ui);
  }
  .patterns {
    font-family: var(--font-mono, ui-monospace, monospace);
    font-size: 11px;
    overflow-wrap: anywhere;
  }
  .muted {
    color: var(--muted);
    font-size: var(--fs-label);
  }
  .accounts {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .who {
    display: grid;
    gap: 2px;
    min-width: 0;
  }
  .who strong,
  .who small {
    overflow-wrap: anywhere;
  }
  .who small,
  .details small {
    color: var(--muted);
    font-weight: 400;
  }
  .details {
    display: grid;
    gap: 2px;
  }
  .state {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--ink);
  }
  .dot {
    width: 6px;
    height: 6px;
    border-radius: 999px;
    background: var(--subtle);
  }
  .attention .dot {
    background: var(--warning);
  }
  .text-button {
    padding: 0;
    border: 0;
    background: none;
    color: var(--accent);
    cursor: pointer;
    font: inherit;
  }
</style>
