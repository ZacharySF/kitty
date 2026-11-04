<script lang="ts">
  /**
   * The safe-mode control and build notices (Q9), mounted by main.ts outside #app.
   * It sits in the top layer with hard-coded inline styles (no CSS variables), so
   * neither theme tokens nor custom CSS can hide, move or recolour it.
   */
  import { buildStore, toggleSafe } from "./build.svelte";

  let dismissedProblem = $state("");
  let dismissedError = $state("");
  let barSpace = $state(0);
  const problem = $derived(buildStore.invalid ? buildStore.problems.find((item) => item.severity === "error") ?? buildStore.problems[0] : undefined);
  const problemKey = $derived(problem ? `${buildStore.revision}:${problem.line}:${problem.message}` : "");
  const showProblem = $derived(!!problem && problemKey !== dismissedProblem);
  const showError = $derived(!!buildStore.error && buildStore.error !== dismissedError);
  const showSafe = $derived(buildStore.safe || buildStore.custom);
  const visible = $derived(showSafe || showProblem || showError);
  const inApp = document.documentElement.dataset.surface !== "quick-note";

  // Sit above the Rika bar: its height is read from the page, clamped so a token cannot push this off screen.
  $effect(() => {
    const root = document.documentElement;
    const measure = () => {
      const space = parseFloat(root.style.getPropertyValue("--rika-space")) || 0;
      barSpace = Math.max(0, Math.min(space, window.innerHeight - 80));
    };
    measure();
    const observer = new MutationObserver(measure);
    observer.observe(root, { attributes: true, attributeFilter: ["style"] });
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  });

  /** Into the top layer when the browser has popovers; otherwise the highest fixed layer. */
  function topLayer(node: HTMLElement) {
    if (typeof node.showPopover !== "function") return;
    try { node.showPopover(); } catch { /* Already shown, or not connected yet. */ }
  }

  const box = "position:fixed;inset:auto auto 0 12px;margin:0;z-index:2147483647;display:grid;gap:6px;max-width:min(560px,calc(100vw - 24px));padding:8px 10px;border:1px solid #8a8a8a;border-radius:8px;background:#111111;color:#f2f2f2;font:12px/1.4 system-ui,-apple-system,'Segoe UI',sans-serif;box-shadow:0 4px 18px rgba(0,0,0,.45);overflow:visible;visibility:visible;opacity:1;transform:none;filter:none;pointer-events:auto";
  const row = "display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0;padding:0;color:#f2f2f2;font:inherit;visibility:visible";
  const button = "all:unset;cursor:pointer;padding:2px 8px;border:1px solid #bdbdbd;border-radius:6px;background:#2a2a2a;color:#ffffff;font:12px/1.4 system-ui,-apple-system,'Segoe UI',sans-serif;visibility:visible;display:inline-block";
  const close = `${button};padding:0 6px;border-color:#6a6a6a`;
</script>

{#if visible}
  <div popover="manual" {@attach topLayer} role="region" aria-label="Build status" data-build-status style={`${box};bottom:${barSpace + 10}px`}>
    {#if showProblem && problem}
      <p role="status" style={row} data-build-problem>
        <span><strong style="font-weight:600;color:#ffb4a8">Build file error</strong> · line {problem.line}: {problem.message} · the last good build stays applied.</span>
        {#if inApp}<button type="button" style={button} onclick={() => (buildStore.editorOpen = true)}>Open build editor</button>{/if}
        <button type="button" style={close} aria-label="Dismiss build file error" onclick={() => (dismissedProblem = problemKey)}>×</button>
      </p>
    {/if}
    {#if showError}
      <p role="alert" style={row} data-build-error>
        <span>{buildStore.error}</span>
        <button type="button" style={close} aria-label="Dismiss build message" onclick={() => (dismissedError = buildStore.error)}>×</button>
      </p>
    {/if}
    {#if showSafe}
      <p style={row} data-safe-control>
        {#if buildStore.safe}
          <span>Safe mode: custom style off</span>
          <button type="button" style={button} onclick={toggleSafe} title="Ctrl+Alt+Shift+S">Turn back on</button>
        {:else}
          <span>Custom style on</span>
          <button type="button" style={button} onclick={toggleSafe} title="Turn off this build's custom CSS and theme tokens in this window">Safe mode (Ctrl+Alt+Shift+S)</button>
        {/if}
      </p>
    {/if}
  </div>
{/if}
