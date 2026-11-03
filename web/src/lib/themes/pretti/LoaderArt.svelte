<script lang="ts">
  /** pretti's view loader: a hypnotic two-tone spiral turning, a glossy heart at its eye. */
  const SPIRAL = Array.from({ length: 150 }, (_, i) => {
    const t = i * 0.16;
    const r = 1.5 + t * 1.75;
    return `${i ? "L" : "M"}${(Math.cos(t) * r).toFixed(2)} ${(Math.sin(t) * r).toFixed(2)}`;
  }).join("");
  const HEART = "M0 10C-14 1-16-7-10-11C-6-14-2-12 0-8C2-12 6-14 10-11C16-7 14 1 0 10Z";
</script>

<span class="pretti-loader">
  <svg class="spiral" viewBox="-46 -46 92 92" aria-hidden="true">
    <path d={SPIRAL} class="pink" />
    <path d={SPIRAL} class="acid" transform="rotate(180)" />
  </svg>
  <svg class="heart" viewBox="-16 -14 32 28" aria-hidden="true"><path d={HEART} /></svg>
</span>

<style>
  .pretti-loader {
    position: relative;
    display: grid;
    place-items: center;
    width: 96px;
    height: 96px;
  }
  .spiral {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    fill: none;
    stroke-linecap: round;
    filter: drop-shadow(0 0 6px rgb(182 255 59 / 0.45));
    animation: pretti-spin calc(var(--motion-ms) * 6) linear infinite;
  }
  .pink {
    stroke: #ff4fb4;
    stroke-width: 3.4;
  }
  .acid {
    stroke: #b6ff3b;
    stroke-width: 2;
  }
  .heart {
    position: relative;
    width: 18px;
    height: 16px;
    fill: #ff9bd6;
    stroke: #2b0a33;
    stroke-width: 1.4;
  }
  @keyframes pretti-spin {
    to { transform: rotate(360deg); }
  }
</style>
