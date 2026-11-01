<script lang="ts">
  /** Scythe's view loader: a crescent sweep, square-capped. */
</script>

<svg class="sweep" width="44" height="44" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9" /></svg>

<style>
  .sweep {
    margin: 6px;
    animation: scythe-sweep calc(var(--motion-ms) * 7) cubic-bezier(0.7, 0, 0.3, 1) infinite;
  }
  path {
    fill: none;
    stroke: var(--br-lineHot);
    stroke-width: 2;
    stroke-linecap: square;
  }
  @keyframes scythe-sweep {
    to { transform: rotate(360deg); }
  }
</style>
