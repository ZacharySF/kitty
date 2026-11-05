<script lang="ts">
  /**
   * Rika's mark: a hairline ring around a solid core, both in the theme's accent, so it
   * follows every theme instead of a fixed glossy sphere. While Rika works or listens an
   * arc travels round the ring and the core breathes; reduced motion keeps it still.
   */
  let { size = 18, active = false }: { size?: number; active?: boolean } = $props();
</script>

<span class="rika-mark" class:active style:--size={`${size}px`} aria-hidden="true">
  <span class="ring"></span>
  <span class="core"></span>
</span>

<style>
  .rika-mark {
    position: relative;
    display: inline-grid;
    flex: 0 0 auto;
    place-items: center;
    width: var(--size);
    height: var(--size);
    color: var(--accent);
  }
  .ring,
  .core {
    grid-area: 1 / 1;
    border-radius: 999px;
  }
  .ring {
    width: 100%;
    height: 100%;
    border: max(1px, calc(var(--size) * 0.08)) solid color-mix(in srgb, currentColor 55%, transparent);
  }
  .core {
    width: 42%;
    height: 42%;
    background: currentColor;
    box-shadow: 0 0 0 max(1px, calc(var(--size) * 0.06)) color-mix(in srgb, currentColor 18%, transparent);
  }
  .active .ring {
    border-color: color-mix(in srgb, currentColor 22%, transparent);
    border-top-color: currentColor;
    animation: rika-mark-turn calc(var(--motion-ms, 160ms) * 7) linear infinite;
  }
  .active .core {
    animation: rika-mark-breathe calc(var(--motion-ms, 160ms) * 10) ease-in-out infinite;
  }
  @keyframes rika-mark-turn {
    to {
      transform: rotate(1turn);
    }
  }
  @keyframes rika-mark-breathe {
    0%,
    100% {
      transform: scale(1);
      opacity: 1;
    }
    50% {
      transform: scale(0.72);
      opacity: 0.7;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .active .ring,
    .active .core {
      animation: none;
    }
  }
  :global(:root[data-motion="reduced"]) .active .ring,
  :global(:root[data-motion="reduced"]) .active .core {
    animation: none;
  }
</style>
