<script lang="ts" module>
  export type DeckLoad = {
    id: number;
    name: string;
    archived: number;
    due: number;
    new: number;
    learning: number;
    review: number;
    recall: number | null;
  };
</script>

<script lang="ts">
  /**
   * Deck tiles (Q40, decluttered): one ring splitting today's load into review,
   * learning and new around today's count, plus the deck name. The split, measured
   * recall and a Review action fade in on hover or keyboard focus.
   */
  let {
    decks,
    selected,
    onselect,
    onreview,
  }: {
    decks: DeckLoad[];
    selected: number;
    onselect: (deck: DeckLoad) => void;
    onreview: (deck: DeckLoad) => void;
  } = $props();

  const R = 30;
  const C = 2 * Math.PI * R;
  /** Today's load, in ring order. */
  const LOAD = [
    { key: "review", color: "var(--violet)" },
    { key: "learning", color: "var(--warning)" },
    { key: "new", color: "var(--c4)" },
  ] as const;
  function segments(deck: DeckLoad) {
    const parts = LOAD.map((part) => ({ value: deck[part.key], color: part.color })).filter((part) => part.value > 0);
    const total = parts.reduce((sum, part) => sum + part.value, 0);
    let offset = 0;
    return parts.map((part) => {
      const length = (part.value / total) * C;
      const gap = parts.length > 1 ? 2.5 : 0;
      const segment = { color: part.color, dash: `${Math.max(0, length - gap)} ${C}`, offset: -offset };
      offset += length;
      return segment;
    });
  }
</script>

<div class="tiles">
  {#each decks as deck, index (deck.id)}
    {@const parts = deck.name.split("::")}
    {@const today = deck.due + deck.new}
    <div class="tile" class:on={deck.id === selected} class:archived={!!deck.archived} style:--d={`var(--c${(index % 6) + 1})`}>
      <button class="pick" aria-pressed={deck.id === selected} aria-label={`${deck.name}, ${today} due today`} onclick={() => onselect(deck)}>
        <svg viewBox="0 0 76 76" aria-hidden="true">
          <circle class="track" cx="38" cy="38" r={R} />
          {#each segments(deck) as segment}
            <circle class="segment" cx="38" cy="38" r={R} style:stroke={segment.color} stroke-dasharray={segment.dash} stroke-dashoffset={segment.offset} />
          {/each}
          <text class="number" x="38" y="41">{today}</text>
          <text class="unit" x="38" y="53">today</text>
        </svg>
        <span class="body">
          {#if parts.length > 1 || deck.archived}<span class="path">{parts.slice(0, -1).join(" › ")}{deck.archived ? `${parts.length > 1 ? " · " : ""}archived` : ""}</span>{/if}
          <span class="name" title={deck.name}>{parts.at(-1)}</span>
        </span>
      </button>
      <div class="details" role="group" aria-label={`${deck.name} details`}>
        <span class="split">
          {#each LOAD.filter((part) => deck[part.key] > 0) as part}<span><i style:background={part.color}></i>{deck[part.key]} {part.key}</span>{:else}Nothing due{/each}
        </span>
        <span class="recall">{deck.recall === null ? "No recall yet" : `${Math.round(deck.recall * 100)}% recall`}</span>
        <button class="review" onclick={() => onreview(deck)} disabled={!!deck.archived || !today}>Review</button>
      </div>
    </div>
  {/each}
</div>

<style>
  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 10px;
    margin: 1rem 0;
  }
  .tile {
    position: relative;
    border: 1px solid var(--hairline);
    border-radius: 14px;
    background:
      radial-gradient(90% 80% at 0% 0%, color-mix(in srgb, var(--d) 12%, transparent), transparent 70%),
      color-mix(in srgb, var(--solid-panel) 70%, transparent);
    transition: border-color 0.2s ease, transform 0.2s ease;
  }
  .tile:hover,
  .tile:focus-within {
    border-color: color-mix(in srgb, var(--d) 60%, transparent);
    transform: translateY(-1px);
  }
  .tile.on {
    border-color: var(--d);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--d) 12%, transparent);
  }
  .tile.archived {
    opacity: 0.6;
  }
  .pick {
    display: grid;
    grid-template-columns: 70px 1fr;
    gap: 12px;
    align-items: start;
    width: 100%;
    min-height: 96px;
    padding: 12px;
    border: 0;
    border-radius: inherit;
    background: none;
    color: var(--ink);
    text-align: left;
  }
  .pick:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  svg {
    width: 70px;
    height: 70px;
  }
  circle {
    fill: none;
    stroke-width: 6;
  }
  .track {
    stroke: color-mix(in srgb, var(--ink) 8%, transparent);
  }
  .segment {
    stroke-linecap: round;
    transform: rotate(-90deg);
    transform-origin: 38px 38px;
  }
  .number {
    fill: var(--ink);
    font: 700 19px var(--font-ui);
    text-anchor: middle;
  }
  .unit {
    fill: var(--subtle);
    font: 7.5px var(--font-mono);
    letter-spacing: 0.1em;
    text-anchor: middle;
    text-transform: uppercase;
  }
  .body {
    display: grid;
    gap: 3px;
    min-width: 0;
    padding-top: 8px;
  }
  .path {
    color: var(--d);
    font: 9.5px var(--font-mono);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .name {
    font: 600 15px/1.25 var(--font-ui);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .details {
    position: absolute;
    left: 94px;
    right: 10px;
    bottom: 10px;
    display: grid;
    grid-template-columns: 1fr auto;
    align-items: end;
    gap: 2px 8px;
    color: var(--muted);
    font: 10.5px var(--font-mono);
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.15s ease;
  }
  .tile:hover .details,
  .tile:focus-within .details {
    opacity: 1;
  }
  /* Only the Review action takes the pointer; the rest of the tile still selects the deck. */
  .tile:hover .review,
  .tile:focus-within .review {
    pointer-events: auto;
  }
  .split {
    grid-column: 1;
    display: flex;
    flex-wrap: wrap;
    gap: 0 8px;
  }
  .split span {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }
  .split i {
    width: 6px;
    height: 6px;
    border-radius: 50%;
  }
  .recall {
    grid-column: 1;
    color: var(--subtle);
  }
  .review {
    grid-column: 2;
    grid-row: 1 / span 2;
    padding: 4px 10px;
    border: 1px solid var(--d);
    border-radius: 999px;
    background: color-mix(in srgb, var(--d) 16%, transparent);
    color: var(--ink);
    font: 500 10px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .review:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  @media (prefers-reduced-motion: reduce) {
    .tile,
    .details {
      transition: none;
    }
  }
</style>
