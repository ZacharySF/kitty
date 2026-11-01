<script lang="ts">
  let { streak, logged, onlog }: { streak: number; logged: boolean; onlog: () => void } = $props();
  const milestone = $derived([7, 14, 30, 60, 100].find((days) => days > streak) ?? (Math.floor(streak / 100) + 1) * 100);
  // Large runs show the current hundred-day milestone, never thousands of SVG paths.
  const start = $derived(milestone > 100 ? milestone - 100 : 0);
  const count = $derived(milestone - start);
  const arc = (i: number) => {
    const segment = 360 / count, gap = Math.min(3, segment / 5);
    const a = (i * segment + gap) * Math.PI / 180 - Math.PI / 2;
    const b = ((i + 1) * segment - gap) * Math.PI / 180 - Math.PI / 2;
    return `M${60 + 46 * Math.cos(a)} ${60 + 46 * Math.sin(a)} A46 46 0 0 1 ${60 + 46 * Math.cos(b)} ${60 + 46 * Math.sin(b)}`;
  };
</script>

<section class="panel streak" aria-label="Study streak">
  <svg viewBox="0 0 120 120" role="img" aria-label={`${streak} day streak, ${milestone - streak} days to ${milestone}`}>
    {#each Array.from({ length: count }) as _, i}
      <path d={arc(i)} class:filled={i + start < streak} />
    {/each}
    <text x="60" y="58" class="number">{streak}</text>
    <text x="60" y="76" class="unit">DAYS</text>
  </svg>
  <div>
    <span class="eyebrow">Study streak</span>
    <strong>{milestone - streak} days to {milestone}</strong>
    <p>{logged ? "Today's study is logged." : "Log real study time to fill today's segment."}</p>
    <button type="button" class="pill" onclick={onlog}>{logged ? "Log more study" : "Log today"}</button>
  </div>
</section>

<style>
  .streak { display: flex; align-items: center; gap: 16px; }
  svg { flex: none; width: 128px; height: 128px; }
  path { fill: none; stroke: color-mix(in srgb, var(--ink) 10%, transparent); stroke-width: 9; transition: stroke 300ms; }
  path.filled { stroke: var(--c1); }
  text { fill: var(--ink); text-anchor: middle; }
  .number { font: 700 34px var(--font-ui); }
  .unit { fill: var(--muted); font: 500 8.5px var(--font-mono); letter-spacing: .16em; }
  .streak div { display: grid; gap: 4px; }
  strong { font-size: 14px; }
  p { margin: 0; color: var(--muted); font-size: 12px; }
  button { justify-self: start; margin-top: 6px; }
  @media (prefers-reduced-motion: reduce) { path { transition: none; } }
</style>
