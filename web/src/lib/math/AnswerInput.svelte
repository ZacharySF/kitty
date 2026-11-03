<script lang="ts">
  import { onMount, tick } from "svelte";
  import { Button } from "@nunera/bracket";
  import { useWorkspaceContext } from "../workspaceContext.svelte";
  import type { AnswerKind } from "./api";
  import { echo, readAnswer } from "./answer";
  import { loadMathfield } from "./mathlive";
  import MathText from "./MathText.svelte";

  /**
   * One answer box (design Q4): numbers and fractions as plain text with a live
   * "3/7 ≈ 0.4286" echo, choices on keys 1–4, and algebra in a MathLive field whose
   * LaTeX goes to SymPy on the backend.
   */
  let {
    kind,
    choices = [],
    disabled = false,
    busy = false,
    label = "Your answer",
    onsubmit,
  }: {
    kind: AnswerKind;
    choices?: string[];
    disabled?: boolean;
    busy?: boolean;
    label?: string;
    onsubmit: (answer: string) => void;
  } = $props();
  const pane = useWorkspaceContext();

  let text = $state("");
  let picked = $state<number | null>(null);
  let flash = $state<"" | "ok" | "bad">("");
  let input = $state<HTMLInputElement>();
  let field = $state<HTMLElement & { value: string; focus(): void }>();
  let fieldReady = $state(false);
  let fieldError = $state("");
  const reading = $derived(readAnswer(text));

  /** Flashes the box green or red for a verdict; null (undecided) leaves it. */
  export async function mark(correct: boolean | null, choice?: number) {
    if (choice !== undefined) picked = choice;
    flash = "";
    await tick();
    if (correct !== null) requestAnimationFrame(() => (flash = correct ? "ok" : "bad"));
  }
  export function focus() {
    (kind === "expression" ? field : input)?.focus();
  }

  function submit(event?: Event) {
    event?.preventDefault();
    if (disabled || busy) return;
    const answer = kind === "expression" ? (field?.value ?? "").trim() : text.trim();
    if (answer) onsubmit(answer);
  }
  function choose(index: number) {
    if (disabled || busy || !choices[index]) return;
    picked = index;
    onsubmit(choices[index]);
  }
  function onkey(event: KeyboardEvent) {
    if (kind !== "choice" || !pane.active || event.ctrlKey || event.metaKey || event.altKey) return;
    if ((event.target as HTMLElement | null)?.closest("input,textarea,select,[contenteditable=true],math-field")) return;
    const index = Number(event.key) - 1;
    if (index >= 0 && index < Math.min(4, choices.length)) {
      event.preventDefault();
      choose(index);
    }
  }

  onMount(() => {
    if (kind !== "expression") return;
    loadMathfield()
      .then(() => (fieldReady = true))
      .catch(() => (fieldError = "The math field could not load; reload the page to try again."));
  });
</script>

<svelte:window onkeydown={onkey} />

{#if kind === "choice"}
  <div class="choices" role="group" aria-label="Choices">
    {#each choices as choice, index (index)}
      <button type="button" class="choice" class:ok={picked === index && flash === "ok"} class:bad={picked === index && flash === "bad"}
        aria-pressed={picked === index} disabled={disabled || busy} onclick={() => choose(index)}>
        {#if index < 4}<kbd>{index + 1}</kbd>{/if}<MathText text={choice} inline />
      </button>
    {/each}
  </div>
{:else if kind === "expression"}
  <!-- Enter is caught before MathLive so it checks instead of committing the field. -->
  <form class="answer expression" class:ok={flash === "ok"} class:bad={flash === "bad"} onsubmit={submit}
    onkeydowncapture={(event) => { if (event.key === "Enter" && (event.target as HTMLElement).tagName === "MATH-FIELD") submit(event); }}>
    {#if fieldReady}
      <math-field bind:this={field} class="field" aria-label={label} math-virtual-keyboard-policy="auto" read-only={disabled || undefined}
        oninput={() => (flash = "")}></math-field>
    {:else}
      <span class="hint" role="status">{fieldError || "Loading the math field…"}</span>
    {/if}
    <Button size="sm" type="submit" disabled={disabled || busy || !fieldReady}>{busy ? "Checking…" : "Check ↵"}</Button>
  </form>
{:else}
  <form class="answer" class:ok={flash === "ok"} class:bad={flash === "bad"} onsubmit={submit}>
    <input bind:this={input} bind:value={text} aria-label={label} autocomplete="off" spellcheck="false"
      {disabled} oninput={() => (flash = "")} placeholder={kind === "fraction" ? "3/7" : "0.25 or 1/4"} />
    <span class="echo" aria-live="polite" data-echo>
      {#if !text.trim()}<span class="hint">{kind === "fraction" ? "fraction, e.g. 3/7" : "number, fraction or %"}</span>
      {:else if !reading}<span class="hint">can't read “{text.trim()}” yet</span>
      {:else}{echo(reading)}{/if}
    </span>
    <Button size="sm" type="submit" disabled={disabled || busy || !reading}>{busy ? "Checking…" : "Check ↵"}</Button>
  </form>
{/if}

<style>
  .answer {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    box-sizing: border-box;
    padding: 8px 8px 8px 14px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: color-mix(in srgb, var(--solid-void, var(--surface)) 60%, transparent);
  }
  .answer:focus-within {
    border-color: var(--accent);
  }
  .answer.ok {
    animation: ok 0.9s ease-out;
  }
  .answer.bad {
    animation: bad 0.9s ease-out;
  }
  @keyframes ok {
    0% { border-color: var(--success); background: color-mix(in srgb, var(--success) 28%, transparent); }
    100% { border-color: var(--success); }
  }
  @keyframes bad {
    0% { border-color: var(--error); background: color-mix(in srgb, var(--error) 28%, transparent); transform: translateX(-4px); }
    20% { transform: translateX(4px); }
    40% { transform: translateX(-2px); }
    60% { transform: none; }
    100% { border-color: var(--error); }
  }
  input {
    flex: 0 1 170px;
    min-width: 0;
    padding: 6px 0;
    border: 0;
    outline: none;
    background: transparent;
    color: var(--ink);
    font: 20px var(--font-mono);
  }
  .echo {
    display: flex;
    flex: 1;
    align-items: center;
    min-width: 0;
    min-height: 36px;
    padding-left: 12px;
    border-left: 1px solid var(--hairline);
    color: var(--ink);
    font: 17px var(--font-mono);
    white-space: nowrap;
  }
  .hint {
    overflow: hidden;
    color: var(--subtle);
    font: 12px var(--font-mono);
    text-overflow: ellipsis;
  }
  .field {
    flex: 1;
    min-width: 0;
    min-height: 40px;
    padding: 4px 6px;
    border: 0;
    outline: none;
    background: transparent;
    color: var(--ink);
    font-size: 20px;
    --caret-color: var(--accent);
    --selection-background-color: color-mix(in srgb, var(--accent) 30%, transparent);
    --contains-highlight-background-color: color-mix(in srgb, var(--accent) 12%, transparent);
  }
  .expression .hint {
    flex: 1;
  }
  .choices {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
    gap: 10px;
    width: 100%;
  }
  .choice {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    min-height: 50px;
    padding: 8px 10px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: transparent;
    color: var(--ink);
    font-size: 17px;
    cursor: pointer;
  }
  .choice:hover:not(:disabled) {
    border-color: var(--accent);
  }
  .choice:disabled {
    cursor: default;
  }
  .choice kbd {
    color: var(--subtle);
    font: 11px var(--font-mono);
  }
  .choice.ok {
    border-color: var(--success);
    background: color-mix(in srgb, var(--success) 16%, transparent);
  }
  .choice.bad {
    border-color: var(--error);
    background: color-mix(in srgb, var(--error) 16%, transparent);
  }
  @media (prefers-reduced-motion: reduce) {
    .answer.ok,
    .answer.bad {
      animation: none;
      border-color: var(--tone, var(--accent));
    }
    .answer.ok { --tone: var(--success); }
    .answer.bad { --tone: var(--error); }
  }
</style>
