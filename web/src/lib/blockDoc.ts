// One page's blocks as the block editor holds them: the same operation vocabulary the
// server applies (src/kitty/core/blocks/pagedoc.py), over the live blocks of one page.
// Deleted blocks leave this document; a canvas block is kept but its elements belong
// to the canvas UI, so operations about them are ignored here.
export type Props = Record<string, unknown>;
export type BlockData = {
  id: string;
  type: string;
  parent_id: string | null;
  order_key: string;
  properties: Props;
  text: string;
};
/** A block moved between pages, as the server's ``adopt`` operation carries it. */
export type AdoptedRow = BlockData & { deleted_at?: string | null };
/**
 * Operations are plain JSON objects, exactly as they travel in a transaction. The editor
 * always sends order keys, and the server logs every placed operation with its key.
 */
export type Op =
  | { op: "insert"; id: string; type: string; parent: string; key: string; props?: Props; text?: string }
  | { op: "set"; id: string; type?: string; props?: Props }
  | { op: "text"; id: string; text: string }
  | { op: "text"; id: string; at: number; del: number; ins: string }
  | { op: "move"; id: string; parent: string; key: string }
  | { op: "delete" | "restore" | "release" | "purge"; id: string }
  | { op: "adopt"; parent: string; key: string; blocks: AdoptedRow[] };
/** What GET /api/blocks/page returns. */
export type PageState = { page_id: string; seq: number; revision: number; root: BlockData; blocks: BlockData[] };
export type Row = { block: BlockData; depth: number; number?: number; hasChildren: boolean };

/** Text-flow block types the editor writes; equal to convert.OUTLINE_TYPES on the server. */
const TEXT_TYPES: Record<string, true> = {
  text: true, heading_1: true, heading_2: true, heading_3: true, to_do: true, bulleted_list: true, numbered_list: true,
  quote: true, callout: true, code: true, divider: true, toggle: true, page_link: true,
};
/** Types whose children belong to another UI. */
const OPAQUE_TYPES: Record<string, true> = { canvas: true };
const VIEW_KEYS: Record<string, true> = { viewport: true, viewport_revision: true, favorite: true, last_opened: true, position: true };
/** A block's state for one tag (blockTags.ts): not an edit either. */
const TAG_KEY = /^tag_[0-9a-f]{32}$/;

export function isTextType(type: string): boolean {
  return Object.hasOwn(TEXT_TYPES, type);
}
function isOpaque(block: BlockData): boolean {
  return Object.hasOwn(OPAQUE_TYPES, block.type);
}

export class BlockConflict extends Error {}

function copy(block: BlockData): BlockData {
  return { ...block, properties: structuredClone(block.properties) };
}

export class PageDoc {
  readonly pageId: string;
  private readonly blocks = new Map<string, BlockData>();
  private readonly kids = new Map<string, Set<string>>();

  private constructor(pageId: string) {
    this.pageId = pageId;
  }

  static from(state: PageState): PageDoc {
    const doc = new PageDoc(state.page_id);
    doc.attach(copy(state.root));
    for (const block of state.blocks) doc.attach(copy(block));
    return doc;
  }

  clone(): PageDoc {
    const doc = new PageDoc(this.pageId);
    for (const block of this.blocks.values()) doc.blocks.set(block.id, copy(block));
    for (const [parent, ids] of this.kids) doc.kids.set(parent, new Set(ids));
    return doc;
  }

  get root(): BlockData {
    return this.blocks.get(this.pageId)!;
  }

  get revision(): number {
    return Number(this.root.properties.revision ?? 1);
  }

  has(id: string): boolean {
    return this.blocks.has(id);
  }

  get(id: string): BlockData {
    const block = this.blocks.get(id);
    if (!block) throw new BlockConflict("Block not found on this page");
    return block;
  }

  children(parentId: string): BlockData[] {
    const ids = this.kids.get(parentId);
    if (!ids) return [];
    return [...ids].map((id) => this.blocks.get(id)!).sort((a, b) => (a.order_key < b.order_key ? -1 : a.order_key > b.order_key ? 1 : a.id < b.id ? -1 : 1));
  }

  depth(id: string): number {
    let level = 0;
    let current = this.get(id);
    while (current.parent_id !== null && current.parent_id !== this.pageId) {
      level += 1;
      current = this.get(current.parent_id);
    }
    return level;
  }

  /** `id` itself and every block below it, parents first. */
  subtree(id: string): BlockData[] {
    const result: BlockData[] = [];
    const walk = (bid: string) => {
      result.push(this.get(bid));
      for (const child of this.children(bid)) walk(child.id);
    };
    walk(id);
    return result;
  }

  isWithin(id: string, ancestor: string): boolean {
    let current: string | null = id;
    while (current !== null) {
      if (current === ancestor) return true;
      current = this.blocks.get(current)?.parent_id ?? null;
    }
    return false;
  }

  private attach(block: BlockData): void {
    this.blocks.set(block.id, block);
    if (block.parent_id !== null) {
      let ids = this.kids.get(block.parent_id);
      if (!ids) this.kids.set(block.parent_id, (ids = new Set()));
      ids.add(block.id);
    }
  }

  private detach(id: string): void {
    const block = this.blocks.get(id);
    if (!block) return;
    for (const child of this.children(id)) this.detach(child.id);
    if (block.parent_id !== null) this.kids.get(block.parent_id)?.delete(id);
    this.kids.delete(id);
    this.blocks.delete(id);
  }

  /**
   * Apply one operation. Strict (the default, for this window's own edits) throws
   * BlockConflict when the operation does not fit; lenient (for changes pushed by the
   * server) ignores operations about blocks this document does not hold and returns
   * true when the page must be reloaded to show the change.
   */
  apply(op: Op, strict = true): boolean {
    const fail = (message: string): boolean => {
      if (strict) throw new BlockConflict(message);
      return false;
    };
    switch (op.op) {
      case "insert": {
        if (this.blocks.has(op.id)) return fail("Block already exists");
        const parent = this.blocks.get(op.parent);
        if (!parent || isOpaque(parent)) return fail("Parent block not found on this page");
        this.attach({ id: op.id, type: op.type, parent_id: parent.id, order_key: op.key, properties: structuredClone(op.props ?? {}), text: op.text ?? "" });
        return false;
      }
      case "set": {
        const block = this.blocks.get(op.id);
        if (!block) return fail("Block not found on this page");
        if (op.type !== undefined) block.type = op.type;
        for (const [key, value] of Object.entries(op.props ?? {})) {
          if (value === null) delete block.properties[key];
          else block.properties[key] = structuredClone(value);
        }
        return false;
      }
      case "text": {
        const block = this.blocks.get(op.id);
        if (!block) return fail("Block not found on this page");
        if ("text" in op) block.text = op.text;
        else {
          const points = Array.from(block.text);
          if (op.at < 0 || op.at > points.length || op.del < 0 || op.at + op.del > points.length) return fail("Text splice is outside the block's text");
          points.splice(op.at, op.del, op.ins);
          block.text = points.join("");
        }
        return false;
      }
      case "move": {
        const block = this.blocks.get(op.id);
        const parent = this.blocks.get(op.parent);
        // A block this window never held arriving under one it shows needs the page reloaded.
        if (!block) return strict ? fail("Block not found on this page") : parent !== undefined && !isOpaque(parent);
        if (block.parent_id === null) return fail("A page's root block cannot be moved");
        if (!parent || isOpaque(parent)) {
          if (strict) return fail("Parent block not found on this page");
          this.detach(op.id);
          return false;
        }
        if (this.isWithin(parent.id, block.id)) return fail("Moving a block inside itself would create a cycle");
        this.kids.get(block.parent_id)?.delete(block.id);
        block.parent_id = parent.id;
        block.order_key = op.key;
        this.attach(block);
        return false;
      }
      case "delete":
      case "release":
      case "purge": {
        if (!this.blocks.has(op.id)) return op.op === "purge" ? false : fail("Block not found on this page");
        if (op.id === this.pageId) return !strict;
        this.detach(op.id);
        return false;
      }
      case "restore":
        // Trashed blocks are not held here; showing them again needs their data.
        if (strict) throw new BlockConflict("Restore through Trash");
        return true;
      case "adopt": {
        const parent = this.blocks.get(op.parent);
        if (!parent || isOpaque(parent)) return fail("Parent block not found on this page");
        const kept = new Set<string>([parent.id]);
        op.blocks.forEach((row, index) => {
          const parentId = index === 0 ? parent.id : row.parent_id;
          if (row.deleted_at || parentId === null || !kept.has(parentId) || this.blocks.has(row.id)) return;
          kept.add(row.id);
          this.attach({ id: row.id, type: row.type, parent_id: parentId, order_key: index === 0 ? op.key : row.order_key,
            properties: structuredClone(row.properties ?? {}), text: row.text ?? "" });
        });
        return false;
      }
    }
  }
}

/** True when a change only touches view state or tag states (no revision bump; safe to apply twice). */
export function viewOnly(ops: Op[]): boolean {
  return ops.every((op) => op.op === "set" && op.type === undefined && Object.keys(op.props ?? {}).every((key) => Object.hasOwn(VIEW_KEYS, key) || TAG_KEY.test(key)));
}

/** The editor's rows: text-flow blocks in reading order; collapsed blocks hide their children. */
export function visibleRows(doc: PageDoc): Row[] {
  const rows: Row[] = [];
  const walk = (parentId: string, depth: number) => {
    let number = 0;
    for (const block of doc.children(parentId)) {
      if (!isTextType(block.type)) {
        number = 0;
        continue;
      }
      number = block.type === "numbered_list" ? number + 1 : 0;
      const hasChildren = doc.children(block.id).some((child) => isTextType(child.type));
      rows.push({ block, depth, hasChildren, ...(block.type === "numbered_list" ? { number } : {}) });
      if (!block.properties.collapsed) walk(block.id, depth + 1);
    }
  };
  walk(doc.pageId, 0);
  return rows;
}

/** The splice turning `before` into `after`, in Unicode code points as the server counts them. */
export function codePointSplice(before: string, after: string): { at: number; del: number; ins: string } | null {
  if (before === after) return null;
  const a = Array.from(before);
  const b = Array.from(after);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
  let end = 0;
  while (end < a.length - start && end < b.length - start && a[a.length - 1 - end] === b[b.length - 1 - end]) end += 1;
  return { at: start, del: a.length - start - end, ins: b.slice(start, b.length - end).join("") };
}
