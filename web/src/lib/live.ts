// Live push from kitty!'s block backend: one WebSocket per window carries every
// committed change (op_log row), whoever made it, so open views refresh without polling.
// Protocol: src/kitty/web_blocks.py. The block editor (build step 2) also sends its
// transactions over this socket.
import { api } from "./api";

export type BlockRoot = { type: string; number: number | null } | null;
export type BlockChange = {
  type: "change";
  seq: number;
  txn_id: string;
  page_id: string;
  at: string;
  device_id: string;
  root: BlockRoot;
  ops: Record<string, unknown>[];
};
/** A change, or null when history was compacted past this window's place: reload everything. */
export type BlockListener = (change: BlockChange | null) => void;

const listeners = new Set<BlockListener>();
let socket: WebSocket | null = null;
let connecting = false;
let lastSeq = 0;
let retryDelay = 500;
let retryTimer: ReturnType<typeof setTimeout> | undefined;

/** Call `listener` for every committed block change until the returned function is called. */
export function onBlockChange(listener: BlockListener): () => void {
  listeners.add(listener);
  void connect();
  return () => {
    listeners.delete(listener);
    if (!listeners.size) disconnect();
  };
}

function notify(change: BlockChange | null) {
  for (const listener of [...listeners]) listener(change);
}

function receive(raw: string) {
  const message = JSON.parse(raw) as { type: string; seq?: number; horizon?: number };
  if (message.type === "hello") {
    // A reconnect replays whatever this window missed while it was away.
    if (lastSeq && (message.seq ?? 0) > lastSeq) socket?.send(JSON.stringify({ type: "resume", after: lastSeq }));
    lastSeq = Math.max(lastSeq, message.seq ?? 0);
  } else if (message.type === "change") {
    lastSeq = Math.max(lastSeq, message.seq ?? 0);
    notify(message as BlockChange);
  } else if (message.type === "reset") {
    lastSeq = message.horizon ?? lastSeq;
    notify(null);
  }
}

async function connect() {
  if (socket || connecting || !listeners.size) return;
  connecting = true;
  try {
    const session = await api<{ token: string; socket: string }>("/blocks/session", {});
    if (!listeners.size) return;
    const scheme = location.protocol === "https:" ? "wss:" : "ws:";
    const next = new WebSocket(`${scheme}//${location.host}${session.socket}?${new URLSearchParams({ token: session.token })}`);
    socket = next;
    next.onopen = () => { retryDelay = 500; };
    next.onmessage = (event) => receive(String(event.data));
    next.onclose = () => {
      if (socket === next) socket = null;
      retry();
    };
  } catch {
    retry();
  } finally {
    connecting = false;
  }
}

function retry() {
  if (!listeners.size || retryTimer !== undefined) return;
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
  current?.close();
}
