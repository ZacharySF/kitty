<script lang="ts">
  import { api } from "./api";
  import { buildStore, setPreferences } from "./build.svelte";
  import Select from "./ui/Select.svelte";

  type AgentInfo = { binary: string | null; session: string; cwd: string; default: string; kinds: string[] };
  let info = $state<AgentInfo | null>(null);
  let choosing = $state(false);
  let external = $state(false);
  let busy = $state(false);
  /** The build's `ui.terminal.coding_agent`, saved when an agent tab is launched. */
  let kind = $state<string>(buildStore.build.ui.terminal.coding_agent);
  let error = $state("");
  let notice = $state("");

  const quote = (text: string) => `'${text.replaceAll("'", "'\\''")}'`;
  const command = $derived(info?.binary ? `${quote(info.binary)} --session ${quote(info.session)}` : "");

  async function load() {
    try {
      info = await api<AgentInfo>("/terminal/agents");
      if (!info.kinds.includes(kind)) kind = info.default;
      error = info.binary ? "" : "The bundled herdr runtime is missing; reinstall kitty!";
    } catch (cause) { error = (cause as Error).message; }
  }
  async function toggleChooser() {
    choosing = !choosing;
    if (!choosing) return;
    kind = buildStore.build.ui.terminal.coding_agent;
    await load();
  }
  async function toggleExternal() {
    external = !external;
    if (external) await load();
  }
  async function launch() {
    if (busy) return;
    busy = true;
    error = notice = "";
    try {
      if (kind === "omp" || kind === "claude" || kind === "codex") void setPreferences({ ui: { terminal: { coding_agent: kind } } });
      const result = await api<{ tab: string; pane: string }>("/terminal/agents", { kind });
      notice = `Agent tab opened (${result.tab}, ${result.pane}).`;
      choosing = false;
    } catch (cause) { error = (cause as Error).message; }
    finally { busy = false; }
  }
</script>

<div class="agent-tools">
  <div class="agent-actions">
    <button class="text-button" aria-expanded={choosing} onclick={toggleChooser} disabled={busy}>New Agent tab</button>
    <button class="text-button" aria-expanded={external} onclick={toggleExternal}>Attach external agent</button>
    {#if notice}<span role="status">{notice}</span>{/if}
  </div>
  {#if choosing && info}
    <form onsubmit={(event) => { event.preventDefault(); void launch(); }}>
      <div class="field"><span>Coding agent</span>
        <Select label="Coding agent" bind:value={kind} disabled={busy} options={info.kinds.map((option) => ({ value: option, label: option }))} />
      </div>
      <button class="text-button" disabled={busy || !info.binary}>{busy ? "Starting agent…" : "Start agent"}</button>
      <span>Own Agent tab · herdr skill · workspace directory · installed agent required</span>
    </form>
  {/if}
  {#if error}<p role="alert">{error}</p>{/if}
  {#if external && info && command}
    <section class="external" aria-label="External agent attachment">
      <p>Use kitty!'s named session, never herdr's default. Open the terminal first. Each external agent creates its own labelled tab; use the returned workspace, tab and root_pane IDs, not another client's focused pane.</p>
      <pre>{command} workspace list
{command} tab create --workspace &lt;workspace-id&gt; --cwd {quote(info.cwd)} --label 'My agent' --no-focus
{command} --skill</pre>
      <p>Read herdr's skill before controlling panes. To view this same session from a terminal outside herdr:</p>
      <pre>env -u HERDR_ENV {command}</pre>
      <p>Tabs and agent states appear live here and in the dock's Agents strip. CLI access grants full shell access as your user.</p>
    </section>
  {/if}
</div>

<style>
  .agent-tools { flex: none; padding: 3px 8px; border-bottom: 1px solid var(--hairline); font: 10.5px var(--font-mono); color: var(--muted); }
  .agent-actions, form, .field { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
  form { padding: 4px 0; }
  p { margin: 4px 0; }
  [role="alert"] { color: var(--br-caution, #e8c872); }
  .external { max-height: 180px; overflow: auto; padding: 6px 0; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; color: var(--ink); margin: 6px 0; font: inherit; user-select: text; }
</style>
