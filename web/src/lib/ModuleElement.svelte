<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api, type ExternalView } from "./api";
  import { modulePath } from "./externalModules";
  import Loader from "./Loader.svelte";

  /**
   * An add-on module's page drawn by its own custom element: the module's script
   * (same-origin, served by rika!) defines `view.tag`. The element gets `rika`, a bridge
   * that reaches only the module's own API routes, before it is attached.
   */
  let { view }: { view: ExternalView } = $props();
  const TIMEOUT_MS = 10_000;
  let host = $state<HTMLDivElement>();
  let phase = $state<"loading" | "ready" | "failed">("loading");
  let problem = $state("");
  let attempt = 0;

  async function load() {
    if (!host) return;
    const target = host;
    phase = "loading";
    problem = "";
    const { module, version, script, tag, label } = view;
    try {
      if (!script || !tag) throw new Error("This add-on does not say which page to show.");
      try {
        // A full URL, so the dev server leaves it alone; a failed import is remembered per
        // URL, so a retry asks for the script afresh.
        const url = new URL(script, location.origin);
        if (attempt) url.searchParams.set("retry", String(attempt));
        await import(/* @vite-ignore */ url.href);
      } catch {
        throw new Error("Its page script could not be loaded.");
      }
      let timer: ReturnType<typeof setTimeout> | undefined;
      const late = new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error("Its page did not finish loading.")), TIMEOUT_MS);
      });
      try { await Promise.race([customElements.whenDefined(tag), late]); } finally { clearTimeout(timer); }
      const element = document.createElement(tag) as HTMLElement & { rika?: unknown };
      element.rika = {
        module,
        version,
        get: (path: string) => api(modulePath(module, path)),
        post: (path: string, body: object) => api(modulePath(module, path), body ?? {}),
      };
      target.replaceChildren(element);
      phase = "ready";
    } catch (cause) {
      target.replaceChildren();
      phase = "failed";
      problem = `${label} could not load. ${cause instanceof Error ? cause.message : ""}`.trim();
    }
  }
  function retry() {
    attempt += 1;
    void load();
  }
  onMount(() => { void load(); });
</script>

<section class="module-view" aria-label={view.label} data-module={view.module}>
  {#if phase === "loading"}<Loader kind="view" label={`Loading ${view.label}…`} />{/if}
  {#if phase === "failed"}
    <Panel title={`${view.label} is unavailable`}>
      <p role="alert">{problem}</p>
      <p class="hint">Try again, or check this add-on in Settings → Modules.</p>
      <Button intent="primary" onclick={retry}>Try again</Button>
    </Panel>
  {/if}
  <div class="host" bind:this={host} hidden={phase !== "ready"}></div>
</section>

<style>
  .module-view { display: flex; flex-direction: column; gap: .8rem; min-width: 0; }
  .host { min-width: 0; }
  .hint { color: var(--muted); line-height: 1.5; }
</style>
