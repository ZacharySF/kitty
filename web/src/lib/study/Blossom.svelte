<script lang="ts">
  /** A five-petal blossom whose petals open as `value` goes 0 → 1; marks an exam on the plan. */
  let { size = 22, value = 1, tone = "var(--accent)" }: { size?: number; value?: number; tone?: string } = $props();

  const PETAL = "M0 -6 C -15 -14 -17 -34 -6 -42 L 0 -37 L 6 -42 C 17 -34 15 -14 0 -6 Z";
  const fill = (index: number) => Math.max(0, Math.min(1, value * 5 - index));
</script>

<svg class="blossom" width={size} height={size} viewBox="-50 -50 100 100" aria-hidden="true">
  {#each { length: 5 } as _, index (index)}
    <g transform={`rotate(${72 * index})`}>
      <path d={PETAL} fill="none" stroke={tone} stroke-opacity="0.38" stroke-width="2" />
      <path d={PETAL} fill={tone} transform={`scale(${0.35 + fill(index) * 0.65})`} opacity={fill(index) === 0 ? 0 : 0.25 + fill(index) * 0.75} />
    </g>
  {/each}
  <circle r="6" fill="var(--ink)" opacity="0.9" />
</svg>

<style>
  .blossom {
    display: block;
    overflow: visible;
  }
</style>
