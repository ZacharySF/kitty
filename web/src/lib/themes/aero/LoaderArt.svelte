<script lang="ts">
  import { onMount } from "svelte";

  /** Kitty's view loader: a cat in text, blinking, over a level meter that sweeps. */
  const FACES = ["=^.^=", "=^.^=", "=^.^=", "=^-^="];
  const LEVELS = "▁▂▃▅▆▇▆▅▃▂";
  let tick = $state(0);
  onMount(() => {
    // Reduced motion (the setting or the system's) shows the first frame and stops.
    if (document.documentElement.dataset.themeMotion === "none" || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const timer = setInterval(() => (tick += 1), 140);
    return () => clearInterval(timer);
  });
  const meter = $derived(Array.from({ length: 10 }, (_, index) => LEVELS[(index + tick) % LEVELS.length]).join(""));
</script>

<pre class="aero-loader" aria-hidden="true">{FACES[Math.floor(tick / 4) % FACES.length]}
{meter}</pre>

<style>
  .aero-loader {
    margin: 6px;
    color: var(--accent);
    font-family: var(--font-code);
    font-size: 15px;
    line-height: 1.35;
    text-align: center;
    text-shadow: 0 0 14px color-mix(in srgb, var(--accent) 60%, transparent);
  }
</style>
