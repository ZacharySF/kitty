<script lang="ts">
  import { nowText } from "../agentSkills";
  import { agentChips } from "./rika";
  import { rika } from "./session.svelte";

  /**
   * The Rika bar (Q1 C, Q2 D, Q4 C, Q19 B): the bottom edge of the window, where the
   * herdr dock used to be. Type and press Enter to ask Rika; replies land as toasts above
   * it and the history drawer holds the rest. Running worker agents show on the right as
   * chips (a state dot and name; past four, one count); a chip opens its Tern window.
   * While Rika is writing a reply the whole bar breathes with a soft ring of light; under
   * reduced motion the ring stays lit and still. The bar publishes its height as
   * --rika-space, so the page ends above it. No Space keycap or hint is drawn (Q5).
   */
  let {
    listening = false,
  }: {
    /** Phase 2's hold-Space voice input sets this while it listens; the ring breathes then too. */
    listening?: boolean;
  } = $props();
  let value = $state("");
  let bar = $state<HTMLElement>();
  const chips = $derived(agentChips(rika.agents));
  const asks = $derived(rika.pending.length);

  async function keydown(event: KeyboardEvent) {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    const text = value.trim();
    if (!text) return;
    value = "";
    if (!(await rika.send(text)) && !value) value = text;
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

<section bind:this={bar} class="rika-bar" class:breathing={rika.streaming || listening} data-streaming={String(rika.streaming)} data-tutorial="rika" aria-label="Rika">
  <span class="mark" aria-hidden="true"><span class="orb"></span></span>
  <label class="field">
    <input bind:value placeholder="Ask Rika…" aria-label="Ask Rika" spellcheck="false" autocomplete="off" onkeydown={keydown} />
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
