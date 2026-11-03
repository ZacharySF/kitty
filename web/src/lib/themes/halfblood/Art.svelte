<script lang="ts">
  /**
   * half blood's ground: a flat ultramarine field scattered with white cloud squiggles and
   * sparks, a heraldic sun with one open eye and a pale crescent moon low in the left
   * gutter, a grass-green hem along the foot. The clouds drift and the sun's rays turn on
   * the theme's motion; sun, moon and sparks are motif marks (`.ornament`).
   */
  const uid = $props.id();
  // Deterministic scatter (no Math.random, so every load matches).
  const rand = (seed: number) => {
    const x = Math.sin(seed * 12.9898) * 43758.5453;
    return x - Math.floor(x);
  };
  // Squiggles: two to four scalloped bumps, a few with a tail curl, laid on a loose grid.
  const CLOUDS = Array.from({ length: 96 }, (_, i) => {
    const x = (i % 12) * 124 + rand(i + 1) * 90 - 20;
    const y = Math.floor(i / 12) * 108 + 34 + rand(i + 101) * 60;
    const bumps = 2 + Math.floor(rand(i + 201) * 3);
    const w = 8 + rand(i + 301) * 5;
    let d = `M${x.toFixed(1)} ${y.toFixed(1)}`;
    for (let b = 0; b < bumps; b++) d += ` q${(w / 2).toFixed(1)} ${(-w * 0.8).toFixed(1)} ${w.toFixed(1)} 0`;
    if (i % 3 === 0) d += ` q${(w * 0.6).toFixed(1)} ${(w * 0.5).toFixed(1)} 0 ${(w * 0.6).toFixed(1)}`;
    return d;
  }).join(" ");
  const SPARKS = Array.from({ length: 26 }, (_, i) => {
    const x = rand(i + 401) * 1440;
    const y = 40 + rand(i + 501) * 760;
    const r = 3 + rand(i + 601) * 3;
    const f = (v: number) => v.toFixed(1);
    return `M${f(x)} ${f(y - r)} Q${f(x)} ${f(y)} ${f(x + r)} ${f(y)} Q${f(x)} ${f(y)} ${f(x)} ${f(y + r)} Q${f(x)} ${f(y)} ${f(x - r)} ${f(y)} Q${f(x)} ${f(y)} ${f(x)} ${f(y - r)} Z`;
  }).join(" ");
  // Sun in splendour: alternating straight and wavy rays.
  const RAYS = Array.from({ length: 16 }, (_, i) => {
    const a = (i / 16) * Math.PI * 2;
    const long = i % 2 === 0;
    const r0 = 30;
    const r1 = long ? 52 : 44;
    const s = 0.1;
    const p = (r: number, t: number) => `${(Math.cos(t) * r).toFixed(1)} ${(Math.sin(t) * r).toFixed(1)}`;
    return long
      ? `M${p(r0, a - s)} L${p(r1, a)} L${p(r0, a + s)} Z`
      : `M${p(r0, a - s * 0.8)} Q${p((r0 + r1) / 2, a + 0.16)} ${p(r1, a)} Q${p((r0 + r1) / 2, a - 0.1)} ${p(r0, a + s * 0.8)} Z`;
  }).join(" ");
  const BLADES = Array.from({ length: 210 }, (_, i) => {
    const x = (i * 6.9) % 1440;
    const h = 10 + rand(i + 701) * 16;
    return `M${x.toFixed(1)} 90 l${(rand(i + 801) * 5 - 2.5).toFixed(1)} ${(-h - 34).toFixed(1)} l3.6 ${(h + 34).toFixed(1)}`;
  }).join(" ");
</script>

<svg class="field" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  <defs>
    <radialGradient id="{uid}-vignette" cx="0.5" cy="0.45" r="0.78">
      <stop offset="0.55" stop-color="#1b2fa0" stop-opacity="0" />
      <stop offset="1" stop-color="#0c1660" stop-opacity="0.7" />
    </radialGradient>
  </defs>
  <rect width="1440" height="900" fill="#1b2fa0" />
  <rect width="1440" height="900" fill="url(#{uid}-vignette)" />
</svg>
<svg class="clouds" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  <path d={CLOUDS} fill="none" stroke="#f7f4ec" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" opacity="0.52" />
  <path class="ornament" d={SPARKS} fill="#f7f4ec" opacity="0.8" />
</svg>
<svg class="hem" viewBox="0 0 1440 90" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
  <path d="M0 52 Q180 40 360 48 T720 46 T1080 50 T1440 42 L1440 90 L0 90 Z" fill="#37b229" />
  <path d={BLADES} fill="#5fdc3e" stroke="#2a8f1f" stroke-width="0.6" />
</svg>

<!-- heraldic sun with an open eye, and a pale crescent moon, low in the left gutter -->
<svg class="sun ornament" viewBox="-56 -56 112 112" aria-hidden="true">
  <g class="rays"><path d={RAYS} fill="#ffb21f" stroke="#0c1660" stroke-width="2" stroke-linejoin="round" /></g>
  <circle r="29" fill="#ff8f14" stroke="#0c1660" stroke-width="2.6" />
  <circle r="22" fill="#ffc23a" />
  <path d="M-17 1 Q0 -14 17 1 Q0 14 -17 1 Z" fill="#fbf8f0" stroke="#0c1660" stroke-width="2.2" stroke-linejoin="round" />
  <circle cy="1" r="7" fill="#d6301f" stroke="#0c1660" stroke-width="1.6" />
  <circle cy="1" r="3" fill="#0c1660" />
  <circle cx="2" cy="-1.4" r="1.2" fill="#fbf8f0" />
  <path d="M-15 -9 q15 -9 30 0" fill="none" stroke="#0c1660" stroke-width="2" stroke-linecap="round" />
</svg>
<svg class="moon ornament" viewBox="-34 -34 68 68" aria-hidden="true">
  <path d="M3.4 -30 A30 30 0 1 0 25.6 15.6 A25 25 0 1 1 3.4 -30 Z" fill="#c9dcff" stroke="#0c1660" stroke-width="2.4" stroke-linejoin="round" />
  <path d="M-17 -3 q3 3 6 0" fill="none" stroke="#0c1660" stroke-width="2" stroke-linecap="round" />
  <path d="M-21 9 q4 2 7 -1" fill="none" stroke="#d6301f" stroke-width="1.8" stroke-linecap="round" />
</svg>

<style>
  svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }
  .clouds {
    inset: 0 -40px;
    width: calc(100% + 80px);
    /* A pixel a step: the window-wide layer redraws every few seconds, not every frame. */
    animation: halfblood-drift calc(var(--motion-ms) * 600) steps(40) infinite alternate;
  }
  .sun,
  .moon {
    inset: auto;
    width: auto;
  }
  /* Low on the left, where the home widgets end and page columns leave the ground open. */
  .sun {
    left: 54px;
    bottom: 96px;
    height: 104px;
  }
  .moon {
    left: 176px;
    bottom: 196px;
    height: 58px;
  }
  .hem {
    inset: auto 0 0;
    height: 92px;
  }
  .rays {
    transform-origin: center;
    transform-box: fill-box;
    animation: halfblood-turn calc(var(--motion-ms) * 500) linear infinite;
  }
  :global(:root[data-motif="off"]) .ornament {
    display: none;
  }
  @keyframes halfblood-drift {
    to { transform: translateX(-40px); }
  }
  @keyframes halfblood-turn {
    to { transform: rotate(1turn); }
  }
</style>
