<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { backendLabel, keyName, loadKeys, moveKey, removeKey, setKey, type KeyStatus, type KeysState } from "./keys";

  let keysState = $state<KeysState | null>(null);
  let error = $state("");
  let notice = $state("");
  let busy = $state(false);
  /** The key whose value field is open, or "+" for a new custom key. */
  let editing = $state<string | null>(null);
  let value = $state("");
  let newName = $state("");

  async function refresh() {
    try { keysState = await loadKeys(); error = ""; }
    catch (cause) { error = (cause as Error).message; }
  }
  onMount(() => { void refresh(); });

  async function run(action: () => Promise<KeysState>, done: string) {
    if (busy) return;
    busy = true; error = ""; notice = "";
    try { keysState = await action(); notice = done; }
    catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
  function open(name: string) { editing = name; value = ""; newName = ""; notice = ""; }
  function close() { editing = null; value = ""; newName = ""; }
  async function save() {
    const name = editing === "+" ? keyName(newName) : editing;
    if (!name) { error = "Key names use A-Z, 0-9 and _ and start with a letter"; return; }
    const secret = value;
    value = "";  // the value leaves the page once and is not kept
    await run(() => setKey(name, secret), `${name} saved. Its value is never shown again.`);
    if (!error) close();
  }
  function remove(key: KeyStatus) {
    if (!window.confirm(`Remove ${key.name}? Programs that use it stop working until you set it again.`)) return;
    void run(() => removeKey(key.name), `${key.name} removed.`);
  }
</script>

<section aria-label="Keys">
  <Panel title="Keys">
    <div class="keys">
      <p class="muted">API keys kitty! and the programs you start with <code>kitty keys run</code> use. kitty! shows only whether a key is set and where it is stored, never its value.</p>
      {#if keysState && !keysState.keyring}
        <p class="warning" role="note">No OS keyring is available, so keys are stored in an owner-only file under <code>.private/</code> in your workspace.</p>
      {/if}
      {#if keysState}
        <ul class="rows">
          {#each keysState.keys as key (key.name)}
            <li class="row" data-key={key.name}>
              <span class="who">
                <strong>{key.label}</strong>
                <small>{key.name} · injected as {key.env}</small>
                {#if key.error}<small class="bad">{key.error}</small>
                {:else}<small class="state">{key.set ? `Set · ${backendLabel(key.backend)}` : "Not set"}</small>{/if}
                {#if key.backend === "env"}<small>Remove it from .env yourself; a key saved here takes precedence.</small>{/if}
              </span>
              <span class="actions">
                <Button size="sm" disabled={busy} onclick={() => open(key.name)}>{key.set ? "Replace" : "Set"}</Button>
                {#if keysState.keyring && key.in_file}
                  <Button size="sm" disabled={busy} onclick={() => run(() => moveKey(key.name), `${key.name} moved to the OS keyring.`)}>Move to keyring</Button>
                {/if}
                {#if key.backend === "keyring" || key.backend === "file" || key.in_file}
                  <Button size="sm" variant="ghost" disabled={busy} onclick={() => remove(key)}>Remove</Button>
                {/if}
              </span>
              {#if editing === key.name}
                <form class="entry" onsubmit={(event) => { event.preventDefault(); void save(); }}>
                  <label>New value for {key.name}
                    <input type="password" autocomplete="off" spellcheck="false" bind:value={value} /></label>
                  <Button size="sm" intent="primary" type="submit" disabled={busy || !value.trim()}>Save</Button>
                  <Button size="sm" variant="ghost" type="button" onclick={close}>Cancel</Button>
                </form>
              {/if}
            </li>
          {/each}
        </ul>
        {#if editing === "+"}
          <form class="entry" onsubmit={(event) => { event.preventDefault(); void save(); }}>
            <label>Name <input autocomplete="off" spellcheck="false" placeholder="MY_TOOL_KEY" bind:value={newName} /></label>
            <label>Value <input type="password" autocomplete="off" spellcheck="false" bind:value={value} /></label>
            <Button size="sm" intent="primary" type="submit" disabled={busy || !keyName(newName) || !value.trim()}>Save</Button>
            <Button size="sm" variant="ghost" type="button" onclick={close}>Cancel</Button>
          </form>
        {:else}
          <span><Button size="sm" disabled={busy} onclick={() => open("+")}>Add a key</Button></span>
        {/if}
        <p class="muted">New keys go to {keysState.keyring ? keysState.keyring_label : "an owner-only file"}.</p>
      {:else if !error}<p class="muted" role="status">Loading keys…</p>{/if}
      <details><summary>Using a key in another program</summary>
        <p><code>kitty keys run +OPENROUTER -- your-command</code> starts the command with that key in its environment only; nothing else on your system sees it. Keys never go into exports, builds or logs.</p>
      </details>
      {#if notice}<p role="status">{notice}</p>{/if}
      {#if error}<p role="alert">{error}</p>{/if}
    </div>
  </Panel>
</section>

<style>
  .keys { display: grid; gap: .8rem; }
  .keys p { margin: 0; }
  .muted { color: var(--muted); font-size: .8rem; line-height: 1.5; }
  .warning { color: var(--warning); font-size: .8rem; line-height: 1.5; }
  .rows { list-style: none; margin: 0; padding: 0; display: grid; gap: .5rem; }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; padding: .65rem .75rem; border: 1px solid var(--border-fine, var(--border)); border-radius: 8px; }
  .who { display: grid; gap: .15rem; flex: 1; min-width: 0; }
  .who small { color: var(--muted); line-height: 1.5; overflow-wrap: anywhere; }
  .who .bad { color: var(--danger, var(--warning)); }
  .actions { display: flex; gap: .5rem; flex-wrap: wrap; }
  .entry { display: flex; flex-wrap: wrap; align-items: end; gap: .5rem; width: 100%; }
  .entry label { display: grid; gap: .2rem; font-size: .8rem; flex: 1; min-width: 12rem; }
  details { font-size: 12px; color: var(--muted); border-top: 1px solid var(--border); padding-top: .85rem; line-height: 1.7; }
  summary { cursor: pointer; color: var(--ink); }
  code { overflow-wrap: anywhere; font-size: 11px; }
</style>
