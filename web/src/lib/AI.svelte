<script lang="ts">
  import { onMount } from "svelte";
  import { api, type TaskRecord } from "./api";
  import AIReview from "./AIReview.svelte";
  import { activeModules } from "./modules.svelte";
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
  };
  let status = $state<Status | null>(null);
  let key = $state("");
  let model = $state("");
  let search = $state("");
  let routing = $state("latency");
  let maximum = $state(2048);
  let dailyBudget = $state(1);
  let confirmed = $state(false);
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
  <section class="panel ai-panel">
    <h2>Connect OpenRouter</h2>
    <p>
      Choose your model, speed/cost preference, and budget.{#if workspaceTools}
        Use AI for a selected note, study plan, flashcard draft, or
        opportunity summary—not automatic account actions.{:else}
        Every inference request is reviewed first; nothing runs automatically.{/if}
    </p>
    {#if error}<p class="error-banner" role="alert">{error}</p>{/if}
    {#if message}<p role="status">{message}</p>{/if}
    {#if status}
      <p>
        <span class="badge"
          >{status.configured ? "KEY SAVED" : "NOT CONNECTED"}</span
        >
        Reserved/reported today (UTC): ${status.spent_today.toFixed(4)} / ${status.daily_budget_usd.toFixed(
          2,
        )}
      </p>
      <label
        >Private API key<input
          type="password"
          autocomplete="new-password"
          bind:value={key}
          placeholder={status.configured
            ? "Leave blank to keep saved key"
            : "Paste your OpenRouter API key"}
        /></label
      >
      <small
        >Stored owner-only in .private/openrouter-key. Never included in exports
        or GitHub. OPENROUTER_API_KEY also works.</small
      >
      <button class="button" disabled={busy || demo} onclick={refreshModels}
        >Fetch current models and prices</button
      >
      <label
        >Find a model<input
          type="search"
          bind:value={search}
          placeholder="Search provider or model name"
        /></label
      >
      <label
        >Model<select bind:value={model}
          ><option value="">Choose a model</option
          >{#each choices as item}<option value={item.id}
              >{item.name} · ${(Number(item.prompt_price) * 1000000).toFixed(3)} /
              ${(Number(item.completion_price) * 1000000).toFixed(3)} per million
              input/output tokens</option
            >{/each}</select
        ></label
      >
      <div class="settings-grid">
        <label
          >Prefer<select bind:value={routing}
            ><option value="latency">Fast response</option><option
              value="throughput">Fast generation</option
            ><option value="price">Lowest token price</option></select
          ></label
        >
        <label
          >Output token limit<input
            type="number"
            min={128}
            max={8192}
            step={128}
            bind:value={maximum}
          /></label
        >
        <label
          >Daily spending cap (USD, UTC)<input
            type="number"
            min={0}
            max={100}
            step="0.1"
            bind:value={dailyBudget}
          /></label
        >
      </div>
      <label class="approval"
        ><input type="checkbox" bind:checked={confirmed} /> Confirm these settings.
        Every inference request still needs its own review.</label
      >
      <div class="actions">
        <button
          class="button primary"
          disabled={busy || !confirmed}
          onclick={() => save()}>Save AI settings</button
        ><button
          class="button"
          disabled={busy || !confirmed || !!key || !status.configured}
          onclick={() => save(true)}>Remove stored key</button
        >
      </div>
      <p class="muted">
        Providers that decline data collection only. Some models may be
        unavailable with this privacy requirement. No automatic retries.
        Failed/time-out requests may be billed and stay reserved against the
        cap.
      </p>
    {:else}<p>Loading AI settings…</p>{/if}
  </section>
  {#if workspaceTools}<section class="panel ai-panel">
    <h2>Work with selected text</h2>
    <label
      >Help with<select bind:value={purpose}
        ><option value="summarize">Summarize my notes</option><option
          value="organize">Organize notes and tasks</option
        ><option value="study-plan">Suggest a study plan</option><option
          value="flashcards">Draft source-grounded flashcards</option
        ><option value="opportunity-summary">Explain an opportunity</option
        ></select
      ></label
    >
    <label
      >Text to send<textarea
        rows={12}
        bind:value={text}
        placeholder="Paste only the text you want this request to use. No other notes, application data, or profile fields are sent."
      ></textarea></label
    >
    <AIReview {purpose} {text} {demo} />
  </section>{/if}
</div>

<style>
  .ai-layout {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 30rem), 1fr));
    gap: 1.2rem;
  }
  .ai-panel {
    padding: 1.3rem;
    display: grid;
    align-content: start;
    gap: 0.9rem;
    min-width: 0;
  }
  label {
    display: grid;
    gap: 0.4rem;
  }
  input,
  select,
  textarea {
    width: 100%;
    min-width: 0;
  }
  .approval {
    display: flex;
    gap: 0.6rem;
    align-items: start;
  }
  .approval input {
    width: auto;
  }
  .settings-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
    gap: 0.7rem;
  }
  .actions {
    display: flex;
    gap: 0.7rem;
    flex-wrap: wrap;
  }
  small {
    color: var(--muted);
  }
</style>
