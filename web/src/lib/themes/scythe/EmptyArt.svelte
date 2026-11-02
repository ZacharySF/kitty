<script lang="ts">
  /** Scythe's empty state: the moon rising over a quiet horizon. */
</script>

<svg class="moonrise" width="140" height="76" viewBox="0 0 96 52" aria-hidden="true">
  <path class="horizon" d="M8 44h80" />
  <g transform="translate(36 6) scale(1.3)"><path class="moon" d="M20 14a8.5 8.5 0 0 1-10-10 8.5 8.5 0 1 0 10 10Z" /></g>
  {#each [16, 28, 70, 80] as x (x)}<path class="blade" d={`M${x} 44l2-7 2 7`} />{/each}
</svg>

<style>
  .horizon,
  .blade {
    fill: none;
    stroke: var(--br-line);
    stroke-width: 1;
  }
  .moon {
    fill: var(--br-lineHot);
  }
</style>
