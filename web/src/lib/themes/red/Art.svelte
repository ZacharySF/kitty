<script lang="ts">
  /**
   * red's ground: one hard black block (the paper margin round it is the Frame). A stencil
   * cat portrait, lit hard from the left, sits in the block at very low strength, and the
   * one accent signs the lower left: "rika" in a fast blood-red brush hand whose drips keep
   * running, slowly (Reduce motion stills them). Both sit where pages leave the block bare.
   * Original drawing, nothing traced.
   */
  const uid = $props.id();
  const STROKES = [
    // r: stem and shoulder
    "M15 30 C14 40 12 48 10 57",
    "M13 41 C17 34 23 30 31 31",
    // i
    "M43 33 C42 41 40 49 38 57",
    // k: stem, arm, leg
    "M60 8 C58 24 55 40 51 57",
    "M54 42 C61 36 68 31 76 28",
    "M57 40 C63 46 68 51 75 57",
    // a: bowl and stem
    "M104 36 C96 31 86 34 84 45 C83 54 92 58 99 51 C102 47 104 41 104 36",
    "M104 34 C103 42 103 50 107 57",
  ];
  // The long signature stroke flicked out over the k and a.
  const CROSS = "M64 24 C82 21 110 16 140 12";
  const DRIPS = [
    [10, 57, 17, 4.4],
    [38, 57, 9, 3.4],
    [51, 57, 26, 4.6],
    [75, 57, 12, 3.8],
    [107, 57, 16, 3.6],
    [139, 13, 18, 3],
  ] as const;
</script>

<svg class="stencil" viewBox="0 0 220 240" aria-hidden="true" data-ornament>
  <path fill="#f4f1ea" d="M44 104 L30 26 L86 66 Q110 58 134 66 L190 26 L176 104 Q188 166 110 186 Q32 166 44 104 Z" />
  <g fill="#0a0a0a">
    <path d="M42 46 L48 86 L72 68 Z" />
    <path d="M178 46 L172 86 L148 68 Z" />
    <path d="M128 70 Q150 82 158 110 Q164 140 148 162 Q132 178 112 182 Q128 160 130 136 Q132 112 120 98 Q136 96 128 70 Z" />
    <path d="M62 112 Q78 98 96 110 Q80 122 62 112 Z" />
    <path d="M124 110 Q142 98 158 112 Q142 122 124 110 Z" />
    <path d="M102 132 L118 132 L110 142 Z" />
    <path d="M110 142 Q104 154 94 152 L94 149 Q103 150 108 141 Z" />
    <path d="M110 142 Q116 154 126 152 L126 149 Q117 150 112 141 Z" />
    <path d="M36 128 L84 136 L84 138.4 L36 131 Z" />
    <path d="M40 146 L84 142 L84 144.4 L41 149 Z" />
  </g>
  <g fill="#f4f1ea">
    <rect x="78" y="106" width="3" height="10" />
    <rect x="140" y="105" width="3" height="10" />
    <path d="M136 136 L184 128 L184 131 L136 138.4 Z" />
    <path d="M136 142 L180 146 L179 149 L136 144.4 Z" />
  </g>
  <g fill="none" stroke="#f4f1ea" stroke-width="3">
    <ellipse cx="62" cy="186" rx="7" ry="4" transform="rotate(28 62 186)" />
    <ellipse cx="76" cy="194" rx="7" ry="4" transform="rotate(18 76 194)" />
    <ellipse cx="92" cy="199" rx="7" ry="4" transform="rotate(8 92 199)" />
    <ellipse cx="128" cy="199" rx="7" ry="4" transform="rotate(-8 128 199)" />
    <ellipse cx="144" cy="194" rx="7" ry="4" transform="rotate(-18 144 194)" />
    <ellipse cx="158" cy="186" rx="7" ry="4" transform="rotate(-28 158 186)" />
  </g>
  <path fill="#f4f1ea" d="M110 196 Q124 198 124 214 L126 220 L94 220 L96 214 Q96 198 110 196 Z" />
  <rect x="94" y="221" width="32" height="3" fill="#f4f1ea" />
  <circle cx="110" cy="230" r="4" fill="#f4f1ea" />
</svg>

<svg class="brush" viewBox="0 0 150 120" aria-hidden="true" data-ornament>
  <defs>
    <filter id="{uid}-rough" x="-10%" y="-10%" width="120%" height="120%">
      <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="4" result="noise" />
      <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.6" />
    </filter>
  </defs>
  <g filter="url(#{uid}-rough)" fill="none" stroke="#d90d1a" stroke-linecap="round" stroke-linejoin="round">
    <g stroke-width="2.6" opacity="0.5" transform="translate(3 -2)">
      {#each STROKES as d (d)}<path {d} />{/each}
    </g>
    <g stroke-width="8.6">
      {#each STROKES as d (d)}<path {d} />{/each}
    </g>
    <path d={CROSS} stroke-width="6" />
    <path d="M70 24 C90 21 114 17 136 14" stroke-width="1.4" opacity="0.6" transform="translate(0 3)" />
    <circle cx="46" cy="22" r="4.8" fill="#d90d1a" stroke="none" />
  </g>
  <g fill="#d90d1a">
    {#each DRIPS as [x, y, length, width], index (index)}
      <path
        class="drip"
        style:animation-delay={`${-index * 2.3}s`}
        d={`M${x - width / 2} ${y - 2} L${x - width / 2.6} ${y + length} A${width * 0.75} ${width * 0.75} 0 1 0 ${x + width / 2.6} ${y + length} L${x + width / 2} ${y - 2} Z`}
      />
    {/each}
  </g>
</svg>

<style>
  .stencil {
    position: absolute;
    right: 3vw;
    bottom: 4vh;
    width: min(34vw, 460px);
    height: auto;
    opacity: 0.07;
  }
  .brush {
    position: absolute;
    left: 2.4vw;
    bottom: 6vh;
    width: min(20vw, 250px);
    height: auto;
    overflow: visible;
    transform: rotate(-5deg);
  }
  .drip {
    transform-box: fill-box;
    transform-origin: 50% 0;
    animation: red-run calc(var(--motion-ms) * 80) ease-in-out infinite alternate;
  }
  @keyframes red-run {
    from {
      transform: scaleY(0.82);
    }
    to {
      transform: scaleY(1.12);
    }
  }
</style>
