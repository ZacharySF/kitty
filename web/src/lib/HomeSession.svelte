<script lang="ts">
  import { onMount } from "svelte";
  import { ApiError, api } from "./api";
  import HomeWidget from "./HomeWidget.svelte";
  import Sparkline from "./usage/Sparkline.svelte";
  import { money, pinSession, pinnedSession, tokens, type SessionDetail } from "./usage/usage";

  /**
   * One coding-agent session on home: how full its context is, in large type, its new
   * tokens over time and one line of the rest. It follows the session used last, or the
   * one pinned in Settings → AI → Usage. It shows only once usage is on for an agent and
   * a session has been read.
   */
  /** `placeholder`: home's edit mode, where the widget shows its title even with nothing to show. */
  let { id, placeholder = false }: { id: string; placeholder?: boolean } = $props();
  const POLL_MS = 5000;
  let session = $state<SessionDetail | null>(null);
  let pinned = $state<string | null>(null);

  async function load() {
    pinned = pinnedSession();
    try {
      session = (await api<{ session: SessionDetail | null }>(`/usage/session${pinned ? `?key=${pinned}` : ""}`)).session;
    } catch (error) {
      // The pinned session is gone (its agent's switch went off): follow the latest again.
      if (pinned && error instanceof ApiError && error.status === 404) {
        pinSession(null);
        await load();
      }
      /* Otherwise quiet on home: the widget keeps what it last showed. */
    }
  }
  function unpin() {
    pinSession(null);
    void load();
  }
  const rest = $derived(
    session
      ? [`${tokens(session.new_tokens)} new`, session.cache_read ? `${tokens(session.cache_read)} cached` : "",
          `${session.tool_calls} ${session.tool_calls === 1 ? "tool call" : "tool calls"}`,
          `${session.files_changed} ${session.files_changed === 1 ? "file" : "files"} changed`,
          money(session.cost_usd, session.cost_source)].filter(Boolean).join(" · ")
      : "",
  );

  onMount(() => {
    void load();
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, POLL_MS);
    return () => window.clearInterval(timer);
  });
</script>

{#if session}
  <HomeWidget {id} title="Agent session" icon="pulse" eyebrow={[session.label, session.cwd_name].filter(Boolean).join(" · ")}>
    {#snippet tail()}{#if pinned}<button type="button" class="text-button" onclick={unpin}>Follow latest</button>{/if}{/snippet}
    <div class="row">
      <p class="big">
        {#if session.context_pct != null}<strong>{session.context_pct}%</strong><span>context</span>
        {:else}<strong>{tokens(session.new_tokens)}</strong><span>new tokens</span>{/if}
      </p>
      <Sparkline series={session.series} width={140} height={34} />
    </div>
    <p class="rest">{rest}</p>
  </HomeWidget>
{:else if placeholder}<HomeWidget {id} title="Agent session" icon="pulse" eyebrow="Shows once usage is on" />{/if}

<style>
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }
  p {
    margin: 0;
  }
  .big {
    display: flex;
    align-items: baseline;
    gap: 8px;
  }
  .big strong {
    color: var(--ink);
    font: 600 34px/1.1 var(--font-ui);
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
  }
  .big span,
  .rest {
    color: var(--muted);
    font-size: 15px;
  }
  .rest {
    margin-top: 8px;
    line-height: 1.45;
    font-variant-numeric: tabular-nums;
  }
</style>
