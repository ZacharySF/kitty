<script lang="ts">
  /**
   * SPEED's ground: off-white rag paper that took the overspray. Pale grey-teal washes bleed
   * at their edges, a white gothic cathedral is drawn over one of them, black ink pools and
   * drips down the lower left (where the pages leave paper showing), teal-green blooms and
   * splatter scatter across, and a crimson wheel is sketched three times over with speed
   * lines. Faint gear rings turn at the top right; a crimson stamp signs the bottom corner.
   * The washes stay pale enough for dim text read over them; the black ink sits where no
   * text does. Original drawing, nothing traced.
   *
   * The painting is paper.source.svg (turbulence washes, bled ink, rough pen lines) rendered
   * to WebP by scripts/render-theme-art.mjs: its filters cost seconds per repaint in WebKit,
   * so none run here. Only the gears move, each its own element turning by transform.
   */
  import paper1600 from "./paper-1600.webp";
  import paper3200 from "./paper-3200.webp";

  /* Each gear in the 300×220 cluster: centre, outer radius (ring plus half its stroke). */
  const GEARS = [
    { cx: 120, cy: 100, r: 84, reverse: false },
    { cx: 236, cy: 160, r: 49, reverse: true },
    { cx: 232, cy: 46, r: 30, reverse: false },
  ];
  const place = ({ cx, cy, r }: (typeof GEARS)[number]) =>
    `left: ${((cx - r) / 3).toFixed(3)}%; top: ${((cy - r) / 2.2).toFixed(3)}%; width: ${((2 * r) / 3).toFixed(3)}%; height: ${((2 * r) / 2.2).toFixed(3)}%`;
</script>

<img class="paper" src={paper1600} srcset={`${paper1600} 1600w, ${paper3200} 3200w`} sizes="max(100vw, 160vh)" alt="" decoding="async" draggable="false" />
<div class="fibres"></div>

<!-- Faint gear rings, turning slowly (snappy motion; Reduce motion stills them). -->
<div class="gears">
  <svg class="gear" style={place(GEARS[0])} viewBox="-84 -84 168 168" aria-hidden="true">
    <circle r="78" stroke-width="11" stroke-dasharray="7.5 8.2" />
    <circle r="64" stroke-width="1.4" />
    <circle r="24" stroke-width="1.4" />
    <path d="M0 -64v128M-64 0h128M-45 -45l90 90M45 -45l-90 90" stroke-width="0.9" />
  </svg>
  <svg class="gear reverse" style={place(GEARS[1])} viewBox="-49 -49 98 98" aria-hidden="true">
    <circle r="44" stroke-width="9" stroke-dasharray="6 6.6" />
    <circle r="35" stroke-width="1.2" />
    <circle r="10" stroke-width="1.2" />
  </svg>
  <svg class="gear" style={place(GEARS[2])} viewBox="-30 -30 60 60" aria-hidden="true">
    <circle r="26" stroke-width="7" stroke-dasharray="4.4 4.6" />
    <circle r="19" stroke-width="1" />
  </svg>
</div>

<!-- The stamp: a boxed sigil and a monochrome colophon line, slightly askew. -->
<svg class="stamp" viewBox="0 0 260 44" aria-hidden="true" data-ornament>
  <g fill="none" stroke="#c3164f" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="5" width="34" height="34" />
    <rect x="6.5" y="8.5" width="27" height="27" stroke-width="0.7" />
    <path d="M12 30l8-16 8 16M14.5 25h11M20 14V9.5M15 11.5h10" />
  </g>
  <text x="46" y="20">RIKA! · HAND INKED</text>
  <text x="46" y="35" class="small">NO. 01 · SPEED EDITION</text>
</svg>

<style>
  /* object-fit: cover is the source's xMidYMid slice. */
  .paper {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    user-select: none;
  }
  /* Paper fibres over the washes: one static noise tile. */
  .fibres {
    position: absolute;
    inset: 0;
    background: url("./fibres-200.webp");
  }
  .gears {
    position: absolute;
    top: 3vh;
    right: 2vw;
    width: min(300px, 26vw);
    aspect-ratio: 300 / 220;
    fill: none;
    stroke: #a6b8b2;
    opacity: 0.7;
  }
  .gear {
    position: absolute;
    overflow: visible;
    animation: speed-turn calc(var(--motion-ms) * 300) linear infinite;
  }
  .gear.reverse {
    animation-direction: reverse;
    animation-duration: calc(var(--motion-ms) * 180);
  }
  /* Above the Rika bar, which covers the window's foot. */
  .stamp {
    position: absolute;
    left: 26px;
    bottom: calc(14px + var(--rika-space, 0px));
    width: 220px;
    height: auto;
    transform: rotate(-4deg);
    opacity: 0.86;
  }
  .stamp text {
    fill: #c3164f;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.18em;
  }
  .stamp text.small {
    font-size: 8.5px;
    font-weight: 600;
    letter-spacing: 0.26em;
  }
  @keyframes speed-turn {
    to {
      transform: rotate(1turn);
    }
  }
</style>
