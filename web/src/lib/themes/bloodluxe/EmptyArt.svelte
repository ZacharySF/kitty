<script lang="ts">
  /** BloodLuxe's empty state: a black thorn wreath around a glossy heart that drips, with a
   *  blue and a pink spark from the marble. */
  const uid = $props.id();
  const THORNS = Array.from({ length: 14 }, (_, i) => {
    const a = (i / 14) * Math.PI * 2;
    const out = i % 2 ? 1 : -1;
    const [cx, cy] = [Math.cos(a) * 27, Math.sin(a) * 27];
    const [tx, ty] = [Math.cos(a + 0.12) * (27 + out * 8), Math.sin(a + 0.12) * (27 + out * 8)];
    const [bx, by] = [Math.cos(a + 0.2) * 27, Math.sin(a + 0.2) * 27];
    return `M${cx.toFixed(1)} ${cy.toFixed(1)} L${tx.toFixed(1)} ${ty.toFixed(1)} L${bx.toFixed(1)} ${by.toFixed(1)} Z`;
  }).join(" ");
</script>

<svg class="bloodluxe-empty" viewBox="-60 -38 120 80" aria-hidden="true">
  <defs>
    <radialGradient id="{uid}-heart" cx="0.36" cy="0.3" r="0.8">
      <stop offset="0" stop-color="#ff7a8c" />
      <stop offset="0.5" stop-color="#c8102e" />
      <stop offset="1" stop-color="#7c0618" />
    </radialGradient>
  </defs>
  <ellipse cx="0" cy="36" rx="40" ry="3" fill="#2f9e1f" opacity="0.4" />
  <g fill="none" stroke="#120708" stroke-width="1.7">
    <ellipse rx="27" ry="27" stroke-dasharray="7 3" />
    <ellipse rx="25" ry="29" transform="rotate(24)" opacity="0.75" />
  </g>
  <path d={THORNS} fill="#120708" />
  <path
    d="M0 13 C-12 4 -16 -2 -16 -7 C-16 -12 -12 -15 -8 -15 C-4 -15 -1.5 -12.5 0 -10 C1.5 -12.5 4 -15 8 -15 C12 -15 16 -12 16 -7 C16 -2 12 4 0 13 Z"
    fill="url(#{uid}-heart)"
    stroke="#120708"
    stroke-width="1.4"
  />
  <path class="drip" d="M-1.6 12 V22 a1.9 1.9 0 0 0 3.8 0 V11" fill="#c8102e" stroke="#120708" stroke-width="1" />
  <ellipse cx="-7" cy="-9" rx="3.2" ry="2" fill="#fff" opacity="0.7" transform="rotate(-30 -7 -9)" />
  <path d="M38 -22 l2 -6 l2 6 l6 2 l-6 2 l-2 6 l-2 -6 l-6 -2 Z" fill="#2546c4" />
  <path d="M-44 14 l1.5 -4 l1.5 4 l4 1.5 l-4 1.5 l-1.5 4 l-1.5 -4 l-4 -1.5 Z" fill="#e8609c" />
</svg>

<style>
  .bloodluxe-empty {
    width: 136px;
    height: auto;
    overflow: visible;
  }
  .drip {
    transform-box: fill-box;
    transform-origin: 50% 0;
    animation: bloodluxe-drip calc(var(--motion-ms) * 8) ease-in infinite alternate;
  }
  @keyframes bloodluxe-drip {
    to { transform: scaleY(1.35); }
  }
</style>
