<script lang="ts">
  /** Liquid Glass's view loader: a lit glass ring turning. */
</script>

<span class="ring" aria-hidden="true"></span>

<style>
  .ring {
    display: block;
    width: 44px;
    height: 44px;
    margin: 6px;
    border-radius: 999px;
    background: conic-gradient(from 20deg, transparent, rgba(255, 255, 255, 0.95));
    -webkit-mask: radial-gradient(circle, transparent 17px, #000 18px);
    mask: radial-gradient(circle, transparent 17px, #000 18px);
    box-shadow: 0 0 18px rgba(255, 255, 255, 0.25);
    animation: lg-turn calc(var(--motion-ms) * 4) linear infinite;
  }
  @keyframes lg-turn {
    to { transform: rotate(360deg); }
  }
</style>
