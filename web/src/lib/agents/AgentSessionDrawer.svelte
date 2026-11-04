<script lang="ts">
  import { api } from "../api";
  import EditDrawer from "../ui/EditDrawer.svelte";
  import Popover from "../ui/Popover.svelte";
  import GuaranteeBadge from "./GuaranteeBadge.svelte";
  import { STATE_LABELS, guaranteeOf, isLive, moreJournal, shortId, within, type SessionDetail } from "./agents";

  /**
   * Q8: one agent session in the side drawer. Its launch (guarantee, state, why), the
   * planted files its project gained or lost, and its journal: who read or wrote which
   * record, when, and what the policy decided; Older pages further back. Hold keeps the
   * journal past the History window (Release lets it go); End revokes the token after a
   * confirmation. `onchange` reports every change so the list follows.
   */
  let { id, purposes, onclose, onchange }: {
    id: string;
    purposes: Record<string, string>;
    onclose: () => void;
    onchange: (detail: SessionDetail) => void;
  } = $props();

  let detail = $state<SessionDetail | null>(null);
  let error = $state("");
  let busy = $state("");
  let ending = $state(false);
  const TONES = { full: "var(--success)", attribution: "var(--warning)", none: "var(--error)" } as const;
  const session = $derived(detail?.session);

  $effect(() => {
    const wanted = id;
    let cancelled = false;
    detail = null;
    error = "";
    api<SessionDetail>(`/agents/session?id=${encodeURIComponent(wanted)}`)
      .then((next) => { if (!cancelled) detail = next; })
      .catch((cause) => { if (!cancelled) error = (cause as Error).message; });
    return () => { cancelled = true; };
  });

  async function run(key: string, work: () => Promise<void>) {
    if (busy) return;
    busy = key;
    try {
      await work();
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = "";
    }
  }
  /** The hold and end responses are the session's first journal page again; keep what is shown. */
  function adopt(next: SessionDetail) {
    detail = detail ? { ...next, journal: moreJournal(next.journal, detail.journal), next: detail.next } : next;
    onchange(next);
  }
  const older = () => run("older", async () => {
    if (!detail?.next) return;
    const page = await api<SessionDetail>(`/agents/session?id=${encodeURIComponent(id)}&before=${detail.next}`);
    detail = { ...page, journal: moreJournal(detail.journal, page.journal) };
  });
  const hold = () => run("hold", async () => {
    if (!detail) return;
    adopt(await api<SessionDetail>("/agents/sessions/hold", { id, hold: !detail.held }));
  });
  const end = () => run("end", async () => {
    ending = false;
    adopt(await api<SessionDetail>("/agents/sessions/end", { id }));
  });

  const stamp = (value: string | null) => {
    const date = new Date(value ?? "");
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };
  const clock = (value: string) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const today = date.toDateString() === new Date().toDateString();
    const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
    return today ? time : `${date.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
  };
</script>

<EditDrawer title={session?.name ?? "Agent session"} meta={session ? [session.kind, session.host, session.pane].filter(Boolean).join(" · ") : ""}
  accent={session ? TONES[guaranteeOf(session.guarantee)] : ""} closeLabel="Close session" {onclose}>
  {#if session && detail}
    <div class="status">
      <GuaranteeBadge guarantee={session.guarantee} detail={session.message ?? ""} />
      <span class="state" data-state={session.state}>{STATE_LABELS[session.state] ?? session.state}</span>
      {#if detail.held}<span class="held" title="Kept past the History window until released">Held</span>{/if}
    </div>
    {#if session.message && session.guarantee !== "full"}<p class="why">{session.message}</p>{/if}
    <dl class="facts">
      <dt>Started</dt><dd>{stamp(session.created_at)}</dd>
      {#if session.confined_at}<dt>Confined</dt><dd>{stamp(session.confined_at)}</dd>{/if}
      {#if session.ended_at}<dt>Ended</dt><dd>{stamp(session.ended_at)}</dd>{/if}
      <dt>Policy</dt><dd>{session.policy_version === null ? "—" : `Version ${session.policy_version}`}</dd>
      {#if session.abi}<dt>Landlock</dt><dd class="mono">ABI {session.abi}</dd>{/if}
      <dt>Folder</dt><dd class="mono" title={session.cwd}>{session.cwd}</dd>
    </dl>

    {#if detail.planted.length}
      <h3>Planted files</h3>
      <ul class="planted" aria-label="Planted files">
        {#each detail.planted as item (item.path)}
          <li data-change={item.change}><code title={item.path}>{within(item.path, session.cwd)}</code><span>{item.change}</span></li>
        {/each}
      </ul>
    {/if}

    <h3>Journal</h3>
    {#if detail.journal.length}
      <ol class="journal" aria-label="Journal">
        {#each detail.journal as row (row.id)}
          <li data-decision={row.decision} title={`Policy version ${row.policy_version} · ${row.mode} · seq ${row.seq}`}>
            <time datetime={row.at}>{clock(row.at)}</time>
            <span class="what"><strong>{row.op === "write" ? "Wrote" : "Read"}</strong> <span title={purposes[row.purpose] ?? row.purpose}>{row.purpose}</span>
              {#if row.block_id}<code title={row.block_id}>{shortId(row.block_id)}</code>{/if}</span>
            <span class="decision">{row.decision}</span>
            <code class="tool">{row.tool}</code>
          </li>
        {/each}
      </ol>
      {#if detail.next}<button type="button" class="pill older" disabled={busy === "older"} onclick={older}>{busy === "older" ? "Loading…" : "Older"}</button>{/if}
    {:else}
      <p class="none">{session.token ? "No accesses yet." : "No token, so nothing was journaled."}</p>
    {/if}
  {:else if !error}
    <p class="none" role="status">Loading session…</p>
  {/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}

  {#snippet footer()}
    {#if session && detail}
      {#if isLive(session.state)}
        <Popover bind:open={ending} label={`End ${session.name}?`} placement="top-start" class="agent-confirm">
          {#snippet trigger(props)}<button type="button" class="pill danger" disabled={!!busy} {...props}>End</button>{/snippet}
          <div class="confirm">
            <strong>End {session.name}?</strong>
            <p>Revokes its token at once. The journal stays.</p>
            <div class="confirm-actions">
              <button type="button" class="pill" onclick={() => (ending = false)}>Cancel</button>
              <button type="button" class="pill danger solid" onclick={end}>End session</button>
            </div>
          </div>
        </Popover>
      {/if}
      <span class="grow"></span>
      <button type="button" class="pill" disabled={!!busy} title={detail.held ? "Let compaction fold this journal" : "Keep this journal past the History window"}
        onclick={hold}>{busy === "hold" ? "Saving…" : detail.held ? "Release" : "Hold"}</button>
    {/if}
  {/snippet}
</EditDrawer>

<style>
  .status {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .state {
    color: var(--muted);
    font: 500 var(--fs-control) var(--font-ui);
  }
  .held {
    padding: 1px 7px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    color: var(--ink);
    font: 600 10.5px var(--font-ui);
    cursor: help;
  }
  .why {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.45;
  }
  .facts {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    gap: 4px 14px;
    margin: 0;
    font-size: 12.5px;
  }
  dt {
    color: var(--muted);
    font: 600 var(--fs-label) var(--font-ui);
  }
  dd {
    min-width: 0;
    margin: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mono,
  code {
    font: 11px var(--font-mono);
  }
  h3 {
    margin: 6px -16px 0;
    padding: 10px 16px 0;
    border-top: 1px solid var(--hairline);
    color: var(--muted);
    font: 600 var(--fs-label) var(--font-ui);
  }
  ul,
  ol {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .planted li {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    padding: 3px 0;
    font-size: 12px;
  }
  .planted code {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .planted span {
    flex-shrink: 0;
    color: color-mix(in srgb, var(--warning) 72%, var(--ink));
    font: 600 10.5px var(--font-ui);
  }
  .journal li {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: baseline;
    gap: 0 10px;
    padding: 5px 0 5px 10px;
    border-left: 2px solid color-mix(in srgb, var(--hairline) 80%, transparent);
    font-size: 12px;
  }
  .journal li[data-decision="deny"] {
    border-left-color: var(--error);
  }
  .journal time {
    color: var(--muted);
    font: 11px var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  .what {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .what strong {
    font-weight: 600;
  }
  .what code {
    margin-left: 4px;
    color: var(--muted);
  }
  .decision {
    color: var(--muted);
    font: 600 10.5px var(--font-ui);
  }
  li[data-decision="deny"] .decision {
    color: color-mix(in srgb, var(--error) 75%, var(--ink));
  }
  .tool {
    grid-column: 2 / -1;
    color: var(--subtle);
    font-size: 10.5px;
  }
  .older {
    align-self: start;
  }
  /* The drawer body is a scrolling flex column: its rows keep their height (pills have min-height 0). */
  .status,
  .why,
  .facts,
  h3,
  ul,
  ol,
  .older {
    flex-shrink: 0;
  }
  .none,
  .error {
    margin: 0;
    color: var(--muted);
    font-size: 12.5px;
  }
  .error {
    color: color-mix(in srgb, var(--error) 75%, var(--ink));
  }
  .grow {
    flex: 1;
  }
  .pill.danger {
    border-color: color-mix(in srgb, var(--error) 45%, var(--hairline));
    color: color-mix(in srgb, var(--error) 75%, var(--ink));
  }
  .pill.danger.solid {
    background: color-mix(in srgb, var(--error) 18%, transparent);
  }
  .confirm {
    display: grid;
    gap: 6px;
    max-width: 260px;
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
