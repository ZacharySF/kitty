<script lang="ts">
  import { onDestroy } from "svelte";
  import { nowText } from "../agentSkills";
  import { SpaceHold } from "./hold";
  import { agentChips, downloadLabel } from "./rika";
  import { rika } from "./session.svelte";

  /**
   * The Rika bar (Q1 C, Q2 D, Q3 A, Q4 C, Q19 B): the bottom edge of the window, where the
   * herdr dock used to be. Type and press Enter to ask Rika; replies land as toasts above
   * it and the history drawer holds the rest. Hold Space in the field to talk (a tap types
   * a space): Rika listens while it is held and sends what you said when you let go; a
   * very long hold turns into a brain dump that keeps listening, with its transcript in
   * the drawer, until Space is pressed again. Escape drops what is being said.
   * Running worker agents show on the right as chips (a state dot and name; past four,
   * one count); a chip opens its Tern window. While Rika listens or writes a reply the
   * whole bar breathes with a soft ring of light; under reduced motion the ring stays lit
   * and still. No Space keycap or hold hint is drawn (Q5): the breathing bar is the only
   * signal. The bar publishes its height as --rika-space, so the page ends above it.
   */
  let value = $state("");
  let bar = $state<HTMLElement>();
  let input = $state<HTMLInputElement>();
  const chips = $derived(agentChips(rika.agents));
  const asks = $derived(rika.pending.length + rika.pendingPlans.length);
  const listening = $derived(rika.listening);
  /** While listening the field shows what Rika hears (as its placeholder, so nothing is typed). */
  const heard = $derived.by(() => {
    const voice = rika.voice;
    if (voice.phase === "idle") return "Ask Rika…";
    if (voice.phase === "loading" && rika.model) return `Getting the voice model ready · ${downloadLabel(rika.model.received, rika.model.total)}`;
    if (voice.phase === "finishing") return voice.partial || "…";
    const latest = voice.partial || voice.segments.at(-1)?.text || "";
    return latest ? latest.split(" ").slice(-14).join(" ") : voice.mode === "dump" ? "Brain dump · listening…" : "Listening…";
  });

  const hold = new SpaceHold({
    start: () => {
      // Words typed first lead the spoken request; the field then shows what Rika hears.
      const typed = value.trim();
      value = "";
      void rika.listen("command", typed);
    },
    latch: () => rika.latch(),
    stop: () => rika.finish(),
    tap: () => {
      // The press was held back so that a hold types nothing; a tap types its space now.
      if (!input) return;
      input.setRangeText(" ", input.selectionStart ?? value.length, input.selectionEnd ?? value.length, "end");
      value = input.value;
    },
  });
  // Listening that ends elsewhere (the drawer's Done, an error) frees the next Space; a dump
  // started from the drawer or Ctrl K ends with the next Space like a latched one.
  $effect(() => {
    if (!listening) hold.reset();
    else if (rika.voice.mode === "dump") hold.adopt();
  });
  onDestroy(() => hold.reset());

  async function keydown(event: KeyboardEvent) {
    if (event.isComposing) return;
    if (event.key === " " && !event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey) {
      event.preventDefault();
      hold.down(event.repeat);
      return;
    }
    if (event.key === "Escape" && (listening || hold.state !== "idle")) {
      event.preventDefault();
      event.stopPropagation();
      const typed = rika.voice.prefix;
      hold.reset();
      rika.cancel();
      value = typed;
      return;
    }
    if (listening) {
      if (event.key.length === 1 || event.key === "Enter" || event.key === "Backspace") event.preventDefault();
      return;
    }
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    const text = value.trim();
    if (!text) return;
    value = "";
    if (!(await rika.send(text)) && !value) value = text;
  }
  /** Space is released wherever focus went (a hold may outlast the field's focus). */
  function keyup(event: KeyboardEvent) {
    if (event.key === " " && hold.state !== "idle") hold.up();
  }

  $effect(() => {
    if (!bar) return;
    const node = bar;
    const root = document.documentElement;
    const measure = new ResizeObserver(() => root.style.setProperty("--rika-space", `${Math.round(node.getBoundingClientRect().height)}px`));
    measure.observe(node);
    return () => {
      measure.disconnect();
      root.style.removeProperty("--rika-space");
    };
  });
</script>

<svelte:window onkeyup={keyup} />
<section bind:this={bar} class="rika-bar" class:breathing={rika.streaming || listening} data-streaming={String(rika.streaming)}
  data-listening={listening ? rika.voice.mode : undefined} data-tutorial="rika" aria-label="Rika">
  <span class="mark" aria-hidden="true"><span class="orb"></span></span>
  <label class="field">
    <input bind:this={input} bind:value placeholder={heard} aria-label="Ask Rika" aria-busy={listening} spellcheck="false" autocomplete="off" onkeydown={keydown} />
  </label>
  {#if chips.kind === "count" || chips.agents.length}
    <div class="chips" role="group" aria-label="Running agents">
      {#if chips.kind === "count"}
        <button class="rika-agent-chip" data-state={chips.status} aria-label={`${chips.count} agents, show them in Rika history`} onclick={() => rika.showDrawer("agents")}
          ><i class="rika-dot" aria-hidden="true"></i>{chips.count} agents</button>
      {:else}
        {#each chips.agents as agent (agent.id)}
          <button class="rika-agent-chip" data-state={agent.status} aria-label={`${agent.name}, ${agent.status}${agent.now ? `, ${nowText(agent.now)}` : ""}: open its window`}
            title={[agent.task || agent.name, agent.now ? nowText(agent.now) : "", agent.now?.intent ?? ""].filter(Boolean).join("\n")} onclick={() => rika.open(agent)}
            ><i class="rika-dot" aria-hidden="true"></i>{agent.name}</button>
        {/each}
      {/if}
    </div>
  {/if}
  <button class="history" aria-pressed={rika.drawer.open} aria-label={asks ? `Rika history, ${asks} waiting for you` : "Rika history"} title="Rika history (Ctrl + J)" onclick={() => rika.toggleDrawer()}
    >History{#if asks}<i>{asks}</i>{/if}</button>
</section>

<style>
  .rika-bar {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 30;
    display: flex;
    align-items: center;
    gap: 10px;
    box-sizing: border-box;
    height: 54px;
    padding: 0 16px;
    border-top: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--solid-void, #141414) 78%, transparent);
    backdrop-filter: blur(20px) saturate(140%);
    -webkit-backdrop-filter: blur(20px) saturate(140%);
    transition: border-color 200ms;
  }
  /* Q4 C: a soft ring of light around the whole bar, breathing while Rika works. */
  .rika-bar::after {
    content: "";
    position: absolute;
    inset: 0;
    pointer-events: none;
    opacity: 0;
    box-shadow:
      inset 0 0 0 1px color-mix(in srgb, var(--c1) 55%, transparent),
      inset 0 0 32px color-mix(in srgb, var(--c1) 22%, transparent),
      0 -8px 44px -10px color-mix(in srgb, var(--c1) 70%, transparent);
  }
  .rika-bar.breathing {
    border-top-color: color-mix(in srgb, var(--c1) 70%, transparent);
  }
  .rika-bar.breathing::after {
    opacity: 0.75;
    animation: breathe 2.6s ease-in-out infinite;
  }
  @keyframes breathe {
    0%,
    100% {
      opacity: 0.35;
    }
    50% {
      opacity: 1;
    }
  }
  .mark {
    display: grid;
    flex: 0 0 auto;
    place-items: center;
    width: 34px;
    height: 34px;
  }
  .orb {
    width: 20px;
    height: 20px;
    border-radius: 999px;
    background: radial-gradient(circle at 35% 30%, color-mix(in srgb, white 70%, var(--c1)), var(--c1) 45%, color-mix(in srgb, var(--c2) 80%, transparent) 100%);
    box-shadow: 0 0 9px color-mix(in srgb, var(--c1) 55%, transparent);
  }
  .field {
    display: flex;
    flex: 1;
    align-items: center;
    min-width: 0;
    height: 34px;
    padding: 0 12px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-field);
    background: color-mix(in srgb, var(--solid-panel, #1c1c1c) 40%, transparent);
    cursor: text;
  }
  .field:focus-within {
    border-color: var(--br-lineHot);
  }
  /* Outranks the app's shared field styles: the field box is the label, not the input. */
  :global(:root body) .field input {
    flex: 1;
    min-width: 0;
    height: 100%;
    padding: 0;
    border: 0;
    outline: none;
    background: transparent;
    box-shadow: none;
    color: var(--ink);
    font: 13px var(--font-ui);
  }
  input::placeholder {
    color: var(--muted);
  }
  /* What Rika hears reads as live words, not as a prompt. */
  .rika-bar[data-listening] input::placeholder {
    color: var(--ink);
  }
  .chips {
    display: flex;
    flex: 0 1 auto;
    gap: 6px;
    min-width: 0;
  }
  .rika-agent-chip,
  .history {
    display: inline-flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 6px;
    min-height: 0;
    padding: 5px 11px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: color-mix(in srgb, var(--solid-panel) 40%, transparent);
    color: var(--muted);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.08em;
    cursor: pointer;
  }
  .rika-agent-chip {
    max-width: 160px;
    overflow: hidden;
    color: var(--ink);
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .rika-agent-chip[data-state="blocked"] {
    border-color: color-mix(in srgb, var(--warning) 50%, var(--hairline));
  }
  .history {
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .rika-agent-chip:hover,
  .history:hover,
  .history[aria-pressed="true"] {
    background: color-mix(in srgb, var(--ink) 12%, transparent);
    color: var(--ink);
  }
  .history i {
    padding: 0 5px;
    border-radius: 999px;
    background: var(--warning);
    color: var(--solid-void);
    font-style: normal;
    letter-spacing: 0;
  }
</style>
