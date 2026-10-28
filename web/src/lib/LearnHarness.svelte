<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "./api";

  // Fixed cross-origin application, not a URL supplied by settings or user data.
  const url = "http://127.0.0.1:3210";
  let available = $state(false);
  let checking = $state(true);
  let error = $state("");
  let generation = $state(0);

  async function check() {
    checking = true;
    error = "";
    try {
      const state = await api<{ available: boolean }>("/extensions/learnharness");
      available = state.available;
      generation += 1;
    } catch (cause) {
      available = false;
      error = cause instanceof Error ? cause.message : "Could not check the local service.";
    } finally {
      checking = false;
    }
  }
  onMount(() => { void check(); });
</script>

<section class="learnharness" aria-label="LearnHarness extension">
  <header>
    <div><h1>LearnHarness</h1><p>Independent local teaching, practice and knowledge graph.</p></div>
    <div class="actions">
      <Button size="sm" onclick={check} disabled={checking}>{checking ? "Checking…" : "Reconnect"}</Button>
      <a href={url} target="_blank" rel="noopener noreferrer">Open in new tab</a>
    </div>
  </header>
  <p class="boundary">External app · separate learning data and provider settings. Kitty never sends resumes,
    applicant records or AI keys. Provider logins should use the new tab; model requests require your own
    LearnHarness provider key or connection and may incur costs.</p>
  {#if error}<p role="alert">{error}</p>{/if}
  {#if available}
    {#key generation}
      <iframe title="LearnHarness local learning application" src={url} referrerpolicy="no-referrer"
        sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"
        onerror={() => { available = false; }}></iframe>
    {/key}
    <small>If the app stops or the frame cannot load, start the service below and choose Reconnect.
      This is the full upstream app, also available independently at {url}.</small>
  {:else if !checking}
    <Panel title="LearnHarness is unavailable">
      <p>Nothing is lost: learning data persists when the server stops. Start LearnHarness on this
        computer, then choose Reconnect. It must listen at <code>{url}</code>.</p>
    </Panel>
  {/if}
  <details open={!available && !checking}>
    <summary>Local setup, start and stop</summary>
    <p>From the Kitty source checkout, with Git, Node.js and npm installed:</p>
    <pre><code>./scripts/learnharness.sh setup
./scripts/learnharness.sh start
./scripts/learnharness.sh status
./scripts/learnharness.sh stop</code></pre>
    <p>Setup installs upstream LearnHarness 0.3.8 in <code>~/Projects/learnharness</code>,
      with unchanged app source and a reviewed dependency-lock patch (see README for remaining audit risks).
      Start/stop use a transient systemd user service (no sudo); start again after reboot.
      Without systemd, use <code>./scripts/learnharness.sh run</code> and stop with Ctrl+C.</p>
    <p>Learning records and credentials stay in the upstream checkout's ignored
      <code>data/kitty-extension/</code> directory, not Kitty. No provider is configured automatically.
      In Kitty demo mode this is still an independent app, not a disposable demo: changes in
      LearnHarness persist. Do not enter private applicant data.</p>
    <a href="https://github.com/amosblomqvist/learnharness" target="_blank" rel="noopener noreferrer">Upstream LearnHarness · AGPL-3.0-only · source and instructions</a>
  </details>
</section>

<style>
  .learnharness { display: flex; flex-direction: column; gap: .8rem; min-width: 0; }
  header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem; }
  h1 { margin: 0; font-size: 1.6rem; }
  header p { margin: .3rem 0 0; color: var(--muted); }
  .actions { display: flex; align-items: center; gap: 1rem; }
  .boundary, small { color: var(--muted); line-height: 1.5; margin: 0; }
  iframe { width: 100%; height: max(640px, 75vh); border: 1px solid var(--border); border-radius: .5rem; background: #111; }
  details { padding: .8rem; border: 1px solid var(--border); border-radius: .5rem; }
  summary { cursor: pointer; }
  details p { line-height: 1.5; }
  pre { overflow-x: auto; padding: .8rem; background: var(--surface); }
  code { overflow-wrap: anywhere; }
</style>
