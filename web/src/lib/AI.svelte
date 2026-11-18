<script lang="ts">
  import { onMount } from "svelte";
  import { api, type TaskRecord } from "./api";
  import AIReview from "./AIReview.svelte";
  import { activeModules } from "./modules.svelte";
  import RikaBrainSettings from "./rika/RikaBrainSettings.svelte";
  import RikaVoiceSettings from "./rika/RikaVoiceSettings.svelte";
  import UsageSection from "./usage/UsageSection.svelte";
  let { revision = 0, demo = false }: { revision?: number; demo?: boolean } =
    $props();
  type Model = {
    id: string;
    name: string;
    prompt_price: string;
    completion_price: string;
    context_length: number;
  };
  type Status = {
    configured: boolean;
    model: string;
    models: Model[];
    max_output_tokens: number;
    routing: string;
    daily_budget_usd: number;
    spent_today: number;
    revision: number;
    privacy_notice: string;
    /** Rika's tier models (Q12); '' means the default in `rika_tier_defaults`. */
    rika_tiers: Record<Tier, string>;
    rika_tier_defaults: Record<Tier, string>;
  };
  type Tier = "fast" | "standard" | "deep";
  const TIERS: [Tier, string][] = [["fast", "Fast tier"], ["standard", "Standard tier"], ["deep", "Deep tier"]];
  let status = $state<Status | null>(null);
  let key = $state("");
  let model = $state("");
  let search = $state("");
  let routing = $state("latency");
  let maximum = $state(2048);
  let dailyBudget = $state(1);
  let confirmed = $state(false);
  let tiers = $state<Record<Tier, string>>({ fast: "", standard: "", deep: "" });
  let busy = $state(false);
  let error = $state("");
  let message = $state("");
  let purpose = $state("summarize");
  let text = $state("");
  const workspaceTools = $derived(activeModules.enabled.includes("workspace"));
  const choices = $derived(
    status?.models.filter((m) =>
      `${m.id} ${m.name}`.toLowerCase().includes(search.toLowerCase()),
    ) ?? [],
  );
  async function load() {
    try {
      status = await api<Status>("/ai/status");
      model = status.model;
      routing = status.routing;
      maximum = status.max_output_tokens;
      dailyBudget = status.daily_budget_usd;
      tiers = { ...status.rika_tiers };
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }
  onMount(load);
  async function save(removeKey = false) {
    if (!status || !confirmed) return;
    busy = true;
    error = "";
    message = "";
    try {
      status = await api<Status>("/ai/settings", {
        revision: status.revision,
        model,
        routing,
        max_output_tokens: maximum,
        daily_budget_usd: dailyBudget,
        rika_tiers: tiers,
        ...(key ? { key } : {}),
        ...(removeKey ? { remove_key: true } : {}),
        confirmed: true,
      });
      key = "";
      confirmed = false;
      message = "AI settings saved. Your key is never returned to the browser.";
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
  async function refreshModels() {
    busy = true;
    error = "";
    message = "";
    try {
      let { task } = await api<{ task: TaskRecord }>("/ai/models/refresh", {
        confirmed: true,
      });
      while (task.status === "running") {
        await new Promise((r) => setTimeout(r, 600));
        task = await api<TaskRecord>(`/tasks/${task.id}`);
      }
      if (task.status === "failed")
        throw new Error(task.error ?? "Model discovery failed");
      await load();
      message =
        "Current public model prices loaded. No inference request was made.";
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
</script>

<div class="ai-layout">
  <section class="ai-panel" aria-labelledby="ai-title">
    <h2 id="ai-title">AI</h2>
    {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
    {#if message}<p role="status">{message}</p>{/if}
    <RikaBrainSettings />
    <h3>OpenRouter</h3>
    {#if status}
      <div class="fields">
        <label
          >API key<input
            type="password"
            autocomplete="new-password"
            bind:value={key}
            placeholder={status.configured
              ? "Saved. Paste a new key to replace it"
              : "Paste your OpenRouter key"}
          /></label
        >
        <div class="field">
          <label for="ai-model">Model</label>
          <span class="pair">
            <select id="ai-model" bind:value={model}
              ><option value="">Choose a model</option
              >{#each choices as item}<option value={item.id}
                  >{item.name} · ${(Number(item.prompt_price) * 1000000).toFixed(2)} in /
                  ${(Number(item.completion_price) * 1000000).toFixed(2)} out per 1M tokens</option
                >{/each}</select
            >
            <input type="search" aria-label="Filter models" bind:value={search} placeholder="Filter" />
            <button class="button" disabled={busy || demo} onclick={refreshModels}>Update list</button>
          </span>
        </div>
        <label
          >Prefer<select bind:value={routing}
            ><option value="latency">Fast response</option><option
              value="throughput">Fast generation</option
            ><option value="price">Lowest price</option></select
          ></label
        >
        <label
          >Longest answer (tokens)<input
            type="number"
            min={128}
            max={8192}
            step={128}
            bind:value={maximum}
          /></label
        >
        <label
          >Daily limit (USD)<input
            type="number"
            min={0}
            max={100}
            step="0.1"
            bind:value={dailyBudget}
          /></label
        >
      </div>
      <h3>Rika's OpenRouter models</h3>
      <datalist id="ai-model-ids">{#each status.models as item}<option value={item.id}>{item.name}</option>{/each}</datalist>
      <div class="fields">
        {#each TIERS as [tier, label] (tier)}
          <label>{label}<input list="ai-model-ids" spellcheck="false" bind:value={tiers[tier]} placeholder={status.rika_tier_defaults[tier]} /></label>
        {/each}
      </div>
      <div class="actions">
        <label class="approval"><input type="checkbox" bind:checked={confirmed} /> These are right</label>
        <button
          class="button primary"
          disabled={busy || !confirmed}
          onclick={() => save()}>Save</button
        ><button
          class="button"
          disabled={busy || !confirmed || !!key || !status.configured}
          onclick={() => save(true)}>Remove key</button
        >
      </div>
      <p class="muted fine">
        The key stays in your system keyring. Only providers that don't collect
        data are used, so some models may be missing. A failed request can still
        be billed.
      </p>
      <RikaVoiceSettings />
    {:else}<p>Loading…</p>{/if}
  </section>
  {#if workspaceTools}<section class="ai-panel" aria-labelledby="ai-try-title">
    <h2 id="ai-try-title">Try it</h2>
    <div class="fields">
      <label
        >Help with<select bind:value={purpose}
          ><option value="summarize">Summarize my notes</option><option
            value="organize">Organize notes and tasks</option
          ><option value="study-plan">Suggest a study plan</option><option
            value="flashcards">Draft flashcards</option
          ><option value="opportunity-summary">Explain an opportunity</option
          ></select
        ></label
      >
    </div>
    <textarea
      rows={8}
      aria-label="Text to send"
      bind:value={text}
      placeholder="Paste the text to send. Nothing else leaves this computer."
    ></textarea>
    <AIReview {purpose} {text} {demo} />
  </section>{/if}
  {#if status}<div class="usage-slot"><UsageSection today={status.spent_today} cap={status.daily_budget_usd} /></div>{/if}
</div>

<style>
  /* One calm column like Settings: no boxes, label left and field right, a hairline between rows. */
  .ai-layout {
    display: grid;
    gap: 2.2rem;
    max-width: 820px;
    margin-inline: auto;
    padding: 0.4rem 1rem 2rem;
  }
  .usage-slot {
    min-width: 0;
    container-type: inline-size;
  }
  .ai-panel {
    display: grid;
    align-content: start;
    gap: 0.9rem;
    min-width: 0;
  }
  h2 {
    margin: 0;
    font-size: 22px;
    font-weight: 500;
  }
  h3 {
    margin: 0.8rem 0 0;
    font-size: 15px;
    font-weight: 500;
    color: var(--muted);
  }
  .fields {
    display: grid;
  }
  .fields > label,
  .field {
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
  .pair {
    display: flex;
    gap: 8px;
    min-width: 0;
  }
  .pair select {
    flex: 1 1 12rem;
  }
  .pair input {
    flex: 0 1 8rem;
  }
  input,
  select,
  textarea {
    width: 100%;
    min-width: 0;
  }
  .approval {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    margin-right: auto;
  }
  .approval input {
    width: auto;
  }
  .actions {
    display: flex;
    gap: 0.7rem;
    flex-wrap: wrap;
    align-items: center;
  }
  .fine {
    font-size: 13px;
    line-height: 1.5;
  }
  @media (max-width: 640px) {
    .fields > label,
    .field {
      grid-template-columns: 1fr;
      gap: 0.35rem;
    }
  }
</style>
