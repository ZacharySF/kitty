<script lang="ts">
  import { Badge, Button, Panel } from "@nunera/bracket";
  import { api, type ExternalModule, type ModulesState, type TaskRecord } from "./api";
  import { parseRepository } from "./externalModules";

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
    const before = new Set(installed.map((item) => item.id));
    try {
      const next = await finish<ModulesState>(api("/external-modules/install", { repo }));
      await onstate(next);
      const added = next.external.installed.find((item) => !before.has(item.id)) ?? next.external.installed.find((item) => item.repo === repo);
      notice = added ? `${added.name} ${added.version} was downloaded and its checksum matched.` : "";
      repository = "";
    } catch (cause) {
      addError = message(cause);
    } finally {
      busy = null;
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
    }
  }
  async function remove(item: ExternalModule) {
    if (busy || !window.confirm(`Remove ${item.name}? Its page goes away after you restart rika!.`)) return;
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
  <p class="intro">
    Add-on modules add new pages to rika!. Each one comes from a GitHub repository: rika! downloads
    the repository's latest release and installs it only if the file matches the SHA-256 checksum
    published with that release. Changes apply after you restart rika!.
  </p>
  <p class="intro">Add only modules you trust: a module runs on this computer and can read your workspace.</p>

  {#if pending.length}
    <div class="banner" role="status">
      <strong>Restart rika! to finish.</strong>
      <ul>{#each pending as item (item.id)}<li>{item.name}: {item.message}</li>{/each}</ul>
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
        {#if item.description}<p class="about">{item.description}</p>{/if}
        <p class="facts">
          <a href={`https://github.com/${item.repo}`} target="_blank" rel="noopener noreferrer">{item.repo}</a>
          {#if item.license}<span>License: {item.license}</span>{/if}
          <span>{item.kind === "process" ? "Runs as its own app on this computer" : "Runs inside rika!"}</span>
        </p>
        {#if item.message}<p class="message" class:warn={item.status !== "active"}>{item.message}</p>{/if}
        {#if item.error}<p class="problem" role="alert">{item.error}</p>{/if}
        {#if latest && item.status !== "restart-to-remove"}
          <div class="update">
            <span>Update available: {item.version} → {latest}</span>
            <Button size="sm" intent="primary" disabled={!!busy} onclick={() => update(item)}>{busy === `update:${item.id}` ? "Downloading and checking…" : "Update"}</Button>
          </div>
        {/if}
        {#if checkErrors[item.id]}<p class="problem" role="alert">Could not check for updates: {checkErrors[item.id]}</p>{/if}
        {#if rowErrors[item.id]}<p class="problem" role="alert">{rowErrors[item.id]}</p>{/if}
      </li>
    {:else}
      <li class="empty">No add-on modules yet. Add one below.</li>
    {/each}
  </ul>

  {#if installed.length}
    <div class="checks">
      <Button size="sm" disabled={!!busy} onclick={check}>{busy === "check" ? "Checking…" : "Check for updates"}</Button>
      {#if upToDate}<span class="muted" role="status">Every add-on module is up to date.</span>{/if}
      {#if checkError}<span class="problem" role="alert">{checkError}</span>{/if}
    </div>
  {/if}

  <form class="add" onsubmit={add} novalidate>
    <h3>Add a module</h3>
    <label for={`${uid}-repo`}>GitHub repository</label>
    <div class="field">
      <input id={`${uid}-repo`} type="text" placeholder="owner/repo" autocomplete="off" spellcheck="false"
        bind:value={repository} disabled={busy === "add"} aria-invalid={invalid ? "true" : undefined}
        aria-describedby={`${uid}-repo-help${invalid ? ` ${uid}-repo-invalid` : ""}`}
        oninput={() => { invalid = ""; addError = ""; }} />
      <Button type="submit" intent="primary" disabled={!!busy || !repository.trim()}>{busy === "add" ? "Downloading and checking…" : "Add module"}</Button>
    </div>
    <p class="help" id={`${uid}-repo-help`}>
      The repository's owner and name as on GitHub, like <code>octo-org/rika-demo</code>. A full
      github.com link works too.
    </p>
    {#if invalid}<p class="problem" id={`${uid}-repo-invalid`} role="alert">{invalid}</p>{/if}
    {#if busy === "add"}<p class="muted" role="status">Downloading and checking…</p>{/if}
    {#if addError}<p class="problem" role="alert">{addError}</p>{/if}
    {#if notice}<p class="muted" role="status">{notice}</p>{/if}
  </form>

  <div class="token">
    <span class="label">GitHub token</span>
    <Badge label={tokenSet ? "Set" : "Not set"} intent={tokenSet ? "success" : "neutral"} size="sm" />
    <span class="help">Needed only for private repositories. Stored as <code>GITHUB</code> in Keys.</span>
    <span class="actions"><Button size="sm" variant="ghost" onclick={onkeys}>{tokenSet ? "Change in Keys" : "Add in Keys"}</Button></span>
  </div>
</Panel>
</div>

<style>
  .addons { margin-top: 16px; }
  .intro, .help, .muted, .facts, .about { color: var(--muted); line-height: 1.5; }
  .intro { margin: 0 0 8px; font-size: 13px; }
  .banner { margin: 8px 0 12px; padding: 10px 12px; border: 1px solid var(--warning); border-radius: 8px; font-size: 13px; }
  .banner ul { margin: 4px 0 0; padding-left: 1.2rem; }
  .installed { display: grid; margin: 8px 0 0; padding: 0; list-style: none; }
  .module, .empty { padding: 10px 0; border-top: 1px solid var(--hairline); font-size: 13px; }
  .empty { color: var(--muted); }
  .head { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
  .name { color: var(--ink); }
  .version { color: var(--muted); font: 500 12px var(--font-mono); }
  .actions { margin-left: auto; display: inline-flex; gap: 8px; }
  .about, .facts, .message, .problem { margin: 4px 0 0; }
  .facts { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; }
  .message.warn { color: var(--ink); }
  .message.warn::before { content: ""; display: inline-block; width: 6px; height: 6px; margin-right: 7px; border-radius: 999px; background: var(--warning); vertical-align: 1px; }
  .problem { color: var(--danger, var(--ink)); font-size: 12px; }
  .update { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 6px; }
  .checks { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; padding: 10px 0; border-top: 1px solid var(--hairline); }
  .add { display: grid; gap: 6px; padding: 12px 0; border-top: 1px solid var(--hairline); }
  h3 { margin: 0 0 2px; font-size: 14px; }
  label, .label { color: var(--ink); font-weight: 500; font-size: 13px; }
  .field { display: flex; gap: 8px; align-items: center; }
  .field input { flex: 1; min-width: 0; }
  .help { margin: 0; font-size: 12px; }
  .muted { margin: 0; font-size: 12px; }
  .token { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 10px; padding-top: 12px; border-top: 1px solid var(--hairline); }
  code { overflow-wrap: anywhere; }
</style>
