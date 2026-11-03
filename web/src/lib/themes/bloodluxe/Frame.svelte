<script lang="ts">
  /** BloodLuxe's window frame: a cream margin bound by twisted black barbed wire, a thorn knot
   *  with a bead of blood at each corner. Edges only; the pointer passes through. */
  const CORNERS = ["tl", "tr", "bl", "br"] as const;
  // A thorn knot: a wound core and eight spikes.
  const SPIKES = Array.from({ length: 8 }, (_, i) => {
    const a = (i * Math.PI) / 4 + (i % 2 ? 0.18 : 0);
    const r = i % 2 ? 17 : 22;
    const tip = [Math.cos(a) * r, Math.sin(a) * r];
    const l = [Math.cos(a - 0.32) * 7, Math.sin(a - 0.32) * 7];
    const rr = [Math.cos(a + 0.32) * 7, Math.sin(a + 0.32) * 7];
    return `M${l[0].toFixed(1)} ${l[1].toFixed(1)} L${tip[0].toFixed(1)} ${tip[1].toFixed(1)} L${rr[0].toFixed(1)} ${rr[1].toFixed(1)} Z`;
  }).join(" ");
</script>

<div class="wire">
  {#each CORNERS as corner (corner)}
    <svg class="knot {corner}" viewBox="-26 -26 52 52" aria-hidden="true">
      <path d={SPIKES} fill="#120708" />
      <circle r="9" fill="#120708" />
      <circle r="9" fill="none" stroke="#fbf1e4" stroke-width="1.2" stroke-dasharray="3 2.4" />
      <circle r="3.6" fill="#d3203b" />
      <circle cx="-1.2" cy="-1.3" r="1.1" fill="#ffd6dc" />
    </svg>
  {/each}
</div>

<style>
  .wire {
    position: absolute;
    inset: 0;
    /* the cream margin the wire is strung across, a fine black inner rule, a blush of red */
    box-shadow:
      inset 0 0 0 16px #fbf1e4,
      inset 0 0 0 17.5px #120708,
      inset 0 0 0 19px rgb(211 32 59 / 0.35);
  }
  .wire::before {
    content: "";
    position: absolute;
    inset: 0;
    background:
      url("./wire-h.svg") repeat-x 0 -3px / 44px 19px,
      url("./wire-h.svg") repeat-x 0 calc(100% + 3px) / 44px 19px,
      url("./wire-v.svg") repeat-y -3px 0 / 19px 44px,
      url("./wire-v.svg") repeat-y calc(100% + 3px) 0 / 19px 44px;
  }
  .knot {
    position: absolute;
    width: 46px;
    height: 46px;
    filter: drop-shadow(0 2px 2px rgb(18 7 8 / 0.25));
  }
  .tl { top: -14px; left: -14px; }
  .tr { top: -14px; right: -14px; }
  .bl { bottom: -14px; left: -14px; }
  .br { bottom: -14px; right: -14px; }
</style>
