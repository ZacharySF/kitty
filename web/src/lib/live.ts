// The live channel: one WebSocket per window, open for the window's whole life. It carries
// every committed block change (op_log row), whoever made it, so open views refresh
// without polling, and every change to the build file (Q8/Q9) so each window restyles at
// once. Protocol: src/kitty/web_live.py. The block editor sends its transactions over the
// same socket with `transact` and receives the server's ack or reject.
import { api, socketProtocols } from "./api";
import type { Op } from "./blockDoc";
import type { Reply, TxnMessage } from "./blockSync";

export type BlockRoot = { type: string; number: number | null } | null;
export type BlockChange = {
  type: "change";
  seq: number;
  txn_id: string;
  page_id: string;
  at: string;
  device_id: string;
  root: BlockRoot;
  ops: Op[];
};
/** A change, or null when history was compacted past this window's place: reload everything. */
export type BlockListener = (change: BlockChange | null) => void;
/** A whole message of one type (`hello`, `build`), as the server sent it. */
export type LiveMessage = { type: string } & Record<string, unknown>;

const listeners = new Set<BlockListener>();
const handlers = new Map<string, Set<(message: LiveMessage) => void>>();
/** Set by keepLive: the window stays connected even with no block listener. */
let always = false;
let socket: WebSocket | null = null;
let ready = false;
let connecting = false;
let lastSeq = 0;
let retryDelay = 500;
let retryTimer: ReturnType<typeof setTimeout> | undefined;
/** Transactions waiting for their ack or reject, by txn_id. */
const replies = new Map<string, (reply: Reply) => void>();
/** Transactions waiting for the socket to open. */
let waiting: TxnMessage[] = [];
const OFFLINE_MS = 10_000;

const wanted = () => always || listeners.size > 0;

/** Keep this window's socket open from now on (every window does, for build pushes). */
export function keepLive(): void {
  always = true;
  void connect();
}

/** Call `handler` for every message of `type` (such as `hello` or `build`) until the returned function is called. */
export function onLive(type: string, handler: (message: LiveMessage) => void): () => void {
  const set = handlers.get(type) ?? new Set();
  handlers.set(type, set);
  set.add(handler);
  return () => set.delete(handler);
}

/** Call `listener` for every committed block change until the returned function is called. */
export function onBlockChange(listener: BlockListener): () => void {
  listeners.add(listener);
  void connect();
  return () => {
    listeners.delete(listener);
    if (!wanted()) disconnect();
  };
}

let readyWaiters: (() => void)[] = [];
/** Resolves once this window's socket is live (its hello arrived); call after onBlockChange. */
export function whenLive(): Promise<void> {
  if (socket && ready) return Promise.resolve();
  return new Promise((resolve) => readyWaiters.push(resolve));
}

let deviceId = "";
/** This rika!'s device ID (as recorded on every op_log row), once the socket said hello. */
export function liveDevice(): string {
  return deviceId;
}

/**
 * Send one transaction over the socket (a window must be listening with onBlockChange).
 * Resolves with the server's ack or reject; status 0 means the socket was not
 * reachable, so the transaction may or may not have been applied.
 */
export function transact(message: TxnMessage): Promise<Reply> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => finish(message.txn_id, { type: "reject", txn_id: message.txn_id, status: 0, error: "rika! is not reachable" }), OFFLINE_MS);
    replies.set(message.txn_id, (reply) => {
      clearTimeout(timer);
      resolve(reply);
    });
    if (socket && ready) socket.send(JSON.stringify({ type: "txn", ...message }));
    else {
      waiting.push(message);
      void connect();
    }
  });
}

function finish(txnId: string, reply: Reply) {
  const resolve = replies.get(txnId);
  replies.delete(txnId);
  waiting = waiting.filter((message) => message.txn_id !== txnId);
  resolve?.(reply);
}

function notify(change: BlockChange | null) {
  for (const listener of [...listeners]) listener(change);
}

function receive(raw: string) {
  const message = JSON.parse(raw) as LiveMessage & { seq?: number; horizon?: number; txn_id?: string; device_id?: string };
  if (message.type === "hello") {
    ready = true;
    deviceId = message.device_id ?? deviceId;
    // A reconnect replays whatever this window missed while it was away.
    if (lastSeq && (message.seq ?? 0) > lastSeq) socket?.send(JSON.stringify({ type: "resume", after: lastSeq }));
    lastSeq = Math.max(lastSeq, message.seq ?? 0);
    const queued = waiting;
    waiting = [];
    for (const txn of queued) socket?.send(JSON.stringify({ type: "txn", ...txn }));
    const live = readyWaiters;
    readyWaiters = [];
    for (const resolve of live) resolve();
  } else if (message.type === "change") {
    lastSeq = Math.max(lastSeq, message.seq ?? 0);
    notify(message as unknown as BlockChange);
  } else if (message.type === "reset") {
    lastSeq = message.horizon ?? lastSeq;
    notify(null);
  } else if ((message.type === "ack" || message.type === "reject") && message.txn_id) {
    finish(message.txn_id, message as unknown as Reply);
  }
  for (const handler of [...(handlers.get(message.type) ?? [])]) handler(message);
}

function dropReplies() {
  for (const txnId of [...replies.keys()]) {
    if (!waiting.some((message) => message.txn_id === txnId)) finish(txnId, { type: "reject", txn_id: txnId, status: 0, error: "Connection to rika! closed" });
  }
}

async function connect() {
  if (socket || connecting || !wanted()) return;
  connecting = true;
  try {
    const session = await api<{ token: string; socket: string }>("/live/session", {});
    const protocols = await socketProtocols();
    if (!wanted()) return;
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const next = new WebSocket(`${scheme}//${location.host}${session.socket}?${new URLSearchParams({ token: session.token })}`, protocols);
    socket = next;
    ready = false;
    next.onopen = () => { retryDelay = 500; };
    next.onmessage = (event) => receive(String(event.data));
    next.onclose = () => {
      if (socket === next) {
        socket = null;
        ready = false;
      }
      // Sent transactions have no answer now; the editor re-syncs and sends again.
      dropReplies();
      retry();
    };
  } catch {
    retry();
  } finally {
    connecting = false;
  }
}

function retry() {
  if (!wanted() || retryTimer !== undefined) return;
  retryTimer = setTimeout(() => {
    retryTimer = undefined;
    void connect();
  }, retryDelay);
  retryDelay = Math.min(retryDelay * 2, 10_000);
}

function disconnect() {
  clearTimeout(retryTimer);
  retryTimer = undefined;
  const current = socket;
  socket = null;
  ready = false;
  current?.close();
}
