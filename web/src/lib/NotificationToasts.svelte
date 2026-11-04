<script lang="ts">
  import { notifier, TOAST_MS, type Toast } from "./notifications.svelte";
  import { SOURCE_LABEL, digestLine, type Notice } from "./notify";
  import ToastStack from "./ui/ToastStack.svelte";

  let { onopen }: { onopen: (notice: Notice) => void } = $props();
  const TONE = { calendar: "var(--c1, var(--violet))", job: "var(--c4, var(--violet))", rika: "var(--c2, var(--violet))" } as const;

  function open(toast: Toast) {
    notifier.dismiss(toast.id);
    if (toast.notice) {
      notifier.read(toast.notice.key);
      onopen(toast.notice);
    } else notifier.drawer = true;
  }
  const when = (notice: Notice) => {
    const due = new Date(notice.due);
    return Number.isNaN(due.getTime()) ? "" : due.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };
</script>

<!-- Rika's reply stack sits closest to the bar; reminders stack above it. -->
<ToastStack items={notifier.toasts} total={TOAST_MS} label="New notifications" offset="var(--rika-toasts, 0px)"
  tone={(toast) => (toast.notice ? TONE[toast.notice.source] : "var(--violet)")} ondismiss={(toast) => notifier.dismiss(toast.id)}>
  {#snippet toast(toast)}
    {@const source = toast.notice?.source}
    <div class="top">
      <i class="dot" aria-hidden="true"></i>
      <span>{source === "rika" ? "Rika · strong match" : source ? SOURCE_LABEL[source] : "Quiet hours"}</span>
      {#if toast.notice}<span class="when">{when(toast.notice)}</span>{/if}
      <button class="x" aria-label="Dismiss notification" onclick={() => notifier.dismiss(toast.id)}>×</button>
    </div>
    {#if toast.notice}
      <strong>{toast.notice.title}</strong>
      <span class="body">{toast.notice.body}</span>
    {:else}
      <strong>Morning digest</strong>
      <span class="body">Held during quiet hours: {digestLine(toast.digest ?? [])}</span>
    {/if}
    <div class="acts">
      {#if toast.notice}
        <button class="pill" onclick={() => toast.notice && notifier.snooze(toast.notice)}
          >{toast.notice.source === "job" ? "Snooze a day" : "Snooze 10 min"}</button
        >
      {/if}
      <button class="pill primary" onclick={() => open(toast)}>Open</button>
    </div>
  {/snippet}
</ToastStack>
