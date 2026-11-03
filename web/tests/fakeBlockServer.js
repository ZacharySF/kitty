// An in-memory stand-in for rika!'s block backend (src/kitty/web_blocks.py, web_live.py) for
// browser harnesses: GET /api/blocks/page, the live WebSocket (txn → ack/reject, change to
// every window), history sessions/preview/restore and Trash. Records are synthetic.
// It applies operations with the editor's own PageDoc; the real server's semantics are
// covered by the Python tests.
import { PageDoc } from "/src/lib/blockDoc.ts";

const AT = () => new Date().toISOString();

export function createBlockServer({ pages, device = "harness-device-0001" }) {
  const byId = new Map();
  let seq = 0;
  const sockets = [];
  const server = {
    sockets,
    /** Transactions received over the socket, in order (what the editor sent). */
    sent: [],
    /** While true, transactions are applied but their acks wait for `release()`. */
    holdAcks: false,
    held: [],
    failNext: null,
  };

  for (const spec of pages) {
    const root = { id: spec.page_id, type: spec.type ?? "page", parent_id: null, order_key: "a0", properties: { revision: spec.revision ?? 1, number: spec.number }, text: spec.title };
    const state = { page_id: spec.page_id, seq: 0, revision: root.properties.revision, root, blocks: spec.blocks };
    // Creating the page is its first transaction, as on kitty's server.
    seq += 1;
    byId.set(spec.page_id, { initial: structuredClone(state), doc: PageDoc.from(state), log: [{ seq, at: AT(), snapshot: structuredClone(state), ops: [] }], trash: [] });
  }

  const pageOf = (blockId) => [...byId.values()].find((entry) => entry.doc.has(blockId));
  const rows = (doc) => doc.subtree(doc.pageId).slice(1).map((block) => structuredClone(block));
  const stateOf = (entry) => ({ page_id: entry.doc.pageId, seq, revision: entry.doc.revision, root: structuredClone(entry.doc.root), blocks: rows(entry.doc) });
  const find = (query) => {
    if (query.get("page")) return byId.get(query.get("page"));
    return [...byId.values()].find((entry) => entry.doc.root.type === query.get("type") && entry.doc.root.properties.number === Number(query.get("number")));
  };

  function deliver(socket, message) {
    setTimeout(() => socket.onmessage?.({ data: JSON.stringify(message) }), 0);
  }
  function broadcast(changes) {
    for (const change of changes) for (const socket of sockets) if (socket.readyState === 1) deliver(socket, change);
  }

  /** Apply one transaction like kitty's transact: base revisions, all-or-nothing, one log row per page. */
  function apply(ops, base, txnId, from = device) {
    if (server.failNext) {
      const status = server.failNext;
      server.failNext = null;
      return { type: "reject", txn_id: txnId, status, error: "Synthetic rejection" };
    }
    for (const [pageId, revision] of Object.entries(base ?? {})) {
      const entry = byId.get(pageId);
      if (!entry) return { type: "reject", txn_id: txnId, status: 404, error: "Page not found" };
      if (entry.doc.revision !== revision) return { type: "reject", txn_id: txnId, status: 409, error: "Page changed in another window; reload before saving" };
    }
    const work = new Map([...byId].map(([id, entry]) => [id, entry.doc.clone()]));
    const logged = new Map();
    const trashed = [];
    const record = (pageId, op) => { work.get(pageId).apply(op); logged.set(pageId, [...(logged.get(pageId) ?? []), op]); };
    try {
      for (const op of ops) {
        const target = op.op === "insert" ? op.parent : op.id;
        const pageId = [...work].find(([, doc]) => doc.has(target))?.[0];
        if (!pageId) throw new Error("Block not found");
        const doc = work.get(pageId);
        if (op.op === "move" && !doc.has(op.parent)) {
          const destination = [...work].find(([, other]) => other.has(op.parent))?.[0];
          if (!destination) throw new Error("Parent block not found");
          const moved = doc.subtree(op.id).map((block) => structuredClone(block));
          record(pageId, { op: "release", id: op.id });
          record(destination, { op: "adopt", parent: op.parent, key: op.key, blocks: moved });
          continue;
        }
        if (op.op === "delete") trashed.push({ pageId, at: AT(), rows: doc.subtree(op.id).map((block) => structuredClone(block)) });
        record(pageId, op);
      }
    } catch (error) {
      return { type: "reject", txn_id: txnId, status: 400, error: error.message };
    }
    const at = AT();
    const result = { type: "ack", txn_id: txnId, seq: {}, revisions: {} };
    const changes = [];
    for (const [pageId, pageOps] of logged) {
      const doc = work.get(pageId);
      const revision = doc.revision + 1;
      doc.apply({ op: "set", id: pageId, props: { revision } });
      const all = [...pageOps, { op: "set", id: pageId, props: { revision } }];
      const entry = byId.get(pageId);
      entry.doc = doc;
      seq += 1;
      entry.log.push({ seq, at, ops: all, device: from });
      result.seq[pageId] = seq;
      result.revisions[pageId] = revision;
      changes.push({ type: "change", seq, txn_id: txnId, page_id: pageId, at, device_id: from, root: { type: doc.root.type, number: doc.root.properties.number ?? null }, ops: all });
    }
    for (const item of trashed) byId.get(item.pageId).trash.push(item);
    return { result, changes };
  }

  /** Another window (or an HTTP adapter) commits `ops` on its own: the change is pushed to every window. */
  server.commit = (ops, from = device) => {
    const outcome = apply(ops, undefined, crypto.randomUUID(), from);
    if (outcome.type === "reject") throw new Error(outcome.error);
    broadcast(outcome.changes);
    return outcome.result;
  };
  server.release = () => {
    server.holdAcks = false;
    for (const send of server.held.splice(0)) send();
  };
  server.state = (pageId) => stateOf(byId.get(pageId));
  server.page = (number) => [...byId.values()].find((entry) => entry.doc.root.properties.number === number);

  class FakeSocket {
    static OPEN = 1;
    readyState = 0;
    constructor(url) {
      this.url = url;
      sockets.push(this);
      setTimeout(() => {
        this.readyState = 1;
        this.onopen?.({});
        this.onmessage?.({ data: JSON.stringify({ type: "hello", seq, device_id: device }) });
      }, 0);
    }
    send(raw) {
      const message = JSON.parse(raw);
      if (message.type !== "txn") return;
      server.sent.push(message);
      const outcome = apply(message.ops, message.base, message.txn_id);
      const reply = () => {
        if (outcome.type === "reject") deliver(this, outcome);
        else {
          deliver(this, outcome.result);
          setTimeout(() => broadcast(outcome.changes), 1);
        }
      };
      if (server.holdAcks) server.held.push(reply);
      else reply();
    }
    close() { this.readyState = 3; this.onclose?.({}); }
  }
  server.FakeSocket = FakeSocket;

  function replay(entry, upto) {
    let doc = PageDoc.from(entry.initial);
    for (const row of entry.log) {
      if (row.seq > upto) break;
      if (row.snapshot) doc = PageDoc.from(row.snapshot);
      else for (const op of row.ops) doc.apply(op);
    }
    return doc;
  }

  /** Handle a /api/blocks request, or return null for other paths. */
  server.fetch = (url, method, body) => {
    const path = url.pathname;
    const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
    if (path === "/api/live/session" && method === "POST") return json({ token: "synthetic-token", socket: "/api/live/socket" });
    if (path === "/api/blocks/page") {
      const entry = find(url.searchParams);
      return entry ? json(stateOf(entry)) : json({ error: "Page not found" }, 404);
    }
    if (path === "/api/blocks/history") {
      const entry = find(url.searchParams);
      if (!entry) return json({ error: "Page not found" }, 404);
      const sessions = entry.log.length ? [{
        device_id: device, start: entry.log[0].at, end: entry.log[entry.log.length - 1].at, first_seq: entry.log[0].seq,
        last_seq: entry.log[entry.log.length - 1].seq, transactions: entry.log.length, ops: { text: entry.log.length },
        moments: entry.log.map((row) => ({ seq: row.seq, at: row.at })),
      }] : [];
      return json({ page_id: entry.doc.pageId, horizon_seq: 0, sessions, snapshots: [] });
    }
    if (path === "/api/blocks/history/preview") {
      const entry = find(url.searchParams);
      const doc = replay(entry, Number(url.searchParams.get("seq")));
      return json({ page_id: doc.pageId, seq: Number(url.searchParams.get("seq")), blocks: [doc.root, ...rows(doc)].map((block) => ({ ...block, deleted_at: null })) });
    }
    if (path === "/api/blocks/history/restore" && method === "POST") {
      const entry = byId.get(body.page);
      const target = replay(entry, body.seq);
      const revision = entry.doc.revision + 1;
      target.apply({ op: "set", id: target.pageId, props: { revision } });
      entry.doc = target;
      seq += 1;
      const at = AT();
      entry.log.push({ seq, at, snapshot: stateOf(entry), ops: [] });
      // The real server sends the operations of the restore; showing them needs a reload here too.
      broadcast([{ type: "change", seq, txn_id: crypto.randomUUID(), page_id: target.pageId, at, device_id: device, root: { type: "page", number: target.root.properties.number },
        ops: [{ op: "restore", id: target.pageId }, { op: "set", id: target.pageId, props: { revision } }] }]);
      return json({ txn_id: "restore", seq: { [target.pageId]: seq }, revisions: { [target.pageId]: revision } });
    }
    if (path === "/api/blocks/trash") {
      const items = [...byId.values()].flatMap((entry) => entry.trash.map((item) => ({
        id: item.rows[0].id, type: item.rows[0].type, page_id: entry.doc.pageId, deleted_at: item.at, title: item.rows[0].text,
        root: { type: entry.doc.root.type, number: entry.doc.root.properties.number, title: entry.doc.root.text },
      }))).reverse();
      return json({ items });
    }
    if (path === "/api/blocks/trash/restore" && method === "POST") {
      const entry = [...byId.values()].find((candidate) => candidate.trash.some((item) => item.rows[0].id === body.id));
      if (!entry) return json({ error: "Block is not in Trash" }, 409);
      const item = entry.trash.find((candidate) => candidate.rows[0].id === body.id);
      entry.trash = entry.trash.filter((candidate) => candidate !== item);
      server.commit(item.rows.map((block) => ({ op: "insert", id: block.id, type: block.type, parent: block.parent_id, key: block.order_key, props: block.properties, text: block.text })));
      return json({ restored: body.id });
    }
    return null;
  };
  server.pageOf = pageOf;
  return server;
}
