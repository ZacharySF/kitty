<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../api";
  import Sparkline from "./Sparkline.svelte";
  import UsageBars from "./UsageBars.svelte";
  import {
    SOURCES, money, newTokens, pinSession, pinnedSession, tokens, usageLine,
    type SessionDetail, type SessionUsage, type UsageKind, type UsageOverview,
  } from "./usage";

  /**
   * Settings → AI → Usage: what Rika, the in-app AI and (once switched on, per agent) the
   * coding agents spent over 30 days. The agents' numbers are read from their own session
   * logs on this computer; nothing but numbers is kept. `today` and `cap` are the AI
   * budget's day so far and its cap.
   */
  let { today, cap }: { today: number; cap: number } = $props();
  const POLL_MS = 5000;
  let usage = $state<UsageOverview | null>(null);
  let error = $state("");
  let busy = $state(false);
  /** The session whose line and sparkline show under the list. */
  let chosen = $state<string | null>(null);
  let detail = $state<SessionDetail | null>(null);
  let pinned = $state(pinnedSession());
  const sourceLabel = (id: string) => SOURCES.find((source) => source.id === id)?.label ?? id;
  const shown = $derived(usage?.sessions.find((session) => session.key === chosen) ?? usage?.sessions[0] ?? null);

  async function load() {
    try {
      usage = await api<UsageOverview>("/usage");
      error = "";
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }
  async function flip(kind: UsageKind) {
    busy = true;
    try {
      usage = await api<UsageOverview>("/usage/settings", { kind: kind.kind, enabled: !kind.enabled });
      error = "";
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
  function pin(session: SessionUsage) {
    pinned = pinned === session.key ? null : session.key;
    pinSession(pinned);
  }
  const when = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";

  $effect(() => {
    const key = shown?.key;
    // Reads `shown.last_at` too, so the line follows a session that is still running.
    void shown?.last_at;
    if (!key) {
      detail = null;
      return;
    }
    let live = true;
    api<{ session: SessionDetail | null }>(`/usage/session?key=${key}`)
      .then((found) => { if (live) detail = found.session; })
      .catch(() => { if (live) detail = null; });
    return () => { live = false; };
  });

  onMount(() => {
    void load();
    // While the first read of a long history runs, ask again sooner.
    let timer: number;
    const again = () => {
      timer = window.setTimeout(async () => {
        if (!document.hidden) await load();
        again();
      }, usage?.scanning ? 1000 : POLL_MS);
    };
    again();
    return () => window.clearTimeout(timer);
  });
</script>

<section class="usage" aria-labelledby="usage-title">
  <h2 id="usage-title">Usage</h2>
  {#if error}<p class="quiet" role="alert">{error}</p>{/if}
  {#if usage}
    <p class="total">
      <strong>{money(usage.total.cost_usd) || "$0"}</strong>
      <span>{tokens(newTokens(usage.total))} new tokens · {tokens(usage.total.cache_read)} cached · last 30 days</span>
    </p>
    <p class="quiet">Today ${today.toFixed(4)} of ${cap.toFixed(2)} for Rika and AI requests (UTC).{#if usage.scanning} Reading agent logs…{/if}</p>
    <UsageBars days={usage.days} />

    <div class="kinds">
      {#each usage.kinds as kind (kind.kind)}
        <label>
          <span>{kind.label}</span>
          <button type="button" class="switch" role="switch" aria-checked={kind.enabled} aria-label={`Read ${kind.label}'s usage from its logs`}
            disabled={busy} onclick={() => flip(kind)}><i></i></button>
        </label>
      {/each}
    </div>
    <p class="quiet">
      Off by default. When on, rika! reads token counts from that agent's own session logs on this computer. It keeps numbers only, never
      messages. Cost marked ≈ is an estimate at list price.{#if usage.skipped} {usage.skipped} log {usage.skipped === 1 ? "line" : "lines"} could not be read.{/if}
    </p>

    {#if usage.models.length}
      <table>
        <thead><tr><th>Model</th><th>From</th><th class="n">New</th><th class="n">Cached</th><th class="n">Cost</th></tr></thead>
        <tbody>
          {#each usage.models.slice(0, 8) as row (row.source + row.model)}
            <tr><td>{row.model}</td><td>{sourceLabel(row.source)}</td><td class="n">{tokens(newTokens(row))}</td>
              <td class="n">{tokens(row.cache_read)}</td><td class="n">{money(row.cost_usd) || "—"}</td></tr>
          {/each}
        </tbody>
      </table>
    {/if}

    {#if usage.sessions.length}
      <h3>Sessions</h3>
      <ul>
        {#each usage.sessions as session (session.key)}
          <li class:on={shown?.key === session.key}>
            <button type="button" class="name" aria-pressed={shown?.key === session.key} onclick={() => (chosen = session.key)}>
              {session.label}{session.cwd_name ? ` · ${session.cwd_name}` : ""}
            </button>
            <span class="when">{when(session.last_at)}</span>
            <span class="n">{tokens(session.new_tokens)} new</span>
            <span class="n">{money(session.cost_usd, session.cost_source) || "—"}</span>
            <button type="button" class="text-button" aria-pressed={pinned === session.key} onclick={() => pin(session)}>{pinned === session.key ? "Unpin" : "Pin"}</button>
          </li>
        {/each}
      </ul>
      {#if shown}
        <div class="detail">
          <p>{usageLine(shown)}{shown.model ? ` · ${shown.model}` : ""}</p>
          {#if detail?.key === shown.key}<Sparkline series={detail.series} width={160} height={32} />{/if}
        </div>
      {/if}
      <p class="quiet">Pin puts a session on home's Agent session widget; unpinned, the widget follows the latest one.</p>
    {/if}
  {:else if !error}<p class="quiet">Loading usage…</p>{/if}
</section>

<style>
  .usage {
    display: grid;
    gap: 14px;
    min-width: 0;
    padding: 4px 0 12px;
    border-top: 1px solid var(--hairline);
    color: var(--ink);
    font-size: 16px;
  }
  h2 {
    margin: 14px 0 0;
    font: 600 22px/1.2 var(--font-ui);
    letter-spacing: -0.01em;
  }
  h3 {
    margin: 8px 0 0;
    font: 600 16px/1.3 var(--font-ui);
  }
  p {
    margin: 0;
  }
  .total {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 4px 14px;
  }
  .total strong {
    color: var(--ink);
    font: 600 40px/1.1 var(--font-ui);
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
  }
  .total span,
  .quiet {
    color: var(--muted);
    font-size: 15px;
    line-height: 1.45;
  }
  .kinds {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 28px;
    padding-top: 12px;
    border-top: 1px solid var(--hairline);
  }
  .kinds label {
    display: inline-flex;
    align-items: center;
    gap: 10px;
  }
  :global(:root body) .switch {
    position: relative;
    flex: none;
    width: 36px;
    height: 20px;
    min-height: 0;
    margin: 0;
    padding: 0;
    border: 1px solid var(--muted);
    border-radius: 999px;
    background: transparent;
    cursor: pointer;
  }
  .switch i {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 14px;
    height: 14px;
    border-radius: 999px;
    background: var(--muted);
    transition: transform 160ms;
  }
  :global(:root body) .switch[aria-checked="true"] {
    border-color: var(--ink);
    background: var(--ink);
  }
  .switch[aria-checked="true"] i {
    transform: translateX(16px);
    background: var(--solid-void, #141414);
  }
  .switch:focus-visible {
    outline: 1px solid var(--ink);
    outline-offset: 3px;
  }
  @media (prefers-reduced-motion: reduce) {
    .switch i {
      transition: none;
    }
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 15px;
  }
  th {
    padding: 6px 0;
    border-bottom: 1px solid var(--hairline);
    color: var(--muted);
    font-weight: 400;
    text-align: left;
  }
  td {
    padding: 7px 0;
    border-bottom: 1px solid var(--hairline);
    overflow-wrap: anywhere;
  }
  .n {
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  th.n,
  td.n {
    padding-left: 14px;
  }
  ul {
    display: grid;
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: 15px;
  }
  li {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto 5.5em 4.5em 4em;
    align-items: center;
    gap: 12px;
    border-bottom: 1px solid var(--hairline);
  }
  :global(:root body) .usage .name {
    min-height: 0;
    padding: 8px 0;
    border: 0;
    background: none;
    color: var(--muted);
    font: inherit;
    text-align: left;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    cursor: pointer;
  }
  :global(:root body) .usage li.on .name,
  :global(:root body) .usage .name:hover {
    color: var(--ink);
  }
  .when {
    color: var(--muted);
    white-space: nowrap;
  }
  li .text-button {
    justify-self: end;
    font-size: 15px;
  }
  .detail {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    font-size: 15px;
    font-variant-numeric: tabular-nums;
  }
  @container (max-width: 520px) {
    .when {
      display: none;
    }
  }
</style>
