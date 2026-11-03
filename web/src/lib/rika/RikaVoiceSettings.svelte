<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../api";
  import { downloadLabel } from "./rika";
  import { rika } from "./session.svelte";

  /**
   * Settings → AI → Rika's voice (docs/rika-design.md Q5 A, Q6 C): the on-device Whisper
   * model voice input uses (it downloads on first use into the workspace's data folder,
   * or now from here) and whether a local engine speaks Rika's replies to spoken requests.
   */
  type Voice = {
    engine: "whisper" | "demo";
    model: string;
    models: { id: string; label: string; size: number; installed: boolean }[];
    installed: boolean;
    downloading: { received: number; total: number } | null;
    speech: string | null;
  };
  let voice = $state<Voice | null>(null);
  let error = $state("");
  let busy = $state(false);
  const progress = $derived(rika.model ?? voice?.downloading ?? null);
  const size = (bytes: number) => `${Math.round(bytes / 1_000_000)} MB`;

  async function load() {
    try {
      voice = await api<Voice>("/rika/voice");
    } catch (cause) {
      error = (cause as Error).message;
    }
  }
  onMount(load);
  // A finished download (the session clears its progress) shows the model as installed.
  $effect(() => {
    if (!rika.model && voice?.downloading) void load();
  });

  async function choose(model: string) {
    busy = true;
    error = "";
    try {
      voice = await api<Voice>("/rika/voice", { model });
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
  async function download() {
    busy = true;
    error = "";
    try {
      await api("/rika/voice/model", {});
      await load();
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<fieldset class="voice">
  <legend>Rika's voice</legend>
  {#if voice}
    {#if voice.engine === "demo"}
      <p class="muted">The demo hears a canned transcript: no microphone audio is transcribed and no model is downloaded.</p>
    {:else}
      <label>Speech-to-text model (on this computer)
        <select value={voice.model} disabled={busy} onchange={(event) => choose(event.currentTarget.value)}>
          {#each voice.models as item (item.id)}
            <option value={item.id}>{item.label} · {size(item.size)}{item.installed ? " · downloaded" : ""}</option>
          {/each}
        </select>
      </label>
      <p class="muted">
        {#if progress}Downloading · {downloadLabel(progress.received, progress.total)}
        {:else if voice.installed}Downloaded. Audio never leaves this computer.
        {:else}Downloads once into this workspace's data folder the first time you hold Space in the Rika bar.{/if}
      </p>
      {#if !voice.installed && !progress}<button class="button" disabled={busy} onclick={download}>Download now</button>{/if}
    {/if}
    <p class="muted">
      Spoken replies: {voice.speech ? `${voice.speech} reads a short summary aloud when you spoke to Rika.` : "no local speech engine (Piper or espeak-ng) is installed, so Rika answers in text only."}
    </p>
  {/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</fieldset>

<style>
  .voice {
    display: grid;
    gap: 8px;
    margin: 0;
    padding: 0;
    border: 0;
  }
  .voice legend {
    padding: 0;
    margin-bottom: 0.4rem;
    font-weight: 600;
  }
  .voice p {
    margin: 0;
  }
  .error {
    color: var(--danger);
  }
</style>
