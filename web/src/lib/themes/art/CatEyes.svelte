<script lang="ts">
  /** Default view-loader art (contract.ts `LoaderArt`): a cat face whose glowing eyes blink and glance. */
  let { size = 84 }: { size?: number } = $props();
</script>

<svg class="face" width={size} height={Math.round((size * 57) / 84)} viewBox="0 0 104 70" aria-hidden="true">
  <path class="head" d="M52 67C30 67 14 57 14 41c0-8 2-14 5-19L17 5l17 10c11-4 25-4 36 0L87 5l-2 17c3 5 5 11 5 19 0 16-16 26-38 26Z" />
  <g class="eye" style:transform-origin="36px 42px">
    <path d="M24 42Q36 31 48 42Q36 53 24 42Z" />
    <ellipse class="pupil" cx="36" cy="42" rx="1.8" ry="6" />
  </g>
  <g class="eye" style:transform-origin="68px 42px">
    <path d="M56 42Q68 31 80 42Q68 53 56 42Z" />
    <ellipse class="pupil" cx="68" cy="42" rx="1.8" ry="6" />
  </g>
</svg>

<style>
  .face {
    display: block;
    overflow: visible;
  }
  .head {
    fill: color-mix(in srgb, var(--solid-raised, var(--br-raised)) 70%, transparent);
    stroke: var(--hairline, var(--border));
    stroke-width: 1.2;
    stroke-linejoin: round;
  }
  .eye {
    animation: kitty-blink 3.6s ease-in-out infinite;
  }
  .eye path {
    fill: var(--c1);
    filter: drop-shadow(0 0 5px color-mix(in srgb, var(--c1) 70%, transparent));
  }
  .pupil {
    fill: var(--solid-void, var(--br-void));
    animation: kitty-glance 3.6s ease-in-out infinite;
  }
  @keyframes kitty-blink {
    0%, 44%, 52%, 100% { transform: scaleY(1); }
    48% { transform: scaleY(0.08); }
  }
  @keyframes kitty-glance {
    0%, 20%, 90%, 100% { transform: translateX(0); }
    28%, 40% { transform: translateX(-4px); }
    60%, 76% { transform: translateX(4px); }
  }
</style>
