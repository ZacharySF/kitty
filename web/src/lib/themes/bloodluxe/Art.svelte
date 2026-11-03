<script lang="ts">
  /**
   * BloodLuxe's ground: a marbled red, white and blue sky (a striped gradient pulled through
   * turbulence) drifting slowly over a cream veil where page titles sit, a rose-dust ridge,
   * a vivid grass field and orange flame tongues that flicker on the theme's motion. The
   * flames are motif marks (`.ornament`, hidden when the motif is off).
   */
  const uid = $props.id();
  // Flame tongues on the grass, tucked into the bottom corners: x, base y, height, lean.
  const FLAMES = [
    [34, 892, 66, -6], [86, 900, 40, 4], [150, 896, 54, 8],
    [1300, 898, 46, 6], [1360, 890, 70, -8], [1412, 900, 42, 4],
  ] as const;
  const flame = ([x, y, h, lean]: readonly number[]) => {
    const w = h * 0.34;
    return `M${x - w} ${y} C${x - w * 1.1} ${y - h * 0.45} ${x + lean - w * 0.2} ${y - h * 0.62} ${x + lean} ${y - h}
      C${x + lean + w * 0.15} ${y - h * 0.7} ${x + w * 1.2} ${y - h * 0.5} ${x + w} ${y} Z`;
  };
  // Grass blades: a deterministic scatter so the field reads as painted, not flat.
  const BLADES = Array.from({ length: 170 }, (_, i) => {
    const x = (i * 97.3) % 1440;
    const y = 780 + ((i * 53.7) % 120);
    const h = 9 + ((i * 7) % 10);
    return `M${x.toFixed(1)} ${y.toFixed(1)} q${i % 2 ? 2 : -2} ${-h / 2} ${(i % 3) - 1} ${-h}`;
  }).join(" ");
</script>

<svg class="marble" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  <defs>
    <linearGradient id="{uid}-stripes" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="130" y2="76" spreadMethod="repeat">
      <stop offset="0" stop-color="#d3203b" />
      <stop offset="0.16" stop-color="#d3203b" />
      <stop offset="0.24" stop-color="#fff8ee" />
      <stop offset="0.38" stop-color="#fff8ee" />
      <stop offset="0.44" stop-color="#2546c4" />
      <stop offset="0.58" stop-color="#2546c4" />
      <stop offset="0.64" stop-color="#fdf2ea" />
      <stop offset="0.76" stop-color="#f5a3c3" />
      <stop offset="0.84" stop-color="#fff8ee" />
      <stop offset="1" stop-color="#d3203b" />
    </linearGradient>
    <filter id="{uid}-marble" filterUnits="userSpaceOnUse" x="-60" y="-60" width="1560" height="1020">
      <feTurbulence type="fractalNoise" baseFrequency="0.0030 0.0054" numOctaves="3" seed="11" result="noise" />
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="340" xChannelSelector="R" yChannelSelector="G" />
    </filter>
  </defs>
  <g filter="url(#{uid}-marble)"><rect x="-260" y="-260" width="1960" height="1420" fill="url(#{uid}-stripes)" /></g>
</svg>

<svg class="scene" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  <defs>
    <!-- a cream veil keeps the marble vivid at the top and the sides, soft where page text sits -->
    <linearGradient id="{uid}-veil" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0.08" stop-color="#fbf3e8" stop-opacity="0" />
      <stop offset="0.17" stop-color="#fbf3e8" stop-opacity="0.86" />
      <stop offset="0.74" stop-color="#fbf3e8" stop-opacity="0.86" />
      <stop offset="0.82" stop-color="#fbf3e8" stop-opacity="0.2" />
    </linearGradient>
    <linearGradient id="{uid}-edges" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#000" />
      <stop offset="0.08" stop-color="#fff" />
      <stop offset="0.92" stop-color="#fff" />
      <stop offset="1" stop-color="#000" />
    </linearGradient>
    <mask id="{uid}-sides"><rect width="1440" height="900" fill="url(#{uid}-edges)" /></mask>
    <linearGradient id="{uid}-grass" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#55c234" />
      <stop offset="1" stop-color="#1f7d17" />
    </linearGradient>
    <linearGradient id="{uid}-flame" x1="0" y1="1" x2="0" y2="0">
      <stop offset="0" stop-color="#ffd23a" />
      <stop offset="0.45" stop-color="#ff7a12" />
      <stop offset="1" stop-color="#e2261b" />
    </linearGradient>
  </defs>
  <rect width="1440" height="900" fill="url(#{uid}-veil)" mask="url(#{uid}-sides)" />
  <!-- rose-dust ridges (the far one paler) and the grass field, low along the foot -->
  <g transform="translate(0 56)">
    <path
      d="M0 676 L70 640 L150 660 L232 620 L300 648 L400 612 L480 650 L600 616 L700 652 L820 618 L900 646 L1000 604 L1090 640 L1180 610 L1270 646 L1360 616 L1440 632 L1440 740 L0 740 Z"
      fill="#e3b3ab"
    />
    <path
      d="M0 700 L90 650 L160 672 L250 626 L330 664 L420 640 L490 676 L580 650 L660 686 L780 644 L870 668 L950 630 L1040 660 L1120 628 L1210 664 L1290 642 L1360 660 L1440 646 L1440 740 L0 740 Z"
      fill="#c47d79"
    />
    <path d="M0 724 Q360 702 720 718 T1440 712 L1440 900 L0 900 Z" fill="url(#{uid}-grass)" />
  </g>
  <path d={BLADES} stroke="#9be86e" stroke-width="1.8" fill="none" stroke-linecap="round" opacity="0.8" />
  <g class="ornament flicker">
    {#each FLAMES as f, index (index)}
      <path d={flame(f)} fill="url(#{uid}-flame)" />
      <path d={flame([f[0], f[1], f[2] * 0.5, f[3] * 0.5])} fill="#ffe27a" />
    {/each}
  </g>
</svg>

<style>
  svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }
  .marble {
    inset: -3vmax;
    width: calc(100% + 6vmax);
    height: calc(100% + 6vmax);
    animation: bloodluxe-drift calc(var(--motion-ms) * 280) ease-in-out infinite alternate;
  }
  .flicker {
    transform-origin: 50% 100%;
    transform-box: fill-box;
    animation: bloodluxe-flicker calc(var(--motion-ms) * 9) ease-in-out infinite alternate;
  }
  :global(:root[data-motif="off"]) .ornament {
    display: none;
  }
  @keyframes bloodluxe-drift {
    to { transform: translate3d(-2.4vmax, 1.2vmax, 0) rotate(0.6deg); }
  }
  @keyframes bloodluxe-flicker {
    to { transform: scaleY(1.07); }
  }
</style>
