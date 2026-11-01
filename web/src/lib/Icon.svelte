<script lang="ts">
  import { ICONS, ICON_STROKE, PETAL, PETAL_ANGLES } from "./icons";

  let { name, size = 20 }: { name: string; size?: number } = $props();
  const icon = $derived(ICONS[name] ?? ICONS.jobs);
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width={ICON_STROKE}
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
  ><path d={icon.path} />{#each icon.accents as [x, y, r], index (index)}<g
      class="blossom"
      transform={`translate(${x} ${y}) scale(${r / 10})`}
      stroke="none"
      >{#each PETAL_ANGLES as angle (angle)}<path d={PETAL} transform={`rotate(${angle})`} />{/each}<circle
        r="2.2"
      /></g
    >{/each}</svg
>

<style>
  /* Pastel petals with a dark heart; both follow the theme. */
  .blossom {
    fill: var(--icon-blossom, var(--c1));
  }
  .blossom circle {
    fill: var(--solid-void, var(--br-void));
  }
</style>
