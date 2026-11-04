<script lang="ts">
  /**
   * c's ground: near-black under an etched diamond lattice, deep blood-red washes that bleed
   * like ink in water, and white engraved line art: a hatched crescent with rays, a gothic
   * tracery arch rising behind the page, and a large astrological wheel of sigils that turns
   * very slowly (calm motion; Reduce motion stills it). Muted teal appears only as two small
   * planets. The engraving stays faint enough for dim text over it. Original drawing.
   *
   * The bleeding washes and the scratch grain are bleed.source.svg and scratch.source.svg
   * rendered to WebP (scripts/render-theme-art.mjs): as live SVG filters they re-ran on
   * every frame of the wheel's turn. The lattice and the line art stay vector.
   */
  import bleed from "./bleed-1600.webp";
  import scratch from "./scratch-256.webp";

  const SIGILS = [
    "M-5 0a5 5 0 1 0 10 0a5 5 0 1 0-10 0M0-5v10M-5 0h10",
    "M3-5a5 5 0 1 0 0 10a4 4 0 1 1 0-10",
    "M0-6l5.2 9H-5.2Z",
    "M0-6v12M-4-2h8M-3 6h6",
    "M-4 4a4 4 0 1 1 8 0M0 0v-6M-3-4h6",
    "M-5-5l10 10M5-5l-10 10M-6 0h12",
    "M0-6L5 0L0 6L-5 0ZM0-2.4v4.8",
    "M-5 3a5 5 0 0 1 10 0M0 3v-9M-3-3l3-3 3 3",
  ];
  const R = 300;
  const TICKS = Array.from({ length: 12 }, (_, index) => (index * Math.PI) / 6);
  const FINE = Array.from({ length: 72 }, (_, index) => (index * Math.PI) / 36);
  const RAYS = Array.from({ length: 16 }, (_, index) => (index * Math.PI) / 8);
</script>

<svg class="ground" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  <defs>
    <pattern id="c-lattice" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M20 0L40 20L20 40L0 20Z" fill="none" stroke="#ffffff" stroke-opacity="0.07" stroke-width="0.8" />
      <path d="M20 8L32 20L20 32L8 20Z" fill="none" stroke="#ffffff" stroke-opacity="0.035" stroke-width="0.6" />
      <circle cx="20" cy="20" r="0.9" fill="#ffffff" fill-opacity="0.12" />
    </pattern>
    <pattern id="c-hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">
      <path d="M0 0v4" stroke="#f1ece8" stroke-opacity="0.42" stroke-width="0.7" />
    </pattern>
    <pattern id="c-scratch" width="256" height="256" patternUnits="userSpaceOnUse">
      <image href={scratch} width="256" height="256" />
    </pattern>
  </defs>

  <rect width="1600" height="1000" fill="url(#c-lattice)" />

  <image href={bleed} width="1600" height="1000" />

  <!-- Gothic tracery: a pointed arch with cusps and a quatrefoil, rising behind the page. -->
  <g transform="translate(560 1000)" fill="none" stroke="#f1ece8" stroke-opacity="0.13" stroke-width="1">
    <path d="M0 0V-420Q0-640 240-760Q480-640 480-420V0" />
    <path d="M30 0V-410Q30-610 240-722Q450-610 450-410V0" />
    <path d="M60 0V-380Q60-520 150-580Q240-520 240-380V0M240 0V-380Q240-520 330-580Q420-520 420-380V0" />
    <circle cx="240" cy="-590" r="70" />
    <path d="M240-650a30 30 0 1 1 0 60a30 30 0 1 1 0-60M180-590a30 30 0 1 0 60 0a30 30 0 1 0-60 0M240-590a30 30 0 1 0 60 0a30 30 0 1 0-60 0" />
    <path d="M0-300h480M0-180h480" stroke-dasharray="2 6" />
  </g>

  <!-- A hatched crescent with rays. -->
  <g transform="translate(1390 170)">
    <path d="M22-70a72 72 0 1 0 0 140a56 56 0 1 1 0-140Z" fill="url(#c-hatch)" stroke="#f1ece8" stroke-opacity="0.4" stroke-width="0.9" />
    {#each RAYS as angle, index (angle)}
      <path
        d={`M${Math.cos(angle) * 88} ${Math.sin(angle) * 88}L${Math.cos(angle) * (index % 2 ? 106 : 124)} ${Math.sin(angle) * (index % 2 ? 106 : 124)}`}
        stroke="#f1ece8"
        stroke-opacity="0.22"
        stroke-width="0.8"
      />
    {/each}
  </g>

  <rect width="1600" height="1000" fill="url(#c-scratch)" opacity="0.05" />
</svg>

<!-- The wheel: engraved rings, twelve sigils, a hexagram scored in red, two teal planets. -->
<svg class="wheel" viewBox={`${-R - 4} ${-R - 4} ${2 * R + 8} ${2 * R + 8}`} aria-hidden="true">
  <g fill="none" stroke="#f1ece8" stroke-linecap="round">
    <circle r={R} stroke-opacity="0.26" stroke-width="1" />
    <circle r={R - 7} stroke-opacity="0.18" stroke-width="0.7" />
    {#each FINE as angle (angle)}
      <path d={`M${Math.cos(angle) * (R - 7)} ${Math.sin(angle) * (R - 7)}L${Math.cos(angle) * (R - 16)} ${Math.sin(angle) * (R - 16)}`} stroke-opacity="0.2" stroke-width="0.7" />
    {/each}
    <circle r={R - 62} stroke-opacity="0.2" stroke-width="0.8" />
    <circle r={R * 0.36} stroke-opacity="0.2" stroke-width="0.8" />
    <circle r={R * 0.12} stroke-opacity="0.28" stroke-width="1" />
    {#each TICKS as angle, index (angle)}
      <path d={`M${Math.cos(angle) * R * 0.36} ${Math.sin(angle) * R * 0.36}L${Math.cos(angle) * (R - 16)} ${Math.sin(angle) * (R - 16)}`} stroke-opacity="0.14" stroke-width="0.7" />
      <path
        transform={`translate(${Math.cos(angle + Math.PI / 12) * (R - 38)} ${Math.sin(angle + Math.PI / 12) * (R - 38)}) scale(1.5)`}
        d={SIGILS[index % SIGILS.length]}
        stroke-opacity="0.4"
        stroke-width="0.8"
      />
    {/each}
    <path
      d={`M0 ${-R * 0.36}L${R * 0.31} ${R * 0.18}H${-R * 0.31}ZM0 ${R * 0.36}L${R * 0.31} ${-R * 0.18}H${-R * 0.31}Z`}
      stroke="#d13a48"
      stroke-opacity="0.5"
      stroke-width="1"
    />
  </g>
  <circle cx={Math.cos(2.2) * (R - 62)} cy={Math.sin(2.2) * (R - 62)} r="6" fill="#5f8f88" fill-opacity="0.6" />
  <circle cx={Math.cos(5.4) * R * 0.36} cy={Math.sin(5.4) * R * 0.36} r="4" fill="#5f8f88" fill-opacity="0.5" />
</svg>

<style>
  .ground {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }
  .wheel {
    position: absolute;
    left: 50%;
    top: 55%;
    width: min(78vmin, 720px);
    height: min(78vmin, 720px);
    translate: -36% -50%;
    animation: c-turn calc(var(--motion-ms) * 900) linear infinite;
  }
  @keyframes c-turn {
    to {
      rotate: 360deg;
    }
  }
</style>
