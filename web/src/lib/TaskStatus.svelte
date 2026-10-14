<script lang="ts">
  import { api, type TaskRecord } from "./api";
  let {
    task,
    oncomplete,
  }: { task: TaskRecord | null; oncomplete?: (task: TaskRecord) => void } =
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
    aria-label="Background operation"
    aria-live="polite"
  >
    <div class="task-heading">
      <strong>{current.kind.replaceAll("-", " ")}</strong><span class="badge"
        >{current.status}</span
      >
    </div>
    {#if current.status === "running"}<p class="muted">
        Working locally. You can keep using this page; it will show the result
        when finished.
      </p>{/if}
    {#if current.error}<p class="error-banner" role="alert">
        {current.error}
      </p>{/if}
    {#if error}<p class="error-banner" role="alert">{error}</p>
      <button class="button" onclick={() => (retry += 1)}
        >Refresh operation</button
      >{/if}
    {#if current.result !== null}<details open>
        <summary>Operation result</summary>
        <pre>{typeof current.result === "string"
            ? current.result
            : JSON.stringify(current.result, null, 2)}</pre>
      </details>{/if}
  </section>
{/if}

<style>
  .task-status {
    border: 1px solid var(--border);
    border-radius: 9px;
    padding: 16px;
    margin: 16px 0;
    background: var(--surface-raised);
    font-size: 13px;
  }
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
  details {
    margin-top: 12px;
  }
  summary {
    cursor: pointer;
  }
  pre {
    font: 12px/1.6 monospace;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 350px;
    overflow: auto;
  }
</style>
