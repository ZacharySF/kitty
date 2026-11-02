<script lang="ts" module>
  /** Where each grade sends the card: Again ←, Hard ↓, Good →, Easy ↑. */
  export const DIRECTIONS: Record<number, [number, number]> = { 1: [-1, 0], 2: [0, 1], 3: [1, 0], 4: [0, -1] };
  /** How long a graded card takes to leave the pile. */
  export const FLY_MS = 300;
</script>

<script lang="ts">
  import { GRADES } from "./GradeKeys.svelte";

  /**
   * Stacked deck (Q38): the current card sits on a pile of the cards still to come.
   * Tap or Space flips the top card; once revealed, dragging it past a threshold (or a
   * grade key, via `flying`) sends it off in that grade's direction.
   */
  let {
    token,
    front,
    back,
    caption,
    revealed,
    answerFace,
    remaining,
    flying = 0,
    onflip,
    onswipe,
  }: {
    token: string;
    front: string;
    back: string;
    caption: string;
    revealed: boolean;
    answerFace: boolean;
    remaining: number;
    flying?: number;
    onflip: () => void;
    onswipe: (rating: number) => void;
  } = $props();

  let dx = $state(0),
    dy = $state(0),
    dragging = $state(false);
  let start: { x: number; y: number; id: number } | null = null;

  /** Cards drawn underneath the top one: the rest of the queue, at most three. */
  let below = $derived(Math.max(0, Math.min(3, remaining - 1)));
  let offset = $derived.by(() => {
    if (!flying) return [dx, dy];
    const [x, y] = DIRECTIONS[flying];
    return [x * 560, y * 380 + (x ? -40 : 0)];
  });
  let lean = $derived(
    !revealed || flying || (!dx && !dy) ? 0 : Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 3 : 1) : dy < 0 ? 4 : 2,
  );
  let leanAmount = $derived(Math.min(1, Math.max(Math.abs(dx), Math.abs(dy)) / 110));

  function down(event: PointerEvent) {
    if (flying || event.button !== 0) return;
    start = { x: event.clientX, y: event.clientY, id: event.pointerId };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }
  function move(event: PointerEvent) {
    if (!start || event.pointerId !== start.id || !revealed) return;
    dragging = true;
    dx = event.clientX - start.x;
    dy = event.clientY - start.y;
  }
  function up() {
    if (!start) return;
    start = null;
    dragging = false;
    const moved = Math.hypot(dx, dy);
    const [x, y] = [dx, dy];
    dx = dy = 0;
    if (moved < 6) return onflip();
    if (Math.abs(x) > 110 && Math.abs(x) > Math.abs(y)) return onswipe(x > 0 ? 3 : 1);
    if (y < -90) return onswipe(4);
    if (y > 90) return onswipe(2);
  }
  function cancel() {
    start = null;
    dragging = false;
    dx = dy = 0;
  }
  function key(event: KeyboardEvent) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    onflip();
  }
</script>

<div class="stack" role="region" aria-label={`Card stack, ${remaining} ${remaining === 1 ? "card" : "cards"} left`}>
  {#each Array.from({ length: below }, (_, index) => below - index) as depth (depth)}
    <div
      class="card under"
      aria-hidden="true"
      style:transform={`translateY(${depth * 13}px) scale(${1 - depth * 0.055})`}
      style:filter={`brightness(${1 - depth * 0.14})`}
    ></div>
  {/each}
  {#key token}<div
    class="card top"
    data-review-card
    class:dragging
    class:flying={!!flying}
    role="button"
    tabindex="0"
    aria-label={revealed ? (answerFace ? "Show question" : "Show answer") : "Flip card"}
    style:transform={`translate(${offset[0]}px, ${offset[1]}px) rotate(${offset[0] * 0.06}deg)`}
    onpointerdown={down}
    onpointermove={move}
    onpointerup={up}
    onpointercancel={cancel}
    onkeydown={key}
  >
    <div class="inner" class:flipped={answerFace}>
      <div class="face front" aria-hidden={answerFace} inert={answerFace}>
        <span class="eyebrow">{caption}</span>
        <div class="content">{@html front}</div>
        <span class="hint">tap or <kbd>Space</kbd> to flip</span>
      </div>
      <div class="face back" aria-hidden={!answerFace} inert={!answerFace}>
        <span class="eyebrow">Answer</span>
        {#if revealed}<div class="content">{@html back}</div>{/if}
        <span class="hint">swipe ← again · ↓ hard · → good · ↑ easy</span>
      </div>
    </div>
    {#if lean}
      <span class="stamp" style:color={GRADES[lean - 1].color} style:opacity={leanAmount}>{GRADES[lean - 1].label}</span>
    {/if}
  </div>{/key}
</div>

<style>
  .stack {
    position: relative;
    display: grid;
    min-height: 260px;
    margin: 0.5rem 0.6rem 2.6rem;
  }
  .card {
    grid-area: 1 / 1;
    border-radius: 16px;
    transition:
      transform 0.32s cubic-bezier(0.2, 0.7, 0.2, 1),
      opacity 0.32s ease;
  }
  .under {
    border: 1px solid var(--hairline);
    background: var(--solid-raised);
    box-shadow: 0 10px 30px color-mix(in srgb, var(--shadow-tint) 45%, transparent);
    transform-origin: 50% 100%;
  }
  .top {
    position: relative;
    z-index: 1;
    display: grid;
    cursor: grab;
    touch-action: none;
    user-select: none;
    outline: none;
  }
  .top:focus-visible .face {
    outline: 2px solid var(--accent);
    outline-offset: 3px;
  }
  .top.dragging {
    transition: none;
    cursor: grabbing;
  }
  .top.flying {
    opacity: 0;
  }
  .inner {
    display: grid;
    transform-style: preserve-3d;
    transition: transform 0.45s cubic-bezier(0.2, 0.65, 0.25, 1);
  }
  .inner.flipped {
    transform: rotateY(180deg);
  }
  .face {
    grid-area: 1 / 1;
    display: grid;
    align-content: center;
    justify-items: center;
    gap: 10px;
    min-height: 260px;
    box-sizing: border-box;
    padding: 1.4rem 1.6rem;
    text-align: center;
    border: 1px solid var(--hairline);
    border-radius: 16px;
    background:
      radial-gradient(120% 90% at 50% 0%, color-mix(in srgb, var(--violet) 9%, transparent), transparent 70%),
      var(--solid-raised);
    box-shadow: 0 10px 30px color-mix(in srgb, var(--shadow-tint) 45%, transparent);
    backface-visibility: hidden;
    -webkit-backface-visibility: hidden;
  }
  .back {
    transform: rotateY(180deg);
  }
  .face .eyebrow {
    color: var(--subtle);
  }
  .content {
    max-width: 100%;
    color: var(--ink);
    font-size: 1.3rem;
    line-height: 1.4;
    overflow-wrap: anywhere;
  }
  .content :global(img) {
    max-width: 100%;
    max-height: 400px;
  }
  .content :global(audio) {
    max-width: 100%;
  }
  .content :global(hr) {
    width: 60%;
    margin: 0.8rem auto;
    border: 0;
    border-top: 1px solid var(--hairline);
  }
  .hint {
    color: var(--subtle);
    font: 10px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .hint kbd {
    padding: 0 4px;
    border: 1px solid var(--hairline);
    font-size: 10px;
  }
  .stamp {
    position: absolute;
    top: 14px;
    left: 50%;
    z-index: 2;
    padding: 2px 10px;
    border: 1.5px solid currentColor;
    border-radius: 999px;
    font: 600 11px var(--font-mono);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    transform: translateX(-50%) rotate(-6deg);
    background: color-mix(in srgb, var(--solid-void) 50%, transparent);
    pointer-events: none;
  }
  @media (max-width: 600px) {
    .face {
      padding: 1rem;
    }
    .content {
      font-size: 1.1rem;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .card,
    .inner {
      transition: none;
    }
    .inner.flipped,
    .back {
      transform: none;
    }
    .face[aria-hidden="true"] {
      visibility: hidden;
    }
  }
</style>
