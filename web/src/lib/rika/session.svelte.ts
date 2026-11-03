import { api, socketProtocols } from "../api";
import { Mic, MicError } from "./mic";
import { mergeEntries, rikaSocketUrl, type Entry, type History, type Id, type RikaAction, type RikaAgent, type RikaApproval, type RikaMessage, type RikaPlan, type Segment, type StreamEvent, type VoiceMode } from "./rika";

/** How long a finished reply's toast stays before it leaves for the drawer. */
export const RIKA_TOAST_MS = 9000;
const MAX_TOASTS = 3;
const RETRY_MS = [500, 1000, 2000, 5000, 10_000];

export type RikaToast = { id: number; message: string; left: number };
export type DrawerSection = "conversation" | "agents";
/**
 * Voice input: `starting` while the microphone opens, `loading` while the backend gets
 * its model ready (downloading it on first use), `listening`, then `finishing` while the
 * last words are transcribed.
 */
export type VoiceState = {
  phase: "idle" | "starting" | "loading" | "listening" | "finishing";
  mode: VoiceMode;
  session: string;
  partial: string;
  segments: Segment[];
  /** Typed text the spoken words continue. */
  prefix: string;
};
const IDLE: VoiceState = { phase: "idle", mode: "command", session: "", partial: "", segments: [], prefix: "" };

/**
 * The browser side of Rika (docs/rika-design.md Q2 D, Q3–Q8, Q19, Q20): the conversation,
 * its actions, approvals and brain-dump plans, voice input, the live worker agents, the
 * reply toasts and the history drawer. One socket (`/api/rika/stream`) carries every
 * live event and the microphone's audio; REST loads history and agents and sends
 * requests, dumps, decisions, undos and opens.
 */
export class RikaSession {
  entries = $state<Entry[]>([]);
  agents = $state<RikaAgent[]>([]);
  toasts = $state<RikaToast[]>([]);
  hasMore = $state(false);
  error = $state("");
  connected = $state(false);
  drawer = $state<{ open: boolean; section: DrawerSection; auto: boolean }>({ open: false, section: "conversation", auto: false });
  voice = $state<VoiceState>({ ...IDLE });
  /** The Whisper model's first-use download, while it runs. */
  model = $state<{ received: number; total: number } | null>(null);
  /** Requests sent whose reply has not started yet. */
  private waiting = $state<string[]>([]);
  private nextBefore: string | null = null;
  private nextToast = 1;
  private socket: WebSocket | null = null;
  private retry = 0;
  private retryTimer = 0;
  private stopped = true;
  private onnavigate: (view: string) => void = () => {};
  private mic: Mic | null = null;
  private nextVoice = 1;
  /** Replies already spoken back (Q6 C), so a repeated final event speaks once. */
  private said = new Set<string>();

  readonly pending = $derived(this.entries.filter((entry): entry is Entry & { kind: "approval" } => entry.kind === "approval" && entry.status === "pending"));
  readonly pendingPlans = $derived(this.entries.filter((entry): entry is Entry & { kind: "plan" } => entry.kind === "plan" && entry.status === "pending"));
  /** Replies still being written (not those paused on an approval), or requests not yet answered. */
  readonly streaming = $derived(
    this.waiting.length > 0 ||
      this.entries.some((entry) => entry.kind === "message" && entry.role === "rika" && entry.status === "pending" &&
        !this.pending.some((approval) => String(approval.message_id) === String(entry.id))),
  );
  readonly listening = $derived(this.voice.phase !== "idle");

  /** Connects; returns the disconnect. `onnavigate` runs Rika's "open view" client actions. */
  start(onnavigate: (view: string) => void): () => void {
    this.onnavigate = onnavigate;
    this.stopped = false;
    void this.connect();
    void this.loadHistory();
    void this.loadAgents();
    return () => this.stop();
  }

  stop() {
    this.stopped = true;
    window.clearTimeout(this.retryTimer);
    this.endVoice();
    this.socket?.close();
    this.socket = null;
    this.connected = false;
  }

  private async connect() {
    if (this.stopped) return;
    // The upgrade carries this window's UI secret as a subprotocol (kitty.web_auth).
    const protocols = await socketProtocols();
    if (this.stopped || this.socket) return;
    const socket = new WebSocket(rikaSocketUrl(window.location), protocols);
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
      // The backend's transcription belonged to this socket; whatever was being said is lost.
      if (this.listening) this.voiceFailed("Rika's connection dropped while listening; nothing was sent");
      if (this.stopped) return;
      this.retryTimer = window.setTimeout(() => void this.connect(), RETRY_MS[Math.min(this.retry, RETRY_MS.length - 1)]);
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
      case "message": {
        const message = event.message;
        if (message.role !== "rika") {
          this.upsert({ kind: "message", ...message });
          break;
        }
        this.reply(message.id, message.reply_to, () => ({ status: "done", ...message }));
        // The request's own message event (with its source) always comes before its reply's.
        const request = message.reply_to == null ? undefined : this.message(message.reply_to);
        if (message.status !== "pending" && request?.source === "voice" && !this.said.has(String(message.id))) {
          this.said.add(String(message.id));
          void this.speak(message);
        }
        break;
      }
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
      case "plan":
        this.plan(event.plan);
        break;
      case "voice.model":
        this.model = event.state === "downloading" ? { received: event.received, total: event.total } : null;
        if (event.state === "failed" && event.message) this.error = event.message;
        break;
      case "voice.state":
      case "voice.partial":
      case "voice.segment":
      case "voice.final":
      case "voice.error":
        this.heard(event);
        break;
    }
  }

  async send(text: string, tier?: RikaMessage["tier"], spoken = false) {
    const request = text.trim();
    if (!request) return false;
    const body: Record<string, unknown> = { text: request };
    if (tier) body.tier = tier;
    if (spoken) body.spoken = true;
    return this.request("/rika/messages", body, request, spoken ? "voice" : "typed");
  }

  /** Sends a brain dump's transcript; its dump note is saved and its plan comes back as a `plan` event. */
  async dump(transcript: string, segments: Segment[], duration: number) {
    if (!transcript.trim()) {
      this.error = "Rika heard nothing in that brain dump";
      this.releaseAuto();
      return false;
    }
    const sent = await this.request("/rika/dumps", { transcript, segments, duration }, transcript, "dump");
    if (!sent) this.releaseAuto();
    return sent;
  }

  private async request(path: string, body: Record<string, unknown>, text: string, source: RikaMessage["source"]) {
    this.error = "";
    const ticket = `local:${Date.now()}:${Math.random()}`;
    // Stamped before sending: an instant reply must not sort above the request it answers.
    const sentAt = new Date().toISOString();
    this.waiting = [...this.waiting, ticket];
    try {
      const sent = await api<{ conversation_id: Id; message_id: Id }>(path, body);
      // The reply's first event names the user message it answers.
      this.waiting = this.waiting.map((item) => (item === ticket ? String(sent.message_id) : item));
      if (!this.message(sent.message_id))
        this.upsert({ kind: "message", id: sent.message_id, conversation_id: sent.conversation_id, role: "user", text, source, created_at: sentAt });
      return true;
    } catch (cause) {
      this.waiting = this.waiting.filter((item) => item !== ticket);
      this.error = (cause as Error).message;
      return false;
    }
  }

  // Voice -----------------------------------------------------------------------

  /**
   * Opens the microphone and streams it to the backend (Q3 A, Q5 A). A command is sent as
   * a spoken request when it ends; a brain dump (Q7 A) shows its live transcript in the
   * drawer, which opens by itself, and is planned when it ends.
   */
  async listen(mode: VoiceMode, prefix = "") {
    if (this.listening) return;
    this.error = "";
    const session = `v${Date.now().toString(36)}${this.nextVoice++}`;
    this.voice = { ...IDLE, phase: "starting", mode, session, prefix };
    if (mode === "dump") this.openAuto();
    const mic = (this.mic = new Mic());
    try {
      await mic.start((pcm) => {
        if (this.voice.session === session && this.voice.phase !== "finishing") this.socket?.send(pcm);
      });
    } catch (cause) {
      if (this.mic === mic) this.voiceFailed(cause instanceof MicError ? cause.message : "The microphone could not be opened");
      return;
    }
    if (this.voice.session !== session) return mic.stop(); // cancelled while the microphone opened
    const socket = this.socket;
    if (!socket || socket.readyState !== WebSocket.OPEN) return this.voiceFailed("Rika is offline; try again in a moment");
    socket.send(JSON.stringify({ type: "voice.start", session, mode: this.voice.mode }));
    if (this.voice.phase === "finishing") socket.send(JSON.stringify({ type: "voice.stop" }));
    else this.voice = { ...this.voice, phase: "loading" };
  }

  /** A held command became a brain dump: keep listening, with the transcript in the drawer. */
  latch() {
    if (!this.listening || this.voice.mode === "dump") return;
    this.voice = { ...this.voice, mode: "dump" };
    if (this.voice.phase !== "starting") this.socket?.send(JSON.stringify({ type: "voice.mode", mode: "dump" }));
    this.openAuto();
  }

  /** Stop listening and send what was said (the backend transcribes the rest first). */
  finish() {
    if (!this.listening || this.voice.phase === "finishing") return;
    const starting = this.voice.phase === "starting";
    this.voice = { ...this.voice, phase: "finishing" };
    this.mic?.stop();
    this.mic = null;
    if (!starting) this.socket?.send(JSON.stringify({ type: "voice.stop" }));
  }

  /** Drop what is being said; nothing is sent. */
  cancel() {
    if (!this.listening) return;
    if (this.voice.phase !== "starting") this.socket?.send(JSON.stringify({ type: "voice.cancel" }));
    const dump = this.voice.mode === "dump";
    this.endVoice();
    if (dump) this.releaseAuto();
  }

  private endVoice() {
    this.mic?.stop();
    this.mic = null;
    this.voice = { ...IDLE };
  }

  private voiceFailed(message: string) {
    const dump = this.voice.mode === "dump";
    this.endVoice();
    this.error = message;
    if (dump) this.releaseAuto();
  }

  private heard(event: Extract<StreamEvent, { session: string }>) {
    if (event.session !== this.voice.session) return;
    switch (event.type) {
      case "voice.state":
        if (event.state === "listening" && this.voice.phase !== "finishing") this.voice = { ...this.voice, phase: "listening" };
        break;
      case "voice.partial":
        this.voice = { ...this.voice, partial: event.text };
        break;
      case "voice.segment":
        this.voice = { ...this.voice, segments: [...this.voice.segments, { start: event.start, end: event.end, text: event.text }] };
        break;
      case "voice.error":
        this.voiceFailed(event.message);
        break;
      case "voice.final": {
        const { mode, prefix } = this.voice;
        this.endVoice();
        if (mode === "dump") void this.dump(event.text, event.segments, event.duration);
        else if (event.text.trim()) void this.send(`${prefix} ${event.text}`.trim(), undefined, true);
        else this.error = "Rika heard nothing; hold Space and speak";
        break;
      }
    }
  }

  /** Approve the ticked items of a plan (with the chosen answer to its question) or dismiss it. */
  async decidePlan(id: Id, decision: "approve" | "dismiss", items: string[] = [], choices: Record<string, number> = {}) {
    try {
      const body = decision === "approve" ? { decision, items, choices } : { decision };
      const { plan } = await api<{ plan: RikaPlan }>(`/rika/plans/${encodeURIComponent(String(id))}`, body);
      this.plan(plan);
    } catch (cause) {
      this.error = (cause as Error).message;
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

  /** Undoes several actions newest first, so each one finds the state it left. */
  async undoAll(ids: Id[]) {
    for (const id of [...ids].reverse()) await this.undo(id);
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

  /** Opens one of the app's views (a dump note's links, the Agents cards' "All agent skills" link). */
  navigate(view: string) {
    this.onnavigate(view);
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

  /** Q20 A: a pending approval opens the drawer by itself; deciding the last one closes what it opened. */
  private approval(approval: RikaApproval) {
    this.upsert({ kind: "approval", ...approval });
    if (approval.status === "pending") this.openAuto();
    else this.releaseAuto();
  }

  /** Q20 A: a brain dump's plan opens the drawer by itself too, and deciding it closes what that opened. */
  private plan(plan: RikaPlan) {
    this.upsert({ kind: "plan", ...plan });
    if (plan.status === "pending") this.openAuto();
    else this.releaseAuto();
  }

  private openAuto() {
    if (!this.drawer.open) this.drawer = { open: true, section: "conversation", auto: true };
    else if (this.drawer.section !== "conversation") this.drawer = { ...this.drawer, section: "conversation" };
  }

  /** Closes a drawer that opened by itself once nothing in it waits for the user. */
  private releaseAuto() {
    const dumping = this.listening && this.voice.mode === "dump";
    if (this.drawer.open && this.drawer.auto && !this.pending.length && !this.pendingPlans.length && !dumping) this.closeDrawer();
  }

  /** Q6 C: a reply to something said aloud is spoken back as a short summary, when a local engine can. */
  private async speak(reply: RikaMessage) {
    try {
      const { audio } = await api<{ text: string; audio: string | null }>(`/rika/speech/${encodeURIComponent(String(reply.id))}`);
      if (audio) await new Audio(audio).play();
    } catch {
      // Speech is a courtesy: without it the reply is still in its toast.
    }
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
