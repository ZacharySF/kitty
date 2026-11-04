<script lang="ts">
  import { KIND_LABEL, KIND_TONE, goalHash, matchLine, type Match } from "./goals";

  /**
   * Q15 A: the morning brief's items, each a kind tag, what moved and why it matters
   * ("keyword match · Summer internship"). An item opens its goal, where Rika's find
   * waits with Link / Not relevant. `compact` (the home rail) keeps each item to one line
   * whose tooltip carries the rest.
   */
  let { items, limit = Infinity, compact = false, onopen }: { items: Match[]; limit?: number; compact?: boolean; onopen?: (item: Match) => void } = $props();
</script>

<ul class="brief-items" class:compact>
  {#each items.slice(0, limit) as item (item.id)}
    <li style:--tone={KIND_TONE[item.kind]}>
      <a href={goalHash(item.goal_id)} title={compact ? `${item.title} · ${matchLine(item)}` : undefined} onclick={() => onopen?.(item)}>
        <span class="kind">{KIND_LABEL[item.kind]}</span>
        <span class="what">
          <b>{item.title}</b>
          {#if !compact}<small>{matchLine(item)}</small>{/if}
        </span>
        {#if item.urgent}<span class="urgent">Soon</span>{/if}
      </a>
    </li>
  {/each}
</ul>

<style>
  .brief-items {
    display: grid;
    gap: 4px;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  a {
    display: flex;
    align-items: baseline;
    gap: 8px;
    min-height: 40px;
    padding: 5px 4px 5px 8px;
    border-left: 2px solid var(--tone);
    border-radius: 0 6px 6px 0;
    color: var(--ink);
    text-decoration: none;
  }
  .compact a {
    min-height: 30px;
    padding-block: 3px;
    align-items: center;
  }
  a:hover b,
  a:focus-visible b {
    color: var(--accent);
  }
  a:hover {
    background: color-mix(in srgb, var(--ink) 5%, transparent);
  }
  .kind {
    flex: none;
    width: 56px;
    color: var(--subtle);
    font: 500 9.5px/1.6 var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .what {
    display: grid;
    flex: 1;
    min-width: 0;
  }
  b {
    overflow: hidden;
    font: 500 12.5px/1.3 var(--font-ui);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* Up to two lines: what it is, then its fit and goal. */
  small {
    display: -webkit-box;
    overflow: hidden;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    color: var(--muted);
    font-size: 11px;
  }
  .urgent {
    flex: none;
    padding: 1px 7px;
    border: 1px solid color-mix(in srgb, var(--warning) 60%, transparent);
    border-radius: 999px;
    color: var(--warning);
    font: 500 9.5px/1.4 var(--font-mono);
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }
</style>
