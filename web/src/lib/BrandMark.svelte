<script lang="ts">
  /**
   * The app icon (the poured cat, scripts/brand_icons.py) in the theme's colours, clipped to the icon's continuous-corner
   * squircle: the space above the surface takes currentColor, the cat and its tail take --brand-liquid (default --c1).
   */
  let { size = 26 }: { size?: number } = $props();
  const uid = $props.id();
  const clip = `brand-tile-${uid}`;
</script>

<svg viewBox="100 100 824 824" width={size} height={size} aria-hidden="true">
  <defs>
    <clipPath id={clip}>
      <path d="M383.41 100L640.59 100C722.19 100 763 100 806.92 113.89C854.88 131.34 892.66 169.12 910.11 217.08C924 261 924 301.81 924 383.41L924 640.59C924 722.19 924 763 910.11 806.92C892.66 854.88 854.88 892.66 806.92 910.11C763 924 722.19 924 640.59 924L383.41 924C301.81 924 261 924 217.08 910.11C169.12 892.66 131.34 854.88 113.89 806.92C100 763 100 722.19 100 640.59L100 383.41C100 301.81 100 261 113.89 217.08C131.34 169.12 169.12 131.34 217.08 113.89C261 100 301.81 100 383.41 100Z" />
    </clipPath>
  </defs>
  <g clip-path={`url(#${clip})`}>
    <rect x="100" y="100" width="824" height="824" fill="currentColor" />
    <path
      fill="var(--brand-liquid, var(--c1))"
      d="M0 600 H180.0 Q256.0 600.0 256.0 570.0 L256.0 552.0 Q256.0 528.0 262.9 505.0 L295.4 396.7 Q304.0 368.0 317.4 394.8 L361.3 482.5 Q372.0 504.0 396.0 504.0 H460.0 Q484.0 504.0 494.7 482.5 L538.6 394.8 Q552.0 368.0 560.6 396.7 L593.1 505.0 Q600.0 528.0 600.0 552.0 L600.0 570.0 Q600.0 600.0 676.0 600.0 H1024 V1024 H0 Z"
    />
    <path fill="none" stroke="var(--brand-liquid, var(--c1))" stroke-width="48" stroke-linecap="round" d="M792 608 V464.0 C792 412.2 812.2 392.0 864 392.0" />
  </g>
</svg>
