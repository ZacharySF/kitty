// Block text for the editor: inline marks (bold, italic, code, link) kept beside plain
// text, Markdown shortcuts typed at the start of a block, Markdown paste and copy.
// A block's `text` stays plain so search, backlinks, [[links]], #tags and ((refs))
// work unchanged; inline formatting lives in its `marks` property as ranges in
// UTF-16 offsets of that text.
import type { BlockData, Props } from "./blockDoc.ts";

export type MarkType = "b" | "i" | "c" | "a";
export type Mark = { s: number; e: number; t: MarkType; href?: string };
/** A block parsed from Markdown; `depth` is its nesting below the first pasted level. */
export type Spec = { type: string; text: string; props: Props; depth: number };

/** Outermost first: how marks nest when rendered and written as Markdown. */
const ORDER: MarkType[] = ["a", "b", "i", "c"];
const TAGS: Record<MarkType, string> = { a: "a", b: "strong", i: "em", c: "code" };

export function marksOf(block: Pick<BlockData, "properties">): Mark[] {
  const value = block.properties.marks;
  return Array.isArray(value) ? (value as Mark[]) : [];
}

/** `href` when it is a web or mail link; anything else (javascript:, data:, relative) is not linked. */
export function safeHref(href: string | undefined): string | null {
  try {
    return href && ["http:", "https:", "mailto:"].includes(new URL(href).protocol) ? href : null;
  } catch {
    return null;
  }
}

const same = (a: Mark, b: Mark) => a.t === b.t && (a.href ?? "") === (b.href ?? "");

/** Sorted, merged, non-empty marks within `length`. */
export function normalizeMarks(marks: Mark[], length = Infinity): Mark[] {
  const clipped = marks
    .map((mark) => ({ ...mark, s: Math.max(0, mark.s), e: Math.min(length, mark.e) }))
    .filter((mark) => mark.e > mark.s)
    .sort((a, b) => ORDER.indexOf(a.t) - ORDER.indexOf(b.t) || (a.href ?? "").localeCompare(b.href ?? "") || a.s - b.s);
  const merged: Mark[] = [];
  for (const mark of clipped) {
    const last = merged[merged.length - 1];
    if (last && same(last, mark) && mark.s <= last.e) last.e = Math.max(last.e, mark.e);
    else merged.push({ ...mark });
  }
  return merged.sort((a, b) => a.s - b.s || ORDER.indexOf(a.t) - ORDER.indexOf(b.t));
}

/** Add `t` over [s, e), or remove it when the whole range already has it. */
export function toggleMark(text: string, marks: Mark[], s: number, e: number, t: MarkType, href?: string): Mark[] {
  if (e <= s) return normalizeMarks(marks, text.length);
  const covered = (() => {
    let at = s;
    for (const mark of [...marks].filter((m) => m.t === t).sort((a, b) => a.s - b.s)) {
      if (mark.s <= at && mark.e > at) at = mark.e;
    }
    return at >= e;
  })();
  const kept: Mark[] = [];
  for (const mark of marks) {
    if (mark.t !== t || mark.e <= s || mark.s >= e) kept.push(mark);
    else {
      if (mark.s < s) kept.push({ ...mark, e: s });
      if (mark.e > e) kept.push({ ...mark, s: e });
    }
  }
  if (!covered || (t === "a" && href)) kept.push({ s, e, t, ...(t === "a" && href ? { href } : {}) });
  return normalizeMarks(kept, text.length);
}

/** Marks of the text range [s, e), re-based to start at 0. */
export function sliceMarks(marks: Mark[], s: number, e: number): Mark[] {
  return normalizeMarks(marks.map((mark) => ({ ...mark, s: mark.s - s, e: Math.min(mark.e, e) - s })), e - s);
}

/** Marks of `a` followed by text of length `offset`'s neighbour `b`. */
export function concatMarks(a: Mark[], offset: number, b: Mark[]): Mark[] {
  return normalizeMarks([...a, ...b.map((mark) => ({ ...mark, s: mark.s + offset, e: mark.e + offset }))]);
}

const escape = (value: string) => value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const attribute = (value: string) => escape(value).replace(/"/g, "&quot;");

function segments(text: string, marks: Mark[]): { text: string; marks: Mark[] }[] {
  const cuts = new Set([0, text.length]);
  for (const mark of marks) {
    cuts.add(Math.max(0, Math.min(text.length, mark.s)));
    cuts.add(Math.max(0, Math.min(text.length, mark.e)));
  }
  const points = [...cuts].sort((a, b) => a - b);
  const result: { text: string; marks: Mark[] }[] = [];
  for (let index = 0; index + 1 < points.length; index += 1) {
    const [s, e] = [points[index], points[index + 1]];
    if (e > s) result.push({ text: text.slice(s, e), marks: ORDER.flatMap((t) => marks.filter((m) => m.t === t && m.s <= s && m.e >= e).slice(0, 1)) });
  }
  return result;
}

/** Escaped HTML for a block's contenteditable; a trailing newline gets a placeholder <br>. */
export function marksHTML(text: string, marks: Mark[]): string {
  let html = "";
  let open: Mark[] = [];
  const opening = (mark: Mark) => mark.t === "a" ? `<a href="${attribute(safeHref(mark.href)!)}" rel="noopener noreferrer">` : `<${TAGS[mark.t]}>`;
  for (const part of segments(text, marks.filter((mark) => mark.t !== "a" || safeHref(mark.href)))) {
    let common = 0;
    while (common < open.length && common < part.marks.length && same(open[common], part.marks[common])) common += 1;
    for (const mark of open.slice(common).reverse()) html += `</${TAGS[mark.t]}>`;
    for (const mark of part.marks.slice(common)) html += opening(mark);
    open = part.marks;
    html += escape(part.text);
  }
  for (const mark of [...open].reverse()) html += `</${TAGS[mark.t]}>`;
  return text.endsWith("\n") ? `${html}<br data-tail="">` : html;
}

/** Inline Markdown for one block's text and marks. */
export function inlineMarkdown(text: string, marks: Mark[]): string {
  let output = "";
  let open: Mark[] = [];
  const close = (mark: Mark) => (mark.t === "a" ? `](${mark.href ?? ""})` : mark.t === "b" ? "**" : mark.t === "i" ? "*" : "`");
  const start = (mark: Mark) => (mark.t === "a" ? "[" : mark.t === "b" ? "**" : mark.t === "i" ? "*" : "`");
  for (const part of segments(text, marks)) {
    let common = 0;
    while (common < open.length && common < part.marks.length && same(open[common], part.marks[common])) common += 1;
    for (const mark of open.slice(common).reverse()) output += close(mark);
    for (const mark of part.marks.slice(common)) output += start(mark);
    open = part.marks;
    output += part.text;
  }
  for (const mark of [...open].reverse()) output += close(mark);
  return output;
}

const INLINE = /\*\*([^*\n]+)\*\*|__([^_\n]+)__|(?<![*\w])\*([^*\n]+)\*(?!\*)|(?<![_\w])_([^_\n]+)_(?![_\w])|`([^`\n]+)`|(?<!\[)\[([^\[\]\n]+)\]\(((?:https?:|mailto:)[^)\s]+)\)/g;

/** Plain text and marks from inline Markdown (bold, italic, code, links); [[links]] stay text. */
export function parseInline(markdown: string): { text: string; marks: Mark[] } {
  let text = "";
  const marks: Mark[] = [];
  let last = 0;
  for (const match of markdown.matchAll(INLINE)) {
    text += markdown.slice(last, match.index);
    last = match.index! + match[0].length;
    const [bold, bold2, italic, italic2, code, label, href] = match.slice(1);
    const inner = bold ?? bold2 ?? italic ?? italic2 ?? code ?? label ?? "";
    const t: MarkType = bold !== undefined || bold2 !== undefined ? "b" : italic !== undefined || italic2 !== undefined ? "i" : code !== undefined ? "c" : "a";
    if (t === "c") {
      marks.push({ s: text.length, e: text.length + inner.length, t });
      text += inner;
      continue;
    }
    const nested = parseInline(inner);
    marks.push(...nested.marks.map((mark) => ({ ...mark, s: mark.s + text.length, e: mark.e + text.length })));
    marks.push({ s: text.length, e: text.length + nested.text.length, t, ...(t === "a" ? { href } : {}) });
    text += nested.text;
  }
  text += markdown.slice(last);
  return { text, marks: normalizeMarks(marks, text.length) };
}

/**
 * A Markdown shortcut typed at the start of a block: the type it turns into. Code
 * blocks keep what is typed, and a marker for the block's own type stays text.
 */
export function shortcut(text: string, type: string): { type: string; text: string; strip: number; props: Props } | null {
  if (type === "code") return null;
  const whole: Record<string, string> = { "---": "divider", "```": "code" };
  const match = /^(#{1,3}|[-*+]|\d{1,9}[.)]|\[[ xX]?\]|>) /.exec(text);
  let result: { type: string; text: string; strip: number; props: Props } | null = null;
  if (Object.hasOwn(whole, text)) result = { type: whole[text], text: "", strip: 3, props: {} };
  else if (match) {
    const marker = match[1];
    const strip = match[0].length;
    const rest = text.slice(strip);
    if (marker.startsWith("#")) result = { type: `heading_${marker.length}`, text: rest, strip, props: {} };
    else if (marker.startsWith("[")) result = { type: "to_do", text: rest, strip, props: { checked: /x/i.test(marker) } };
    else if (marker === ">") result = { type: "quote", text: rest, strip, props: {} };
    else if (/\d/.test(marker)) result = { type: "numbered_list", text: rest, strip, props: {} };
    else result = { type: "bulleted_list", text: rest, strip, props: {} };
  }
  return result && result.type !== type ? result : null;
}

/** Blocks for pasted Markdown or plain text: one block per line, fenced code kept whole. */
export function parseMarkdown(markdown: string): Spec[] {
  const lines = markdown.replace(/\r\n?/g, "\n").split("\n");
  const specs: Spec[] = [];
  const indents: number[] = [];
  const depthOf = (indent: number) => {
    while (indents.length && indents[indents.length - 1] >= indent) indents.pop();
    indents.push(indent);
    return indents.length - 1;
  };
  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index].replace(/\t/g, "  ");
    if (!raw.trim()) continue;
    const indent = raw.length - raw.trimStart().length;
    const line = raw.trim();
    const fence = /^(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/.exec(line);
    if (fence) {
      const body: string[] = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith(fence[1])) body.push(lines[index++]);
      specs.push({ type: "code", text: body.join("\n"), props: fence[2] ? { language: fence[2] } : {}, depth: depthOf(indent) });
      continue;
    }
    const depth = depthOf(indent);
    if (/^([-*_])( *\1){2,}$/.test(line)) {
      specs.push({ type: "divider", text: "", props: {}, depth });
      continue;
    }
    let type = "text";
    let rest = line;
    const props: Props = {};
    let match: RegExpExecArray | null;
    if ((match = /^(#{1,6})\s+(.*)$/.exec(line))) {
      type = `heading_${Math.min(3, match[1].length)}`;
      rest = match[2];
    } else if ((match = /^(?:[-*+]\s+)?\[([ xX]?)\]\s+(.*)$/.exec(line))) {
      type = "to_do";
      props.checked = match[1].toLowerCase() === "x";
      rest = match[2];
    } else if ((match = /^[-*+]\s+(.*)$/.exec(line))) {
      type = "bulleted_list";
      rest = match[1];
    } else if ((match = /^\d{1,9}[.)]\s+(.*)$/.exec(line))) {
      type = "numbered_list";
      rest = match[1];
    } else if ((match = /^>\s?(.*)$/.exec(line))) {
      type = "quote";
      rest = match[1];
    }
    const inline = parseInline(rest);
    if (inline.marks.length) props.marks = inline.marks;
    specs.push({ type, text: inline.text, props, depth });
  }
  return specs;
}

/** Markdown for copied blocks; `depth` is relative to the shallowest one. */
export function blocksMarkdown(entries: { block: BlockData; depth: number }[]): string {
  const base = Math.min(...entries.map((entry) => entry.depth));
  const counters: number[] = [];
  return entries.map(({ block, depth }) => {
    const level = depth - base;
    const indent = "  ".repeat(level);
    counters.length = level + 1;
    counters[level] = block.type === "numbered_list" ? (counters[level] ?? 0) + 1 : 0;
    const text = inlineMarkdown(block.text, marksOf(block));
    switch (block.type) {
      case "heading_1": return `${indent}# ${text}`;
      case "heading_2": return `${indent}## ${text}`;
      case "heading_3": return `${indent}### ${text}`;
      case "to_do": return `${indent}- [${block.properties.checked ? "x" : " "}] ${text}`;
      case "bulleted_list": case "toggle": return `${indent}- ${text}`;
      case "numbered_list": return `${indent}${counters[level]}. ${text}`;
      case "quote": case "callout": return `${indent}> ${text}`;
      case "divider": return `${indent}---`;
      case "code": return `${indent}\`\`\`${String(block.properties.language ?? "")}\n${block.text}\n${indent}\`\`\``;
      case "page_link": return `${indent}[[${block.text}]]`;
      default: return `${indent}${text}`;
    }
  }).join("\n");
}
