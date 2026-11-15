<script lang="ts">
  import { onMount } from "svelte";
  import { Panel } from "@nunera/bracket";
  import { api } from "../api";
  import { buildStore, setPreferences } from "../build.svelte";
  import Select from "../ui/Select.svelte";
  import { GROUPS, change, chosen, type Group } from "./customise";
  import type { MemoryState, Preference } from "./settings/settings";

  /**
   * Customise: how Rika answers (kept as preference sentences in her memory, where each can
   * still be edited or forgotten) and how large her conversation reads on this build.
   */
  let preferences = $state<Preference[] | null>(null);
  let error = $state("");
  let busy = $state(false);
  onMount(() => void load());
  async function load() {
    try { preferences = (await api<MemoryState>("/rika/memory")).preferences; error = ""; }
    catch (cause) { error = (cause as Error).message; }
  }
  async function pick(group: Group, id: string) {
    if (!preferences || busy) return;
    const choice = group.choices.find((item) => item.id === id)!;
    const { forget, add } = change(group, choice, preferences);
    busy = true;
    try {
      for (const item of forget) await api(`/rika/preferences/${item.id}/delete`, {});
      if (add) await api("/rika/preferences", { text: add });
    } catch (cause) { error = (cause as Error).message; }
    busy = false;
    await load();
  }
</script>

<Panel title="Customise" aria-label="Customise Rika">
  <div class="rows">
    {#each GROUPS as group (group.id)}
      <div class="set-row">
        <span class="set-label">{group.label}</span>
        <span class="set-end">
          <Select label={group.label} kind="segments" disabled={!preferences || busy} value={preferences ? chosen(group, preferences).id : ""}
            options={group.choices.map((choice) => ({ value: choice.id, label: choice.label }))} onchange={(id) => pick(group, id)} />
        </span>
      </div>
    {/each}
    <div class="set-row">
      <span class="set-label">Text size</span>
      <span class="set-end">
        <Select label="Text size" kind="segments" value={buildStore.build.ui.rika.text}
          options={[{ value: "normal", label: "Normal" }, { value: "large", label: "Large" }]}
          onchange={(text) => void setPreferences({ ui: { rika: { text: text as "normal" | "large" } } })} />
      </span>
    </div>
  </div>
  <p class="set-note">Replies and tone are kept in Memory as sentences you can edit. They apply from Rika's next message.</p>
  {#if error}<p class="set-note" role="alert">{error}</p>{/if}
</Panel>
