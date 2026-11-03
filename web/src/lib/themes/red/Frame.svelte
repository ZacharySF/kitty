<script lang="ts">
  /**
   * red's frame: the white paper margin round the black block, as on a printed cover, with
   * printer's crop marks and the magazine furniture set in tracked capitals (the motif's
   * small-caps voice, in the app's own face): an issue line along the foot and a running
   * title up the right edge. Edges only; red.css keeps the page clear of it; it ends above the Rika bar.
   */
  const issue = new Intl.DateTimeFormat("en", { month: "2-digit", year: "2-digit" }).format(new Date());
</script>

<div class="margin top"></div>
<div class="margin bottom">
  <p class="issue" data-ornament>
    <span>Volume one&ensp;·&ensp;Number one</span>
    <span>rika!&ensp;issue {issue}</span>
    <span>Printed in black and red</span>
  </p>
</div>
<div class="margin left"></div>
<div class="margin right"><p class="running" data-ornament>The rika study journal</p></div>
<svg class="crops" aria-hidden="true" data-ornament>
  <path d="M2 14v-8M6 2h8" />
</svg>
<svg class="crops flip" aria-hidden="true" data-ornament>
  <path d="M2 14v-8M6 2h8" />
</svg>

<style>
  .margin {
    position: absolute;
    background: #f3f0e8;
  }
  .top {
    top: 0;
    left: 0;
    right: 0;
    height: var(--red-head);
  }
  .bottom {
    bottom: 0;
    left: 0;
    right: 0;
    height: var(--red-foot);
    display: flex;
    align-items: center;
    padding-inline: calc(var(--red-side) + 10px);
  }
  .left,
  .right {
    top: var(--red-head);
    bottom: var(--red-foot);
    width: var(--red-side);
  }
  .left {
    left: 0;
  }
  .right {
    right: 0;
    display: grid;
    place-items: center;
  }
  p {
    margin: 0;
    color: #0a0a0a;
    font-size: 9px;
    font-weight: 700;
    line-height: 1;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    white-space: nowrap;
  }
  .issue {
    display: flex;
    flex: 1;
    justify-content: space-between;
    max-width: none;
  }
  .running {
    writing-mode: vertical-rl;
    font-size: 7.5px;
    font-weight: 600;
    letter-spacing: 0.3em;
  }
  .crops {
    position: absolute;
    top: 0;
    left: 0;
    width: 16px;
    height: 16px;
    fill: none;
    stroke: #0a0a0a;
    stroke-width: 0.8;
    opacity: 0.6;
  }
  .crops.flip {
    left: auto;
    right: 0;
    transform: scaleX(-1);
  }
</style>
