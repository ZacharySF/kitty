<script lang="ts">
  /** red's view loader: a blood-red brush stroke paints across, drips, and is painted again. */
</script>

<svg class="stroke" width="96" height="44" viewBox="0 0 96 44" aria-hidden="true">
  <path class="swash" d="M6 16 C30 11 62 10 90 13" />
  <path class="run" d="M34 14h3.2l-.6 16a2.4 2.4 0 1 1-2 0Z" />
  <path class="run late" d="M66 13h2.4l-.5 9a1.8 1.8 0 1 1-1.4 0Z" />
</svg>

<style>
  .stroke {
    margin: 4px;
    overflow: visible;
  }
  .swash {
    fill: none;
    stroke: #d90d1a;
    stroke-width: 7;
    stroke-linecap: round;
    stroke-dasharray: 90;
    animation: red-paint calc(var(--motion-ms) * 12) cubic-bezier(0.7, 0, 0.3, 1) infinite;
  }
  .run {
    fill: #d90d1a;
    transform-box: fill-box;
    transform-origin: 50% 0;
    animation: red-drip calc(var(--motion-ms) * 12) ease-in infinite;
  }
  .run.late {
    animation-delay: calc(var(--motion-ms) * 2);
  }
  @keyframes red-paint {
    0% { stroke-dashoffset: 90; }
    45%, 80% { stroke-dashoffset: 0; }
    100% { stroke-dashoffset: -90; }
  }
  @keyframes red-drip {
    0%, 35% { transform: scaleY(0); }
    75% { transform: scaleY(1); }
    100% { transform: scaleY(1); opacity: 0; }
  }
</style>
