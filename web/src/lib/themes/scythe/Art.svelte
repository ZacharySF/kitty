<script lang="ts">
  /** Scythe's night meadow: a crescent moon high on the right, a mist band sweeping slowly
   *  over a row of grass at the foot of the window. */
  const BLADES = Array.from({ length: 48 }, (_, index) => ({ x: index * 2.1 + ((index * 7) % 5) * 0.3, h: 8 + ((index * 13) % 9) }));
</script>

<svg class="moon" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14a8.5 8.5 0 0 1-10-10 8.5 8.5 0 1 0 10 10Z" /></svg>
<div class="mist"></div>
<svg class="grass" viewBox="0 0 100 20" preserveAspectRatio="none" aria-hidden="true">
  {#each BLADES as blade, index (index)}<path d={`M${blade.x} 20 L${blade.x + 0.6} ${20 - blade.h} L${blade.x + 1.2} 20`} />{/each}
</svg>

<style>
  .moon {
    position: absolute;
    top: 7vh;
    right: 9vw;
    width: 72px;
    height: 72px;
    fill: color-mix(in srgb, var(--br-lineHot) 32%, transparent);
    filter: drop-shadow(0 0 18px color-mix(in srgb, var(--ink) 18%, transparent));
  }
  .mist {
    position: absolute;
    left: -30%;
    right: -30%;
    bottom: 6vh;
    height: 18vh;
    background: radial-gradient(50% 50% at 50% 50%, color-mix(in srgb, var(--c2) 10%, transparent), transparent 70%);
    animation: scythe-mist calc(var(--motion-ms) * 300) ease-in-out infinite alternate;
  }
  .grass {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    width: 100%;
    height: 34px;
    fill: color-mix(in srgb, var(--br-line) 45%, transparent);
  }
  @keyframes scythe-mist {
    from { transform: translateX(-12%); }
    to { transform: translateX(12%); }
  }
</style>
