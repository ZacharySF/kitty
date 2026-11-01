<script lang="ts">
  import { PETAL } from "./icons";

  /**
   * Q16 option B, chosen in Settings → Appearance: sparse sakura petals falling
   * behind the glass, over the nebula. Pure CSS transforms; under reduced motion
   * (the setting or the system) animation stops and petals rest where they are.
   */
  const COUNT = 16;
  const colors = ["var(--c1)", "var(--c2)", "var(--c1)", "var(--c3)"];
  /** Deterministic scatter, so the sky looks the same on every load. */
  let seed = 7;
  const random = () => {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 4294967296;
  };
  const petals = Array.from({ length: COUNT }, (_, index) => {
    const fall = 16 + random() * 12;
    return {
      x: (index / COUNT) * 100 + random() * (100 / COUNT),
      size: 10 + random() * 8,
      fall,
      delay: -random() * fall,
      sway: 8 + random() * 18,
      swayDuration: 2.6 + random() * 2.4,
      spin: 4 + random() * 6,
      rest: 6 + random() * 82,
      color: colors[index % colors.length],
      opacity: 0.35 + random() * 0.35,
    };
  });
</script>

<div class="petal-drift" aria-hidden="true">
  {#each petals as petal, index (index)}
    <span class="lane" style:left={`${petal.x}%`} style:--rest={`${petal.rest}vh`} style:animation-duration={`${petal.fall}s`} style:animation-delay={`${petal.delay}s`}>
      <span class="sway" style:--sway={`${petal.sway}px`} style:animation-duration={`${petal.swayDuration}s`} style:animation-delay={`${petal.delay}s`}>
        <svg
          width={petal.size}
          height={petal.size}
          viewBox="-6 -11 12 12"
          style:opacity={petal.opacity}
          style:animation-duration={`${petal.spin}s`}
          style:animation-delay={`${petal.delay}s`}
        ><path d={PETAL} fill={petal.color} /></svg>
      </span>
    </span>
  {/each}
</div>

<style>
  .petal-drift {
    position: fixed;
    inset: 0;
    z-index: -1;
    overflow: hidden;
    pointer-events: none;
  }
  .lane {
    position: absolute;
    top: 0;
    width: 0;
    transform: translateY(var(--rest));
    animation: petal-fall linear infinite;
    will-change: transform;
  }
  .sway {
    position: absolute;
    top: -18px;
    left: 0;
    display: block;
    animation: petal-sway ease-in-out infinite alternate;
  }
  svg {
    display: block;
    animation: petal-tumble linear infinite;
  }
  @keyframes petal-fall {
    from {
      transform: translateY(0);
    }
    to {
      transform: translateY(calc(100vh + 36px));
    }
  }
  @keyframes petal-sway {
    from {
      transform: translateX(calc(var(--sway) * -1));
    }
    to {
      transform: translateX(var(--sway));
    }
  }
  @keyframes petal-tumble {
    0% {
      transform: rotate(0deg) scaleX(1);
    }
    50% {
      transform: rotate(180deg) scaleX(0.45);
    }
    100% {
      transform: rotate(360deg) scaleX(1);
    }
  }
</style>
