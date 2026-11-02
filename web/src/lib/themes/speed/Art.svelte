<script lang="ts">
  /**
   * SPEED's ground: off-white rag paper that took the overspray. Pale grey-teal washes bleed
   * at their edges, a white gothic cathedral is drawn over one of them, black ink pools and
   * drips down the lower left (where the pages leave paper showing), teal-green blooms and
   * splatter scatter across, and a crimson wheel is sketched three times over with speed
   * lines. Faint gear rings tick at the top right; a crimson stamp signs the bottom corner.
   * The washes stay pale enough for dim text read over them; the black ink sits where no
   * text does. Original drawing, nothing traced.
   */

  /* Deterministic splatter: a small LCG, so every render draws the same spray. */
  function spray(seed: number, count: number, cx: number, cy: number, spread: number, size: number) {
    let state = seed;
    const next = () => (state = (Math.imul(state, 1664525) + 1013904223) >>> 0) / 4294967296;
    return Array.from({ length: count }, () => {
      const angle = next() * Math.PI * 2;
      const reach = Math.pow(next(), 1.5) * spread;
      return { x: cx + Math.cos(angle) * reach, y: cy + Math.sin(angle) * reach * 0.6, r: 0.8 + Math.pow(next(), 3.2) * size };
    });
  }

  const INK = spray(7, 64, 250, 640, 300, 8);
  const TEAL = [...spray(19, 30, 1010, 140, 220, 7), ...spray(23, 22, 1420, 860, 200, 6)];
  const DRIPS = [
    { x: 74, h: 170 },
    { x: 118, h: 96 },
    { x: 172, h: 240 },
    { x: 236, h: 64 },
    { x: 288, h: 150 },
    { x: 342, h: 108 },
    { x: 391, h: 40 },
  ];
  const ROSE = Array.from({ length: 16 }, (_, index) => (index * Math.PI) / 8);
  const SPOKES = Array.from({ length: 12 }, (_, index) => (index * Math.PI) / 6);
</script>

<svg class="paper" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  <defs>
    <filter id="spd-wash" x="-20%" y="-20%" width="140%" height="140%">
      <feTurbulence type="fractalNoise" baseFrequency="0.012" numOctaves="3" seed="11" result="noise" />
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="80" xChannelSelector="R" yChannelSelector="G" result="bled" />
      <feGaussianBlur in="bled" stdDeviation="6" />
    </filter>
    <filter id="spd-bleed" x="-20%" y="-20%" width="140%" height="140%">
      <feTurbulence type="fractalNoise" baseFrequency="0.03" numOctaves="2" seed="5" result="noise" />
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="28" xChannelSelector="R" yChannelSelector="G" result="bled" />
      <feGaussianBlur in="bled" stdDeviation="1.2" />
    </filter>
    <filter id="spd-rough" x="-5%" y="-5%" width="110%" height="110%">
      <feTurbulence type="fractalNoise" baseFrequency="0.06" numOctaves="2" seed="2" result="noise" />
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="3.4" xChannelSelector="R" yChannelSelector="G" />
    </filter>
    <filter id="spd-grain" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="2" seed="3" />
      <feColorMatrix values="0 0 0 0 0.2  0 0 0 0 0.22  0 0 0 0 0.21  0 0 0 0.08 0" />
    </filter>
  </defs>

  <!-- Pale grey-teal washes: rag paper that took the overspray. -->
  <g filter="url(#spd-wash)" opacity="0.8">
    <rect x="-60" y="90" width="560" height="420" fill="#d2d9d6" />
    <rect x="860" y="-40" width="520" height="210" fill="#d9dfdc" />
    <rect x="1180" y="560" width="520" height="480" fill="#d3dad6" />
    <ellipse cx="760" cy="990" rx="520" ry="90" fill="#cfd7d3" />
  </g>

  <!-- A white gothic cathedral drawn over its grey wash. -->
  <g transform="translate(250 120) scale(1.18)">
    <rect x="-40" y="-40" width="360" height="390" fill="#c9d2ce" opacity="0.6" filter="url(#spd-wash)" />
    <g fill="none" stroke="#fdfdfb" stroke-width="1.3" stroke-linejoin="round" stroke-linecap="round" filter="url(#spd-rough)">
      <path d="M14 330V78l24-70 24 70v252M38 8v-30M30 -12h16M28 44h20M22 100h32M22 160h32M22 220h32M22 280h32" />
      <path d="M198 330V78l24-70 24 70v252M222 8v-30M214 -12h16M212 44h20M206 100h32M206 160h32M206 220h32M206 280h32" />
      <path d="M26 78l-6-10M50 78l6-10M210 78l-6-10M234 78l6-10M30 60l-5-8M46 60l5-8M214 60l-5-8M230 60l5-8" />
      <path d="M62 330V118l68-74 68 74v212M130 44V6M120 20h20" />
      <path d="M70 118l-14-22M190 118l14-22M62 160l-18-6M198 160l18-6M62 236l-22-8M198 236l22-8" />
      <circle cx="130" cy="136" r="38" />
      <circle cx="130" cy="136" r="31" />
      <circle cx="130" cy="136" r="11" />
      {#each ROSE as angle (angle)}
        <path d={`M${130 + Math.cos(angle) * 11} ${136 + Math.sin(angle) * 11}L${130 + Math.cos(angle) * 31} ${136 + Math.sin(angle) * 31}`} />
      {/each}
      {#each [80, 105, 130, 155, 180] as x (x)}
        <path d={`M${x - 9} 316V230q9-28 18 0v86`} />
      {/each}
      <path d="M68 210h124M68 330h124M68 196h124" />
      <path d="M108 330v-58q22-44 44 0v58M116 330v-52q14-30 28 0v52" />
      {#each [26, 50, 210, 234] as x (x)}
        <path d={`M${x - 6} 314v-40q6-14 12 0v40`} />
      {/each}
      <path d="M-24 330h308M-14 330v-60l10-16 10 16v60M274 330v-60l-10-16-10 16v60" />
    </g>
  </g>

  <!-- Teal-green watercolour blooms. -->
  <g filter="url(#spd-wash)">
    <ellipse cx="1010" cy="132" rx="170" ry="80" fill="#2a8a70" opacity="0.32" />
    <ellipse cx="1060" cy="108" rx="80" ry="44" fill="#1f7a62" opacity="0.36" />
    <ellipse cx="1430" cy="880" rx="230" ry="96" fill="#2a8a70" opacity="0.3" />
    <ellipse cx="300" cy="610" rx="260" ry="70" fill="#2a8a70" opacity="0.42" />
  </g>

  <!-- Black ink pooled across the teal, dripping down the lower left. -->
  <g transform="translate(40 600)">
    <g filter="url(#spd-bleed)">
      <path d="M0 22c70-34 150-10 220-16 60-6 110 6 150 34-34 22-110 16-170 26-70 12-140 2-200-8Z" fill="#141716" opacity="0.86" />
      <path d="M60 26c46-12 106-8 140 0M240 30c30-6 60-2 84 4" stroke="#1f7a62" stroke-width="12" fill="none" opacity="0.7" />
    </g>
    {#each DRIPS as drip (drip.x)}
      <path d={`M${drip.x} 34v${drip.h}`} stroke="#141716" stroke-width={drip.x % 3 ? 2.6 : 3.8} stroke-linecap="round" opacity="0.86" />
      <circle cx={drip.x} cy={38 + drip.h} r={drip.x % 3 ? 3.2 : 4.6} fill="#141716" opacity="0.86" />
    {/each}
  </g>

  <!-- Splatter. -->
  <g>
    {#each INK as dot, index (index)}<circle cx={dot.x} cy={dot.y} r={dot.r} fill="#141716" opacity="0.82" />{/each}
    {#each TEAL as dot, index (index)}<circle cx={dot.x} cy={dot.y} r={dot.r} fill="#1f7a62" opacity="0.62" />{/each}
  </g>

  <!-- A crimson wheel sketched three times over, spokes and speed lines. -->
  <g transform="translate(1000 700)" fill="none" stroke="#c3164f" stroke-linecap="round" filter="url(#spd-rough)">
    <circle r="150" stroke-width="1.5" opacity="0.62" />
    <circle r="143" cx="4" cy="-3" stroke-width="1" opacity="0.42" />
    <ellipse rx="156" ry="147" stroke-width="0.9" opacity="0.32" />
    <circle r="84" stroke-width="1.2" opacity="0.52" />
    <circle r="24" stroke-width="1.6" opacity="0.66" />
    <circle r="9" stroke-width="1.2" opacity="0.6" />
    {#each SPOKES as angle (angle)}
      <path d={`M${Math.cos(angle) * 26} ${Math.sin(angle) * 26}L${Math.cos(angle) * 146} ${Math.sin(angle) * 146}`} stroke-width="1" opacity="0.42" />
    {/each}
    {#each [-196, -160, -128, -96] as y, index (y)}
      <path d={`M${-440 + index * 36} ${y + 160}l${250 - index * 26} ${-30 + index * 5}`} stroke-width={1.7 - index * 0.25} opacity="0.46" />
    {/each}
    <path d="M80 -196l52 40 22-8-34 52" stroke-width="1.6" opacity="0.56" />
  </g>

  <rect width="1600" height="1000" filter="url(#spd-grain)" />
</svg>

<!-- Faint gear rings, ticking round (snappy motion; Reduce motion stills them). -->
<svg class="gears" viewBox="0 0 300 220" aria-hidden="true">
  <g class="gear">
    <circle cx="120" cy="100" r="78" stroke-width="11" stroke-dasharray="7.5 8.2" />
    <circle cx="120" cy="100" r="64" stroke-width="1.4" />
    <circle cx="120" cy="100" r="24" stroke-width="1.4" />
    <path d="M120 36v128M56 100h128M75 55l90 90M165 55l-90 90" stroke-width="0.9" />
  </g>
  <g class="gear reverse">
    <circle cx="236" cy="160" r="44" stroke-width="9" stroke-dasharray="6 6.6" />
    <circle cx="236" cy="160" r="35" stroke-width="1.2" />
    <circle cx="236" cy="160" r="10" stroke-width="1.2" />
  </g>
  <g class="gear">
    <circle cx="232" cy="46" r="26" stroke-width="7" stroke-dasharray="4.4 4.6" />
    <circle cx="232" cy="46" r="19" stroke-width="1" />
  </g>
</svg>

<!-- The stamp: a boxed sigil and a monochrome colophon line, slightly askew. -->
<svg class="stamp" viewBox="0 0 260 44" aria-hidden="true" data-ornament>
  <g fill="none" stroke="#c3164f" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="5" width="34" height="34" />
    <rect x="6.5" y="8.5" width="27" height="27" stroke-width="0.7" />
    <path d="M12 30l8-16 8 16M14.5 25h11M20 14V9.5M15 11.5h10" />
  </g>
  <text x="46" y="20">RIKA! · HAND INKED</text>
  <text x="46" y="35" class="small">NO. 01 · SPEED EDITION</text>
</svg>

<style>
  .paper {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }
  .gears {
    position: absolute;
    top: 3vh;
    right: 2vw;
    width: min(300px, 26vw);
    height: auto;
    overflow: visible;
    fill: none;
    stroke: #a6b8b2;
    opacity: 0.7;
  }
  .gear {
    transform-box: fill-box;
    transform-origin: center;
    animation: speed-tick calc(var(--motion-ms) * 300) steps(60) infinite;
  }
  .gear.reverse {
    animation-direction: reverse;
    animation-duration: calc(var(--motion-ms) * 180);
  }
  .stamp {
    position: absolute;
    left: 26px;
    bottom: 14px;
    width: 220px;
    height: auto;
    transform: rotate(-4deg);
    opacity: 0.86;
  }
  .stamp text {
    fill: #c3164f;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.18em;
  }
  .stamp text.small {
    font-size: 8.5px;
    font-weight: 600;
    letter-spacing: 0.26em;
  }
  @keyframes speed-tick {
    to {
      transform: rotate(1turn);
    }
  }
</style>
