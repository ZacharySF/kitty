<script lang="ts" module>
  export const GRADES = [
    { n: 1, label: "Again", hint: "missed", color: "var(--error)" },
    { n: 2, label: "Hard", hint: "struggled", color: "var(--warning)" },
    { n: 3, label: "Good", hint: "recalled", color: "var(--success)" },
    { n: 4, label: "Easy", hint: "effortless", color: "var(--c4)" },
  ] as const;
  /** A key that is currently held down: a grade 1–4, or Space. */
  export type HeldKey = number | "space" | null;
  export function interval(seconds: number) {
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
  }
</script>

<script lang="ts">
  /**
   * Keyboard strip (Q39): real keycaps for 1–4 and Space. A key sinks while it is
   * pressed by mouse or by the keyboard (`held`), teaching the shortcuts in use.
   */
  let {
    revealed,
    intervals,
    held = null,
    ongrade,
    onspace,
  }: {
    revealed: boolean;
    intervals: { rating: number; seconds: number }[];
    held?: HeldKey;
    ongrade: (rating: number) => void;
    onspace: () => void;
  } = $props();
</script>

<div class="keys" role="group" aria-label="Grade keys">
  {#each GRADES as grade, index}
    <button
      class="key"
      class:held={held === grade.n}
      style:--g={grade.color}
      title={`${grade.label}: you ${grade.hint} it`}
      disabled={!revealed}
      onclick={() => ongrade(grade.n)}
    >
      <kbd>{grade.n}</kbd>
      <span>{grade.label}</span>
      <small>{revealed && intervals[index] ? interval(intervals[index].seconds) : " "}</small>
    </button>
  {/each}
  <span class="sep" aria-hidden="true"></span>
  <button
    class="key wide"
    class:held={held === "space"}
    style:--g={revealed ? GRADES[2].color : "var(--violet)"}
    title={revealed ? "Space grades Good" : "Space flips the card"}
    onclick={onspace}
  >
    <kbd>Space</kbd>
    <span>{revealed ? "Good" : "Flip"}</span>
    <small>{revealed ? "default" : "show answer"}</small>
  </button>
</div>

<style>
  .keys {
    display: flex;
    align-items: stretch;
    gap: 7px;
    padding: 10px;
    border: 1px solid var(--hairline);
    border-radius: 12px;
    background: color-mix(in srgb, var(--solid-inset) 70%, transparent);
  }
  .key {
    flex: 1;
    min-width: 0;
    display: grid;
    justify-items: center;
    gap: 3px;
    padding: 0 0 2px;
    border: 0;
    background: none;
    color: var(--ink);
    font: 10px var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    cursor: pointer;
  }
  .key.wide {
    flex: 1.5;
  }
  .key kbd {
    display: grid;
    place-items: center;
    width: 100%;
    height: 34px;
    box-sizing: border-box;
    border: 1px solid var(--border);
    border-bottom: 3px solid color-mix(in srgb, var(--g) 85%, var(--border));
    border-radius: 8px;
    background: linear-gradient(to bottom, var(--solid-raised), var(--solid-panel));
    color: var(--ink);
    font: 13px var(--font-mono);
    transition: transform 0.08s ease, border-bottom-width 0.08s ease;
  }
  .key.wide kbd {
    font-size: 11px;
  }
  .key:hover:not(:disabled) kbd {
    border-color: var(--muted);
    border-bottom-color: var(--g);
  }
  .key.held kbd,
  .key:active:not(:disabled) kbd {
    transform: translateY(2px);
    border-bottom-width: 1px;
    background: color-mix(in srgb, var(--g) 20%, var(--solid-raised));
  }
  .key:focus-visible {
    outline: none;
  }
  .key:focus-visible kbd {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .key:disabled {
    cursor: default;
    opacity: 0.45;
  }
  .key small {
    min-height: 1.2em;
    color: var(--subtle);
    font-size: 9.5px;
    letter-spacing: 0.02em;
    text-transform: none;
  }
  .sep {
    width: 1px;
    background: var(--hairline);
  }
  @media (max-width: 420px) {
    .key span {
      display: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .key kbd {
      transition: none;
    }
  }
</style>
