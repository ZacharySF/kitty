<script lang="ts">
  import { api } from "./api";
  import { popOutQuickNote } from "./quickNotes";
  import { ternBlockState, ternEventLabel, ternPaneLabel, ternPanes, ternTarget, type TernStatus } from "./terminal";

  /**
   * The panes of rika!'s own Tern session in the Tern view (docs/terminal-dock-design.md,
   * Tern): their live state, one command line for a pane, focus in rika!'s Tern window
   * and capture to a quick note. `tern` is the view's status (it polls); every action
   * hands the status it answers back through `onstatus`.
   */
  let { tern, onstatus }: { tern: TernStatus; onstatus: (next: TernStatus) => void } = $props();
  let busy = $state(false);
  let note = $state("");
  let failed = $state(false);
  let savedNote = $state<number | null>(null);
  let command = $state("");
  let chosen = $state<number | null>(null);
  const panes = $derived(ternPanes(tern));
  const target = $derived(ternTarget(panes, chosen));

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
    onstatus(await api<TernStatus>(`/terminal/tern/${path}`, body));
    return done;
  });
  const addPane = () => act(async () => {
    const before = new Set(panes.map((pane) => pane.id));
    const next = await api<TernStatus>("/terminal/tern/new", {});
    onstatus(next);
    // The new pane is where the next command goes.
    chosen = ternPanes(next).find((pane) => !before.has(pane.id))?.id ?? chosen;
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

</script>

<section class="panes" aria-label="Tern panes">
  <header>
    <h2>Panes</h2>
    <button disabled={busy} onclick={addPane}>New pane</button>
  </header>
    {#if !tern.running}
      <p class="tern-note" role="status">No panes yet: New pane starts the session with one.</p>
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

<style>
  .panes {
    display: grid;
    gap: 8px;
    padding: .8rem;
    border: 1px solid var(--hairline, var(--border));
    border-radius: 8px;
    font: 11px var(--font-mono);
    letter-spacing: 0.08em;
  }
  header {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  h2 {
    margin: 0 auto 0 0;
    color: var(--muted);
    font: inherit;
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
