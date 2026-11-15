<script lang="ts">
  import { sparkPoints, type UsageStep } from "./usage";

  /** A session's new tokens per five minutes: one line in the ink, no axes. */
  let { series, width = 120, height = 28, label = "New tokens over the session" }: { series: UsageStep[]; width?: number; height?: number; label?: string } = $props();
  const points = $derived(sparkPoints(series, width, height));
</script>

{#if points}
  <svg class="spark" viewBox={`0 0 ${width} ${height}`} {width} {height} role="img" aria-label={label}>
    <polyline {points} />
  </svg>
{/if}

<style>
  .spark {
    flex: none;
    display: block;
  }
  polyline {
    fill: none;
    stroke: var(--ink);
    stroke-width: 1.5;
    stroke-linejoin: round;
    stroke-linecap: round;
  }
</style>
