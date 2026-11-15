<script lang="ts">
  import { onDestroy } from "svelte";
  import { SpaceHold } from "./hold";
  import { activeModules } from "../modules.svelte";
  import { downloadLabel, voiceEnabled } from "./rika";
  import RikaMark from "./RikaMark.svelte";
  import { rika } from "./session.svelte";

  /**
   * Rika's field (Q3 A, Q4 C, Q5), at the foot of the home panel and of the drawer. Type and
   * press Enter to ask. Hold Space to talk (a tap types a space): Rika listens while it is
   * held and sends what you said when you let go; a very long hold turns into a brain dump
   * that keeps listening, with its transcript in the conversation, until Space is pressed
   * again; Brain dump beside the field starts one directly. Both are the voice module's:
   * without it Space types and there is no Brain dump. Escape drops what is being said.
   * While Rika listens or writes, the mark turns and the field's edge takes the accent;
   * nothing else is drawn for it.
   */
  let value = $state("");
  let input = $state<HTMLInputElement>();
  const listening = $derived(rika.listening);
  /** Without the voice module Space only types and there is no brain dump. */
  const voiceOn = $derived(voiceEnabled(activeModules));
  /** While listening the field shows what Rika hears (as its placeholder, so nothing is typed). */
  const heard = $derived.by(() => {
    const voice = rika.voice;
    if (voice.phase === "idle") return "Ask Rika…";
    if (voice.phase === "loading" && rika.model) return `Getting the voice model ready, ${downloadLabel(rika.model.received, rika.model.total)}`;
    if (voice.phase === "finishing") return voice.partial || "…";
    const latest = voice.partial || voice.segments.at(-1)?.text || "";
    return latest ? latest.split(" ").slice(-14).join(" ") : voice.mode === "dump" ? "Brain dump, listening…" : "Listening…";
  });

  export function focus() {
    input?.focus();
  }

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
  // Listening that ends elsewhere (Done in the transcript, an error) frees the next Space; a
  // dump started from the conversation or Ctrl K ends with the next Space like a latched one.
  $effect(() => {
    if (!listening) hold.reset();
    else if (rika.voice.mode === "dump") hold.adopt();
  });
  onDestroy(() => hold.reset());

  async function keydown(event: KeyboardEvent) {
    if (event.isComposing) return;
    if (voiceOn && event.key === " " && !event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey) {
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
      // Typing is held while Rika listens; Ctrl / ⌘ chords (copy, paste, select all) still work.
      if (!event.ctrlKey && !event.metaKey && (event.key.length === 1 || event.key === "Enter" || event.key === "Backspace")) event.preventDefault();
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
</script>

<svelte:window onkeyup={keyup} />
<div class="rika-compose">
  <label class="rika-field" class:working={rika.streaming || listening} data-streaming={String(rika.streaming)}
    data-listening={listening ? rika.voice.mode : undefined}>
    <RikaMark size={16} active={rika.streaming || listening} />
    <input bind:this={input} bind:value placeholder={heard} aria-label="Ask Rika" aria-busy={listening} spellcheck="false" autocomplete="off" onkeydown={keydown} />
  </label>
  {#if voiceOn}<button type="button" class="pill dump" disabled={rika.listening} title="Talk as long as you like; Rika plans it for you to approve"
    onclick={() => { rika.showDrawer("conversation"); void rika.listen("dump"); }}>Brain dump</button>{/if}
</div>

<style>
  .rika-compose {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .dump {
    flex: none;
    min-height: 40px;
    padding: 0 14px;
    font-size: 12.5px;
  }
  .rika-field {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    height: 40px;
    padding: 0 12px;
    border: 1px solid var(--hairline);
    border-radius: var(--r-field);
    background: color-mix(in srgb, var(--solid-inset, #111) 45%, transparent);
    cursor: text;
  }
  .rika-field:focus-within {
    border-color: var(--br-lineHot);
  }
  .rika-field.working {
    border-color: var(--accent);
  }
  /* Outranks the app's shared field styles: the field box is the label, not the input. */
  :global(:root body) .rika-field input {
    flex: 1;
    min-width: 0;
    height: 100%;
    padding: 0;
    border: 0;
    outline: none;
    background: transparent;
    box-shadow: none;
    color: var(--ink);
    font: 13.5px var(--font-ui);
  }
  input::placeholder {
    color: var(--muted);
  }
  /* What Rika hears reads as live words, not as a prompt. */
  .rika-field[data-listening] input::placeholder {
    color: var(--ink);
  }
</style>
