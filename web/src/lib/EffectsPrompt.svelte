<script lang="ts">
  // The pending-approvals list: a non-blocking prompt for each effect an agent or connected app
  // proposed (src/kitty/core/effects.py). It shows what would happen, who asked, and the values
  // it was proposed against; Approve binds to that exact digest and is refused if anything
  // changed since. Like PairingPrompt, it polls while the window is visible.
  import { onMount } from "svelte";
  import ActorName from "./ActorName.svelte";
  import { actorName } from "./actors";
  import {
    EFFECTS_EVENT, approveEffect, conditionText, denyEffect, loadEffects, previewLines, refusalText,
    type Effect, type EffectsState,
  } from "./effects";

  let pending = $state<Effect[]>([]);
  let busy = $state("");
  let error = $state("");
  let notice = $state("");

  async function refresh() {
    try { pending = (await loadEffects()).pending; }
    catch { /* The prompt stays quiet; a decision reports its own error. */ }
  }
  onMount(() => {
    void refresh();
    const timer = setInterval(() => { if (document.visibilityState === "visible") void refresh(); }, 4000);
    const adopt = (event: Event) => { pending = (event as CustomEvent<EffectsState>).detail.pending; };
    window.addEventListener(EFFECTS_EVENT, adopt);
    window.addEventListener("focus", refresh);
    return () => {
      clearInterval(timer);
      window.removeEventListener(EFFECTS_EVENT, adopt);
      window.removeEventListener("focus", refresh);
    };
  });

  async function answer(effect: Effect, approve: boolean) {
    if (busy) return;
    busy = effect.id; error = ""; notice = "";
    try {
      const decision = approve ? await approveEffect(effect) : await denyEffect(effect);
      pending = decision.pending;
      notice = !approve ? `Denied: ${effect.title}.`
        : decision.notice ?? (decision.effect.state === "executed" ? `Done: ${effect.title}.` : "");
    } catch (cause) {
      error = refusalText(cause);
      await refresh();
    } finally { busy = ""; }
  }
</script>

{#each pending as effect (effect.id)}
  <div class="demo-notice effect" role="group" aria-label={`Approval request: ${effect.title}`} data-kind={effect.kind}>
    <div class="head">
      <strong>{effect.title}</strong>
      <ActorName actor={effect.actor} label={actorName(effect.actor)} />
      {#if !effect.executable}<span class="manual">You do this yourself; rika! never runs it</span>{/if}
    </div>
    <dl class="preview">
      {#each previewLines(effect) as line (line.label)}<dt>{line.label}</dt><dd>{line.value}</dd>{/each}
    </dl>
    {#if effect.handoff}<p class="handoff">{effect.handoff}</p>{/if}
    <details>
      <summary>Checked again before it runs</summary>
      <dl class="conditions">
        {#each Object.entries(effect.preconditions) as [name, value] (name)}<dt>{name}</dt><dd class="mono">{conditionText(value)}</dd>{/each}
        <dt>digest</dt><dd class="mono">{effect.digest}</dd>
      </dl>
      {#if effect.preview}<pre class="mono">{JSON.stringify(effect.preview, null, 2)}</pre>{/if}
    </details>
    <div class="actions">
      <button class="text-button" disabled={!!busy} onclick={() => answer(effect, true)}
        >{effect.executable ? "Approve" : "Approve (I'll submit it myself)"}</button>
      <button class="text-button" disabled={!!busy} onclick={() => answer(effect, false)}>Deny</button>
    </div>
  </div>
{/each}
{#if notice}<div class="demo-notice" role="status">{notice}<button class="icon-button" aria-label="Dismiss approval message" onclick={() => (notice = "")}>×</button></div>{/if}
{#if error}<div class="error-banner" role="alert">{error}<button class="icon-button" aria-label="Dismiss approval error" onclick={() => (error = "")}>×</button></div>{/if}

<style>
  .effect { flex-direction: column; align-items: stretch; gap: 6px; }
  /* A multi-line card, not the one-line notice pill it shares its look with. */
  :global(:root) .demo-notice.effect { border-radius: var(--r-panel); padding: 10px 14px; }
  .head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .manual { color: var(--warning); }
  .preview, .conditions { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; margin: 0; }
  dt { color: var(--subtle); }
  dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
  .handoff { margin: 0; }
  .mono { font-family: var(--font-mono, monospace); font-size: 11px; }
  pre { max-height: 220px; overflow: auto; margin: 6px 0 0; }
  .actions { display: flex; gap: 8px; }
</style>
