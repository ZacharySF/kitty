<script lang="ts">
  import { onMount } from "svelte";
  import { Panel } from "@nunera/bracket";
  import { api, ApiError } from "../api";
  import EmptyState from "../EmptyState.svelte";
  import Popover from "../ui/Popover.svelte";
  import StatusDot from "../ui/StatusDot.svelte";
  import AgentSessionDrawer from "./AgentSessionDrawer.svelte";
  import GuaranteeBadge from "./GuaranteeBadge.svelte";
  import { STATE_LABELS, canApply, flipSummary, shortId, type AgentsOverview, type Simulation } from "./agents";

  /**
   * Settings → Agents (kitty.web_agents): what this computer can guarantee an agent tab,
   * the agent sessions (one opens in the side drawer with its journal), and the policy in
   * force with its source. Simulate replays the journal under the edited source and lists
   * every decision it would flip; Apply puts exactly that simulated source in force after
   * a confirmation. Retention, purposes and tools are read-only.
   */
  const FLIPS_SHOWN = 50;
  const LINE = 20;
  let overview = $state<AgentsOverview | null>(null);
  let error = $state("");
  let selected = $state<string | null>(null);
  let source = $state("");
  let simulation = $state<Simulation | null>(null);
  /** The source the shown simulation replayed; Apply needs it to equal the editor's text. */
  let simulated = $state<string | null>(null);
  let problems = $state<string[]>([]);
  let busy = $state("");
  let applying = $state(false);
  let allFlips = $state(false);
  let notice = $state("");
  let scrollTop = $state(0);

  const active = $derived(overview?.policy.source ?? "");
  const dirty = $derived(!!overview && source !== active);
  const applicable = $derived(!busy && canApply(source, simulated, active));
  const lines = $derived(Math.max(1, source.split("\n").length));
  const applied = $derived(overview?.versions.find((item) => item.version === overview?.policy.version)?.created_at ?? null);
  const flips = $derived(simulation ? (allFlips ? simulation.flips : simulation.flips.slice(0, FLIPS_SHOWN)) : []);
  const names = $derived(new Map((overview?.sessions ?? []).map((session) => [session.id, session.name])));
  const reasons = $derived(Object.entries(overview?.retention.reasons ?? {}));
  const REASONS: Record<string, string> = { replay_window: "Replay window", pinned_citation: "Pinned citations", user_hold: "Holds" };

  async function refresh() {
    try {
      const next = await api<AgentsOverview>("/agents");
      // The editor keeps a draft; it follows the policy only while it shows the policy.
      if (!overview || source === overview.policy.source) source = next.policy.source;
      overview = next;
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  onMount(() => void refresh());

  async function run(key: string, work: () => Promise<void>) {
    if (busy) return;
    busy = key;
    notice = "";
    try {
      await work();
      error = "";
    } catch (cause) {
      if (cause instanceof ApiError && cause.problems.length) problems = cause.problems.map(String);
      else error = (cause as Error).message;
    } finally {
      busy = "";
    }
  }
  const simulate = () => run("simulate", async () => {
    const text = source;
    problems = [];
    simulation = null;
    simulated = null;
    allFlips = false;
    simulation = await api<Simulation>("/agents/policy/simulate", { source: text });
    simulated = text;
  });
  const apply = () => run("apply", async () => {
    applying = false;
    problems = [];
    const result = await api<AgentsOverview & { version: number }>("/agents/policy/apply", { source: simulated });
    overview = result;
    source = result.policy.source;
    simulation = null;
    simulated = null;
    notice = `Version ${result.version} applied.`;
  });
  function revert() {
    source = active;
    problems = [];
  }

  const stamp = (value: string | null) => {
    const date = new Date(value ?? "");
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };
  const count = (value: number) => value.toLocaleString("en");
</script>

<Panel title="Agents" aria-label="Agents">
  {#if overview}
    {@const host = overview.host}
    <div class="rows">
      <section class="group" aria-label="This computer">
        <div class="set-row section"><span class="set-label">This computer</span></div>
        <div class="set-row">
          <span class="set-label">Kernel</span><span class="set-desc mono">{host.kernel}</span>
        </div>
        <div class="set-row">
          <span class="set-label">Landlock</span><span class="set-desc mono">{host.abi ? `ABI ${host.abi}` : "Not available"}</span>
        </div>
        <div class="set-row">
          <span class="set-label">Guarantee</span><GuaranteeBadge guarantee={host.guarantee} />
        </div>
        <div class="set-row top">
          <span class="set-label">Still open</span>
          {#if host.open.length}
            <ul class="open" aria-label="Still open">
              {#each host.open as route (route)}<li>{route}</li>{/each}
            </ul>
          {:else}<span class="set-desc">Nothing</span>{/if}
        </div>
      </section>

      <section class="group" aria-label="Sessions">
        <div class="set-row section">
          <span class="set-label">Sessions</span><span class="set-desc num">{count(overview.sessions.length)}</span>
        </div>
        {#if overview.sessions.length}
          <div class="set-row table">
            <table>
              <thead><tr><th>Agent</th><th>State</th><th>Guarantee</th><th>Started</th><th class="n">Accesses</th></tr></thead>
              <tbody>
                {#each overview.sessions as session (session.id)}
                  <tr class:selected={session.id === selected} data-state={session.state}>
                    <td>
                      <button type="button" class="open-session" aria-label={session.name} aria-expanded={session.id === selected} onclick={() => (selected = session.id)}
                        ><strong>{session.name}</strong></button>
                      <small>{[session.kind, session.host, session.pane].filter(Boolean).join(" · ")}</small>
                    </td>
                    <td>{STATE_LABELS[session.state] ?? session.state}{#if session.held}<span class="held" title="Kept past the History window">Held</span>{/if}</td>
                    <td><GuaranteeBadge guarantee={session.guarantee} detail={session.message ?? ""} /></td>
                    <td class="when">{stamp(session.created_at)}</td>
                    <td class="n">{count(session.accesses)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {:else}
          <div class="set-row empty">
            <EmptyState level={3} title="No agent sessions yet">
              <p>Each Agent tab rika! starts appears here with its journal.</p>
            </EmptyState>
          </div>
        {/if}
      </section>

      <section class="group" aria-label="Policy">
        <div class="set-row section">
          <span class="set-label">Policy</span>
          <span class="set-desc policy-meta">
            <span>Version {overview.policy.version}</span>
            <span class="mode">{overview.policy.mode === "observe" ? "Observe" : "Enforce"}</span>
            <span>{overview.policy.version === 0 ? "Built-in" : applied ? `Applied ${stamp(applied)}` : "Applied"}</span>
          </span>
          {#if dirty}<StatusDot state="unsaved" detail={`Draft differs from version ${overview.policy.version}`} />{/if}
        </div>
        <div class="set-row editor-row">
          <div class="editor">
            <div class="gutter" aria-hidden="true">
              <div style:transform={`translateY(${-scrollTop}px)`}>
                {#each { length: lines } as _, index (index)}<div class="number" style:height={`${LINE}px`}>{index + 1}</div>{/each}
              </div>
            </div>
            <textarea bind:value={source} aria-label="Policy source (YAML)" aria-invalid={problems.length > 0} spellcheck="false" autocomplete="off" wrap="off"
              rows={Math.min(Math.max(lines, 8), 22)} oninput={() => (problems = [])} onscroll={(event) => (scrollTop = event.currentTarget.scrollTop)}></textarea>
          </div>
        </div>
        {#if problems.length}
          <ul class="problems" aria-label="Policy problems">
            {#each problems as problem (problem)}<li>{problem}</li>{/each}
          </ul>
        {/if}
        <div class="set-row actions">
          {#if dirty}<button type="button" class="text-button" disabled={!!busy} onclick={revert}>Revert</button>{/if}
          <span class="set-end">
            <button type="button" class="pill" disabled={!!busy} onclick={simulate}>{busy === "simulate" ? "Simulating…" : "Simulate"}</button>
            <Popover bind:open={applying} label="Apply this policy?" placement="top-end" class="agent-confirm">
              {#snippet trigger(props)}<button type="button" class="pill primary" disabled={!applicable} title={applicable ? "" : "Simulate this source first"} {...props}
                  >{busy === "apply" ? "Applying…" : "Apply"}</button>{/snippet}
              <div class="confirm">
                <strong>Apply as version {(overview.versions[0]?.version ?? 0) + 1}?</strong>
                <p>Agents get these rules on their next access.</p>
                {#if simulation}<p class="num">{flipSummary(simulation)}</p>{/if}
                <div class="confirm-actions">
                  <button type="button" class="pill" onclick={() => (applying = false)}>Cancel</button>
                  <button type="button" class="pill primary" onclick={apply}>Apply policy</button>
                </div>
              </div>
            </Popover>
          </span>
        </div>
        {#if notice}<p class="set-note" role="status">{notice}</p>{/if}
        {#if simulation}
          <div class="set-row table simulation" class:stale={simulated !== source} role="region" aria-label="Simulation"
            title={simulated !== source ? "Edited since this simulation: simulate again to apply" : undefined}>
            <p class="summary num">{flipSummary(simulation)}{#if !simulation.enforced} · observe mode{/if}</p>
            {#if simulation.flips.length}
              <table aria-label="Flips">
                <thead><tr><th>When</th><th>Agent</th><th>Access</th><th>Block</th><th class="end">Change</th></tr></thead>
                <tbody>
                  {#each flips as flip (flip.id)}
                    <tr data-draft={flip.draft}>
                      <td class="when">{stamp(flip.at)}</td>
                      <td title={flip.session}>{names.get(flip.session) ?? flip.name}</td>
                      <td><strong>{flip.op === "write" ? "Write" : "Read"}</strong> {flip.purpose} <code>{flip.tool}</code></td>
                      <td><code title={flip.block_id ?? ""}>{shortId(flip.block_id) || "—"}</code></td>
                      <td class="end change">{flip.recorded} → <b>{flip.draft}</b></td>
                    </tr>
                  {/each}
                </tbody>
              </table>
              {#if simulation.flips.length > flips.length}
                <button type="button" class="text-button" onclick={() => (allFlips = true)}>Show all {count(simulation.flips.length)}</button>
              {/if}
              {#if simulation.truncated}<p class="set-note">Listing the first {count(simulation.flips.length)} of {count(simulation.total)}.</p>{/if}
            {/if}
          </div>
        {/if}
      </section>

      <section class="group" aria-label="Retention">
        <div class="set-row section"><span class="set-label">Retention</span></div>
        <div class="set-row">
          <span class="set-label">Journal rows</span><span class="set-desc num">{count(overview.retention.journal_rows)}</span>
        </div>
        <div class="set-row">
          <span class="set-label" title="History compaction folds nothing at or above this op_log seq">Kept from</span>
          <span class="set-desc num">{overview.retention.floor === null ? "Nothing kept" : `seq ${count(overview.retention.floor)}`}</span>
        </div>
        <div class="set-row">
          <span class="set-label">Reasons</span>
          <span class="set-desc">{reasons.length ? reasons.map(([reason, n]) => `${REASONS[reason] ?? reason} ${count(n)}`).join(" · ") : "None"}</span>
        </div>
        <div class="set-row">
          <span class="set-label" title="op_log rows the reasons may keep past the History window">Cap</span>
          <span class="set-desc num">{count(overview.retention.cap)} rows{#if overview.retention.over_cap.length} · {count(overview.retention.over_cap.length)} released{/if}</span>
        </div>
      </section>

      <section class="group" aria-label="Purposes">
        <div class="set-row section"><span class="set-label">Purposes</span></div>
        <div class="set-row table">
          <dl class="pairs">
            {#each Object.entries(overview.purposes) as [purpose, description] (purpose)}<dt><code>{purpose}</code></dt><dd>{description}</dd>{/each}
          </dl>
        </div>
      </section>

      <section class="group" aria-label="Tools">
        <div class="set-row section"><span class="set-label">Tools</span></div>
        <div class="set-row table">
          <ul class="tools">
            {#each Object.entries(overview.tools) as [tool, about] (tool)}<li><code>{tool}</code><span>{about.op} · {about.scope}</span></li>{/each}
          </ul>
        </div>
      </section>
    </div>
  {:else if !error}
    <p class="set-note" role="status">Loading agents…</p>
  {/if}
  {#if error}<p class="set-note" role="alert">{error} <button class="text-button" onclick={refresh}>Retry</button></p>{/if}
</Panel>

{#if selected && overview}
  <AgentSessionDrawer id={selected} purposes={overview.purposes} onclose={() => (selected = null)} onchange={() => void refresh()} />
{/if}

<style>
  .group {
    display: contents;
  }
  .group:first-child > .set-row:first-child {
    border-top: 0;
  }
  .section .set-label {
    color: var(--muted);
    font: 600 var(--fs-label) var(--font-ui);
  }
  /* `.group >` outranks Settings' own `.page .set-row` (same specificity, later in the bundle). */
  .group > .set-row.top {
    align-items: start;
  }
  .mono,
  code {
    font: 11px var(--font-mono);
  }
  .num {
    color: var(--muted);
    font: 11px var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  ul,
  dl {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .open {
    display: grid;
    gap: 3px;
    color: var(--ink);
    font-size: 12.5px;
    line-height: 1.4;
  }
  .group > .set-row.table,
  .group > .set-row.empty {
    display: block;
    padding-top: 4px;
    padding-bottom: 8px;
  }
  .group > .set-row.empty {
    display: grid;
    justify-items: center;
  }
  .empty p {
    margin: 0;
    color: var(--muted);
    font-size: 12.5px;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
  }
  th {
    padding: 6px 8px 6px 0;
    color: var(--muted);
    font: 600 var(--fs-label) var(--font-ui);
    text-align: left;
    white-space: nowrap;
  }
  td {
    padding: 6px 8px 6px 0;
    border-top: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
    color: var(--ink);
    vertical-align: middle;
  }
  td small {
    display: block;
    margin-top: 1px;
    color: var(--muted);
    font: 10.5px var(--font-mono);
  }
  .n {
    font: 11px var(--font-mono);
    font-variant-numeric: tabular-nums;
    text-align: right;
    white-space: nowrap;
  }
  th.n {
    font: 600 var(--fs-label) var(--font-ui);
  }
  .end {
    padding-right: 0;
    text-align: right;
  }
  .when {
    color: var(--muted);
    white-space: nowrap;
  }
  tbody tr:hover td,
  tr.selected td {
    background: color-mix(in srgb, var(--ink) 4%, transparent);
  }
  tr.selected td:first-child {
    box-shadow: inset 2px 0 var(--accent);
  }
  .open-session {
    padding: 0;
    border: 0;
    background: transparent;
    color: var(--ink);
    font: 600 12.5px var(--font-ui);
    text-align: left;
    cursor: pointer;
  }
  .open-session:hover strong {
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .open-session:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  tr[data-state="ended"] .open-session,
  tr[data-state="refused"] .open-session,
  tr[data-state="expired"] .open-session {
    color: var(--muted);
  }
  .held {
    margin-left: 6px;
    padding: 0 6px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    font: 600 10px var(--font-ui);
    cursor: help;
  }
  .policy-meta {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 0 10px;
    font: 11px var(--font-mono);
  }
  .mode {
    color: var(--ink);
  }
  .group > .set-row.editor-row {
    display: block;
    padding-top: 8px;
    padding-bottom: 4px;
  }
  /* The build editor's look (BuildEditor.svelte): mono 12.5/20 on the inset fill, a line gutter. */
  .editor {
    display: flex;
    min-width: 0;
    overflow: hidden;
    border: 1px solid var(--border-fine, var(--border));
    border-radius: var(--r-field, 8px);
    font: 12.5px / 20px var(--font-mono);
  }
  .gutter {
    flex: none;
    min-width: 3.2em;
    padding: 8px 6px 8px 0;
    overflow: hidden;
    border-right: 1px solid var(--border-fine, var(--border));
    background: var(--solid-inset, #111111);
    color: var(--muted);
    text-align: right;
    user-select: none;
  }
  .number {
    padding-left: 6px;
  }
  :global(:root body) .editor textarea {
    flex: 1;
    display: block;
    min-width: 0;
    height: auto;
    margin: 0;
    padding: 8px;
    border: 0;
    border-radius: 0;
    outline: none;
    resize: vertical;
    background: var(--solid-inset, #111111);
    box-shadow: none;
    color: var(--ink);
    font: inherit;
    white-space: pre;
    overflow: auto;
    tab-size: 2;
  }
  .editor:focus-within {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  .problems {
    display: grid;
    gap: 2px;
    padding: 4px 0;
    color: color-mix(in srgb, var(--error) 75%, var(--ink));
    font-size: 12px;
  }
  .problems li::before {
    content: "";
    display: inline-block;
    width: 6px;
    height: 6px;
    margin-right: 7px;
    border-radius: 999px;
    background: var(--error);
    vertical-align: 1px;
  }
  .group > .set-row.actions {
    border-top: 0;
    min-height: 40px;
  }
  .summary {
    margin: 4px 0 6px;
  }
  .stale {
    opacity: 0.55;
  }
  .change {
    color: var(--muted);
    white-space: nowrap;
  }
  tr[data-draft="deny"] .change b {
    color: color-mix(in srgb, var(--error) 75%, var(--ink));
  }
  tr[data-draft="allow"] .change b {
    color: color-mix(in srgb, var(--success) 75%, var(--ink));
  }
  .pairs {
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr);
    gap: 3px 16px;
    font-size: 12.5px;
  }
  .pairs dt {
    color: var(--ink);
  }
  .pairs dd {
    margin: 0;
    color: var(--muted);
  }
  .tools {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 2px 18px;
  }
  .tools li {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    padding: 3px 0;
    border-bottom: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
  }
  .tools span {
    color: var(--muted);
    font-size: 11.5px;
  }
  .confirm {
    display: grid;
    gap: 6px;
    max-width: 280px;
    padding: 8px;
  }
  .confirm p {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }
  .confirm-actions {
    display: flex;
    justify-content: flex-end;
    gap: 6px;
    margin-top: 4px;
  }
</style>
