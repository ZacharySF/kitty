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

  async function check(action: "status" | "start" = "status") {
    checking = true;
    error = "";
    try {
      const state = await api<{ available: boolean }>(
        action === "start" ? "/extensions/learnharness/start" : "/extensions/learnharness",
        action === "start" ? {} : undefined,
      );
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
      <Button size="sm" onclick={() => check()} disabled={checking}>{checking ? "Checking…" : "Reconnect"}</Button>
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
    <small>The bundled learning server stops when this Kitty instance quits; learning records persist.
      An independently started LearnHarness server is never stopped by Kitty.</small>
  {:else if !checking}
    <Panel title="LearnHarness is unavailable">
      <p>Start the included learning app on this computer. No source checkout, Git, npm or separate
        Node.js installation is needed in the desktop app. Existing learning records are preserved.</p>
      <Button onclick={() => check("start")} disabled={checking}>Start LearnHarness</Button>
    </Panel>
  {/if}
  <details>
    <summary>Learning data and provider setup</summary>
    <p>The bundled app stores learning records and provider connections in this workspace's
      separate <code>data/learnharness/</code> directory. Kitty does not copy credentials or learning
      records from an existing upstream checkout. Demo workspace learning records are disposable;
      an independently started server keeps its own data.</p>
    <p>No provider is configured automatically. Use LearnHarness settings to connect your provider
      and choose models before generating lessons. Requests may send learning context to that
      provider and incur costs. Do not enter private applicant data.</p>
    <p>Source developers can build the included runtime with
      <code>uv run python desktop/package_learnharness.py</code>, or run an independent server
      using <code>./scripts/learnharness.sh setup</code> and <code>./scripts/learnharness.sh start</code>.</p>
    <p>LearnHarness 0.3.8 · AGPL-3.0-only. The desktop bundle includes its license, corresponding
      source archive and reviewed dependency-lock patch.</p>
    <a href="https://github.com/amosblomqvist/learnharness" target="_blank" rel="noopener noreferrer">Upstream LearnHarness · source and instructions</a>
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
  code { overflow-wrap: anywhere; }
</style>
