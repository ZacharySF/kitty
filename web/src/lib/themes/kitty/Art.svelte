<script lang="ts">
  import { PETAL, PETAL_ANGLES } from "../../icons";

  /**
   * Kitty's backdrop art: blossom sprigs reaching in from the lower corners, where page
   * margins leave them visible, over the pink, lavender and sky washes of --backdrop. Low
   * enough in opacity to stay inside the contrast budget; the blooms sway slowly (calm
   * motion), and Reduce motion stills them.
   */
  const SPRIGS = [
    { left: true, scale: 2.2, rotate: -28 },
    { left: false, scale: 2.6, rotate: 208 },
  ];
  const BUDS = [[0, 0, 1], [26, 10, 0.72], [12, 30, 0.6], [44, -6, 0.5], [-14, 18, 0.55]] as const;
</script>

{#each SPRIGS as sprig, index (index)}
  <svg class="sprig" class:left={sprig.left} width="260" height="220" viewBox="-60 -60 260 220" aria-hidden="true">
    <g transform={`translate(${sprig.left ? 0 : 140} 100) rotate(${sprig.rotate}) scale(${sprig.scale})`}>
      <path class="branch" d="M-30 -8 C 0 6, 30 18, 52 18 M 18 9 C 20 22, 14 30, 12 36 M 34 15 C 42 6, 44 -2, 44 -6 M -6 4 C -10 10, -14 16, -14 18" />
      {#each BUDS as [bx, by, size], bud (bud)}
        <g class="bloom" style:animation-delay={`${-(index * 5 + bud) * 1.7}s`}>
          <g transform={`translate(${bx} ${by}) scale(${size})`}>
            {#each PETAL_ANGLES as angle (angle)}<path d={PETAL} transform={`rotate(${angle})`} />{/each}
            <circle r="2.3" />
          </g>
        </g>
      {/each}
    </g>
  </svg>
{/each}

<style>
  .sprig {
    position: absolute;
    bottom: -30px;
    right: -40px;
    overflow: visible;
  }
  .sprig.left {
    left: -40px;
    right: auto;
  }
  .branch {
    fill: none;
    stroke: color-mix(in srgb, var(--c2) 30%, transparent);
    stroke-width: 0.8;
    stroke-linecap: round;
  }
  /* Opacity on each whole bloom, so overlapping petals do not stack up brighter. */
  .bloom {
    opacity: 0.26;
    transform-box: fill-box;
    transform-origin: center;
    animation: kitty-sway calc(var(--motion-ms) * 25) ease-in-out infinite alternate;
  }
  .bloom path {
    fill: var(--c1);
  }
  .bloom circle {
    fill: color-mix(in srgb, var(--c6) 60%, var(--c1));
  }
  @keyframes kitty-sway {
    from { transform: rotate(-6deg); }
    to { transform: rotate(8deg); }
  }
</style>
