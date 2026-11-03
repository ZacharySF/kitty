<script lang="ts">
  /** half blood's window frame: a night-navy band strung with bone-white thorn filigree,
   *  crossed bones with a red bead at each corner. Edges only; the pointer passes through. */
  const CORNERS = ["tl", "tr", "bl", "br"] as const;
  // One bone from (-15,-15) to (15,15): a shaft with paired knuckles at each end.
  const BONE = "M-12 -12 L12 12";
  const KNUCKLES = [
    [-15.2, -10.8], [-10.8, -15.2], [15.2, 10.8], [10.8, 15.2],
  ] as const;
</script>

<div class="filigree">
  {#each CORNERS as corner (corner)}
    <svg class="bones {corner}" viewBox="-22 -22 44 44" aria-hidden="true">
      <circle r="17" fill="#0b1250" />
      {#each [0, 90] as turn (turn)}
        <g transform={`rotate(${turn})`}>
          <path d={BONE} stroke="#0b1250" stroke-width="8.5" stroke-linecap="round" />
          {#each KNUCKLES as [x, y], index (index)}<circle cx={x} cy={y} r="4.6" fill="#0b1250" />{/each}
          <path d={BONE} stroke="#f7f1e3" stroke-width="5" stroke-linecap="round" />
          {#each KNUCKLES as [x, y], index (index)}<circle cx={x} cy={y} r="3.2" fill="#f7f1e3" />{/each}
        </g>
      {/each}
      <circle r="4.2" fill="#e8262d" stroke="#0b1250" stroke-width="1.4" />
    </svg>
  {/each}
</div>

<style>
  .filigree {
    position: absolute;
    inset: 0;
    box-shadow:
      inset 0 0 0 18px #0b1250,
      inset 0 0 0 19.5px #f7f1e3,
      inset 0 0 0 21px rgb(11 18 80 / 0.4);
  }
  .filigree::before {
    content: "";
    position: absolute;
    inset: 0;
    background:
      url("./vine-h.svg") repeat-x 0 -1px / 56px 19px,
      url("./vine-h.svg") repeat-x 0 calc(100% + 1px) / 56px 19px,
      url("./vine-v.svg") repeat-y -1px 0 / 19px 56px,
      url("./vine-v.svg") repeat-y calc(100% + 1px) 0 / 19px 56px;
  }
  .bones {
    position: absolute;
    width: 42px;
    height: 42px;
  }
  .tl { top: -10px; left: -10px; }
  .tr { top: -10px; right: -10px; }
  .bl { bottom: -10px; left: -10px; }
  .br { bottom: -10px; right: -10px; }
</style>
