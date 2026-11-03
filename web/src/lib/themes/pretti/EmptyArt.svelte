<script lang="ts">
  /** pretti's empty state: a glossy butterfly resting on a ringed target, a heart and two stars. */
  const uid = $props.id();
  const HEART = "M0 10C-14 1-16-7-10-11C-6-14-2-12 0-8C2-12 6-14 10-11C16-7 14 1 0 10Z";
  const STAR = "M0-12L3-3.5L12-3.5L5 2L7.5 11L0 5.6L-7.5 11L-5 2L-12-3.5L-3-3.5Z";
  const WING = "M0 0C-6-14-22-18-24-8C-25 0-12 4 0 0ZM0 0C-4 8-16 18-20 10C-22 4-12 0 0 0Z";
</script>

<svg class="pretti-empty" viewBox="0 0 132 84" aria-hidden="true">
  <defs>
    <radialGradient id="{uid}-heart" cx="0.35" cy="0.3" r="0.8">
      <stop offset="0" stop-color="#ffd1ef" />
      <stop offset="0.4" stop-color="#ff4fb4" />
      <stop offset="1" stop-color="#8a1a7a" />
    </radialGradient>
  </defs>
  <g transform="translate(66 64)">
    {#each [22, 17.6, 13.2, 8.8, 4.4] as r, index (r)}<ellipse rx={r * 1.7} ry={r * 0.52} fill={index % 2 ? "#ffe3f3" : "#e8418f"} opacity="0.9" />{/each}
  </g>
  <g class="fly" transform="translate(66 38) scale(1.1)" stroke="#2b0a33" stroke-width="1">
    <path class="wing" d={WING} fill="#b6ff3b" />
    <path class="wing" d={WING} fill="#c48bff" transform="scale(-1 1)" />
    <path d="M0-8v18" stroke-width="2.4" stroke-linecap="round" />
    <path d="M0-8c-2-5-4-7-6-8M0-8c2-5 4-7 6-8" fill="none" />
    <circle cx="-14" cy="-8" r="2.4" fill="#fff" stroke="none" opacity="0.8" />
    <circle cx="14" cy="-8" r="2.4" fill="#fff" stroke="none" opacity="0.8" />
  </g>
  <g class="beat" transform="translate(110 20) scale(0.78)">
    <path d={HEART} fill="url(#{uid}-heart)" stroke="#2b0a33" stroke-width="1.2" />
    <ellipse cx="-6" cy="-6" rx="3" ry="1.6" fill="#fff" opacity="0.75" transform="rotate(-30 -6 -6)" />
  </g>
  <path transform="translate(18 18) scale(0.62)" d={STAR} fill="#ffd84a" stroke="#2b0a33" stroke-width="1.4" />
  <path transform="translate(28 70) scale(0.42)" d={STAR} fill="#ff4fb4" stroke="#2b0a33" stroke-width="1.6" />
</svg>

<style>
  .pretti-empty {
    width: 140px;
    height: auto;
    overflow: visible;
    filter: drop-shadow(0 6px 14px rgb(5 0 10 / 0.5));
  }
  .beat {
    transform-box: fill-box;
    transform-origin: center;
    animation: pretti-beat calc(var(--motion-ms) * 6) ease-in-out infinite alternate;
  }
  @keyframes pretti-beat {
    to { scale: 1.12; }
  }
</style>
