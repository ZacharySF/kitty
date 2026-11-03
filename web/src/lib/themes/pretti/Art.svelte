<script lang="ts">
  /**
   * pretti's ground: purple-black sprayed with airbrushed acid green, magenta and violet,
   * hypnotic ringed targets, glossy hearts, outlined stars and butterflies, all closed in by
   * a dark vignette. The pieces gather where the window shows its ground (the bottom-left
   * under the home widgets, the bottom edge, the far corners), never under the right rail's
   * text; the spiral turns and the butterflies flap on the theme's motion. `.ornament`
   * pieces are motif marks (Theme motif off hides them).
   *
   * The airbrushed ground and the spray grain are air.source.svg and spray.source.svg
   * rendered to WebP (scripts/render-theme-art.mjs): as live SVG filters (a 56px blur over
   * the window) they re-ran on every frame the stars twinkled.
   */
  import air from "./air-1440.webp";
  import spray from "./spray-256.webp";
  const uid = $props.id();
  const STAR = "M0-12L3-3.5L12-3.5L5 2L7.5 11L0 5.6L-7.5 11L-5 2L-12-3.5L-3-3.5Z";
  const HEART = "M0 10C-14 1-16-7-10-11C-6-14-2-12 0-8C2-12 6-14 10-11C16-7 14 1 0 10Z";
  const WING = "M0 0C-6-14-22-18-24-8C-25 0-12 4 0 0ZM0 0C-4 8-16 18-20 10C-22 4-12 0 0 0Z";
  const TARGETS = [
    { x: 70, y: 800, r: 170 },
    { x: 1450, y: 910, r: 132 },
  ];
  const HEARTS = [
    { x: 318, y: 610, s: 1.15, r: -14 },
    { x: 172, y: 468, s: 0.8, r: 16 },
    { x: 760, y: 878, s: 0.85, r: -8 },
  ];
  const STARS = [
    { x: 236, y: 560, s: 1.2, c: "#ffd84a" },
    { x: 52, y: 540, s: 0.75, c: "#ff4fb4" },
    { x: 1000, y: 884, s: 1.1, c: "#ff4fb4" },
    { x: 520, y: 880, s: 0.8, c: "#ffd84a" },
    { x: 1300, y: 856, s: 0.7, c: "#b6ff3b" },
  ];
  /* An Archimedean spiral as one path, drawn in the loader and the corner piece alike. */
  const SPIRAL = Array.from({ length: 300 }, (_, i) => {
    const t = i * 0.12;
    const r = 2 + t * 3.1;
    return `${i ? "L" : "M"}${(Math.cos(t) * r).toFixed(1)} ${(Math.sin(t) * r).toFixed(1)}`;
  }).join("");
</script>

<svg class="ground" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  <defs>
    <pattern id="{uid}-spray" width="256" height="256" patternUnits="userSpaceOnUse">
      <image href={spray} width="256" height="256" />
    </pattern>
    <radialGradient id="{uid}-heart" cx="0.35" cy="0.3" r="0.8">
      <stop offset="0" stop-color="#ffc2e8" />
      <stop offset="0.35" stop-color="#ff4fb4" />
      <stop offset="1" stop-color="#8a1a7a" />
    </radialGradient>
    <radialGradient id="{uid}-vignette" cx="0.5" cy="0.48" r="0.75">
      <stop offset="0.5" stop-color="#05000a" stop-opacity="0" />
      <stop offset="1" stop-color="#05000a" stop-opacity="0.66" />
    </radialGradient>
  </defs>

  <image href={air} width="1440" height="900" />

  <g class="ornament">
    {#each TARGETS as target, index (index)}
      <g transform={`translate(${target.x} ${target.y})`}>
        {#each Array.from({ length: 7 }, (_, ring) => ring) as ring (ring)}
          <circle r={target.r * (1 - ring / 7)} fill={ring % 2 ? "#f3a6d4" : "#d8358a"} opacity="0.62" />
        {/each}
        <circle r={target.r} fill="none" stroke="#2b0a33" stroke-width="3" />
        <ellipse cx={-target.r * 0.32} cy={-target.r * 0.42} rx={target.r * 0.34} ry={target.r * 0.14} fill="#ffd9f0" opacity="0.3" transform="rotate(-24)" />
      </g>
    {/each}
    {#each HEARTS as heart, index (index)}
      <g transform={`translate(${heart.x} ${heart.y}) rotate(${heart.r}) scale(${heart.s * 2})`}>
        <path d={HEART} fill="url(#{uid}-heart)" stroke="#2b0a33" stroke-width="1.2" />
        <ellipse cx="-6" cy="-6" rx="3.2" ry="1.8" fill="#ffe3f4" opacity="0.7" transform="rotate(-30 -6 -6)" />
      </g>
    {/each}
    {#each STARS as star, index (index)}
      <g class="twinkle" style:animation-delay={`${index * -0.7}s`} transform={`translate(${star.x} ${star.y}) scale(${star.s * 1.6})`}>
        <path d={STAR} fill={star.c} fill-opacity="0.8" stroke="#2b0a33" stroke-width="1.4" stroke-linejoin="round" />
        <path d={STAR} fill="none" stroke="#ffe8f6" stroke-opacity="0.5" stroke-width="0.6" transform="scale(0.6)" />
      </g>
    {/each}
  </g>
  <rect width="1440" height="900" fill="url(#{uid}-spray)" opacity="0.06" />
  <rect width="1440" height="900" fill="url(#{uid}-vignette)" />
</svg>

<!-- Moving pieces live outside the blurred ground so only they repaint. -->
<svg class="spiral ornament" viewBox="-100 -100 200 200" aria-hidden="true">
  <path d={SPIRAL} stroke="#ff7ac8" stroke-width="5" opacity="0.34" />
  <path d={SPIRAL} stroke="#b6ff3b" stroke-width="1.4" opacity="0.5" transform="rotate(20)" />
</svg>
{#each [{ left: "15%", top: "64%", s: 1.1, r: -18, c: "#b6ff3b" }, { left: "6%", top: "47%", s: 0.75, r: 14, c: "#c48bff" }] as fly, index (index)}
  <svg class="fly ornament" style:left={fly.left} style:top={fly.top} style:--turn={`${fly.r}deg`} style:--size={`${fly.s * 64}px`} viewBox="-28 -24 56 48" aria-hidden="true">
    <g stroke="#2b0a33" stroke-width="1">
      <path class="wing" d={WING} fill={fly.c} fill-opacity="0.78" />
      <path class="wing right" d={WING} fill={fly.c} fill-opacity="0.78" transform="scale(-1 1)" />
      <path d="M0-8v18" stroke-width="2.4" stroke-linecap="round" />
      <path d="M0-8c-2-5-4-7-6-8M0-8c2-5 4-7 6-8" fill="none" />
      <circle cx="-14" cy="-8" r="2.6" fill="#ffe8f6" fill-opacity="0.7" stroke="none" />
      <circle cx="14" cy="-8" r="2.6" fill="#ffe8f6" fill-opacity="0.7" stroke="none" />
    </g>
  </svg>
{/each}

<style>
  .ground {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }
  .spiral {
    position: absolute;
    left: -9vmin;
    bottom: -10vmin;
    width: 46vmin;
    height: 46vmin;
    fill: none;
    stroke-linecap: round;
    animation: pretti-turn calc(var(--motion-ms) * 140) linear infinite;
  }
  .fly {
    position: absolute;
    width: var(--size);
    height: var(--size);
    transform: rotate(var(--turn));
  }
  .wing {
    transform-box: view-box;
    transform-origin: center;
    animation: pretti-flap calc(var(--motion-ms) * 9) ease-in-out infinite alternate;
  }
  .wing.right {
    animation-name: pretti-flap-right;
  }
  .twinkle {
    animation: pretti-twinkle calc(var(--motion-ms) * 12) ease-in-out infinite alternate;
  }
  :global(:root[data-motif="off"]) .ornament {
    display: none;
  }
  @keyframes pretti-turn {
    to { transform: rotate(-360deg); }
  }
  @keyframes pretti-flap {
    to { transform: scaleX(0.55); }
  }
  @keyframes pretti-flap-right {
    from { transform: scale(-1, 1); }
    to { transform: scale(-0.55, 1); }
  }
  @keyframes pretti-twinkle {
    to { opacity: 0.45; }
  }
</style>
