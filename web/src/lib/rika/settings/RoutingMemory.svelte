<script lang="ts">
  import { onMount } from "svelte";
  import { Panel } from "@nunera/bracket";
  import { api } from "../../api";
  import EmptyState from "../../EmptyState.svelte";
  import "../rika.css";
  import { usd } from "../rika";
  import { compactTokens, trendPath, trendRange, type Route, type RoutesState } from "./settings";

  /**
   * Q13 A: Settings → Rika → Routing memory. Per kind of request, the cheapest tier and
   * shortest tool path that worked, how often it ran and what it costs on average; Forget
   * drops a row so the next request of that kind is routed afresh. Stays on this machine.
   */
  let memory = $state<RoutesState | null>(null);
  let error = $state("");
  let forgetting = $state("");
  const range = $derived(memory ? trendRange(memory.summary.trend) : null);
  const line = $derived(memory ? trendPath(memory.summary.trend, 200, 48) : "");
  const trendLabel = $derived(range ? `Average cost per request went from ${usd(range.from)} to ${usd(range.to)} over 14 days` : "No requests in the last 14 days");

  async function refresh() {
    try {
      memory = await api<RoutesState>("/rika/routes");
      error = "";
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  async function forget(route: Route) {
    forgetting = route.signature;
    try {
      memory = await api<RoutesState>("/rika/routes/forget", { signature: route.signature });
      error = "";
    } catch (cause) {
      error = `Could not forget “${route.label}”: ${(cause as Error).message}`;
    } finally {
      forgetting = "";
    }
  }
  onMount(() => void refresh());
</script>

<Panel title="Routing memory" aria-label="Routing memory">
  <div class="rows">
    <div class="set-row head">
      <span class="set-desc">What Rika learned about your requests: the cheapest tier and shortest tool path that worked for each kind. Stays on this machine.</span>
      <figure class="trend">
        <figcaption>Avg cost per request · 14 days</figcaption>
        <svg viewBox="0 0 200 48" preserveAspectRatio="none" role="img" aria-label={trendLabel}>
          <line x1="0" y1="46" x2="200" y2="46" class="floor" />
          {#if line}<path d={line} class="spark" />{/if}
        </svg>
        <span class="num">{range ? `${usd(range.from)} → ${usd(range.to)}` : "No requests yet"}</span>
      </figure>
    </div>
    {#if memory}
      {@const today = memory.summary}
      <div class="set-row today" role="status">
        <span class="set-label">Today</span>
        <span class="set-desc num">
          {today.today_requests} request{today.today_requests === 1 ? "" : "s"} · {compactTokens(today.today_tokens)} tokens · {usd(today.today_cost_usd)} · cache hits {Math.round(today.cached_share * 100)}%
        </span>
      </div>
      {#if memory.routes.length}
        <div class="set-row table">
          <table>
            <thead>
              <tr><th scope="col">Kind of request</th><th scope="col">Tier</th><th scope="col">Tool path</th><th scope="col" class="n">Uses</th><th scope="col" class="n">Avg tokens</th><th scope="col" class="n">Avg cost</th><th scope="col"><span class="sr-only">Forget</span></th></tr>
            </thead>
            <tbody>
              {#each memory.routes as route (route.signature)}
                <tr>
                  <td title={route.example ? `For example: “${route.example}”` : undefined}>{route.label}</td>
                  <td>
                    <span class="chips">
                      <span class="rika-chip" data-tier={route.tier}>{route.tier}</span>
                      {#if route.shortcut?.active}<span class="rika-chip shortcut" data-tier="direct" title={`Runs with no model: ${route.shortcut.template} · used ${route.shortcut.uses} time${route.shortcut.uses === 1 ? "" : "s"}`}>Shortcut</span>{/if}
                    </span>
                    {#if route.escalations}<small>escalated {route.escalations}×</small>{/if}
                  </td>
                  <td><code>{route.path.length ? route.path.join(" → ") : "—"}</code></td>
                  <td class="n">{route.uses}</td>
                  <td class="n">{compactTokens(route.avg_tokens)}</td>
                  <td class="n">{usd(route.avg_cost_usd)}</td>
                  <td class="end">
                    <button type="button" class="forget" aria-label={`Forget ${route.label}`} disabled={forgetting === route.signature} onclick={() => forget(route)}>Forget</button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {:else}
        <div class="set-row empty">
          <EmptyState level={3} title="Nothing learned yet">
            <p>As you ask Rika things, each kind of request appears here with the cheapest tier and tool path that worked.</p>
          </EmptyState>
        </div>
      {/if}
    {:else if !error}
      <p class="set-note" role="status">Loading routing memory…</p>
    {/if}
  </div>
  {#if error}<p class="set-note" role="alert">{error} <button class="text-button" onclick={refresh}>Retry</button></p>{/if}
</Panel>

<style>
  .head {
    align-items: start;
    padding-top: 10px;
    padding-bottom: 10px;
  }
  .trend {
    display: grid;
    flex-shrink: 0;
    gap: 3px;
    width: 220px;
    margin: 0 0 0 auto;
  }
  figcaption {
    color: var(--muted);
    font: 600 var(--fs-label) var(--font-ui);
  }
  .trend svg {
    width: 100%;
    height: 40px;
  }
  .floor {
    stroke: var(--hairline);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }
  .spark {
    fill: none;
    stroke: var(--success);
    stroke-width: 1.6;
    stroke-linejoin: round;
    vector-effect: non-scaling-stroke;
  }
  .num {
    color: var(--muted);
    font: 11px var(--font-mono);
    font-variant-numeric: tabular-nums;
  }
  .set-row.table,
  .set-row.empty {
    display: block;
    padding-top: 4px;
    padding-bottom: 4px;
  }
  .set-row.empty {
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
    padding: 7px 8px 7px 0;
    border-top: 1px solid color-mix(in srgb, var(--hairline) 60%, transparent);
    color: var(--ink);
    vertical-align: middle;
  }
  .chips {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  td small {
    display: block;
    margin-top: 2px;
    color: var(--muted);
    font-size: 10.5px;
  }
  td code {
    color: var(--muted);
    font: 11px var(--font-mono);
    overflow-wrap: anywhere;
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
  tbody tr:hover td {
    background: color-mix(in srgb, var(--ink) 4%, transparent);
  }
  .forget {
    padding: 2px 9px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: 500 11px var(--font-ui);
    cursor: pointer;
    opacity: 0;
    transition: opacity 120ms;
  }
  tr:hover .forget,
  tr:focus-within .forget,
  .forget:focus-visible {
    opacity: 1;
  }
  .forget:hover {
    border-color: var(--muted);
    color: var(--ink);
  }
  .forget:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }
  /* Touch screens have no hover to reveal Forget. */
  @media (hover: none) {
    .forget {
      opacity: 1;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .forget {
      transition: none;
    }
  }
  @container (max-width: 640px) {
    .head {
      flex-wrap: wrap;
    }
    .trend {
      margin-left: 0;
    }
  }
</style>
