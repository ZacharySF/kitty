<script lang="ts">
  /** SPEED's view loader: a crimson gear that ticks round in twelve steps inside a sketched ring. */
</script>

<svg class="ticker" width="64" height="64" viewBox="0 0 32 32" aria-hidden="true">
  <circle class="sketch" cx="16" cy="16" r="14.2" />
  <circle class="sketch faint" cx="16.6" cy="15.6" r="13.6" />
  <g class="gear">
    <circle cx="16" cy="16" r="9" stroke-width="3.4" stroke-dasharray="2.36 2.36" />
    <circle cx="16" cy="16" r="6.4" stroke-width="1.6" />
    <circle cx="16" cy="16" r="1.8" stroke-width="1.2" />
  </g>
</svg>

<style>
  .ticker {
    margin: 4px;
    fill: none;
    stroke: #c3164f;
  }
  .sketch {
    stroke: #141716;
    stroke-width: 0.8;
  }
  .sketch.faint {
    stroke: #c3164f;
    opacity: 0.6;
  }
  .gear {
    transform-origin: 16px 16px;
    animation: speed-gear calc(var(--motion-ms) * 25) steps(12) infinite;
  }
  @keyframes speed-gear {
    to {
      transform: rotate(360deg);
    }
  }
</style>
