<script lang="ts">
  /**
   * red's ground: one hard black block (the paper margin round it is the Frame). A stencil
   * cat portrait, lit hard from the left, sits in the block at very low strength, and the
   * one accent signs the lower left: "rika" in a fast blood-red brush hand whose drips keep
   * running, slowly (Reduce motion stills them). Both sit where pages leave the block bare.
   * Original drawing, nothing traced.
   *
   * The rough brush letters are brush.source.svg rendered to WebP by
   * scripts/render-theme-art.mjs: as a live filter the turbulence re-ran on every frame the
   * drips ran. The drips stay vector and move.
   */
  import brush from "./brush-680.webp";
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
  <image href={brush} x="-10" y="-10" width="170" height="140" />
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
