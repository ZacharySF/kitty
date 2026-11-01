<script lang="ts">
  /**
   * Loading placeholder. `eyes`: a cat face whose glowing eyes blink and glance, with a
   * visible caption, for whole views. `rows`: shimmering skeleton rows where a list will
   * appear; the label is announced but not shown. Motion stops under reduced motion.
   */
  let { kind = "rows", label, rows = 3 }: { kind?: "eyes" | "rows"; label: string; rows?: number } = $props();
  const widths = [0.82, 0.64, 0.74, 0.58, 0.7];
</script>

{#if kind === "eyes"}
  <div class="kitty-loader eyes" data-kind="eyes" role="status">
    <svg class="face" width="84" height="57" viewBox="0 0 104 70" aria-hidden="true">
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
    <p>{label}</p>
  </div>
{:else}
  <div class="kitty-loader rows" data-kind="rows" role="status" aria-label={label}>
    {#each Array.from({ length: rows }, (_, index) => widths[index % widths.length]) as width, index (index)}
      <div class="skeleton-row" aria-hidden="true">
        <span class="dot"></span>
        <span class="lines"><span style:width={`${width * 100}%`}></span><span style:width={`${width * 60}%`}></span></span>
      </div>
    {/each}
  </div>
{/if}

<style>
  .kitty-loader.eyes {
    display: grid;
    justify-items: center;
    gap: 8px;
    padding: 24px;
    color: var(--muted);
    text-align: center;
  }
  .eyes p {
    margin: 0;
    font-size: 12.5px;
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
  .kitty-loader.rows {
    display: grid;
    gap: 14px;
    width: 100%;
    max-width: 640px;
    padding: 16px 4px;
  }
  .skeleton-row {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .dot,
  .lines span {
    background: linear-gradient(
      90deg,
      var(--surface-raised) 0 30%,
      color-mix(in srgb, var(--violet) 22%, var(--surface-raised)) 50%,
      var(--surface-raised) 70% 100%
    );
    background-size: 300% 100%;
    animation: kitty-shimmer 1.6s linear infinite;
  }
  .dot {
    flex: none;
    width: 28px;
    height: 28px;
    border-radius: 999px;
  }
  .lines {
    display: grid;
    flex: 1;
    gap: 7px;
  }
  .lines span {
    height: 9px;
    border-radius: 5px;
  }
  .lines span + span {
    height: 7px;
    opacity: 0.6;
  }
  @keyframes kitty-shimmer {
    from { background-position: 100% 0; }
    to { background-position: 0 0; }
  }
  @media (prefers-reduced-motion: reduce) {
    .eye,
    .pupil,
    .dot,
    .lines span {
      animation: none;
    }
  }
</style>
