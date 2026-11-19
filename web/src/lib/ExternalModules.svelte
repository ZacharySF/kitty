<script lang="ts">
  import { Badge, Button, Panel } from "@nunera/bracket";
  import { api, type ExternalModule, type ModulesState, type TaskRecord } from "./api";
  import { parseRepository } from "./externalModules";
  import { moduleStore } from "./modulePicker.svelte";

  /**
   * Settings → Modules, add-on part: modules installed from a GitHub repository's latest
   * release. Installing, updating and removing are saved at once and apply after restart.
   */
  let { modules, onstate, onkeys }: {
    modules: ModulesState;
    /** A newer saved state from the backend. */
    onstate: (next: ModulesState) => Promise<void> | void;
    /** Opens Settings → Keys. */
    onkeys: () => void;
  } = $props();
  const uid = $props.id();

  const STATUS: Record<ExternalModule["status"], { label: string; intent: "success" | "warning" | "danger" }> = {
    active: { label: "Active", intent: "success" },
    "restart-to-install": { label: "Restart to install", intent: "warning" },
    "restart-to-update": { label: "Restart to update", intent: "warning" },
    "restart-to-remove": { label: "Restart to remove", intent: "warning" },
    failed: { label: "Could not start", intent: "danger" },
  };
  const installed = $derived(modules.external?.installed ?? []);
  const tokenSet = $derived(!!modules.external?.token.configured);
  const pending = $derived(installed.filter((item) => item.status.startsWith("restart-to-")));

  /** One operation at a time: "add", "check", or "update:<id>" / "remove:<id>". */
  let busy = $state<string | null>(null);
  let repository = $state("");
  let invalid = $state("");
  let addError = $state("");
  let notice = $state("");
  let updates = $state<Record<string, string>>({});
  let checkErrors = $state<Record<string, string>>({});
  let checked = $state(false);
  let checkError = $state("");
  let rowErrors = $state<Record<string, string>>({});

  /** Waits for a background task; a failure's message is a plain sentence shown as is. */
  async function finish<T>(start: Promise<{ task: TaskRecord }>): Promise<T> {
    let task = (await start).task;
    while (task.status === "running") {
      await new Promise((resolve) => setTimeout(resolve, 400));
      task = await api<TaskRecord>(`/tasks/${task.id}`);
    }
    if (task.status === "failed") throw new Error(task.error || "Something went wrong. Please try again.");
    return task.result as T;
  }
  const message = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));

  async function add(event: SubmitEvent) {
    event.preventDefault();
    if (busy) return;
    notice = "";
    addError = "";
    const repo = parseRepository(repository);
    if (!repo) {
      invalid = "Enter the repository as owner/repo, for example octo-org/rika-demo.";
      return;
    }
    invalid = "";
    busy = "add";
    moduleStore.addon = repo;
    const before = new Set(installed.map((item) => item.id));
    try {
      const next = await finish<ModulesState>(api("/external-modules/install", { repo }));
      await onstate(next);
      const added = next.external.installed.find((item) => !before.has(item.id)) ?? next.external.installed.find((item) => item.repo === repo);
      notice = added ? `Added ${added.name} ${added.version}.` : "";
      repository = "";
    } catch (cause) {
      addError = message(cause);
    } finally {
      busy = null;
      moduleStore.addon = "";
    }
  }
  async function check() {
    if (busy) return;
    busy = "check";
    checkError = "";
    notice = "";
    try {
      const result = await finish<{ updates?: Record<string, string>; errors?: Record<string, string> }>(api("/external-modules/check", {}));
      updates = result.updates ?? {};
      checkErrors = result.errors ?? {};
      checked = true;
    } catch (cause) {
      checkError = message(cause);
    } finally {
      busy = null;
    }
  }
  async function update(item: ExternalModule) {
    if (busy) return;
    busy = `update:${item.id}`;
    moduleStore.addon = item.name;
    rowErrors = { ...rowErrors, [item.id]: "" };
    try {
      await onstate(await finish<ModulesState>(api("/external-modules/update", { id: item.id })));
      const rest = { ...updates };
      delete rest[item.id];
      updates = rest;
    } catch (cause) {
      rowErrors = { ...rowErrors, [item.id]: message(cause) };
    } finally {
      busy = null;
      moduleStore.addon = "";
    }
  }
  async function remove(item: ExternalModule) {
    if (busy || !window.confirm(`Remove ${item.name}?`)) return;
    busy = `remove:${item.id}`;
    rowErrors = { ...rowErrors, [item.id]: "" };
    try {
      await onstate(await api<ModulesState>("/external-modules/remove", { id: item.id }));
    } catch (cause) {
      rowErrors = { ...rowErrors, [item.id]: message(cause) };
    } finally {
      busy = null;
    }
  }
  /** A newer release than the installed one, found by the last check. */
  const newer = (item: ExternalModule) => (updates[item.id] && updates[item.id] !== item.version ? updates[item.id] : null);
  const upToDate = $derived(checked && !installed.some((item) => newer(item)) && !Object.keys(checkErrors).length);
</script>

<div class="addons">
<Panel title="Add-on modules" aria-label="Add-on modules">
  <p class="intro">Only install modules you trust. They can access your workspace.</p>

  {#if pending.length}
    <div class="banner" role="status">
      <strong>Restart rika! to finish.</strong>
    </div>
  {/if}

  <ul class="installed" aria-label="Installed add-on modules">
    {#each installed as item (item.id)}
      {@const status = STATUS[item.status]}
      {@const latest = newer(item)}
      <li class="module" data-module={item.id}>
        <div class="head">
          <strong class="name">{item.name}</strong>
          <span class="version" title={item.sha256 ? `SHA-256 ${item.sha256}` : undefined}>{item.version}</span>
          <Badge label={status.label} intent={status.intent} size="sm" />
          <span class="actions">
            {#if item.status !== "restart-to-remove"}
              <Button size="sm" variant="ghost" intent="danger" disabled={!!busy} onclick={() => remove(item)}>{busy === `remove:${item.id}` ? "Removing…" : "Remove"}</Button>
            {/if}
          </span>
        </div>
        <p class="facts">
          <a href={`https://github.com/${item.repo}`} target="_blank" rel="noopener noreferrer">{item.repo}</a>
          {#if item.license}<span>License: {item.license}</span>{/if}
        </p>
        {#if item.error}<p class="problem" role="alert">{item.error}</p>{/if}
        {#if latest && item.status !== "restart-to-remove"}
          <div class="update">
            <span>{item.version} → {latest}</span>
            <Button size="sm" intent="primary" disabled={!!busy} onclick={() => update(item)}>Update</Button>
          </div>
        {/if}
        {#if checkErrors[item.id]}<p class="problem" role="alert">Could not check for updates: {checkErrors[item.id]}</p>{/if}
        {#if rowErrors[item.id]}<p class="problem" role="alert">{rowErrors[item.id]}</p>{/if}
      </li>
    {:else}
      <li class="empty">No modules installed.</li>
    {/each}
  </ul>

  {#if installed.length}
    <div class="checks">
      <Button size="sm" disabled={!!busy} onclick={check}>{busy === "check" ? "Checking…" : "Check for updates"}</Button>
      {#if upToDate}<span class="muted" role="status">Up to date.</span>{/if}
      {#if checkError}<span class="problem" role="alert">{checkError}</span>{/if}
    </div>
  {/if}

  <form class="add" onsubmit={add} novalidate>
    <label for={`${uid}-repo`}>GitHub repository</label>
    <div class="field">
      <input id={`${uid}-repo`} type="text" placeholder="owner/repo" autocomplete="off" spellcheck="false"
        bind:value={repository} disabled={busy === "add"} aria-invalid={invalid ? "true" : undefined}
        aria-describedby={invalid ? `${uid}-repo-invalid` : undefined}
        oninput={() => { invalid = ""; addError = ""; }} />
      <Button type="submit" intent="primary" disabled={!!busy || !repository.trim()}>Add</Button>
    </div>
    {#if invalid}<p class="problem" id={`${uid}-repo-invalid`} role="alert">{invalid}</p>{/if}
    {#if addError}<p class="problem" role="alert">{addError}</p>{/if}
    {#if notice}<p class="muted" role="status">{notice}</p>{/if}
  </form>

  <div class="token">
    <span class="label">GitHub token</span>
    <Badge label={tokenSet ? "Set" : "Not set"} intent={tokenSet ? "success" : "neutral"} size="sm" />
    <span class="help">For private repositories.</span>
    <span class="actions"><Button size="sm" variant="ghost" onclick={onkeys}>{tokenSet ? "Change in Keys" : "Add in Keys"}</Button></span>
  </div>
</Panel>
</div>

<style>
  .addons { margin-top: 16px; }
  .intro, .help, .muted, .facts { color: var(--muted); line-height: 1.5; }
  .intro { margin: 0 0 8px; font-size: 13px; }
  .banner { margin: 8px 0 12px; padding: 10px 12px; border: 1px solid var(--warning); border-radius: 8px; font-size: 13px; }
  .installed { display: grid; margin: 8px 0 0; padding: 0; list-style: none; }
  .module, .empty { padding: 10px 0; border-top: 1px solid var(--hairline); font-size: 13px; }
  .empty { color: var(--muted); }
  .head { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
  .name { color: var(--ink); }
  .version { color: var(--muted); font: 500 12px var(--font-mono); }
  .actions { margin-left: auto; display: inline-flex; gap: 8px; }
  .facts, .problem { margin: 4px 0 0; }
  .facts { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; }
  .problem { color: var(--danger, var(--ink)); font-size: 12px; }
  .update { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 6px; }
  .checks { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 10px 0; border-top: 1px solid var(--hairline); }
  .add { display: grid; gap: 6px; padding: 12px 0; border-top: 1px solid var(--hairline); }
  label, .label { color: var(--ink); font-weight: 500; font-size: 13px; }
  .field { display: flex; gap: 8px; align-items: center; }
  .field input { flex: 1; min-width: 0; }
  .help { margin: 0; font-size: 12px; }
  .muted { margin: 0; font-size: 12px; }
  .token { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 10px; padding-top: 12px; border-top: 1px solid var(--hairline); }
</style>
