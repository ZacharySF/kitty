<script lang="ts">
  import { onMount } from "svelte";
  import { Button } from "@nunera/bracket";
  import { api } from "./api";
  type Reminder = {
    event_id: string;
    occurrence: string;
    minutes: number;
    title: string;
    due: string;
  };
  let reminders = $state<Reminder[]>([]);
  let enabled = $state(false);
  let error = $state("");
  let supported = $state(false);
  let native = $state(false);
  const seen = new Set<string>();
  let refreshing = false;
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      reminders = (await api<{ items: Reminder[] }>("/calendar/reminders"))
        .items;
      enabled = localStorage.getItem("kitty-calendar-notifications") === "true";
      if (enabled && supported && Notification.permission === "granted") {
        for (const item of reminders) {
          const key = `${item.event_id}:${item.occurrence}:${item.minutes}`;
          if (!seen.has(key)) {
            try {
              new Notification("kitty! calendar reminder", {
                body: item.title,
                tag: key,
              });
              seen.add(key);
            } catch {
              error = "System notifications are unavailable here.";
            }
          }
        }
      }
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      refreshing = false;
    }
  }
  async function toggle() {
    if (enabled) {
      enabled = false;
      localStorage.removeItem("kitty-calendar-notifications");
      return;
    }
    if (!supported) {
      error = "System notifications are unavailable here.";
      return;
    }
    try {
      enabled = (await Notification.requestPermission()) === "granted";
      localStorage.setItem("kitty-calendar-notifications", String(enabled));
      if (!enabled) error = "Notification permission was not granted.";
      await refresh();
    } catch {
      error = "Notification permission cannot be requested here.";
    }
  }
  async function dismiss(item: Reminder) {
    try {
      await api("/calendar/reminders/dismiss", item);
      await refresh();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }
  onMount(() => {
    supported = "Notification" in window;
    native = "__TAURI_INTERNALS__" in window;
    void refresh();
    const interval = setInterval(() => void refresh(), 30000);
    const wake = () => void refresh();
    window.addEventListener("focus", wake);
    return () => {
      clearInterval(interval);
      window.removeEventListener("focus", wake);
    };
  });
</script>

{#if reminders.length || error}
  <aside class="calendar-reminders" aria-label="Calendar reminders">
    <header>
      <strong>Reminders</strong>
      <Button size="sm" variant="ghost" onclick={toggle}
        title={native ? "Desktop delivery depends on system permissions; reminders show only while kitty runs." : undefined}
        aria-pressed={enabled}>{enabled ? "Notifications on" : "Notifications off"}</Button>
    </header>
    {#if error}<p role="status">
        {error}<button
          class="close"
          aria-label="Dismiss message"
          onclick={() => (error = "")}>×</button
        >
      </p>{/if}
    {#each reminders as item}
      <div role="status">
        <span
          ><strong>{item.title}</strong><small
            >{new Date(item.due).toLocaleString()}</small
          ></span
        ><Button size="sm" onclick={() => dismiss(item)}>Dismiss</Button>
      </div>
    {/each}
  </aside>
{/if}

<style>
  .calendar-reminders {
    background: var(--surface-raised);
    border: 1px solid var(--warning);
    border-radius: 0.6rem;
    padding: 0.7rem;
    color: var(--ink);
    margin: 0.6rem;
    max-height: 18rem;
    overflow: auto;
  }
  header,
  .calendar-reminders > div {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.7rem;
    margin: 0.3rem 0;
  }
  .close {
    background: none;
    border: 0;
    color: inherit;
    font: inherit;
    cursor: pointer;
    margin-left: 0.4rem;
  }
  small {
    display: block;
    color: var(--muted);
    font-size: 0.75rem;
  }
  p {
    display: flex;
    justify-content: space-between;
    font-size: 0.8rem;
    margin: 0.4rem 0;
    color: var(--muted);
  }
</style>
