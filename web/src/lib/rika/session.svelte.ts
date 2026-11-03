import { api } from "../api";
import { mergeEntries, rikaSocketUrl, type Entry, type History, type Id, type RikaAction, type RikaAgent, type RikaApproval, type RikaMessage, type StreamEvent } from "./rika";

/** How long a finished reply's toast stays before it leaves for the drawer. */
export const RIKA_TOAST_MS = 9000;
const MAX_TOASTS = 3;
const RETRY_MS = [500, 1000, 2000, 5000, 10_000];

export type RikaToast = { id: number; message: string; left: number };
export type DrawerSection = "conversation" | "agents";

/**
 * The browser side of Rika (docs/rika-design.md Q2 D, Q19, Q20): the conversation,
 * its actions and approvals, the live worker agents, the reply toasts and the history
 * drawer. One socket (`/api/rika/stream`) carries every live event; REST loads history
 * and agents and sends requests, decisions, undos and opens.
 */
export class RikaSession {
  entries = $state<Entry[]>([]);
  agents = $state<RikaAgent[]>([]);
  toasts = $state<RikaToast[]>([]);
  hasMore = $state(false);
  error = $state("");
  connected = $state(false);
  drawer = $state<{ open: boolean; section: DrawerSection; auto: boolean }>({ open: false, section: "conversation", auto: false });
  /** Requests sent whose reply has not started yet. */
  private waiting = $state<string[]>([]);
  private nextBefore: string | null = null;
  private nextToast = 1;
  private socket: WebSocket | null = null;
  private retry = 0;
  private retryTimer = 0;
  private stopped = true;
  private onnavigate: (view: string) => void = () => {};

  readonly pending = $derived(this.entries.filter((entry): entry is Entry & { kind: "approval" } => entry.kind === "approval" && entry.status === "pending"));
  /** Replies still being written (not those paused on an approval), or requests not yet answered. */
  readonly streaming = $derived(
    this.waiting.length > 0 ||
      this.entries.some((entry) => entry.kind === "message" && entry.role === "rika" && entry.status === "pending" &&
        !this.pending.some((approval) => String(approval.message_id) === String(entry.id))),
  );

  /** Connects; returns the disconnect. `onnavigate` runs Rika's "open view" client actions. */
  start(onnavigate: (view: string) => void): () => void {
    this.onnavigate = onnavigate;
    this.stopped = false;
    this.connect();
    void this.loadHistory();
    void this.loadAgents();
    return () => this.stop();
  }

  stop() {
    this.stopped = true;
    window.clearTimeout(this.retryTimer);
    this.socket?.close();
    this.socket = null;
    this.connected = false;
  }

  private connect() {
    if (this.stopped) return;
    const socket = new WebSocket(rikaSocketUrl(window.location));
    this.socket = socket;
    socket.onopen = () => {
      if (this.socket !== socket) return;
      // A reconnect catches up on whatever the socket missed.
      if (this.retry) { void this.loadHistory(); void this.loadAgents(); }
      this.retry = 0;
      this.connected = true;
    };
    socket.onmessage = (event) => {
      try { this.apply(JSON.parse(String(event.data)) as StreamEvent); } catch { /* A malformed frame is skipped. */ }
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.connected = false;
      this.socket = null;
      if (this.stopped) return;
      this.retryTimer = window.setTimeout(() => this.connect(), RETRY_MS[Math.min(this.retry, RETRY_MS.length - 1)]);
      this.retry += 1;
    };
  }

  async loadHistory(older = false) {
    try {
      const query = older && this.nextBefore ? `?before=${encodeURIComponent(this.nextBefore)}` : "";
      const page = await api<History>(`/rika/history${query}`);
      this.entries = mergeEntries(this.entries, page.items);
      if (older || !this.nextBefore) {
        this.hasMore = Boolean(page.has_more);
        this.nextBefore = page.next_before ?? null;
      }
    } catch (cause) {
      this.error = (cause as Error).message;
    }
  }

  async loadAgents() {
    try {
      this.agents = (await api<{ agents: RikaAgent[] }>("/rika/agents")).agents;
    } catch (cause) {
      this.error = (cause as Error).message;
    }
  }

  apply(event: StreamEvent) {
    switch (event.type) {
      case "message":
        if (event.message.role === "rika") this.reply(event.message.id, event.message.reply_to, () => ({ status: "done", ...event.message }));
        else this.upsert({ kind: "message", ...event.message });
        break;
      case "token":
        this.reply(event.message_id, event.reply_to, (current) => ({ text: (current?.text ?? "") + event.text, status: "pending" }));
        break;
      case "tier":
        this.reply(event.message_id, event.reply_to, () => ({ tier: event.tier, model: event.model }));
        break;
      case "action":
        this.upsert({ kind: "action", ...event.action });
        break;
      case "approval":
        this.approval(event.approval);
        break;
      case "agents":
        this.agents = event.agents;
        break;
      case "navigate":
        this.onnavigate(event.view);
        break;
    }
  }

  async send(text: string, tier?: RikaMessage["tier"]) {
    const request = text.trim();
    if (!request) return false;
    this.error = "";
    const ticket = `local:${Date.now()}:${Math.random()}`;
    // Stamped before sending: an instant reply must not sort above the request it answers.
    const sentAt = new Date().toISOString();
    this.waiting = [...this.waiting, ticket];
    try {
      const sent = await api<{ conversation_id: Id; message_id: Id }>("/rika/messages", tier ? { text: request, tier } : { text: request });
      // The reply's first event names the user message it answers.
      this.waiting = this.waiting.map((item) => (item === ticket ? String(sent.message_id) : item));
      if (!this.message(sent.message_id))
        this.upsert({ kind: "message", id: sent.message_id, conversation_id: sent.conversation_id, role: "user", text: request, created_at: sentAt });
      return true;
    } catch (cause) {
      this.waiting = this.waiting.filter((item) => item !== ticket);
      this.error = (cause as Error).message;
      return false;
    }
  }

  async decide(id: Id, decision: "approve" | "reject") {
    try {
      const { approval } = await api<{ approval: RikaApproval }>(`/rika/approvals/${encodeURIComponent(String(id))}`, { decision });
      this.approval(approval);
    } catch (cause) {
      this.error = (cause as Error).message;
    }
  }

  async undo(id: Id) {
    try {
      const { action } = await api<{ action: RikaAction }>(`/rika/actions/${encodeURIComponent(String(id))}/undo`, {});
      this.upsert({ kind: "action", ...action });
    } catch (cause) {
      this.error = (cause as Error).message;
    }
  }

  /** Focuses the agent's Tern window; without Tern, the server names a view to show it in. */
  async open(agent: RikaAgent) {
    try {
      const opened = await api<{ ok: boolean; view?: string | null }>(`/rika/agents/${encodeURIComponent(agent.id)}/open`, {});
      if (opened.view) this.onnavigate(opened.view);
    } catch (cause) {
      this.error = (cause as Error).message;
    }
  }

  showDrawer(section: DrawerSection = this.drawer.section) {
    this.drawer = { open: true, section, auto: false };
  }
  closeDrawer() {
    this.drawer = { ...this.drawer, open: false, auto: false };
  }
  toggleDrawer() {
    if (this.drawer.open) this.closeDrawer();
    else this.showDrawer();
  }

  dismiss(id: number) {
    this.toasts = this.toasts.filter((toast) => toast.id !== id);
  }

  /** The actions a reply's turn ran, in order. */
  actionsOf(message: Id): RikaAction[] {
    return this.entries.filter((entry): entry is Entry & { kind: "action" } => entry.kind === "action" && String(entry.message_id) === String(message));
  }
  message(id: Id): (Entry & { kind: "message" }) | undefined {
    return this.entries.find((entry): entry is Entry & { kind: "message" } => entry.kind === "message" && String(entry.id) === String(id));
  }

  /** Q20 A: an approval opens the drawer by itself; deciding the last one closes what it opened. */
  private approval(approval: RikaApproval) {
    this.upsert({ kind: "approval", ...approval });
    if (approval.status === "pending" && !this.drawer.open) this.drawer = { open: true, section: "conversation", auto: true };
    else if (approval.status !== "pending" && this.drawer.open && this.drawer.auto && !this.pending.length) this.closeDrawer();
  }

  private upsert(entry: Entry) {
    this.entries = mergeEntries(this.entries, [entry]);
  }

  /**
   * Applies one event of a rika reply. The first event of a new reply answers the oldest
   * request still waiting (or the one it names), so the bar knows the turn has started.
   */
  private reply(id: Id, request: Id | null | undefined, patch: (current: RikaMessage | undefined) => Partial<RikaMessage>) {
    const current = this.message(id);
    if (!current && this.waiting.length) {
      const index = request == null ? -1 : this.waiting.indexOf(String(request));
      this.waiting = this.waiting.filter((_, at) => at !== Math.max(index, 0));
    }
    this.upsert({ kind: "message", role: "rika", text: "", status: "pending", created_at: new Date().toISOString(), ...current, ...patch(current), id });
    this.raise(id);
  }

  /** One toast per live reply, newest last; finished replies count down. */
  private raise(message: Id) {
    const key = String(message);
    if (this.toasts.some((toast) => toast.message === key)) {
      this.toasts = this.toasts.map((toast) => (toast.message === key ? { ...toast, left: RIKA_TOAST_MS } : toast));
      return;
    }
    this.toasts = [...this.toasts, { id: this.nextToast++, message: key, left: RIKA_TOAST_MS }].slice(-MAX_TOASTS);
  }
}

export const rika = new RikaSession();
