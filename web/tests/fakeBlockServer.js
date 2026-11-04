// An in-memory stand-in for rika!'s block backend (src/kitty/web_blocks.py, web_live.py) for
// browser harnesses: GET /api/blocks/page, the live WebSocket (txn → ack/reject, change to
// every window), history sessions (per actor)/preview/restore, undoing an agent session and
// Trash. Records are synthetic.
// It applies operations with the editor's own PageDoc; the real server's semantics are
// covered by the Python tests.
import { PageDoc } from "/src/lib/blockDoc.ts";

const AT = () => new Date().toISOString();
/** Who made a transaction (src/kitty/core/blocks/actor.py); windows are the person. */
export const USER = { kind: "user", name: null, session: null };

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
    /** Bodies of POST /api/blocks/sessions/undo, in order. */
    undos: [],
  };

  for (const spec of pages) {
    const root = { id: spec.page_id, type: spec.type ?? "page", parent_id: null, order_key: "a0", properties: { revision: spec.revision ?? 1, number: spec.number }, text: spec.title };
    const state = { page_id: spec.page_id, seq: 0, revision: root.properties.revision, root, blocks: spec.blocks };
    // Creating the page is its first transaction, as on kitty's server.
    seq += 1;
    byId.set(spec.page_id, { initial: structuredClone(state), doc: PageDoc.from(state), log: [{ seq, at: AT(), snapshot: structuredClone(state), ops: [], actor: USER }], trash: [] });
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
  function apply(ops, base, txnId, from = device, actor = USER) {
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
      entry.log.push({ seq, at, ops: all, device: from, actor });
      result.seq[pageId] = seq;
      result.revisions[pageId] = revision;
      changes.push({ type: "change", seq, txn_id: txnId, page_id: pageId, at, device_id: from, actor, root: { type: doc.root.type, number: doc.root.properties.number ?? null }, ops: all });
    }
    for (const item of trashed) byId.get(item.pageId).trash.push(item);
    return { result, changes };
  }

  /** Another window, an HTTP adapter or an agent (`actor`) commits `ops` on its own: the change is pushed to every window. */
  server.commit = (ops, from = device, actor = USER) => {
    const outcome = apply(ops, undefined, crypto.randomUUID(), from, actor);
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
      // One session per run of transactions by the same device and actor, newest first.
      const sessions = [];
      for (const row of entry.log) {
        const actor = row.actor ?? USER;
        const last = sessions[sessions.length - 1];
        if (!last || last.device_id !== (row.device ?? device) || JSON.stringify(last.actor) !== JSON.stringify(actor)) {
          sessions.push({ device_id: row.device ?? device, actor, start: row.at, end: row.at, first_seq: row.seq, last_seq: row.seq,
            transactions: 0, ops: {}, moments: [] });
        }
        const current = sessions[sessions.length - 1];
        current.end = row.at;
        current.last_seq = row.seq;
        current.transactions += 1;
        current.ops.text = (current.ops.text ?? 0) + 1;
        current.moments.push({ seq: row.seq, at: row.at });
      }
      return json({ page_id: entry.doc.pageId, horizon_seq: 0, sessions: sessions.reverse(), snapshots: [] });
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
    if (path === "/api/blocks/sessions/undo" && method === "POST") {
      // Text-only stand-in for kitty.core.blocks.sessions.undo_session: a block whose text is still
      // what the session left goes back to what it found; one changed since is kept and reported.
      server.undos.push(body);
      const reverted = [], conflicts = [], ops = [];
      let transactions = 0, actor = null;
      for (const entry of byId.values()) {
        const rows = entry.log.filter((row) => row.actor?.session === body.session);
        if (!rows.length) continue;
        transactions += rows.length;
        actor = rows[0].actor;
        const before = replay(entry, rows[0].seq - 1), after = replay(entry, rows[rows.length - 1].seq);
        for (const id of new Set(rows.flatMap((row) => row.ops.filter((op) => op.op === "text").map((op) => op.id)))) {
          const now = entry.doc.get(id).text, item = { id, page_id: entry.doc.pageId, text: now };
          if (now === after.get(id).text) { reverted.push({ ...item, fields: ["text"] }); ops.push({ op: "text", id, text: before.get(id).text }); }
          else if (now !== before.get(id).text) conflicts.push({ ...item, fields: ["text"], reasons: ["changed by someone else since"] });
        }
      }
      if (!transactions) return json({ error: "No kept history belongs to that session" }, 404);
      const report = { session: body.session, actor, transactions, dry_run: !!body.dry_run, applied: false,
        reverted_count: reverted.length, conflict_count: conflicts.length, reverted, conflicts };
      if (body.dry_run || !ops.length) return json(report);
      const result = server.commit(ops);
      return json({ ...report, applied: true, txn_id: crypto.randomUUID(), seq: result.seq, revisions: result.revisions });
    }
    if (path === "/api/blocks/citations") {
      // Every pinned citation reads as changed since: its block's text now against the cited moment.
      const citations = url.searchParams.get("cites").split(",").map((cite) => {
        const [id, cited] = cite.split("@");
        const entry = pageOf(id);
        const then = entry ? replay(entry, Number(cited)) : null;
        const was = then?.has(id) ? then.get(id).text : null, now = entry ? entry.doc.get(id).text : null;
        const changed = was !== null && now !== was;
        return { cite: `((${cite}))`, id, cited_seq: Number(cited), seq: changed ? seq : Number(cited), changed,
          status: was === null ? "unknown" : changed ? "changed" : "unchanged", then: was === null ? null : { type: "text", text: was },
          now: now === null ? null : { type: "text", text: now },
          diff: changed ? [{ op: "delete", text: was }, { op: "insert", text: now }] : [] };
      });
      return json({ citations });
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
