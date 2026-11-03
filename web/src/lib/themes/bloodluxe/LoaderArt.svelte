<script lang="ts">
  /** BloodLuxe's view loader: a marbled red, white and blue orb turning inside a ring of
   *  black barbed wire, a drop of blood gathering under it. */
  const BARBS = Array.from({ length: 10 }, (_, i) => {
    const a = (i / 10) * Math.PI * 2;
    const p = (r: number, t: number) => `${(Math.cos(t) * r).toFixed(1)} ${(Math.sin(t) * r).toFixed(1)}`;
    return `M${p(38, a - 0.07)} L${p(47, a + 0.05)} L${p(38, a + 0.09)} Z M${p(38, a + 0.02)} L${p(31, a + 0.12)} L${p(38, a + 0.14)} Z`;
  }).join(" ");
</script>

<span class="bloodluxe-loader">
  <span class="orb"></span>
  <svg class="wire" viewBox="-50 -50 100 100" aria-hidden="true">
    <circle r="38" fill="none" stroke="#120708" stroke-width="2" stroke-dasharray="9 2.5" />
    <circle r="38" fill="none" stroke="#120708" stroke-width="1.2" transform="rotate(14)" stroke-dasharray="4 6" />
    <path d={BARBS} fill="#120708" />
  </svg>
  <svg class="drop" viewBox="0 0 20 26" aria-hidden="true"><path d="M10 0C10 0 2 11 2 17a8 8 0 0 0 16 0C18 11 10 0 10 0Z" /></svg>
</span>

<style>
  .bloodluxe-loader {
    position: relative;
    display: grid;
    place-items: center;
    width: 100px;
    height: 108px;
  }
  .orb {
    width: 52px;
    height: 52px;
    margin-bottom: 8px;
    border: 1.5px solid #120708;
    border-radius: 50%;
    background:
      radial-gradient(circle at 34% 30%, rgb(255 255 255 / 0.85) 0 9%, transparent 26%),
      conic-gradient(from 20deg, #c8102e 0 18%, #fff8ee 0 30%, #2546c4 0 46%, #fff8ee 0 56%, #f5a3c3 0 70%, #fff8ee 0 80%, #c8102e 0);
    animation: bloodluxe-turn calc(var(--motion-ms) * 6) linear infinite;
  }
  .wire {
    position: absolute;
    top: 0;
    left: 0;
    width: 100px;
    height: 100px;
    animation: bloodluxe-turn calc(var(--motion-ms) * 24) linear infinite reverse;
  }
  .drop {
    position: absolute;
    bottom: 0;
    left: calc(50% - 5px);
    width: 10px;
    height: 13px;
    fill: #c8102e;
    stroke: #120708;
    stroke-width: 1.4;
    transform-origin: 50% 0;
    animation: bloodluxe-fall calc(var(--motion-ms) * 5) ease-in infinite;
  }
  @keyframes bloodluxe-turn {
    to { transform: rotate(360deg); }
  }
  @keyframes bloodluxe-fall {
    0% { transform: translateY(-14px) scale(0.3); opacity: 0; }
    60% { opacity: 1; }
    100% { transform: translateY(0) scale(1); opacity: 1; }
  }
</style>
