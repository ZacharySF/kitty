<script lang="ts" module>
  /** The brain as points: two hemispheres, temporal lobes, cerebellum and stem. Front faces -x. */
  function brainPoints(n: number, seed = 5): number[][] {
    const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const dir = () => {
      const u = rand() * 2 - 1, a = rand() * Math.PI * 2, s = Math.sqrt(1 - u * u);
      return [s * Math.cos(a), s * Math.sin(a), u];
    };
    const out: number[][] = [];
    while (out.length < n) {
      const pick = rand();
      if (pick < 0.66) {
        // Cerebrum: long and low, the back tapering, the underside flattened.
        const side = rand() < 0.5 ? -1 : 1, [dx, dy, dz] = dir();
        if (dz * side < -0.55 && rand() < 0.7) continue;
        const g = 1 + 0.045 * Math.sin(dx * 15 + dz * 7) * Math.sin(dy * 13);
        let y = dy * 0.5 * g * (1 - 0.14 * Math.max(0, dx));
        if (y < -0.1) y = -0.1 + (y + 0.1) * 0.5;
        out.push([dx * 0.92 * g, y, dz * 0.3 * g + side * 0.32]);
      } else if (pick < 0.8) {
        const side = rand() < 0.5 ? -1 : 1, [dx, dy, dz] = dir();
        if (dz * side < 0 || dy > 0.2) continue;
        out.push([-0.12 + dx * 0.48, -0.24 + dy * 0.2, side * 0.4 + dz * 0.17]);
      } else if (pick < 0.95) {
        const [dx, dy, dz] = dir();
        out.push([0.58 + dx * 0.28, -0.36 + dy * 0.15, dz * 0.42]);
      } else {
        const t = rand(), a = rand() * Math.PI * 2;
        out.push([0.26 - 0.06 * t + 0.055 * Math.cos(a), -0.3 - 0.42 * t, 0.055 * Math.sin(a)]);
      }
    }
    return out;
  }
  const cache = new Map<number, number[][]>();
  const pointsFor = (n: number) => cache.get(n) ?? cache.set(n, brainPoints(n)).get(n)!;
  const TILT = 0.22, MIDY = -0.11, EXTENT = 0.98, STILL = -0.6, RATE = 0.9;
</script>

<script lang="ts">
  /**
   * The loader: the Brain, small, as a turning cloud of points in the text colour. Still
   * (one three-quarter frame) under reduced motion. Decorative; the caption beside it says
   * what is loading.
   */
  let { size = 48 }: { size?: number } = $props();
  let canvas = $state<HTMLCanvasElement>();

  $effect(() => {
    const node = canvas;
    if (!node) return;
    const ctx = node.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    node.width = Math.round(size * dpr);
    node.height = Math.round(size * dpr);
    const points = pointsFor(Math.round(Math.min(460, Math.max(70, size * 4.6))));
    const dot = Math.max(1, size / 64);
    const scale = size / (2 * EXTENT), half = size / 2;
    const ct = Math.cos(TILT), st = Math.sin(TILT);
    const color = getComputedStyle(node).color;
    const draw = (theta: number) => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size, size);
      ctx.fillStyle = color;
      const c = Math.cos(theta), s = Math.sin(theta);
      for (const [x, y0, z] of points) {
        const y = y0 - MIDY, x1 = x * c + z * s, z1 = -x * s + z * c;
        const y2 = y * ct - z1 * st, z2 = y * st + z1 * ct, f = 3.2 / (3.2 + z2);
        ctx.globalAlpha = 0.3 + 0.7 * Math.min(1, Math.max(0, (0.9 - z2) / 1.8));
        ctx.fillRect(half + x1 * f * scale - dot / 2, half - y2 * f * scale - dot / 2, dot, dot);
      }
      ctx.globalAlpha = 1;
    };
    draw(STILL);
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const start = performance.now();
    let raf = requestAnimationFrame(function frame(now) {
      draw(STILL + ((now - start) / 1000) * RATE);
      raf = requestAnimationFrame(frame);
    });
    return () => cancelAnimationFrame(raf);
  });
</script>

<canvas bind:this={canvas} class="brain-cloud" style:width={`${size}px`} style:height={`${size}px`} aria-hidden="true"></canvas>

<style>
  .brain-cloud {
    display: block;
    flex: none;
    color: var(--ink);
  }
</style>
