<script lang="ts">
  import { api, type TaskRecord } from "./api";
  let {
    task,
    oncomplete,
    compact = false,
  }: { task: TaskRecord | null; oncomplete?: (task: TaskRecord) => void; compact?: boolean } =
    $props();
  let current = $state<TaskRecord | null>(null);
  let error = $state("");
  let retry = $state(0);
  $effect(() => {
    const initial = task;
    void retry;
    current = initial;
    error = "";
    if (!initial || initial.status !== "running") return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function inspect() {
      try {
        const result = await api<TaskRecord>(`/tasks/${initial!.id}`);
        if (stopped) return;
        current = result;
        if (result.status === "running") timer = setTimeout(inspect, 1500);
        else oncomplete?.(result);
      } catch (cause) {
        if (!stopped) error = (cause as Error).message;
      }
    }
    void inspect();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  });
</script>

{#if current}
  <section
    class="task-status"
    class:compact
    aria-label="Background operation"
    aria-live="polite"
  >
    {#if !compact}<div class="task-heading">
      <strong>{current.kind.replaceAll("-", " ")}</strong><span class="badge"
        >{current.status}</span
      >
    </div>{/if}
    {#if current.status === "running"}<p class="muted">
        {compact ? "Checking public postings…" : "Working locally. You can keep using this page; it will show the result when finished."}
      </p>{/if}
    {#if current.error}<p class="error-banner" role="alert">
        {current.error}
      </p>{/if}
    {#if error}<p class="error-banner" role="alert">{error}</p>
      <button class="button" onclick={() => (retry += 1)}
        >Refresh operation</button
      >{/if}
    {#if !compact && current.result !== null}<div class="result">
        <span class="section-head">Operation result</span>
        <pre>{typeof current.result === "string"
            ? current.result
            : JSON.stringify(current.result, null, 2)}</pre>
      </div>{/if}
  </section>
{/if}

<style>
  /* Q2: a section inside its page's surface, split off by a hairline, never a nested box. */
  .task-status {
    padding: 12px 0 0;
    margin: 16px 0;
    border-top: 1px solid var(--hairline);
    font-size: 13px;
  }
  .task-status.compact { border: 0; padding: 0; margin: 0; background: transparent; }
  .compact p { margin: 0; }
  .task-heading {
    display: flex;
    gap: 12px;
    align-items: center;
    justify-content: space-between;
    text-transform: capitalize;
  }
  p {
    margin: 12px 0 0;
    line-height: 1.6;
  }
  .result {
    margin-top: 12px;
  }
  pre {
    margin: 4px 0 0;
    font: 12px/1.6 var(--font-mono);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 350px;
    overflow: auto;
  }
</style>
