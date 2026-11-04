<script lang="ts" module>
  import type { AnswerKind, Verdict } from "./api";

  export type FeedbackLine = { verdict: Verdict; answer: string; kind: AnswerKind };
  type Tone = "equal" | "mistake" | "different" | "undecided";

  /** The line's tone: the checker's kind, or one read from `correct` for exact answers. */
  export function toneOf(verdict: Verdict): Tone {
    return verdict.kind ?? (verdict.correct === null ? "undecided" : verdict.correct ? "equal" : "different");
  }
  const TITLE: Record<Tone, string> = {
    equal: "Correct",
    mistake: "Not quite · common mistake",
    different: "Not quite",
    undecided: "Couldn't decide",
  };
  const ICON: Record<Tone, string> = { equal: "✓", mistake: "✗", different: "✗", undecided: "?" };
</script>

<script lang="ts">
  import MathText from "./MathText.svelte";
  import { asMath } from "./tex";

  /** Feedback lines (design Q4 B), newest first; older ones fade. */
  let { lines }: { lines: FeedbackLine[] } = $props();
</script>

<div class="feed" aria-live="polite">
  {#each lines as line, index (lines.length - index)}
    {@const tone = toneOf(line.verdict)}
    <div class="line {tone}" class:old={index > 0} data-tone={tone}>
      <span class="icon" aria-hidden="true">{ICON[tone]}</span>
      <div class="body">
        <p>
          <b>{TITLE[tone]}</b>
          <span class="wrote">(you wrote <MathText inline text={line.kind === "expression" ? `$${line.answer}$` : line.answer.replace(/\$/g, "\\$")} />)</span>
        </p>
        {#if line.verdict.feedback}<p class="why"><MathText inline text={line.verdict.feedback} /></p>{/if}
        {#if tone === "undecided" && !line.verdict.feedback}<p class="why">Nothing was marked: check it yourself or write it another way.</p>{/if}
        {#if line.verdict.canonical && (line.verdict.correct || line.verdict.revealed)}
          <p class="why answer">{line.verdict.correct ? "Exactly" : "The answer"}: <MathText inline text={asMath(line.verdict.canonical)} /></p>
        {/if}
      </div>
    </div>
  {/each}
</div>

<style>
  .feed {
    display: grid;
    gap: 8px;
    width: 100%;
  }
  .line {
    --tone: var(--error);
    display: flex;
    gap: 10px;
    padding: 9px 12px;
    border: 1px solid color-mix(in srgb, var(--tone) 55%, transparent);
    border-radius: 10px;
    background: color-mix(in srgb, var(--tone) 10%, transparent);
    text-align: left;
  }
  .line.equal {
    --tone: var(--success);
  }
  .line.undecided {
    --tone: var(--warning);
  }
  .line.old {
    opacity: 0.55;
  }
  .icon {
    color: var(--tone);
    font: 700 15px/1.4 var(--font-mono);
  }
  .body {
    display: grid;
    gap: 3px;
    min-width: 0;
  }
  p {
    margin: 0;
    color: var(--ink);
    font-size: 13.5px;
  }
  b {
    color: var(--tone);
  }
  .wrote,
  .why {
    color: var(--muted);
  }
  .why {
    font-size: 12.5px;
  }
  .answer {
    color: var(--ink);
  }
</style>
