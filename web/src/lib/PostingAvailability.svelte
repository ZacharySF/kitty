<script lang="ts">
  import { Button } from "@nunera/bracket";
  import { api, type TaskRecord } from "./api";
  import TaskStatus from "./TaskStatus.svelte";
  import { useWorkspaceContext } from "./workspaceContext.svelte";

  let { jobIds, label = "Check availability", demo = false, oncomplete }: {
    jobIds: number[];
    label?: string;
    demo?: boolean;
    oncomplete?: (unavailableIds: number[]) => void | Promise<void>;
  } = $props();
  const pane = useWorkspaceContext();
  let task = $state<TaskRecord | null>(null);
  let starting = $state(false);
  let error = $state("");
  let summary = $state("");
  const ids = $derived([...new Set(jobIds)]);
  const checking = $derived(starting || task?.status === "running");

  async function complete(result: TaskRecord) {
    task = null;
    try {
      if (result.status !== "completed") throw new Error(result.error || "Availability check failed");
      const data = result.result as { postings: { job_id: number; status: string }[] };
      if (!Array.isArray(data?.postings)) throw new Error("Availability check returned incomplete results");
      const unavailable = data.postings.filter(item => item.status === "closed");
      const open = data.postings.filter(item => item.status === "open").length;
      await oncomplete?.(unavailable.map(item => item.job_id));
      const uncertain = data.postings.length - open - unavailable.length;
      summary = `${open} open · ${unavailable.length} unavailable${uncertain ? ` · ${uncertain} unconfirmed` : ""}`;
    } catch (cause) {
      error = (cause as Error).message;
    }
  }

  async function check() {
    if (!pane.active || checking || demo || !ids.length || ids.length > 100) return;
    starting = true;
    error = summary = "";
    try {
      const result = await api<{ task: TaskRecord }>("/postings/refresh", {
        confirm: true, limit: ids.length, job_ids: [...ids],
      });
      if (result.task.status === "running") task = result.task;
      else await complete(result.task);
    } catch (cause) {
      error = (cause as Error).message;
    } finally {
      starting = false;
    }
  }
</script>

<div class="availability-check" aria-busy={checking}>
  <Button onclick={check} disabled={demo || checking || !ids.length || ids.length > 100}>
    {checking ? "Checking…" : label}
  </Button>
  {#if ids.length > 100}<span class="muted">Check up to 100 at once.</span>{/if}
  {#if task}<TaskStatus {task} compact oncomplete={result => void complete(result)} />{/if}
  {#if summary}<span role="status">{summary}</span>{/if}
  {#if error}<span class="error" role="alert">{error}</span>{/if}
</div>

<style>
  .availability-check { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; font-size: .8rem; }
  .error { color: var(--error); }
</style>
