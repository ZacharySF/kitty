<script lang="ts">
  /** half blood's view loader: the heraldic sun-eye, its rays turning while the eye watches. */
  const RAYS = Array.from({ length: 16 }, (_, i) => {
    const a = (i / 16) * Math.PI * 2;
    const long = i % 2 === 0;
    const r0 = 30;
    const r1 = long ? 50 : 42;
    const s = 0.11;
    const p = (r: number, t: number) => `${(Math.cos(t) * r).toFixed(1)} ${(Math.sin(t) * r).toFixed(1)}`;
    return long
      ? `M${p(r0, a - s)} L${p(r1, a)} L${p(r0, a + s)} Z`
      : `M${p(r0, a - s * 0.8)} Q${p((r0 + r1) / 2, a + 0.16)} ${p(r1, a)} Q${p((r0 + r1) / 2, a - 0.1)} ${p(r0, a + s * 0.8)} Z`;
  }).join(" ");
</script>

<span class="halfblood-loader">
  <svg viewBox="-54 -54 108 108" aria-hidden="true">
    <g class="rays"><path d={RAYS} fill="#ffb21f" stroke="#0c1660" stroke-width="2" stroke-linejoin="round" /></g>
    <circle r="29" fill="#ff8f14" stroke="#0c1660" stroke-width="2.6" />
    <circle r="22" fill="#ffc23a" />
    <path d="M-17 1 Q0 -14 17 1 Q0 14 -17 1 Z" fill="#fbf8f0" stroke="#0c1660" stroke-width="2.2" stroke-linejoin="round" />
    <g class="iris"><circle cy="1" r="7" fill="#d6301f" stroke="#0c1660" stroke-width="1.6" /><circle cy="1" r="3" fill="#0c1660" /></g>
  </svg>
</span>

<style>
  .halfblood-loader {
    display: grid;
    place-items: center;
    width: 92px;
    height: 92px;
  }
  svg {
    width: 100%;
    height: 100%;
  }
  .rays {
    transform-origin: center;
    transform-box: fill-box;
    animation: halfblood-rays calc(var(--motion-ms) * 40) linear infinite;
  }
  .iris {
    animation: halfblood-glance calc(var(--motion-ms) * 24) ease-in-out infinite alternate;
  }
  @keyframes halfblood-rays {
    to { transform: rotate(1turn); }
  }
  @keyframes halfblood-glance {
    0%, 30% { transform: translateX(-6px); }
    70%, 100% { transform: translateX(6px); }
  }
</style>
