<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "./api";
  import { popOutQuickNote } from "./quickNotes";
  import { ternBlockState, ternEventLabel, ternPaneLabel, ternPanes, ternTarget, type TernStatus } from "./terminal";

  /**
   * The panes of rika!'s own Tern session in the dock (docs/terminal-dock-design.md,
   * Tern): their live state, one command line for a pane, focus in rika!'s Tern
   * window and capture to a quick note. Renders nothing unless Tern is installed.
   */
  const POLL_MS = 3000;
  let tern = $state<TernStatus | null>(null);
  let busy = $state(false);
  let note = $state("");
  let failed = $state(false);
  let savedNote = $state<number | null>(null);
  let command = $state("");
  let chosen = $state<number | null>(null);
  const panes = $derived(tern ? ternPanes(tern) : []);
  const target = $derived(ternTarget(panes, chosen));

  async function load() {
    try {
      tern = await api<TernStatus>("/terminal/tern");
    } catch (cause) {
      // An absent route (module off) or an unreadable daemon: keep what was shown, say why.
      if (tern?.installed) {
        note = (cause as Error).message;
        failed = true;
      }
    }
  }
  async function act(run: () => Promise<string>) {
    if (busy) return;
    busy = true;
    savedNote = null;
    try {
      note = await run();
      failed = false;
    } catch (cause) {
      note = (cause as Error).message;
      failed = true;
    } finally {
      busy = false;
    }
  }
  const update = (path: string, body: object, done: string) => act(async () => {
    tern = await api<TernStatus>(`/terminal/tern/${path}`, body);
    return done;
  });
  const addPane = () => act(async () => {
    const before = new Set(panes.map((pane) => pane.id));
    tern = await api<TernStatus>("/terminal/tern/new", {});
    // The new pane is where the next command goes.
    chosen = ternPanes(tern).find((pane) => !before.has(pane.id))?.id ?? chosen;
    return "Opened a new pane in rika!'s Tern session.";
  });
  async function runCommand(event: SubmitEvent) {
    event.preventDefault();
    if (!target || !command.trim()) return;
    const label = ternPaneLabel(target);
    await update("run", { block: target.id, text: command }, `Ran in ${label}.`);
    if (!failed) command = "";
  }
  const capture = (id: number) => act(async () => {
    const saved = await api<{ note_id: number; truncated: boolean }>("/terminal/tern/capture", { block: id });
    savedNote = saved.note_id;
    return `Saved to quick note #${saved.note_id}${saved.truncated ? " (newest output only)" : ""}.`;
  });

  onMount(() => {
    void load();
    const timer = window.setInterval(() => { if (!document.hidden && !busy) void load(); }, POLL_MS);
    return () => window.clearInterval(timer);
  });
</script>

{#if tern?.installed}
  <section class="tern" aria-label="Tern">
    <header>
      <p class="heading"><span class="name">Tern</span> <span class="meta">kitty session · {tern.version}{tern.windows ? ` · ${tern.windows} window${tern.windows === 1 ? "" : "s"}` : ""}</span></p>
      <button disabled={busy} onclick={addPane}>New pane</button>
      <button disabled={busy} onclick={() => update("open", {}, "Opened a Tern window on rika!'s session.")}>Open in Tern</button>
    </header>
    {#if !tern.running}
      <p class="tern-note" role="status">rika!'s Tern session is not running. New pane starts it.</p>
    {:else if !panes.length}
      <p class="tern-note" role="status">rika!'s Tern session has no panes. New pane adds one.</p>
    {:else}
      <ul aria-label="Tern panes">
        {#each panes as pane (pane.id)}
          {@const label = ternPaneLabel(pane)}
          {@const live = ternBlockState(pane)}
          <li class="pane" class:focused={pane.focused}>
            <span class="name" title={pane.cwd ?? ""}>{label}</span>
            <span class="state" data-state={live.state}>{live.label}</span>
            <button disabled={busy} aria-label={`Focus ${label} in Tern`}
              onclick={() => update("focus", { block: pane.id }, `Showing ${label} in rika!'s Tern window.`)}>Focus</button>
            {#if tern.quick_notes}
              <button disabled={busy} aria-label={`Capture ${label} to a quick note`} onclick={() => capture(pane.id)}>Capture</button>
            {/if}
          </li>
        {/each}
      </ul>
      <form class="run" onsubmit={runCommand}>
        <select aria-label="Tern pane" value={target?.id ?? ""} onchange={(event) => { chosen = Number(event.currentTarget.value); }}>
          {#each panes as pane (pane.id)}
            <option value={pane.id}>{ternPaneLabel(pane)} · {pane.id}</option>
          {/each}
        </select>
        <input type="text" aria-label="Command line for the Tern pane" placeholder="One command line" spellcheck="false"
          autocomplete="off" maxlength="4096" bind:value={command} />
        <button type="submit" disabled={busy || !command.trim() || !target || target.exited !== null}>Run</button>
      </form>
    {/if}
    {#if tern.activity.length}
      <ol aria-label="Recent Tern activity">
        {#each tern.activity as event, index (index)}
          <li><span class="state" data-state={event.event === "pane_spawned" ? "working" : "exited"}>{ternEventLabel(event)}</span></li>
        {/each}
      </ol>
    {/if}
    {#if note}
      <p class="tern-note" role={failed ? "alert" : "status"}>{note}
        {#if savedNote !== null}<button onclick={() => popOutQuickNote(savedNote!)}>Open note</button>{/if}</p>
    {/if}
  </section>
{/if}

<style>
  .tern {
    display: grid;
    gap: 8px;
    margin-top: 12px;
    padding-top: 10px;
    border-top: 1px solid var(--hairline);
    font: 11px var(--font-mono);
    letter-spacing: 0.08em;
  }
  header {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  .heading {
    margin: 0 auto 0 0;
    color: var(--muted);
    text-transform: uppercase;
  }
  .tern-note {
    margin: 0;
    color: var(--muted);
  }
  .tern-note[role="alert"] { color: var(--br-danger, #ee928c); }
  ul,
  ol {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .pane {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 4px 6px 4px 12px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 40%, transparent);
  }
  .pane.focused { border-color: color-mix(in srgb, var(--ink) 30%, transparent); }
  .name { color: var(--ink); font-weight: 600; }
  .meta { color: var(--muted); }
  .state { color: var(--muted); text-transform: uppercase; }
  .state::before {
    content: "";
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-right: 5px;
    border-radius: 999px;
    background: currentColor;
    vertical-align: 1px;
  }
  .state[data-state="working"] { color: var(--br-reference, #9cc7f0); }
  .state[data-state="exited"] { color: var(--br-dim, #8d8d8d); }
  .run {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .run select,
  .run input {
    min-width: 0;
    padding: 4px 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--ink);
    font: 11px var(--font-mono);
  }
  .run select { flex: 0 1 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .run input { flex: 1; }
  button {
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    padding: 3px 10px;
    font: 500 10px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
    cursor: pointer;
  }
  button:hover:not(:disabled),
  button:focus-visible { color: var(--ink); }
  button:disabled { opacity: 0.5; cursor: default; }
</style>
