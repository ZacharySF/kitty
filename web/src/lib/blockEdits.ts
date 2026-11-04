// Editing actions as block transactions: each returns the operations one user action
// sends (with ids and fractional order keys) plus where the caret or selection goes.
// They read a PageDoc and never change it; the sync engine applies the operations.
import { keysBetween } from "./blockKeys.ts";
import { isTextType, visibleRows, type BlockData, type Op, type PageDoc, type Props } from "./blockDoc.ts";
import { concatMarks, marksOf, sliceMarks, type Mark, type Spec } from "./blockText.ts";

export type Focus = { id: string; offset: number };
export type Edit = { ops: Op[]; focus?: Focus; select?: string[] };

/** Types that continue when Enter splits them; Enter on an empty one leaves the list. */
const CONTINUING: Record<string, true> = { bulleted_list: true, numbered_list: true, to_do: true, toggle: true };
/** Blocks with no editable text: Backspace next to them removes them. */
export const ATOMIC: Record<string, true> = { divider: true, page_link: true };

const marksProp = (marks: Mark[]): Props => ({ marks: marks.length ? marks : null });

function readingOrder(doc: PageDoc): Map<string, number> {
  const order = new Map<string, number>();
  const walk = (parentId: string) => {
    for (const child of doc.children(parentId)) {
      if (!isTextType(child.type)) continue;
      order.set(child.id, order.size);
      walk(child.id);
    }
  };
  walk(doc.pageId);
  return order;
}

/** The selected blocks whose ancestors are not also selected, in reading order. */
export function topBlocks(doc: PageDoc, ids: string[]): BlockData[] {
  const chosen = new Set(ids.filter((id) => doc.has(id)));
  const order = readingOrder(doc);
  return [...chosen]
    .filter((id) => order.has(id))
    .filter((id) => {
      let parent = doc.get(id).parent_id;
      while (parent && parent !== doc.pageId) {
        if (chosen.has(parent)) return false;
        parent = doc.get(parent).parent_id;
      }
      return true;
    })
    .sort((a, b) => order.get(a)! - order.get(b)!)
    .map((id) => doc.get(id));
}

/** Neighbouring order keys around `index` among `parent`'s children, ignoring `moving`. */
function siblings(doc: PageDoc, parentId: string, moving: Set<string>): BlockData[] {
  return doc.children(parentId).filter((child) => !moving.has(child.id));
}

function placeAfter(doc: PageDoc, block: BlockData, count: number, moving = new Set<string>()): string[] {
  const list = siblings(doc, block.parent_id!, moving);
  const index = list.findIndex((item) => item.id === block.id);
  return keysBetween(block.order_key, list[index + 1]?.order_key ?? null, count);
}

function placeBefore(doc: PageDoc, block: BlockData, count: number, moving = new Set<string>()): string[] {
  const list = siblings(doc, block.parent_id!, moving);
  const index = list.findIndex((item) => item.id === block.id);
  return keysBetween(list[index - 1]?.order_key ?? null, block.order_key, count);
}

function firstChildKeys(doc: PageDoc, parentId: string, count: number, moving = new Set<string>()): string[] {
  return keysBetween(null, siblings(doc, parentId, moving)[0]?.order_key ?? null, count);
}

function lastChildKeys(doc: PageDoc, parentId: string, count: number, moving = new Set<string>()): string[] {
  const list = siblings(doc, parentId, moving);
  return keysBetween(list[list.length - 1]?.order_key ?? null, null, count);
}

/** Enter: split the block at the caret (or leave an empty list, or open a line above). */
export function split(doc: PageDoc, id: string, start: number, end: number, newId: () => string): Edit {
  const block = doc.get(id);
  const continues = Object.hasOwn(CONTINUING, block.type);
  if (continues && block.text === "" && block.type !== "toggle") return { ops: [{ op: "set", id, type: "text" }], focus: { id, offset: 0 } };
  const type = continues ? block.type : "text";
  const created = newId();
  if (start === 0 && end === 0 && block.text !== "") {
    const [key] = placeBefore(doc, block, 1);
    return { ops: [{ op: "insert", id: created, type, parent: block.parent_id!, key, props: {}, text: "" }], focus: { id, offset: 0 } };
  }
  const marks = marksOf(block);
  const ops: Op[] = [{ op: "text", id, text: block.text.slice(0, start) }];
  if (marks.length) ops.push({ op: "set", id, props: marksProp(sliceMarks(marks, 0, start)) });
  const after = sliceMarks(marks, end, block.text.length);
  const nested = !block.properties.collapsed && doc.children(id).some((child) => isTextType(child.type));
  const parent = nested ? id : block.parent_id!;
  const [key] = nested ? firstChildKeys(doc, id, 1) : placeAfter(doc, block, 1);
  ops.push({ op: "insert", id: created, type, parent, key, props: after.length ? { marks: after } : {}, text: block.text.slice(end) });
  return { ops, focus: { id: created, offset: 0 } };
}

/** `source` joins the end of `target`; its children follow it and it goes to Trash. */
function merge(doc: PageDoc, target: BlockData, source: BlockData): Edit {
  const ops: Op[] = [{ op: "text", id: target.id, text: target.text + source.text }];
  const marks = concatMarks(marksOf(target), target.text.length, marksOf(source));
  if (marks.length || marksOf(target).length) ops.push({ op: "set", id: target.id, props: marksProp(marks) });
  const kids = doc.children(source.id);
  if (kids.length) {
    const keys = source.parent_id === target.id ? placeBefore(doc, source, kids.length) : lastChildKeys(doc, target.id, kids.length);
    kids.forEach((kid, index) => ops.push({ op: "move", id: kid.id, parent: target.id, key: keys[index] }));
  }
  ops.push({ op: "delete", id: source.id });
  return { ops, focus: { id: target.id, offset: target.text.length } };
}

/** Backspace at the start: a typed block becomes text first, then joins the block above. */
export function mergeBackward(doc: PageDoc, id: string): Edit {
  const block = doc.get(id);
  if (block.type !== "text") return { ops: [{ op: "set", id, type: "text" }], focus: { id, offset: 0 } };
  const rows = visibleRows(doc);
  const index = rows.findIndex((row) => row.block.id === id);
  if (index <= 0) return { ops: [] };
  const previous = rows[index - 1].block;
  if (Object.hasOwn(ATOMIC, previous.type)) return { ops: [{ op: "delete", id: previous.id }], focus: { id, offset: 0 } };
  return merge(doc, previous, block);
}

/** Delete at the end: the block below joins this one. */
export function mergeForward(doc: PageDoc, id: string): Edit {
  const rows = visibleRows(doc);
  const index = rows.findIndex((row) => row.block.id === id);
  const next = rows[index + 1]?.block;
  const block = doc.get(id);
  if (!next) return { ops: [] };
  if (Object.hasOwn(ATOMIC, next.type)) return { ops: [{ op: "delete", id: next.id }], focus: { id, offset: block.text.length } };
  return merge(doc, block, next);
}

/** Tab: nest each block under its previous sibling. */
export function indent(doc: PageDoc, ids: string[]): Edit {
  const work = doc.clone();
  const ops: Op[] = [];
  for (const top of topBlocks(doc, ids)) {
    const block = work.get(top.id);
    const list = work.children(block.parent_id!).filter((item) => isTextType(item.type));
    const previous = list[list.findIndex((item) => item.id === block.id) - 1];
    if (!previous) continue;
    const moves: Op[] = [{ op: "move", id: block.id, parent: previous.id, key: lastChildKeys(work, previous.id, 1)[0] }];
    if (previous.properties.collapsed) moves.push({ op: "set", id: previous.id, props: { collapsed: false } });
    for (const op of moves) work.apply(op);
    ops.push(...moves);
  }
  return { ops };
}

/** Shift+Tab: move blocks out to follow their parent; later siblings become their children. */
export function outdent(doc: PageDoc, ids: string[]): Edit {
  const work = doc.clone();
  const ops: Op[] = [];
  const groups: BlockData[][] = [];
  for (const top of topBlocks(doc, ids)) {
    if (top.parent_id === doc.pageId) continue;
    const group = groups[groups.length - 1];
    if (group && group[0].parent_id === top.parent_id) group.push(top);
    else groups.push([top]);
  }
  for (const group of groups) {
    const parent = work.get(group[0].parent_id!);
    const moving = new Set(group.map((item) => item.id));
    const last = group[group.length - 1];
    const following = work.children(parent.id).filter((item) => !moving.has(item.id) && item.order_key > last.order_key);
    const keys = placeAfter(work, parent, group.length);
    const moves: Op[] = group.map((item, index) => ({ op: "move", id: item.id, parent: parent.parent_id!, key: keys[index] }));
    const childKeys = lastChildKeys(work, last.id, following.length);
    following.forEach((item, index) => moves.push({ op: "move", id: item.id, parent: last.id, key: childKeys[index] }));
    for (const op of moves) work.apply(op);
    ops.push(...moves);
  }
  return { ops };
}

/** Ctrl/Cmd+Shift+Up/Down: move blocks past their neighbour, or out of their parent at an end. */
export function moveBlocks(doc: PageDoc, ids: string[], direction: -1 | 1): Edit {
  const tops = topBlocks(doc, ids);
  if (!tops.length) return { ops: [] };
  const group = tops.filter((item) => item.parent_id === tops[0].parent_id);
  const moving = new Set(group.map((item) => item.id));
  const parentId = group[0].parent_id!;
  const edge = direction < 0 ? group[0] : group[group.length - 1];
  const all = doc.children(parentId).filter((item) => isTextType(item.type));
  const position = all.findIndex((item) => item.id === edge.id);
  const neighbour = direction < 0
    ? all.slice(0, position).reverse().find((item) => !moving.has(item.id))
    : all.slice(position + 1).find((item) => !moving.has(item.id));
  let target: BlockData;
  let parent: string;
  if (neighbour) {
    target = neighbour;
    parent = parentId;
  } else if (parentId !== doc.pageId) {
    target = doc.get(parentId);
    parent = target.parent_id!;
  } else return { ops: [] };
  const keys = direction < 0 ? placeBefore(doc, target, group.length, moving) : placeAfter(doc, target, group.length, moving);
  return { ops: group.map((item, index) => ({ op: "move", id: item.id, parent, key: keys[index] })), select: group.map((item) => item.id) };
}

/** Drag and drop: place blocks before, after or as the first children of `targetId`. */
export function dropBlocks(doc: PageDoc, ids: string[], targetId: string, where: "before" | "after" | "inside"): Edit {
  const tops = topBlocks(doc, ids);
  const target = doc.get(targetId);
  if (!tops.length || tops.some((item) => doc.isWithin(targetId, item.id))) return { ops: [] };
  const moving = new Set(tops.map((item) => item.id));
  const parent = where === "inside" ? target.id : target.parent_id!;
  const keys = where === "inside" ? firstChildKeys(doc, target.id, tops.length, moving)
    : where === "before" ? placeBefore(doc, target, tops.length, moving) : placeAfter(doc, target, tops.length, moving);
  const ops: Op[] = tops.map((item, index) => ({ op: "move", id: item.id, parent, key: keys[index] }));
  if (where === "inside" && target.properties.collapsed) ops.push({ op: "set", id: target.id, props: { collapsed: false } });
  return { ops, select: tops.map((item) => item.id) };
}

/** Turn into: only the type changes; text and properties stay. */
export function turnInto(doc: PageDoc, ids: string[], type: string): Edit {
  return { ops: ids.filter((id) => doc.has(id) && doc.get(id).type !== type).map((id) => ({ op: "set", id, type })) };
}

/** Delete selected blocks (with their children) to Trash; the caret goes to the block above. */
export function deleteBlocks(doc: PageDoc, ids: string[]): Edit {
  const tops = topBlocks(doc, ids);
  if (!tops.length) return { ops: [] };
  const rows = visibleRows(doc);
  const gone = (id: string) => tops.some((top) => doc.isWithin(id, top.id));
  const first = rows.findIndex((row) => row.block.id === tops[0].id);
  const before = rows.slice(0, Math.max(first, 0)).reverse().find((row) => !gone(row.block.id));
  const after = rows.slice(first + 1).find((row) => !gone(row.block.id));
  const focus = before ? { id: before.block.id, offset: before.block.text.length } : after ? { id: after.block.id, offset: 0 } : undefined;
  return { ops: tops.map((item) => ({ op: "delete", id: item.id })), ...(focus ? { focus } : {}) };
}

/**
 * Paste blocks at the caret: the first joins the current block, the rest follow it
 * (nested by their Markdown depth) and the text after the caret ends the last one.
 */
export function insertSpecs(doc: PageDoc, id: string, start: number, end: number, specs: Spec[], newId: () => string): Edit {
  if (!specs.length) return { ops: [] };
  const block = doc.get(id);
  const marks = marksOf(block);
  const before = block.text.slice(0, start);
  const after = block.text.slice(end);
  const afterMarks = sliceMarks(marks, end, block.text.length);
  const [first, ...rest] = specs;
  const firstMarks = marksOf({ properties: first.props });
  const ops: Op[] = [];
  if (before && !after && first.type !== "text") {
    // A heading or list pasted at the end of written text follows it rather than joining its text.
    return placeSpecs(doc, block, specs, first.depth, "", [], newId);
  }
  if (!before && !after) {
    const { marks: _marks, ...props } = first.props;
    ops.push({ op: "set", id, ...(first.type !== block.type ? { type: first.type } : {}), props: { ...props, ...marksProp(firstMarks) } });
    ops.push({ op: "text", id, text: first.text });
  } else {
    const joined = concatMarks(sliceMarks(marks, 0, start), before.length, firstMarks);
    const tail = rest.length ? "" : after;
    ops.push({ op: "text", id, text: before + first.text + tail });
    const all = rest.length ? joined : concatMarks(joined, before.length + first.text.length, afterMarks);
    if (all.length || marks.length) ops.push({ op: "set", id, props: marksProp(all) });
  }
  if (!rest.length) return { ops, focus: { id, offset: before.length + first.text.length } };
  const placed = placeSpecs(doc, block, rest, first.depth, after, afterMarks, newId);
  return { ops: [...ops, ...placed.ops], focus: placed.focus };
}

/** Paste blocks after `id` (with blocks selected rather than a caret). */
export function pasteAfter(doc: PageDoc, id: string, specs: Spec[], newId: () => string): Edit {
  if (!specs.length) return { ops: [] };
  const placed = placeSpecs(doc, doc.get(id), specs, specs[0].depth, "", [], newId);
  return { ops: placed.ops, focus: placed.focus, select: [] };
}

/**
 * Inserts for `specs` following `block`: depth `base` becomes its siblings, deeper
 * levels nest under the last block one level up; `after` ends the last block.
 */
function placeSpecs(doc: PageDoc, block: BlockData, specs: Spec[], base: number, after: string, afterMarks: Mark[], newId: () => string): { ops: Op[]; focus: Focus } {
  const chain: string[] = [block.id];
  const placed = specs.map((spec) => {
    const level = Math.max(0, Math.min(spec.depth - base, chain.length));
    const parent = level === 0 ? block.parent_id! : chain[level - 1];
    const created = newId();
    chain.length = level;
    chain.push(created);
    return { spec, parent, id: created };
  });
  const byParent = new Map<string, string[]>();
  for (const item of placed) byParent.set(item.parent, [...(byParent.get(item.parent) ?? []), item.id]);
  const keys = new Map<string, string>();
  for (const [parent, children] of byParent) {
    const generated = parent === block.parent_id ? placeAfter(doc, block, children.length)
      : parent === block.id ? firstChildKeys(doc, block.id, children.length) : keysBetween(null, null, children.length);
    children.forEach((child, index) => keys.set(child, generated[index]));
  }
  const last = placed[placed.length - 1];
  const ops: Op[] = placed.map((item) => {
    const own = marksOf({ properties: item.spec.props });
    const itemMarks = item === last ? concatMarks(own, item.spec.text.length, afterMarks) : own;
    const { marks: _marks, ...props } = item.spec.props;
    return { op: "insert", id: item.id, type: item.spec.type, parent: item.parent, key: keys.get(item.id)!,
      props: { ...props, ...(itemMarks.length ? { marks: itemMarks } : {}) }, text: item === last ? item.spec.text + after : item.spec.text };
  });
  return { ops, focus: { id: last.id, offset: last.spec.text.length } };
}

/** A new block of `type` right after `id` (the `/` menu on a non-empty block). */
export function insertAfter(doc: PageDoc, id: string, type: string, newId: () => string, props: Props = {}, text = ""): Edit {
  const block = doc.get(id);
  const created = newId();
  const [key] = placeAfter(doc, block, 1);
  return { ops: [{ op: "insert", id: created, type, parent: block.parent_id!, key, props, text }], focus: { id: created, offset: 0 } };
}

/** The first block of an empty page. */
export function firstBlock(doc: PageDoc, newId: () => string): Edit {
  const created = newId();
  return { ops: [{ op: "insert", id: created, type: "text", parent: doc.pageId, key: lastChildKeys(doc, doc.pageId, 1)[0], props: {}, text: "" }], focus: { id: created, offset: 0 } };
}

/** Keys that append `count` blocks after the last of another page's top-level `keys`. */
export function appendKeys(keys: string[], count: number): string[] {
  return keysBetween(keys.length ? [...keys].sort().pop()! : null, null, count);
}
