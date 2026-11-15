<script lang="ts">
  import { onMount } from "svelte";
  import { scene } from "./random";

  /**
   * Kitty's ground: a night concourse seen through aqua glass. Bars of cold light, the
   * window's uprights, a fine screen-door grid and scraps of text drifting behind it all:
   * faces, timetables, fragments. Rolled anew on every load (random.ts), so no two
   * sessions look the same. The widget titles' leading mark is one of the rolls too.
   */
  const view = scene(crypto.getRandomValues(new Uint32Array(1))[0]);
  onMount(() => {
    // The mark before every widget title (aero.css): this session's roll.
    document.documentElement.style.setProperty("--aero-glyph", JSON.stringify(view.glyph));
    return () => document.documentElement.style.removeProperty("--aero-glyph");
  });
</script>

<div class="aero">
  {#each view.panes as left, index (index)}<i class="pane" style:left={`${left}%`}></i>{/each}
  {#each view.streaks as streak, index (index)}
    <i class="streak" data-tint={streak.tint} style:top={`${streak.top}%`} style:left={`${streak.left}%`} style:width={`${streak.width}%`}
      style:height={`${streak.height}px`} style:opacity={streak.alpha}></i>
  {/each}
  {#each view.scraps as scrap, index (index)}
    <span class="scrap" class:turned={scrap.turned} style:top={`${scrap.top}%`} style:left={`${scrap.left}%`} style:font-size={`${scrap.size}px`}
      style:opacity={scrap.alpha}>{scrap.text}</span>
  {/each}
  <pre class="board">{view.board.join("\n")}</pre>
  <p class="ticker"><span>{view.ticker}   ✦   {view.ticker}</span></p>
  <i class="grid"></i>
</div>

<style>
  .aero {
    position: absolute;
    inset: 0;
    overflow: hidden;
    /* Deep water: darkest at the edges, a cold lift in the middle where the light would be. */
    background:
      radial-gradient(90vmax 60vmax at 62% 38%, rgba(36, 118, 150, 0.34), transparent 70%),
      radial-gradient(70vmax 50vmax at 12% 88%, rgba(24, 96, 110, 0.3), transparent 72%),
      linear-gradient(180deg, #03101a, #041722 55%, #020b12);
    color: #bff3ff;
  }
  .pane {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 1px;
    background: linear-gradient(180deg, transparent, rgba(150, 232, 255, 0.16) 20%, rgba(150, 232, 255, 0.05) 80%, transparent);
  }
  .streak {
    position: absolute;
    border-radius: 999px;
    background: linear-gradient(90deg, transparent, #7fe6ff 30%, #d8fbff 50%, #7fe6ff 70%, transparent);
    box-shadow: 0 0 24px 6px rgba(111, 227, 255, 0.5);
    animation: aero-drift 46s ease-in-out infinite alternate;
  }
  .streak[data-tint="white"] {
    background: linear-gradient(90deg, transparent, #eafcff 50%, transparent);
    box-shadow: 0 0 26px 8px rgba(220, 250, 255, 0.45);
    animation-duration: 64s;
  }
  .streak[data-tint="amber"] {
    background: linear-gradient(90deg, transparent, #ffd76a 40%, #fff1c2 50%, #ffd76a 60%, transparent);
    box-shadow: 0 0 22px 6px rgba(255, 215, 106, 0.45);
    animation-duration: 38s;
    animation-direction: alternate-reverse;
  }
  .scrap,
  .board,
  .ticker {
    position: absolute;
    margin: 0;
    font-family: var(--font-code);
    line-height: 1.25;
    white-space: pre;
    letter-spacing: 0.04em;
  }
  .scrap.turned {
    transform: rotate(90deg);
    transform-origin: left top;
  }
  .board {
    right: 3%;
    bottom: 9%;
    font-size: 12px;
    opacity: 0.16;
  }
  .ticker {
    left: 0;
    right: 0;
    bottom: 10px;
    overflow: hidden;
    font-size: 12px;
    opacity: 0.2;
  }
  .ticker span {
    display: inline-block;
    padding-left: 100%;
    animation: aero-ticker 120s linear infinite;
  }
  /* A screen-door grid over everything: the image is seen through a fine mesh. */
  .grid {
    position: absolute;
    inset: 0;
    background:
      repeating-linear-gradient(0deg, rgba(0, 0, 0, 0.22) 0 1px, transparent 1px 3px),
      repeating-linear-gradient(90deg, rgba(0, 0, 0, 0.12) 0 1px, transparent 1px 3px);
  }
  :global(:root[data-theme-motion="none"]) :is(.streak, .ticker span) {
    animation: none;
  }
  :global(:root[data-theme-motion="none"]) .ticker span {
    padding-left: 12px;
  }
  /* The scraps are decoration: Appearance → Theme motif turns them off. */
  :global(:root[data-motif="off"]) :is(.scrap, .board, .ticker) {
    display: none;
  }
  @keyframes aero-drift {
    from { transform: translateX(-4vw); }
    to { transform: translateX(6vw); }
  }
  @keyframes aero-ticker {
    to { transform: translateX(-100%); }
  }
</style>
