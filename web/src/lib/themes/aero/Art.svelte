<script lang="ts">
  import { onMount } from "svelte";
  import { roll, stamp, word } from "./random";
  import platform from "./scenes/platform.jpg";
  import corridor from "./scenes/corridor.jpg";
  import beams from "./scenes/beams.jpg";

  /**
   * Kitty's ground: one of three film stills (a train leaving a platform, a green corridor,
   * a car park against a blue dusk), cross-processed and grainy, framed differently on every
   * load (random.ts). Over it, set like a record sleeve: the page's name down one edge in
   * signal orange or tube yellow, and the camera's date stamp in the corner. The stills are
   * drawn, not photographed (scripts/kitty-theme-scenes.py).
   */
  const STILLS = { platform, corridor, beams };
  const view = roll(crypto.getRandomValues(new Uint32Array(1))[0]);
  let page = $state(word(location.hash));
  onMount(() => {
    // The panes take the still's cast (aero.css).
    document.documentElement.dataset.aeroStill = view.still;
    const follow = () => (page = word(location.hash));
    window.addEventListener("hashchange", follow);
    return () => {
      delete document.documentElement.dataset.aeroStill;
      window.removeEventListener("hashchange", follow);
    };
  });
</script>

<div class="aero" style:--aero-ink={view.ink}>
  <img class="still" class:flipped={view.flipped} src={STILLS[view.still]} alt="" style:object-position={`${view.x}% ${view.y}%`} />
  <i class="shade"></i>
  <p class="word" data-edge={view.edge}><i></i><i></i>{page}</p>
  <p class="stamp">{stamp(new Date())}</p>
</div>

<style>
  .aero {
    position: absolute;
    inset: 0;
    overflow: hidden;
    background: #050a0b;
  }
  .still {
    position: absolute;
    inset: -3%;
    width: 106%;
    height: 106%;
    object-fit: cover;
    /* A slow drift, as if the frame were still settling. */
    animation: aero-settle 60s ease-in-out infinite alternate;
  }
  .still.flipped {
    scale: -1 1;
  }
  /* The page is read in the dark: the still stays a still, not a lamp. */
  .shade {
    position: absolute;
    inset: 0;
    background: linear-gradient(180deg, rgb(0 0 0 / 0.58), rgb(0 0 0 / 0.46) 30%, rgb(0 0 0 / 0.64));
  }
  /* The page's name runs down the gutter beside the page, like the spine of a sleeve. */
  .word {
    position: absolute;
    top: 78px;
    right: 3px;
    margin: 0;
    color: var(--aero-ink);
    font: 700 43px / 0.9 var(--font-ui);
    letter-spacing: -0.04em;
    white-space: nowrap;
    writing-mode: vertical-rl;
  }
  .word[data-edge="left"] {
    right: auto;
    left: 3px;
    rotate: 180deg;
    top: auto;
    bottom: 60px;
  }
  /* The sleeve's two squares, before the word. */
  .word i {
    display: inline-block;
    width: 0.56em;
    height: 0.56em;
    margin-bottom: 0.16em;
    background: currentColor;
  }
  .word i + i {
    margin-bottom: 0.4em;
  }
  /* The stamp keeps the foot corner Rika's field is not in: Rika stands on the left of home
     unless moved to the right, where its side panel on other pages stands too. */
  .stamp {
    position: absolute;
    right: 26px;
    bottom: 20px;
    margin: 0;
    color: #ff7a2f;
    font: 700 17px/1 var(--font-ui);
    letter-spacing: 0.14em;
    opacity: 0.9;
  }
  :global(:root:has(.home[data-rika="right"]:not(.solo), .rika-dock)) .stamp {
    right: auto;
    left: 26px;
  }
  @keyframes aero-settle {
    from { translate: -1% -0.6%; }
    to { translate: 1% 0.6%; }
  }
  :global(:root[data-theme-motion="none"]) .still {
    animation: none;
  }
  :global(:root[data-motif="off"]) :is(.word, .stamp) {
    display: none;
  }
</style>
