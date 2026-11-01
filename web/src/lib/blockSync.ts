// The block editor's link to kitty's server (platform design Q6): every edit is applied
// to the window's copy at once (keystrokes echo locally) and queued; queued edits go
// out as one `txn` at a time with the page's `base` revision, text as code-point
// splices. An `ack` confirms them; a `reject` re-syncs the page and replays the edits
// that still fit. Other windows' `change` messages are applied under the edits not yet
// confirmed here, so they never clobber what is being typed.
import { BlockConflict, PageDoc, codePointSplice, viewOnly, type Op, type PageState } from "./blockDoc.ts";

export type TxnMessage = { txn_id: string; ops: Op[]; base: Record<string, number> };
export type Ack = { type: "ack"; txn_id: string; seq: Record<string, number>; revisions: Record<string, number | null> };
export type Reject = { type: "reject"; txn_id: string; status: number; error: string };
export type Reply = Ack | Reject;
export type Change = { seq: number; txn_id: string; page_id: string; ops: Op[] };

export type SyncOptions = {
  state: PageState;
  send: (message: TxnMessage) => Promise<Reply>;
  load: () => Promise<PageState>;
  /** The window's copy (`local`) changed: render it. */
  onupdate: () => void;
  /** The page revision moved; `own` when this window's transaction moved it. */
  onrevision?: (from: number, to: number, own: boolean) => void;
  onerror?: (message: string) => void;
  /** How long typing waits (ms) before it is sent; structural edits go at once. */
  debounce?: number;
  newId?: () => string;
};

type Flight = { txnId: string; ops: Op[]; predicted: PageDoc };
/** Typing repeats two kinds of operation per block; only the last of each is worth sending. */
function replaceable(op: Op): string | null {
  if (op.op === "text" && "text" in op) return `text:${op.id}`;
  if (op.op === "set" && op.type === undefined && op.props && Object.keys(op.props).join() === "marks") return `marks:${op.id}`;
  return null;
}

/** Later full-text (or inline-mark) replacements make earlier ones for the same block redundant. */
function coalesce(ops: Op[]): Op[] {
  const last = new Map<string, number>();
  ops.forEach((op, index) => {
    const key = replaceable(op);
    if (key) last.set(key, index);
  });
  return ops.filter((op, index) => {
    const key = replaceable(op);
    return key === null || last.get(key) === index;
  });
}

export class PageSync {
  server: PageDoc;
  local: PageDoc;
  lastTxn = "";
  private readonly options: SyncOptions;
  private queue: Op[] = [];
  private inflight: Flight | null = null;
  private buffered: Change[] = [];
  private loadedSeq: number;
  private readonly own = new Set<string>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private firstTyped = 0;
  private holding = false;
  private holdUntil: number | null = null;
  private holdTimer: ReturnType<typeof setTimeout> | undefined;
  private retryTimer: ReturnType<typeof setTimeout> | undefined;
  private reloading = false;
  private reloadAfter = false;
  private failures = 0;
  private disposed = false;
  private waiters: (() => void)[] = [];

  constructor(options: SyncOptions) {
    this.options = options;
    this.server = PageDoc.from(options.state);
    this.local = this.server.clone();
    this.loadedSeq = options.state.seq;
  }

  get pageId(): string {
    return this.server.pageId;
  }

  /** Edits not yet confirmed by the server. */
  get pending(): boolean {
    return this.queue.length > 0 || this.inflight !== null;
  }

  /** Apply one user action here at once and queue it for the server. */
  commit(ops: Op[], { typing = false }: { typing?: boolean } = {}): boolean {
    if (!ops.length) return true;
    try {
      for (const op of ops) this.local.apply(op);
    } catch (error) {
      if (!(error instanceof BlockConflict)) throw error;
      this.rebuild();
      return false;
    }
    this.queue.push(...ops);
    this.options.onupdate();
    clearTimeout(this.timer);
    if (typing && this.options.debounce) {
      const now = Date.now();
      this.firstTyped ||= now;
      // Keep sending while someone types without pausing: at most four debounce windows apart.
      const wait = Math.max(0, Math.min(this.options.debounce, this.firstTyped + this.options.debounce * 4 - now));
      this.timer = setTimeout(() => this.pump(), wait);
    } else if (typing) this.timer = setTimeout(() => this.pump(), 0);
    else this.pump();
    return true;
  }

  /** Send everything now and wait until the server confirmed it (or it was dropped). */
  flush(): Promise<void> {
    clearTimeout(this.timer);
    this.pump();
    if (!this.pending) return Promise.resolve();
    return new Promise((resolve) => this.waiters.push(resolve));
  }

  /**
   * Run another writer of this page (a Notes HTTP save) with this window's edits out of
   * the way: everything queued is confirmed first, and nothing is sent until the change
   * that writer committed (it returns the new revision) has arrived here.
   */
  async exclusive(write: (revision: number) => Promise<number | null>): Promise<void> {
    await this.flush();
    this.holding = true;
    let revision: number | null = null;
    try {
      revision = await write(this.server.revision);
    } finally {
      if (revision !== null && revision > this.server.revision) {
        this.holdUntil = revision;
        clearTimeout(this.holdTimer);
        // The change normally arrives within milliseconds; if it does not, reload the page.
        this.holdTimer = setTimeout(() => { this.release(); void this.reload(); }, 3000);
      } else this.release();
    }
  }

  /**
   * Send operations that leave this page (a move onto another page) as their own
   * transaction once everything queued is confirmed, and apply them only when acked.
   */
  async transactNow(ops: Op[]): Promise<Reply> {
    await this.flush();
    this.holding = true;
    const txnId = (this.options.newId ?? (() => crypto.randomUUID()))();
    this.own.add(txnId);
    const from = this.server.revision;
    let reply: Reply;
    try {
      reply = await this.options.send({ txn_id: txnId, ops, base: { [this.pageId]: from } });
    } catch (error) {
      reply = { type: "reject", txn_id: txnId, status: 0, error: String(error) };
    }
    if (reply.type === "ack") {
      for (const op of ops) this.server.apply(op, false);
      const to = reply.revisions[this.pageId] ?? from;
      this.server.apply({ op: "set", id: this.pageId, props: { revision: to } });
      this.rebuild();
      this.options.onrevision?.(from, to, true);
    } else {
      this.own.delete(txnId);
      this.options.onerror?.(reply.error);
      if (reply.status === 409) await this.reload();
    }
    this.release();
    return reply;
  }

  /** A pushed change (null: history was compacted past this window, so reload). */
  receive(change: Change | null): void {
    if (this.disposed) return;
    if (change === null) {
      void this.reload();
      return;
    }
    if (change.page_id !== this.pageId || change.seq <= this.loadedSeq) return;
    if (this.own.delete(change.txn_id)) return;
    if (this.inflight || this.reloading) {
      // Applied for real once the transaction settles; shown now under this window's edits.
      this.buffered.push(change);
      if (this.inflight) this.rebuild();
      return;
    }
    const from = this.server.revision;
    this.applyRemote([change]);
    this.rebuild();
    if (this.server.revision !== from) this.options.onrevision?.(from, this.server.revision, false);
    this.checkHold();
  }

  dispose(): void {
    this.disposed = true;
    clearTimeout(this.timer);
    clearTimeout(this.holdTimer);
    clearTimeout(this.retryTimer);
  }

  private release(): void {
    clearTimeout(this.holdTimer);
    this.holding = false;
    this.holdUntil = null;
    this.pump();
  }

  private checkHold(): void {
    if (this.holdUntil !== null && this.server.revision >= this.holdUntil) this.release();
  }

  private applyRemote(changes: Change[]): void {
    let reload = false;
    for (const change of changes) {
      for (const op of change.ops) reload = this.server.apply(op, false) || reload;
    }
    if (reload) void this.reload();
  }

  /**
   * The window's copy: the server's page, changes received while a transaction is in
   * flight, then every edit not yet confirmed; queued edits that no longer fit are dropped.
   */
  private rebuild(): void {
    const local = this.server.clone();
    for (const change of this.buffered) for (const op of change.ops) local.apply(op, false);
    for (const op of this.inflight?.ops ?? []) {
      try {
        local.apply(op);
      } catch {
        // The in-flight transaction will be rejected and re-synced.
      }
    }
    const kept: Op[] = [];
    for (const op of this.queue) {
      try {
        local.apply(op);
        kept.push(op);
      } catch (error) {
        if (!(error instanceof BlockConflict)) throw error;
      }
    }
    this.queue = kept;
    this.local = local;
    this.options.onupdate();
  }

  private settleWaiters(): void {
    if (this.pending) return;
    const waiters = this.waiters;
    this.waiters = [];
    for (const resolve of waiters) resolve();
  }

  private pump(): void {
    if (this.disposed || this.inflight || this.holding || this.reloading) return;
    this.firstTyped = 0;
    const ops = coalesce(this.queue);
    this.queue = [];
    const predicted = this.server.clone();
    const sent: Op[] = [];
    const kept: Op[] = [];
    for (const op of ops) {
      try {
        if (op.op === "text" && "text" in op && predicted.has(op.id)) {
          const splice = codePointSplice(predicted.get(op.id).text, op.text);
          kept.push(op);
          if (!splice) continue;
          const out: Op = { op: "text", id: op.id, ...splice };
          predicted.apply(out);
          sent.push(out);
          continue;
        }
        predicted.apply(op);
        sent.push(op);
        kept.push(op);
      } catch (error) {
        if (!(error instanceof BlockConflict)) throw error;
      }
    }
    if (!sent.length) {
      this.settleWaiters();
      return;
    }
    const txnId = (this.options.newId ?? (() => crypto.randomUUID()))();
    this.inflight = { txnId, ops: kept, predicted };
    this.own.add(txnId);
    this.lastTxn = txnId;
    const base = { [this.pageId]: this.server.revision };
    this.options.send({ txn_id: txnId, ops: sent, base }).then(
      (reply) => this.settle(reply),
      (error: unknown) => this.settle({ type: "reject", txn_id: txnId, status: 0, error: String(error) }),
    );
  }

  private settle(reply: Reply): void {
    const flight = this.inflight;
    if (!flight || this.disposed) return;
    this.inflight = null;
    const buffered = this.buffered;
    this.buffered = [];
    const from = this.server.revision;
    if (reply.type === "ack") {
      this.failures = 0;
      const to = reply.revisions[this.pageId] ?? from;
      const seq = reply.seq[this.pageId] ?? 0;
      this.server = flight.predicted;
      this.server.apply({ op: "set", id: this.pageId, props: { revision: to } });
      // Changes committed before this transaction were already part of its base revision.
      this.applyRemote(buffered.filter((change) => change.seq > seq || viewOnly(change.ops)));
      this.rebuild();
      this.options.onrevision?.(from, to, true);
      if (this.server.revision !== to) this.options.onrevision?.(to, this.server.revision, false);
      this.checkHold();
      if (this.reloadAfter) void this.reload();
      else this.pump();
      this.settleWaiters();
      return;
    }
    this.own.delete(flight.txnId);
    this.applyRemote(buffered);
    if (reply.status === 0 || reply.status === 503) {
      // Offline or busy: send the same edits again shortly (a duplicate is refused and re-synced).
      this.queue = [...flight.ops, ...this.queue];
      this.rebuild();
      clearTimeout(this.retryTimer);
      this.retryTimer = setTimeout(() => this.pump(), reply.status === 0 ? 1000 : 300);
      return;
    }
    this.failures += 1;
    if (reply.status === 409 && this.failures <= 3) this.queue = [...flight.ops, ...this.queue];
    else {
      // The server refused these edits themselves; keep the ones queued after them.
      this.options.onerror?.(reply.error);
      if (this.failures > 3) this.queue = [];
    }
    void this.reload();
  }

  /** Load the page again and replay the edits not yet confirmed on top of it. */
  async reload(): Promise<void> {
    if (this.disposed) return;
    if (this.inflight || this.reloading) {
      this.reloadAfter = true;
      return;
    }
    this.reloadAfter = false;
    this.reloading = true;
    const from = this.server.revision;
    try {
      const state = await this.options.load();
      if (this.disposed) return;
      this.server = PageDoc.from(state);
      this.loadedSeq = state.seq;
      const buffered = this.buffered.filter((change) => change.seq > state.seq && !this.own.has(change.txn_id));
      this.buffered = [];
      this.applyRemote(buffered);
    } catch (error) {
      if (this.disposed) return;
      const gone = error instanceof Error && "status" in error && error.status === 404;
      this.options.onerror?.(gone ? "This page is no longer here; it may have been moved to Trash." : error instanceof Error ? error.message : String(error));
      clearTimeout(this.retryTimer);
      if (!gone) this.retryTimer = setTimeout(() => void this.reload(), 2000);
      return;
    } finally {
      this.reloading = false;
    }
    this.rebuild();
    if (this.server.revision !== from) this.options.onrevision?.(from, this.server.revision, false);
    this.checkHold();
    this.pump();
    this.settleWaiters();
  }
}
