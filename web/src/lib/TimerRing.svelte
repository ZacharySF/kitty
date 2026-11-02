<script lang="ts">
  import type { Snippet } from "svelte";

  /**
   * A thin ring in the phase colour that drains as the phase runs, with optional
   * dots for completed focus rounds. `children` sits in the centre (the digits).
   */
  let {
    remaining,
    total,
    phase,
    label,
    rounds = 0,
    size = 260,
    stroke = 6,
    children,
  }: {
    remaining: number;
    total: number;
    phase: "priming" | "focus" | "short_break";
    label: string;
    rounds?: number;
    size?: number;
    stroke?: number;
    children?: Snippet;
  } = $props();

  const radius = $derived((size - stroke) / 2);
  const circumference = $derived(2 * Math.PI * radius);
  const fraction = $derived(total > 0 ? Math.min(1, Math.max(0, remaining / total)) : 0);
</script>

<div class="timer-ring" data-phase={phase} role="img" aria-label={label} style:width={`${size}px`} style:height={`${size}px`}>
  <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
    <circle class="track" cx={size / 2} cy={size / 2} r={radius} stroke-width={stroke} />
    <circle
      class="progress"
      cx={size / 2}
      cy={size / 2}
      r={radius}
      stroke-width={stroke}
      stroke-dasharray={circumference}
      style:stroke-dashoffset={`${circumference * (1 - fraction)}px`}
      transform={`rotate(-90 ${size / 2} ${size / 2})`}
    />
  </svg>
  {#if children || rounds}
    <div class="centre">
      {#if children}{@render children()}{/if}
      {#if rounds}
        <span class="rounds" aria-hidden="true">{#each Array.from({ length: Math.min(rounds, 8) }) as _, index (index)}<i></i>{/each}{#if rounds > 8}<b>+{rounds - 8}</b>{/if}</span>
      {/if}
    </div>
  {/if}
</div>

<style>
  .timer-ring {
    --phase: var(--accent);
    position: relative;
    display: inline-grid;
    place-items: center;
    flex: none;
  }
  .timer-ring[data-phase="priming"] {
    --phase: var(--c3, var(--accent));
  }
  .timer-ring[data-phase="short_break"] {
    --phase: var(--success);
  }
  svg {
    position: absolute;
    inset: 0;
  }
  circle {
    fill: none;
  }
  .track {
    stroke: color-mix(in srgb, var(--phase) 16%, transparent);
  }
  .progress {
    stroke: var(--phase);
    stroke-linecap: round;
    filter: drop-shadow(0 0 6px color-mix(in srgb, var(--phase) 45%, transparent));
    transition: stroke-dashoffset 260ms linear;
  }
  .centre {
    position: relative;
    display: grid;
    justify-items: center;
    gap: 6px;
  }
  .rounds {
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .rounds i {
    width: 6px;
    height: 6px;
    border-radius: 999px;
    background: var(--phase);
  }
  .rounds b {
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
  }
  @media (prefers-reduced-motion: reduce) {
    .progress {
      transition: none;
    }
  }
</style>
