<script lang="ts">
  /** Default empty-state art (contract.ts `EmptyArt`): a line-art kitty asleep on a cushion. */
</script>

<svg class="sleeping-cat" viewBox="0 0 160 100" aria-hidden="true">
  <ellipse class="cushion" cx="84" cy="87" rx="66" ry="7.5" />
  <path class="line" d="M48 85C40 61 58 41 90 41C118 41 136 58 133 80" />
  <path class="line soft" d="M104 55C117 60 124 71 121 85" />
  <path class="line" d="M133 80C135 91 121 93 105 91C93 90 83 89 74 87" />
  <path class="head" d="M36 85C30 75 31 63 39 57L37 44L50 52C54 51 60 51 64 52L75 45L74 58C80 65 80 77 75 85Z" />
  <path class="ear" d="M40.5 55.5 39.6 49 46.5 53Z" />
  <path class="ear" d="M71.5 54.5 71.9 49 65.6 53Z" />
  <path class="fine" d="M44 68.5q4 3.2 8 0M59.5 68.5q4 3.2 8 0" />
  <path class="nose" d="M54.4 73.6h3.4l-1.7 1.9Z" />
  <path class="fine faint" d="M41 75.5 30 74.5M41 78.5 31 81M71 75.5l11-1M71 78.5l10 2.5" />
  <path class="fine" d="M46 85q4.5-4.2 9 0M58 85q4.5-4.2 9 0" />
  <g class="zs">
    <text x="84" y="36">z</text>
    <text x="93" y="26" class="z2">z</text>
    <text x="104" y="15" class="z3">z</text>
  </g>
</svg>

<style>
  .sleeping-cat {
    width: 150px;
    height: auto;
    overflow: visible;
  }
  .cushion {
    fill: color-mix(in srgb, var(--violet) 10%, transparent);
    stroke: var(--hairline, var(--border));
  }
  .line,
  .head,
  .fine {
    fill: none;
    stroke: color-mix(in srgb, var(--ink) 78%, transparent);
    stroke-width: 1.7;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .head {
    fill: var(--solid-void, var(--br-void));
  }
  .soft {
    stroke: color-mix(in srgb, var(--ink) 38%, transparent);
    stroke-width: 1.3;
  }
  .fine {
    stroke-width: 1.3;
  }
  .faint {
    stroke: color-mix(in srgb, var(--ink) 34%, transparent);
    stroke-width: 1;
  }
  .ear {
    fill: color-mix(in srgb, var(--c1) 55%, transparent);
  }
  .nose {
    fill: var(--c1);
  }
  .zs text {
    fill: var(--violet);
    font: 600 11px var(--font-mono);
    animation: kitty-z 3.6s ease-in-out infinite;
  }
  .zs .z2 {
    font-size: 9px;
    animation-delay: 0.5s;
  }
  .zs .z3 {
    font-size: 7.5px;
    animation-delay: 1s;
  }
  @keyframes kitty-z {
    0%,
    100% { opacity: 0.25; transform: translateY(2px); }
    50% { opacity: 0.9; transform: translateY(-2px); }
  }
</style>
