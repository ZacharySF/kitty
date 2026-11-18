<script lang="ts">
  import { onMount } from "svelte";
  import HomeWidget from "./HomeWidget.svelte";

  /**
   * The date, the time large (AM or PM small beside it) and a dial with the same time: a
   * point for each hour (larger at the quarters), a short hour hand and a minute hand in the
   * tile's colour. Ticks on the minute, so it costs nothing between.
   */
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
  const parts = $derived(new Intl.DateTimeFormat([], { hour: "numeric", minute: "2-digit" }).formatToParts(now));
  const period = $derived(parts.find((part) => part.type === "dayPeriod")?.value ?? "");
  const time = $derived(parts.filter((part) => part.type !== "dayPeriod").map((part) => part.value).join("").trim());
  const date = $derived(now.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" }));
  const minuteTurn = $derived(now.getMinutes() * 6);
  const hourTurn = $derived((now.getHours() % 12) * 30 + now.getMinutes() / 2);
</script>

<HomeWidget {id} title={date} icon="clock" hero tint="var(--tile-6)" art={dial}>
  <p class="figure"><time datetime={now.toISOString()}>{time}{#if period}<small>{period}</small>{/if}</time></p>
</HomeWidget>

{#snippet dial()}
  <svg class="dial" viewBox="0 0 120 120" aria-hidden="true">
    <circle class="face" cx="60" cy="60" r="58" />
    {#each { length: 12 } as _, hour (hour)}
      <circle class="mark" class:quarter={hour % 3 === 0} cx="60" cy="11" r={hour % 3 === 0 ? 2.6 : 1.5} transform={`rotate(${hour * 30} 60 60)`} />
    {/each}
    <line class="hand hour" x1="60" y1="60" x2="60" y2="33" transform={`rotate(${hourTurn} 60 60)`} />
    <line class="hand minute" x1="60" y1="60" x2="60" y2="19" transform={`rotate(${minuteTurn} 60 60)`} />
    <circle class="pin" cx="60" cy="60" r="4.2" />
  </svg>
{/snippet}

<style>
  small {
    margin-left: 0.2em;
    color: var(--muted);
    font-size: 0.3em;
    font-weight: 500;
    letter-spacing: 0;
  }
  .dial {
    width: 152px;
    height: 152px;
    stroke-linecap: round;
  }
  .face {
    fill: color-mix(in srgb, var(--ink) 7%, transparent);
  }
  .mark {
    fill: var(--muted);
  }
  .mark.quarter {
    fill: var(--ink);
  }
  .hand {
    stroke: var(--ink);
    stroke-width: 5.5;
  }
  .hand.minute {
    stroke: var(--lead, var(--ink));
    stroke-width: 3.2;
  }
  .pin {
    fill: var(--solid-void, var(--br-void));
    stroke: var(--ink);
    stroke-width: 2.4;
  }
</style>
