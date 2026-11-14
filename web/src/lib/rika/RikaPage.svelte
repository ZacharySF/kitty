<script lang="ts">
  import { untrack } from "svelte";
  import "../settingsRows.css";
  import { api } from "../api";
  import Select from "../ui/Select.svelte";
  import UsageSection from "../usage/UsageSection.svelte";
  import { useWorkspaceContext } from "../workspaceContext.svelte";
  import type { View } from "../workspace";
  import RikaAgents from "./RikaAgents.svelte";
  import RikaConversation from "./RikaConversation.svelte";
  import RikaInput from "./RikaInput.svelte";
  import RikaVoiceSettings from "./RikaVoiceSettings.svelte";
  import EmailSettings from "./settings/EmailSettings.svelte";
  import MemorySettings from "./settings/MemorySettings.svelte";
  import RoutingMemory from "./settings/RoutingMemory.svelte";
  import { rika } from "./session.svelte";

  /**
   * Rika's own page: the conversation beside her agents, and everything that is hers to
   * manage (memory, email, routing, voice, usage) one tab away. While it is on screen it
   * holds the conversation the way home's panel does, so the drawer and reply toasts stay
   * away here and Ctrl + J focuses the field; everywhere else the drawer is unchanged.
   */
  type Tab = "chat" | "agents" | "memory" | "email" | "routing" | "voice" | "usage";
  let { onview }: { onview: (next: View) => void } = $props();
  const pane = useWorkspaceContext();
  let tab = $state<Tab>("chat");
  let field = $state<RikaInput>();
  let spend = $state<{ today: number; cap: number } | null>(null);
  const waiting = $derived(rika.pending.length + rika.pendingPlans.length);
  const working = $derived(rika.agents.filter((agent) => agent.status !== "done").length);

  $effect(() => {
    if (pane.active) return untrack(() => rika.showPanel());
  });
  let seen = rika.focusRequest;
  $effect(() => {
    const request = rika.focusRequest;
    if (!pane.active || request === seen) return;
    seen = request;
    // "Show the agents" from anywhere lands on the Agents tab; anything else is the conversation.
    if (rika.drawer.section === "agents") tab = "agents";
    else {
      tab = "chat";
      field?.focus();
    }
  });
  $effect(() => {
    if (tab !== "usage" || spend) return;
    void api<{ spent_today: number; daily_budget_usd: number }>("/ai/status")
      .then((status) => (spend = { today: status.spent_today, cap: status.daily_budget_usd }))
      .catch(() => (spend = { today: 0, cap: 0 }));
  });
</script>

<section class="rika-page" aria-labelledby={`${pane.id}-rika-title`}>
  <header>
    <h2 id={`${pane.id}-rika-title`}>Rika</h2>
    {#if waiting}<span class="waiting">{waiting} waiting</span>{/if}
    <div class="tabs">
      <Select label="Rika's page" kind="segments" value={tab} onchange={(next) => (tab = next as Tab)}
        options={[
          { value: "chat", label: "Chat" },
          { value: "agents", label: "Agents", count: rika.agents.length },
          { value: "memory", label: "Memory" },
          { value: "email", label: "Email" },
          { value: "routing", label: "Routing" },
          { value: "voice", label: "Voice" },
          { value: "usage", label: "Usage" },
        ]} />
    </div>
  </header>

  {#if tab === "chat"}
    <div class="chat">
      <div class="talk">
        <div class="scroll"><RikaConversation brief={pane.active} sections={false} /></div>
        <div class="foot"><RikaInput bind:this={field} /></div>
      </div>
      <aside aria-label="Rika's agents">
        <h3>Agents{#if working}<span class="count">{working} working</span>{/if}</h3>
        <div class="scroll"><RikaAgents /></div>
      </aside>
    </div>
  {:else if tab === "agents"}
    <div class="column">
      <RikaAgents />
      <p class="links">
        <button class="text-button" onclick={() => onview("herdr")}>Open the terminals</button>
        <button class="text-button" onclick={() => onview("agent-skills")}>All agent skills</button>
      </p>
    </div>
  {:else}
    <!-- data-settings-page: themes draw a Settings page's panels flat, as part of the page. -->
    <div class="column settings-rows" data-settings-page="rika">
      {#if tab === "memory"}<MemorySettings />
      {:else if tab === "email"}<EmailSettings />
      {:else if tab === "routing"}<RoutingMemory />
      {:else if tab === "voice"}<RikaVoiceSettings />
      {:else if spend}<UsageSection today={spend.today} cap={spend.cap} />{/if}
    </div>
  {/if}
</section>

<style>
  .rika-page {
    display: flex;
    flex-direction: column;
    gap: 18px;
    /* The window below the top bar: the conversation and the agents scroll inside, the page does not. */
    height: calc(100dvh - 96px);
    min-height: 320px;
    max-width: 1240px;
    margin-inline: auto;
    padding: 8px 24px 20px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
    padding-bottom: 14px;
    border-bottom: 1px solid var(--hairline);
  }
  h2 {
    margin: 0;
    font-size: 28px;
    font-weight: 500;
  }
  .waiting {
    color: var(--warning);
  }
  .tabs {
    margin-left: auto;
    min-width: 0;
  }
  .chat {
    flex: 1 1 auto;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(0, 1.7fr) minmax(260px, 1fr);
    gap: 32px;
  }
  .talk,
  aside {
    display: flex;
    flex-direction: column;
    min-width: 0;
    min-height: 0;
  }
  aside {
    padding-left: 32px;
    border-left: 1px solid var(--hairline);
  }
  h3 {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin: 0 0 12px;
    font-size: 17px;
    font-weight: 500;
  }
  .count {
    color: var(--muted);
    font-size: 14px;
    font-weight: 400;
  }
  .scroll {
    flex: 1 1 auto;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding-right: 4px;
    overflow-y: auto;
    overscroll-behavior: contain;
    scrollbar-width: thin;
    scrollbar-color: var(--hairline) transparent;
  }
  .foot {
    flex: none;
    padding: 12px 0 2px;
  }
  .column {
    flex: 1 1 auto;
    min-height: 0;
    width: min(100%, 820px);
    margin-inline: auto;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--hairline) transparent;
    container-type: inline-size;
  }
  .links {
    display: flex;
    gap: 18px;
    margin: 16px 0 0;
  }
  @media (max-width: 860px) {
    .chat {
      grid-template-columns: minmax(0, 1fr);
    }
    aside {
      display: none;
    }
  }
</style>
