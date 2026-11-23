<script lang="ts">
  import { onMount } from "svelte";
  import { brain as store, type Brain, type Tier } from "./brain.svelte";

  /**
   * Settings → AI → Rika's brain (kitty.core.rika.brain): what Rika thinks with on this
   * computer. omp uses the omp coding agent's own sign-in (a Claude subscription, for
   * example), so no OpenRouter key is needed; OpenRouter uses the key and models below.
   * The model and thinking level are chosen beside Rika's field.
   */
  const TIERS: [Tier, string][] = [["fast", "Fast tier"], ["standard", "Standard tier"], ["deep", "Deep tier"]];
  const brain = $derived(store.current);
  let models = $state<Record<Tier, string>>({ fast: "", standard: "", deep: "" });
  let error = $state("");
  let message = $state("");
  let busy = $state(false);

  onMount(async () => {
    try {
      models = { ...(await store.load(true)).models };
    } catch (cause) {
      error = (cause as Error).message;
    }
  });
  async function save(provider: Brain["provider"]) {
    busy = true;
    error = "";
    message = "";
    try {
      models = { ...(await store.save({ provider, models })).models };
      message = provider === "omp" ? "Rika now thinks with omp." : "Rika now thinks with OpenRouter.";
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<fieldset class="brain">
  <legend>Rika's brain</legend>
  {#if brain}
    <div class="choice" role="radiogroup" aria-label="Rika's brain">
      <label><input type="radio" name="rika-brain" checked={brain.provider === "omp"} disabled={busy}
          onchange={() => save("omp")} /> omp, with omp's own sign-in</label>
      <label><input type="radio" name="rika-brain" checked={brain.provider === "openrouter"} disabled={busy}
          onchange={() => save("openrouter")} /> OpenRouter, with the key below</label>
    </div>
    {#if brain.provider === "omp"}
      {#if !brain.omp_installed}<p class="error" role="alert">omp is not installed on this computer, so Rika can't think with it yet.</p>{/if}
      <div class="fields">
        {#each TIERS as [tier, label] (tier)}
          <label>{label}<input spellcheck="false" bind:value={models[tier]} placeholder={brain.defaults[tier]} /></label>
        {/each}
      </div>
      <div class="actions"><button class="button" disabled={busy} onclick={() => save("omp")}>Save omp models</button></div>
      <p class="muted fine">
        Rika's requests go through omp on this computer and use whatever omp is signed in with, such as your
        Claude subscription. The key, model list, prices and daily limit below don't apply to them. Replies
        arrive whole rather than word by word.
      </p>
    {/if}
  {/if}
  {#if message}<p role="status">{message}</p>{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</fieldset>

<style>
  .brain {
    display: grid;
    gap: 8px;
    margin: 0;
    padding: 0;
    border: 0;
  }
  .brain legend {
    padding: 0;
    margin-bottom: 0.4rem;
    font-weight: 600;
  }
  .brain p {
    margin: 0;
  }
  .choice {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem 1.5rem;
  }
  .choice label {
    display: flex;
    gap: 0.5rem;
    align-items: center;
  }
  .fields {
    display: grid;
  }
  .fields > label {
    display: grid;
    grid-template-columns: 11rem minmax(0, 1fr);
    align-items: center;
    gap: 1rem;
    padding: 9px 0;
    border-top: 1px solid var(--border);
    font-size: 15px;
  }
  .fields > :first-child {
    border-top: 0;
  }
  .actions {
    display: flex;
  }
  .fine {
    font-size: 13px;
  }
  .error {
    color: var(--danger);
  }
</style>
