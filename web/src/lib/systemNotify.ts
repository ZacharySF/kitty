/**
 * System notifications, one path for every source. In the desktop app the shell sends
 * them to the Linux notification server itself (`/api/desktop/notify`), so there is no
 * permission to ask for and a click comes back as a `kitty:notice` event with the
 * notice's key and the action (`default` opens it; `snooze` and `done` are its buttons). In a
 * browser tab they are Web Notifications, which need the browser's permission.
 */
import { api } from "./api";

export const NOTICE_EVENT = "kitty:notice";
export type SystemNotice = { key: string; title: string; body: string; actions: boolean };

let desktop: Promise<boolean> | null = null;
/** Whether this page runs in the desktop app (asked once). */
export function inDesktopApp(): Promise<boolean> {
  desktop ??= api<{ available: boolean }>("/desktop").then((state) => state.available, () => false);
  return desktop;
}

/** Whether a system notification can be sent right now without asking for anything. */
export async function systemReady(): Promise<boolean> {
  return (await inDesktopApp()) || ("Notification" in window && Notification.permission === "granted");
}

/** Sends one notice; false when the system could not take it. */
export async function sendSystem(notice: SystemNotice): Promise<boolean> {
  const title = notice.title.replace(/\s+/g, " ").trim().slice(0, 120);
  const body = notice.body.slice(0, 400);
  try {
    if (await inDesktopApp()) {
      await api("/desktop/notify", { key: notice.key.slice(0, 200), title, body, actions: notice.actions });
      return true;
    }
    if (!("Notification" in window) || Notification.permission !== "granted") return false;
    const shown = new Notification(`rika! · ${title}`, { body, tag: notice.key });
    shown.onclick = () => {
      window.focus();
      window.dispatchEvent(new CustomEvent(NOTICE_EVENT, { detail: { key: notice.key, action: "default" } }));
    };
    return true;
  } catch {
    return false;
  }
}

/** Takes down the desktop's notices under these keys (read or cleared in the app). */
export async function closeSystem(keys: string[]): Promise<void> {
  if (!keys.length || !(await inDesktopApp())) return;
  try { await api("/desktop/notify/close", { keys: keys.slice(0, 50) }); } catch { /* already gone */ }
}
