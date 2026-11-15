<script lang="ts">
  import { onMount } from "svelte";
  import HomeWidget from "./HomeWidget.svelte";

  /** The time and the date, large. Ticks on the minute, so it costs nothing between. */
  let { id }: { id: string } = $props();
  let now = $state(new Date());
  onMount(() => {
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      now = new Date();
      timer = setTimeout(tick, 60_000 - (Date.now() % 60_000) + 50);
    };
    tick();
    return () => clearTimeout(timer);
  });
  const time = $derived(now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
  const date = $derived(now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }));
</script>

<HomeWidget {id} title={date} icon="clock">
  <p><time datetime={now.toISOString()}>{time}</time></p>
</HomeWidget>

<style>
  p {
    margin: 0;
    color: var(--ink);
    font: 300 56px/1.1 var(--font-ui);
    letter-spacing: -0.03em;
    font-variant-numeric: tabular-nums;
  }
</style>
