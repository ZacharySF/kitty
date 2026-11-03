<script lang="ts">
  import { onDestroy, tick } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api } from "../api";
  import TimerRing from "../TimerRing.svelte";
  import { useWorkspaceContext } from "../workspaceContext.svelte";
  import type { Id, SprintDeal, SprintHistory, SprintRecord } from "./api";
  import type { MathSettings } from "./settings";
  import Spark from "./Spark.svelte";
  import { choose, clock, current, finish, perMinute, remainingSeconds, score, startSprint, trend, typed, type Sprint, type SprintResult } from "./sprint";

  /**
   * The mental-math sprint (design Q2 A): a big centred prompt inside a countdown ring.
   * Typed: the answer is taken the instant it is right. Multiple choice: keys 1–4,
   * +1 right, −1 wrong. Time up records the run and shows the results with the trend.
   */
  let {
    settings,
    history,
    onrecorded,
    onsettings,
    onstart,
  }: {
    settings: MathSettings;
    history: SprintHistory | null;
    onrecorded: () => Promise<void> | void;
    onsettings: () => void;
    onstart?: () => void;
  } = $props();
  const pane = useWorkspaceContext();

  let status = $state<"idle" | "dealing" | "running" | "done">("idle");
  let run = $state<Sprint | null>(null);
  let sprintId: Id | null = null;
  let now = $state(performance.now());
  let answer = $state("");
  let flash = $state<"" | "right" | "wrong">("");
  let result = $state<SprintResult | null>(null);
  let recorded = $state(false);
  let error = $state("");
  let input = $state<HTMLInputElement>();
  let timer: ReturnType<typeof setInterval> | undefined;
  let flashTimer: ReturnType<typeof setTimeout> | undefined;

  const duration = $derived(run?.duration_s ?? settings.duration_s);
  const mode = $derived(run?.mode ?? settings.mode);
  const remaining = $derived(run && status === "running" ? remainingSeconds(run, now) : status === "done" ? 0 : settings.duration_s);
  const label = $derived(mode === "mc" ? "multiple choice" : "typed");
  const past = $derived(
    (history?.sprints ?? [])
      .filter((item: SprintRecord) => item.mode === mode && item.duration_s === duration)
      .sort((a, b) => a.finished_at.localeCompare(b.finished_at))
      .map((item) => item.score),
  );
  const shape = $derived(trend(past));

  async function start() {
    if (status === "dealing" || status === "running") return;
    error = "";
    status = "dealing";
    try {
      const deal = await api<SprintDeal>("/math/sprints", { mode: settings.mode, duration_s: settings.duration_s, ops: settings.ops });
      sprintId = deal.sprint_id;
      run = startSprint(settings.mode, settings.duration_s, deal.problems, performance.now());
    } catch (cause) {
      error = (cause as Error).message;
      status = "idle";
      return;
    }
    answer = "";
    result = null;
    recorded = false;
    status = "running";
    now = performance.now();
    clearInterval(timer);
    timer = setInterval(() => {
      now = performance.now();
      if (run && remainingSeconds(run, now) <= 0) void end();
    }, 100);
    onstart?.();
    await tick();
    input?.focus({ preventScroll: true });
  }

  async function end() {
    if (!run || status !== "running") return;
    clearInterval(timer);
    result = finish(run, performance.now());
    status = "done";
    try {
      await api(`/math/sprints/${encodeURIComponent(String(sprintId))}/result`, result);
      await onrecorded();
      recorded = true;
    } catch (cause) {
      error = `The run was not saved: ${(cause as Error).message}`;
    }
  }

  /** Stop abandons the run: nothing is recorded. */
  function stop() {
    clearInterval(timer);
    run = null;
    status = "idle";
  }

  function pulse(kind: "right" | "wrong") {
    flash = kind;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => (flash = ""), 260);
  }

  function onType() {
    if (run && status === "running" && typed(run, answer, performance.now())) {
      answer = "";
      pulse("right");
    }
  }

  function pick(value: number) {
    if (!run || status !== "running") return;
    const verdict = choose(run, value, performance.now());
    if (verdict) pulse(verdict);
  }

  function onkey(event: KeyboardEvent) {
    if (!pane.active || event.ctrlKey || event.metaKey || event.altKey) return;
    if ((event.target as HTMLElement | null)?.closest("input,textarea,select,[contenteditable=true],[role=dialog]")) return;
    if (status === "running" && run?.mode === "mc") {
      const choices = current(run).choices ?? [];
      const index = Number(event.key) - 1;
      if (index >= 0 && index < choices.length) {
        event.preventDefault();
        pick(choices[index]);
      }
    } else if (status === "done" && event.key.toLowerCase() === "r" && !event.repeat) {
      event.preventDefault();
      void start();
    }
  }

  onDestroy(() => {
    clearInterval(timer);
    clearTimeout(flashTimer);
  });
</script>

<svelte:window onkeydown={onkey} />

<div class="sprint" class:right={flash === "right"} class:wrong={flash === "wrong"} role="group" aria-label="Mental math sprint">
  {#if status === "done" && run && result}
    <div class="end">
      <span class="eyebrow">Time · {duration} s · {label}</span>
      <strong class="final" data-score={result.score}>{result.score}</strong>
      <span class="sub">
        {#if mode === "mc"}{run.right} right − {run.wrong} wrong{:else}{run.right} correct{/if}
        · {perMinute(run.right, duration).toFixed(1)} per minute
        {#if recorded && shape.newBest}· <em>new best</em>{:else if recorded && past.length > 1}· best {shape.best}{/if}
      </span>
      {#if recorded}
        <div class="trend">
          <Spark values={past} w={380} h={76} label="Sprint score trend" />
          <span class="caption">{past.length} run{past.length === 1 ? "" : "s"} at {duration} s, {label}{history?.streak_days ? ` · ${history.streak_days}-day streak` : ""}</span>
        </div>
      {:else if !error}<span class="caption" role="status">Saving the run…</span>{/if}
      <div class="row">
        <Button variant="solid" intent="primary" onclick={start}>Restart <kbd>R</kbd></Button>
        <Button variant="ghost" onclick={onsettings}>Settings</Button>
      </div>
    </div>
  {:else}
    <div class="dial">
      <TimerRing {remaining} total={duration} phase={status === "running" && remaining <= 5 ? "priming" : "focus"} label={`${clock(remaining)} left`} size={340} stroke={7} />
      <div class="face">
        <span class="clock" aria-live="off">{clock(remaining)}</span>
        {#if status === "running" && run}
          <span class="prompt" data-prompt>{current(run).q} =</span>
          {#if run.mode === "zetamac"}
            <input bind:this={input} class="typed" inputmode="numeric" autocomplete="off" aria-label="Answer" bind:value={answer} oninput={onType} />
          {:else}
            <span class="hint">press 1–4</span>
          {/if}
        {:else}
          <span class="prompt idle" aria-hidden="true">47 × 8 =</span>
          <Button variant="solid" intent="primary" size="lg" disabled={status === "dealing"} onclick={start}>{status === "dealing" ? "Dealing…" : "Start sprint"}</Button>
        {/if}
      </div>
    </div>
    {#if status === "running" && run}
      {#if run.mode === "mc"}
        <div class="choices" role="group" aria-label="Choices">
          {#each current(run).choices ?? [] as value, index (`${run.index}:${value}`)}
            <button type="button" class="choice" onclick={() => pick(value)}><kbd>{index + 1}</kbd>{value}</button>
          {/each}
        </div>
      {/if}
      <p class="score" aria-live="polite">
        {#if run.mode === "mc"}<b class="ok">{run.right} right</b> · <b class="bad">{run.wrong} wrong</b> · score <b data-live-score>{score(run)}</b>
        {:else}<b data-live-score>{run.right}</b> right{/if}
      </p>
      <button type="button" class="quit" onclick={stop}>Stop</button>
    {:else}
      <p class="caption">
        {settings.duration_s} s · {settings.mode === "mc" ? "Four choices: +1 right, −1 wrong." : "Type the answer; it's taken the instant it's right."}
      </p>
    {/if}
  {/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</div>

<style>
  .sprint {
    position: relative;
    display: grid;
    justify-items: center;
    align-content: center;
    gap: 16px;
    width: min(100%, 620px);
    box-sizing: border-box;
    padding: 24px;
    border: 1px solid var(--hairline);
    border-radius: 18px;
    background: color-mix(in srgb, var(--solid-panel, var(--surface)) 45%, transparent);
    transition: box-shadow 200ms ease;
  }
  .sprint.right {
    box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--success) 70%, transparent);
  }
  .sprint.wrong {
    box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--error) 75%, transparent);
  }
  .dial {
    position: relative;
    display: grid;
    place-items: center;
  }
  .dial > :global(*) {
    grid-area: 1 / 1;
  }
  .face {
    display: grid;
    justify-items: center;
    gap: 10px;
  }
  .clock {
    color: var(--muted);
    font: 500 15px var(--font-mono);
    letter-spacing: 0.08em;
  }
  .prompt {
    color: var(--ink);
    font: 600 46px/1.05 var(--font-mono);
    letter-spacing: -0.02em;
    white-space: nowrap;
  }
  .prompt.idle {
    color: var(--subtle);
  }
  .typed {
    width: 170px;
    padding: 6px 10px;
    border: 0;
    border-bottom: 2px solid var(--accent);
    border-radius: 0;
    background: transparent;
    color: var(--ink);
    font: 600 34px var(--font-mono);
    text-align: center;
    outline: none;
  }
  .hint,
  .caption {
    margin: 0;
    color: var(--muted);
    font-size: 13px;
    text-align: center;
  }
  .hint {
    color: var(--subtle);
    font: 500 12px var(--font-mono);
  }
  .choices {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 110px));
    gap: 10px;
  }
  .choice {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    height: 52px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: color-mix(in srgb, var(--solid-panel, var(--surface)) 80%, transparent);
    color: var(--ink);
    font: 600 21px var(--font-mono);
    cursor: pointer;
  }
  .choice:hover {
    border-color: var(--accent);
  }
  .choice kbd,
  .row :global(kbd) {
    color: var(--subtle);
    font: 500 11px var(--font-mono);
  }
  .score {
    margin: 0;
    color: var(--muted);
    font: 500 14px var(--font-mono);
  }
  .score b {
    color: var(--ink);
  }
  .score .ok {
    color: var(--success);
  }
  .score .bad {
    color: var(--error);
  }
  .quit {
    position: absolute;
    top: 12px;
    right: 14px;
    padding: 4px 10px;
    border: 1px solid var(--hairline);
    border-radius: 999px;
    background: transparent;
    color: var(--muted);
    font: 500 11px var(--font-mono);
    cursor: pointer;
  }
  .end {
    display: grid;
    justify-items: center;
    gap: 8px;
  }
  .eyebrow {
    color: var(--subtle);
    font: 500 10.5px var(--font-mono);
    letter-spacing: 0.12em;
    text-transform: uppercase;
  }
  .final {
    color: var(--ink);
    font: 600 104px/1 var(--font-mono);
  }
  .sub {
    color: var(--muted);
    font-size: 14px;
  }
  .sub em {
    color: var(--success);
    font-style: normal;
    font-weight: 600;
  }
  .trend {
    display: grid;
    justify-items: center;
    gap: 4px;
    margin: 10px 0 6px;
  }
  .row {
    display: flex;
    gap: 8px;
  }
  .error {
    margin: 0;
    color: var(--warning);
    font-size: 13px;
  }
  @media (prefers-reduced-motion: reduce) {
    .sprint {
      transition: none;
    }
  }
</style>
