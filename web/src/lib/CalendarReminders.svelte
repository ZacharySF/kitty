<script lang="ts">
  import { onMount } from "svelte";
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
              error =
                "System notification delivery is unavailable in this runtime. In-app reminders remain active.";
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
      error =
        "This browser/WebKit runtime does not expose notifications. In-app reminders remain active.";
      return;
    }
    try {
      enabled = (await Notification.requestPermission()) === "granted";
      localStorage.setItem("kitty-calendar-notifications", String(enabled));
      if (!enabled)
        error =
          "System notification permission was not granted. Use browser/system permissions to change it; in-app reminders remain active.";
      await refresh();
    } catch {
      error =
        "Notification permission cannot be requested in this runtime. In-app reminders remain active.";
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
  <aside class="calendar-reminders" aria-label="Global calendar reminders">
    <header>
      <strong>Calendar reminders</strong><button onclick={toggle}
        >{enabled ? "Disable notifications" : "Enable notifications"}</button
      >
    </header>
    {#if error}<p role="status">
        {error}<button
          aria-label="Dismiss notification message"
          onclick={() => (error = "")}>×</button
        >
      </p>{/if}
    {#if native}<p class="hint">
        Linux WebKit notification support depends on runtime/system permissions.
        In-app reminders work while kitty runs, including other sections;
        suspended or exited apps cannot guarantee alarm delivery.
      </p>{/if}
    {#each reminders as item}
      <div role="status">
        <span
          ><strong>{item.title}</strong><small
            >{new Date(item.due).toLocaleString()}</small
          ></span
        ><button onclick={() => dismiss(item)}>Dismiss</button>
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
  button {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 0.4rem;
    padding: 0.35rem 0.55rem;
    color: var(--ink);
    font: inherit;
    font-size: 0.8rem;
    cursor: pointer;
  }
  small {
    display: block;
    color: var(--muted);
    font-size: 0.75rem;
  }
  .hint,
  p {
    font-size: 0.8rem;
    margin: 0.4rem 0;
    color: var(--muted);
  }
</style>
