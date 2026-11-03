<script lang="ts">
  import { onMount } from "svelte";
  import { Button, Panel } from "@nunera/bracket";
  import { api } from "./api";
  import TernPanes from "./TernPanes.svelte";
  import { ternBlockState, ternPluginState, type TernPluginStatus, type TernStatus } from "./terminal";

  /**
   * Stencil Tern beside herdr (docs/terminal-dock-design.md, Tern). A truthful status
   * panel, never a terminal: Tern's web client needs WebGPU, which rika!'s desktop web
   * view lacks, so the terminal itself opens in Tern's own window on rika!'s daemon.
   * Reading the status never starts Tern.
   */
  const POLL_MS = 3000;
  let status = $state<TernStatus | null>(null);
  let error = $state("");
  let busy = $state(false);
  let path = $state("");
  let editedPath = false;
  /** A broken saved path opens the executable section; it stays open until closed. */
  let showPath = $state(false);

  function adopt(next: TernStatus) {
    status = next;
    if (!editedPath) path = next.override ?? "";
    if (!next.installed && next.override) showPath = true;
  }
  async function load() {
    try {
      adopt(await api<TernStatus>("/terminal/tern"));
      if (!busy) error = "";
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  async function act(action: "open" | "start" | "settings", body: object = {}) {
    if (busy) return;
    busy = true;
    error = "";
    try {
      if (action === "settings") editedPath = false;
      adopt(await api<TernStatus>(`/terminal/tern/${action}`, body));
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  const size = (cols: number | null, rows: number | null) => (cols && rows ? `${cols}×${rows}` : "");
  const windowsLabel = (count: number) => (count ? `${count} Tern window${count === 1 ? "" : "s"} open` : "No Tern window open");

  /** The rika! plugin for Tern (platform design Q1). Reading it runs `tern plugin`, so it is read
   *  once per detected Tern and after each action, not on every status poll. */
  let plugin = $state<TernPluginStatus | null>(null);
  let pluginError = $state("");
  let pluginBusy = $state(false);
  let pluginFor: string | null = null;
  async function pluginAct(action: "install" | "remove" | null) {
    pluginBusy = true;
    pluginError = "";
    try {
      plugin = await api<TernPluginStatus>(action ? `/terminal/tern/plugin/${action}` : "/terminal/tern/plugin", action ? {} : undefined);
    } catch (cause) {
      pluginError = (cause as Error).message;
    } finally {
      pluginBusy = false;
    }
  }
  $effect(() => {
    const found = status?.installed ? status.path : null;
    if (found && found !== pluginFor) {
      pluginFor = found;
      void pluginAct(null);
    }
  });

  onMount(() => {
    void load();
    const timer = window.setInterval(() => { if (!document.hidden && !busy) void load(); }, POLL_MS);
    return () => window.clearInterval(timer);
  });
</script>

<section class="tern" aria-label="Stencil Tern">
  <header>
    <div>
      <h1>Tern</h1>
      <p>Stencil's native terminal, beside rika!'s herdr. rika! runs the Tern you installed on a session of its own; your own Tern sessions are never touched.</p>
    </div>
    {#if status?.installed}
      <div class="actions"><Button intent="primary" size="sm" disabled={busy} onclick={() => act("open")}>Open in Tern</Button></div>
    {/if}
  </header>

  {#if error}<p class="error" role="alert">{error}</p>{/if}

  {#if !status}
    {#if !error}<p class="muted" role="status">Looking for Tern…</p>{/if}
  {:else if !status.installed}
    <Panel title={status.problem ? "Tern cannot be used" : "Tern is not installed"}>
      {#if status.problem}<p class="error" role="alert">{status.problem}</p>{/if}
      <p>rika! never bundles Tern: it is Stencil's closed-beta app and needs your own installation and Stencil account.
        Install it from <a href={status.install_url} target="_blank" rel="noopener noreferrer">stencil.so/tern</a>,
        then choose Check again. herdr stays rika!'s built-in terminal.</p>
      <Button size="sm" disabled={busy} onclick={load}>Check again</Button>
    </Panel>
  {:else}
    <dl class="facts">
      <div><dt>Installed</dt><dd>Tern {status.version}</dd></div>
      <div><dt>{status.source === "settings" ? "From settings" : "On PATH"}</dt><dd><code>{status.path}</code></dd></div>
      <div><dt>Session</dt><dd>{status.running ? "rika!'s Tern session is running" : "Not running"}</dd></div>
      <div><dt>Windows</dt><dd>{windowsLabel(status.windows)}</dd></div>
    </dl>
    {#if !status.running}
      <Panel title="rika!'s Tern session">
        <p>rika!'s Tern session is not running. Open in Tern starts it in <code>{status.workspace}</code> and shows it in a Tern window; you can also start it without a window.</p>
        <Button size="sm" disabled={busy} onclick={() => act("start")}>Start rika!'s Tern session</Button>
      </Panel>
    {:else}
      <ul class="sessions" aria-label="rika!'s Tern sessions">
        {#each status.sessions as session (session.id)}
          {#each session.tabs as tab (tab.id)}
            {#each tab.blocks as block (block.id)}
              {@const live = ternBlockState(block)}
              <li class:focused={block.focused}>
                <span class="where">{session.name} · tab {tab.number ?? "?"}{tab.name ? ` ${tab.name}` : ""}</span>
                <span class="title">{block.title || block.program}</span>
                <span class="cwd"><code>{block.cwd}</code></span>
                <span class="size">{size(block.cols, block.rows)}</span>
                <span class="state" data-state={live.state}>{live.label}</span>
              </li>
            {/each}
          {/each}
        {/each}
      </ul>
      <p class="muted">Scripts and agents can drive this session with Tern's own commands:
        <code>TERN_DAEMON_SOCKET={status.socket} tern ls</code></p>
    {/if}
    <TernPanes tern={status} onstatus={adopt} />
    <p class="muted">rika! never draws a Tern terminal itself: Tern's web client draws with WebGPU, which rika!'s window does not provide, so panes show in Tern's own window. Quitting rika! closes the Tern windows it opened and stops its Tern session.</p>
    <Panel title="rika! plugin for Tern">
      <p>Adds commands to your own Tern that send selections, panes, failed commands and web pages to rika!'s Inbox, and a rika! Inbox block.
        The first time, Tern asks to pair: approve its code in the bar above or in Settings → Connected apps.</p>
      {#if pluginError}<p class="error" role="alert">{pluginError}</p>{/if}
      {#if plugin}
        {@const shown = ternPluginState(plugin)}
        <p class="muted" role="status" data-plugin-state={shown.action ?? (plugin.installed ? "installed" : "unavailable")}>
          {shown.label}{plugin.installed && plugin.tern_status ? ` · Tern reports: ${plugin.tern_status}` : ""}</p>
        {#if shown.problem}<p class="error" role="alert">{shown.problem}</p>{/if}
        {#if shown.action || plugin.installed}
          <div class="actions">
            {#if shown.action}
              <Button intent="primary" size="sm" disabled={pluginBusy} onclick={() => pluginAct("install")}>
                {shown.action === "update" ? "Update the plugin in Tern" : "Install rika! plugin into Tern"}</Button>
            {/if}
            {#if plugin.installed}
              <Button size="sm" variant="ghost" disabled={pluginBusy} onclick={() => pluginAct("remove")}>Remove from Tern</Button>
            {/if}
          </div>
        {/if}
      {:else if !pluginError}
        <p class="muted" role="status">Checking Tern's plugins…</p>
      {/if}
    </Panel>
  {/if}

  {#if status}
    <details bind:open={showPath}>
      <summary>Tern executable</summary>
      <p class="muted">rika! uses <code>tern</code> from PATH unless a path is saved here.</p>
      <label class="path">Tern executable path
        <input type="text" spellcheck="false" placeholder="/home/you/.local/bin/tern" bind:value={path} oninput={() => { editedPath = true; }} />
      </label>
      <div class="actions">
        <Button size="sm" disabled={busy || !path.trim()} onclick={() => act("settings", { path: path.trim() })}>Save path</Button>
        <Button size="sm" variant="ghost" disabled={busy || !status.override} onclick={() => { path = ""; void act("settings", { path: "" }); }}>Use tern on PATH</Button>
      </div>
    </details>
  {/if}
</section>

<style>
  .tern { display: flex; flex-direction: column; gap: .8rem; min-width: 0; max-width: 960px; margin: 0 auto; }
  header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem; }
  h1 { margin: 0; font-size: 1.6rem; }
  header p { margin: .3rem 0 0; color: var(--muted); max-width: 60ch; line-height: 1.5; }
  .actions { display: flex; align-items: center; gap: .65rem; flex-wrap: wrap; }
  .muted { color: var(--muted); font-size: .85rem; line-height: 1.5; margin: 0; }
  .error { color: var(--br-danger, var(--danger, #e5484d)); margin: 0; }
  .facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .6rem; margin: 0; }
  .facts div { padding: .6rem .75rem; border: 1px solid var(--border-fine, var(--border)); border-radius: 8px; min-width: 0; }
  dt { color: var(--muted); font: 11px var(--font-mono); letter-spacing: .08em; text-transform: uppercase; }
  dd { margin: .25rem 0 0; overflow-wrap: anywhere; }
  .sessions { display: grid; gap: .4rem; margin: 0; padding: 0; list-style: none; }
  .sessions li { display: grid; grid-template-columns: minmax(110px, auto) minmax(120px, 1fr) minmax(0, 2fr) auto auto; align-items: center;
    gap: .75rem; padding: .5rem .75rem; border: 1px solid var(--hairline, var(--border)); border-radius: 8px; font: 12px var(--font-mono); }
  .sessions li.focused { border-color: color-mix(in srgb, var(--ink) 30%, transparent); }
  .where, .size, .cwd { color: var(--muted); }
  .cwd { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .title { color: var(--ink); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .state { text-transform: uppercase; letter-spacing: .08em; font-size: 11px; color: var(--muted); }
  .state::before { content: ""; display: inline-block; width: 7px; height: 7px; margin-right: 5px; border-radius: 999px; background: currentColor; vertical-align: 1px; }
  .state[data-state="working"] { color: var(--br-reference, #9cc7f0); }
  .state[data-state="exited"] { color: var(--br-dim, #8d8d8d); }
  details { padding: .8rem; border: 1px solid var(--border); border-radius: .5rem; display: grid; gap: .6rem; }
  summary { cursor: pointer; }
  .path { display: grid; gap: .35rem; font-size: .85rem; }
  .path input { font: 13px var(--font-mono); padding: .45rem .6rem; }
  code { overflow-wrap: anywhere; }
</style>
