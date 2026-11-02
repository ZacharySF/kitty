<script lang="ts">
  /** A quiet light field under the glass: soft white highlights drifting very slowly, so the
   *  blur has something to bend without adding colour. */
</script>

<div class="lg-aurora"></div>

<style>
  .lg-aurora {
    position: absolute;
    inset: -25vmax;
    background:
      radial-gradient(30vmax 22vmax at 28% 30%, rgba(255, 255, 255, 0.1), transparent 72%),
      radial-gradient(26vmax 20vmax at 72% 64%, rgba(225, 232, 245, 0.07), transparent 72%),
      radial-gradient(22vmax 18vmax at 60% 22%, rgba(255, 255, 255, 0.05), transparent 72%);
    animation: lg-aurora 80s ease-in-out infinite alternate;
    will-change: transform;
  }
  :global(:root[data-window="transparent"]) .lg-aurora {
    opacity: 0.5;
  }
  /* Fluid motion: none at all when the theme's motion is off. */
  :global(:root[data-theme-motion="none"]) .lg-aurora {
    animation: none;
  }
  @keyframes lg-aurora {
    0% { transform: translate3d(-3%, -2%, 0) rotate(0deg) scale(1); }
    50% { transform: translate3d(3%, 2%, 0) rotate(8deg) scale(1.08); }
    100% { transform: translate3d(-2%, 3%, 0) rotate(-6deg) scale(1.02); }
  }
</style>
