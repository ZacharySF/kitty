/**
 * The running notifier behind the toast stack, the bell and quiet hours (Q60). It polls
 * every reminder source the active modules provide and runs `step` over what is due.
 * It lives in the page, so it only checks while kitty is open: in the desktop app,
 * **Keep running on close** keeps the hidden window (and this notifier) running.
 */
import { api } from "./api";
import { buildStore, onBuildChange } from "./build.svelte";
import {
  DEFAULT_QUIET, QUIET_EVENT, SYSTEM_KEY, calendarNotice, digestLine, emptyLedger, jobNotice, readLedger, quietOf, step,
  writeLedger, type CalendarReminder, type JobReminder, type Ledger, type Notice, type QuietHours,
} from "./notify";

export type Toast = { id: number; notice: Notice | null; digest: Notice[] | null; left: number };
/** How long a toast stays before it leaves for the notification center. */
export const TOAST_MS = 8000;
const MAX_TOASTS = 4;
const POLL_MS = 30_000;
const CALENDAR_SNOOZE_MS = 10 * 60_000;

class Notifier {
  visible = $state<Notice[]>([]);
  toasts = $state<Toast[]>([]);
  ledger = $state<Ledger>(emptyLedger());
  quiet = $state<QuietHours>(DEFAULT_QUIET);
  system = $state(false);
  error = $state("");
  drawer = $state(false);
  readonly unread = $derived(this.visible.filter(n => !this.ledger.read.includes(n.key)).length);
  readonly heldNotices = $derived(this.visible.filter(n => this.ledger.held.includes(n.key)));
  private sources = { calendar: false, job: false };
  private nextToast = 1;
  private busy = false;

  /** Starts polling the sources the active modules provide; returns the stop function. */
  start(sources: { calendar: boolean; job: boolean }): () => void {
    this.sources = sources;
    this.ledger = readLedger(localStorage);
    this.quiet = quietOf(buildStore.build.ui.notifications.quiet);
    // Before notifications covered every source the switch was calendar-only.
    this.system = (localStorage.getItem(SYSTEM_KEY) ?? localStorage.getItem("kitty-calendar-notifications")) === "true" && "Notification" in window && Notification.permission === "granted";
    void this.refresh();
    const timer = setInterval(() => void this.refresh(), POLL_MS);
    const wake = () => void this.refresh();
    // Quiet hours are a build preference: Settings' dial while it moves, then every build change.
    const quiet = (event: Event) => {
      this.quiet = (event as CustomEvent<QuietHours | undefined>).detail ?? quietOf(buildStore.build.ui.notifications.quiet);
      void this.refresh();
    };
    const stopBuild = onBuildChange((next, previous) => {
      if (JSON.stringify(next.ui.notifications.quiet) !== JSON.stringify(previous.ui.notifications.quiet))
        window.dispatchEvent(new CustomEvent(QUIET_EVENT));
    });
    window.addEventListener("focus", wake);
    window.addEventListener(QUIET_EVENT, quiet);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", wake);
      window.removeEventListener(QUIET_EVENT, quiet);
      stopBuild();
    };
  }

  async refresh(): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    try {
      const [calendar, job] = await Promise.all([
        this.sources.calendar ? api<{ items: CalendarReminder[] }>("/calendar/reminders") : null,
        this.sources.job ? api<{ due: JobReminder[] }>("/reminders") : null,
      ]);
      const notices = [...(calendar?.items.map(calendarNotice) ?? []), ...(job?.due.map(jobNotice) ?? [])];
      const result = step(this.ledger, notices, this.quiet, new Date());
      this.visible = result.visible;
      this.save(result.ledger);
      for (const notice of result.toasts) this.toast({ notice, digest: null });
      if (result.digest) this.toast({ notice: null, digest: result.digest });
      this.error = "";
    } catch (cause) {
      this.error = (cause as Error).message;
    } finally {
      this.busy = false;
    }
  }

  private save(ledger: Ledger) {
    this.ledger = ledger;
    try { writeLedger(localStorage, ledger); } catch { /* private mode: state lasts for this page */ }
  }

  private toast(toast: Omit<Toast, "id" | "left">) {
    this.toasts = [...this.toasts, { ...toast, id: this.nextToast++, left: TOAST_MS }].slice(-MAX_TOASTS);
    if (!this.system) return;
    const title = toast.notice ? toast.notice.title : "Morning digest";
    const body = toast.notice ? toast.notice.body : `Held during quiet hours: ${digestLine(toast.digest ?? [])}`;
    try {
      new Notification(`kitty! · ${title}`, { body, tag: toast.notice?.key ?? "kitty-digest" });
    } catch {
      this.error = "System notifications are unavailable here.";
    }
  }

  dismiss(id: number) {
    this.toasts = this.toasts.filter(toast => toast.id !== id);
  }

  read(...keys: string[]) {
    this.save({ ...this.ledger, read: [...new Set([...this.ledger.read, ...keys])] });
  }

  markAllRead() {
    this.read(...this.visible.map(n => n.key));
  }

  /** Calendar reminders come back in ten minutes; job-search reminders move a day (the reminders API). */
  async snooze(notice: Notice) {
    this.toasts = this.toasts.filter(toast => toast.notice?.key !== notice.key);
    try {
      if (notice.target.kind === "job") await api(`/reminders/${notice.target.id}/snooze`, {});
      else this.save({ ...this.ledger, snoozed: { ...this.ledger.snoozed, [notice.key]: new Date(Date.now() + CALENDAR_SNOOZE_MS).toISOString() } });
      this.read(notice.key);
      await this.refresh();
    } catch (cause) {
      this.error = (cause as Error).message;
    }
  }

  /** Dismisses a calendar reminder or marks a job-search reminder done. */
  async clear(notice: Notice) {
    const { target } = notice;
    try {
      if (target.kind === "job") await api(`/reminders/${target.id}/complete`, {});
      else await api("/calendar/reminders/dismiss", { event_id: target.event_id, occurrence: target.occurrence, minutes: target.minutes });
      this.toasts = this.toasts.filter(toast => toast.notice?.key !== notice.key);
      await this.refresh();
    } catch (cause) {
      this.error = (cause as Error).message;
    }
  }

  async toggleSystem() {
    if (this.system) {
      this.system = false;
      localStorage.setItem(SYSTEM_KEY, "false");
      return;
    }
    if (!("Notification" in window)) {
      this.error = "System notifications are unavailable here.";
      return;
    }
    try {
      this.system = (await Notification.requestPermission()) === "granted";
      localStorage.setItem(SYSTEM_KEY, String(this.system));
      if (!this.system) this.error = "Notification permission was not granted.";
    } catch {
      this.error = "Notification permission cannot be requested here.";
    }
  }
}

export const notifier = new Notifier();
