<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api, type ExternalView } from "./api";
  import { modulePath } from "./externalModules";
  import Loader from "./Loader.svelte";

  /**
   * An add-on module that runs its own local app, shown in a sandboxed frame (the
   * LearnHarness pattern). Opening the page checks the app and starts it when it is not
   * running yet.
   */
  let { view }: { view: ExternalView } = $props();
  type Service = { url: string; available: boolean };
  let url = $state("");
  let phase = $state<"checking" | "starting" | "ready" | "failed">("checking");
  let problem = $state("");
  let generation = $state(0);

  /** Only an app on this computer may be framed. */
  function local(address: string) {
    try {
      const parsed = new URL(address);
      return ["http:", "https:"].includes(parsed.protocol) && ["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname);
    } catch {
      return false;
    }
  }
  async function open(start = false) {
    problem = "";
    try {
      phase = start ? "starting" : "checking";
      let service = await api<Service>(modulePath(view.module, start ? "start" : ""), start ? {} : undefined);
      if (!service.available && !start) {
        phase = "starting";
        service = await api<Service>(modulePath(view.module, "start"), {});
      }
      if (!service.available) throw new Error("It did not report that it is running.");
      if (!local(service.url)) throw new Error("It gave an address outside this computer, so rika! will not show it.");
      url = service.url;
      generation += 1;
      phase = "ready";
    } catch (cause) {
      phase = "failed";
      problem = `${view.label} could not start: ${cause instanceof Error ? cause.message : "unknown error"}`;
    }
  }
  onMount(() => { void open(); });
</script>

<section class="module-frame" aria-label={view.label} data-module={view.module}>
  <header>
    <h1>{view.label}</h1>
    {#if phase === "ready"}
      <div class="actions">
        <Button size="sm" onclick={() => open()}>Reconnect</Button>
        <a href={url} target="_blank" rel="noopener noreferrer">Open in new tab</a>
      </div>
    {/if}
  </header>
  {#if phase === "checking"}
    <Loader kind="view" label={`Checking ${view.label}…`} />
  {:else if phase === "starting"}
    <Loader kind="view" label={`Starting ${view.label}…`} />
  {:else if phase === "failed"}
    <Panel title={`${view.label} is not running`}>
      <p role="alert">{problem}</p>
      <p class="hint">This add-on runs as its own app on this computer. Try again, or check it in Settings → Modules.</p>
      <Button intent="primary" onclick={() => open(true)}>Try again</Button>
    </Panel>
  {:else}
    {#key generation}
      <iframe title={`${view.label} add-on`} src={url} referrerpolicy="no-referrer"
        sandbox="allow-scripts allow-same-origin allow-forms allow-downloads"
        onerror={() => { phase = "failed"; problem = `${view.label} could not start: its page did not load.`; }}></iframe>
    {/key}
  {/if}
</section>

<style>
  .module-frame { display: flex; flex-direction: column; gap: .8rem; min-width: 0; }
  header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem; }
  h1 { margin: 0; font-size: 1.6rem; }
  .actions { display: flex; align-items: center; gap: 1rem; }
  .hint { color: var(--muted); line-height: 1.5; }
  iframe { width: 100%; height: max(640px, 75vh); border: 1px solid var(--border); border-radius: .5rem; background: #111; }
</style>
