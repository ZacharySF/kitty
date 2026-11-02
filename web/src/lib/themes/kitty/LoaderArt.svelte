<script lang="ts">
  import { PETAL } from "../../icons";
  import CatEyes from "../art/CatEyes.svelte";

  /** Kitty's view loader: the blinking cat inside a petal spinner, five petals lighting in turn. */
  const ANGLES = [0, 72, 144, 216, 288];
</script>

<span class="petal-loader">
  <svg class="spinner" viewBox="-60 -60 120 120" aria-hidden="true">
    {#each ANGLES as angle, index (angle)}
      <g transform={`rotate(${angle}) translate(0 -46)`}>
        <path class="petal" d={PETAL} transform="rotate(180) scale(1.2)" style:animation-delay={`${index * 0.18}s`} />
      </g>
    {/each}
  </svg>
  <span class="cat"><CatEyes size={64} /></span>
</span>

<style>
  .petal-loader {
    position: relative;
    display: grid;
    place-items: center;
    width: 112px;
    height: 112px;
  }
  .spinner {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    overflow: visible;
    animation: kitty-spin calc(var(--motion-ms) * 25) linear infinite;
  }
  .petal {
    fill: var(--c1);
    opacity: 0.3;
    animation: kitty-petal-pulse 0.9s ease-in-out infinite;
  }
  .cat {
    position: relative;
    margin-top: 6px;
  }
  @keyframes kitty-spin {
    to { transform: rotate(360deg); }
  }
  @keyframes kitty-petal-pulse {
    0%, 100% { opacity: 0.3; }
    40% { opacity: 1; }
  }
</style>
