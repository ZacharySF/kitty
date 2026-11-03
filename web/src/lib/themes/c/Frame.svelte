<script lang="ts">
  /**
   * c's frame: twisted rope borders in the window's gutters, tied off at each corner with an
   * interlaced knot around a blood-red diamond. The foot rope runs just above the Rika bar.
   * Edges only; the pointer passes through.
   */
  const CORNERS = ["top-left", "top-right", "bottom-left", "bottom-right"];
</script>

<div class="rope top" data-ornament></div>
<div class="rope foot" data-ornament></div>
<div class="rope left" data-ornament></div>
<div class="rope right" data-ornament></div>
{#each CORNERS as corner (corner)}
  <svg class={`knot ${corner}`} viewBox="-10 -10 20 20" aria-hidden="true" data-ornament>
    <circle r="7" fill="#0b0a0c" stroke="#ece6e2" stroke-opacity="0.7" stroke-width="1.1" />
    <path d="M-7 0a7 3.2 0 0 0 14 0a7 3.2 0 0 0-14 0M0-7a3.2 7 0 0 0 0 14a3.2 7 0 0 0 0-14" fill="none" stroke="#ece6e2" stroke-opacity="0.55" stroke-width="1" />
    <path d="M0-2.6L2.6 0L0 2.6L-2.6 0Z" fill="#c42f3d" />
  </svg>
{/each}

<style>
  .rope {
    position: absolute;
  }
  .rope.top,
  .rope.foot {
    left: 20px;
    right: 20px;
    height: 8px;
    background: url("./rope-h.svg") left center / 9.6px 8px repeat-x;
  }
  .rope.top {
    top: 2px;
  }
  .rope.foot {
    bottom: 2px;
  }
  .rope.left,
  .rope.right {
    top: 20px;
    bottom: 20px;
    width: 8px;
    background: url("./rope-v.svg") center top / 8px 9.6px repeat-y;
  }
  .rope.left {
    left: 3px;
  }
  .rope.right {
    right: 3px;
  }
  .knot {
    position: absolute;
    width: 18px;
    height: 18px;
  }
  .top-left { top: -2px; left: -2px; }
  .top-right { top: -2px; right: -2px; }
  .bottom-left { bottom: -2px; left: -2px; }
  .bottom-right { bottom: -2px; right: -2px; }
</style>
