<script lang="ts">
  /** Liquid Glass's empty state: a frosted orb holding a quiet check. */
</script>

<span class="orb">
  <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 12.5 4 4 8-9" /></svg>
</span>

<style>
  .orb {
    display: grid;
    place-items: center;
    width: 76px;
    height: 76px;
    border: 1px solid rgba(255, 255, 255, 0.3);
    border-radius: 999px;
    background: radial-gradient(circle at 32% 28%, rgba(255, 255, 255, 0.35), rgba(255, 255, 255, 0.05) 60%);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.6), 0 14px 30px -10px rgba(0, 0, 0, 0.7);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
  }
  path {
    fill: none;
    stroke: var(--ink);
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
</style>
