<script lang="ts">
  /** c's view loader: a sigil ring that turns slowly while its red hexagram counter-turns. */
</script>

<svg class="ring" width="56" height="56" viewBox="0 0 20 20" fill="none" aria-hidden="true">
  <g class="outer">
    <circle cx="10" cy="10" r="9" stroke="#ece6e2" stroke-opacity="0.7" stroke-width="0.6" />
    <circle cx="10" cy="10" r="7.4" stroke="#ece6e2" stroke-opacity="0.5" stroke-width="1.6" stroke-dasharray="0.5 2.4" />
  </g>
  <path class="star" d="M10 4.6 14.7 12.7H5.3ZM10 15.4 14.7 7.3H5.3Z" stroke="#d6404d" stroke-width="0.7" />
  <path d="M10 8.8 11.2 10 10 11.2 8.8 10Z" fill="#c42f3d" />
</svg>

<style>
  .ring {
    margin: 6px;
  }
  .outer,
  .star {
    transform-origin: 10px 10px;
    animation: c-loader calc(var(--motion-ms) * 22) linear infinite;
  }
  .star {
    animation-direction: reverse;
    animation-duration: calc(var(--motion-ms) * 34);
  }
  @keyframes c-loader {
    to {
      transform: rotate(360deg);
    }
  }
</style>
