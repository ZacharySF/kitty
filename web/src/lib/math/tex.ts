import katex from "katex";

/**
 * KaTeX for the whole app (design: Rendering). Math is written between `\( … \)` or
 * `$…$` (inline) and `\[ … \]` or `$$…$$` (display), as Anki and the math prompts do.
 * A single `$` follows Pandoc's rule so prices stay text: the opening `$` is not followed
 * by a space and the closing one is not preceded by a space or followed by a digit.
 */
export type MathSegment = { kind: "text" | "inline" | "display"; value: string };

const PAIRS: [open: string, close: string, kind: MathSegment["kind"]][] = [
  ["$$", "$$", "display"],
  ["\\[", "\\]", "display"],
  ["\\(", "\\)", "inline"],
];

/** The end of the inline `$…$` opened at `start`, or -1. */
function closingDollar(text: string, start: number): number {
  if (!text[start + 1] || /\s/.test(text[start + 1])) return -1;
  for (let at = start + 1; at < text.length; at += 1) {
    if (text[at] === "\\") {
      at += 1;
      continue;
    }
    if (text[at] !== "$") continue;
    if (/\s/.test(text[at - 1]) || /\d/.test(text[at + 1] ?? "")) continue;
    return at;
  }
  return -1;
}

export function splitMath(text: string): MathSegment[] {
  const parts: MathSegment[] = [];
  let plain = "";
  const flush = () => {
    if (plain) parts.push({ kind: "text", value: plain });
    plain = "";
  };
  for (let at = 0; at < text.length; ) {
    if (text.startsWith("\\$", at)) {
      plain += "$";
      at += 2;
      continue;
    }
    const pair = PAIRS.find(([open]) => text.startsWith(open, at));
    if (pair) {
      const end = text.indexOf(pair[1], at + pair[0].length);
      if (end > at + pair[0].length) {
        flush();
        parts.push({ kind: pair[2], value: text.slice(at + pair[0].length, end).trim() });
        at = end + pair[1].length;
        continue;
      }
    } else if (text[at] === "$") {
      const end = closingDollar(text, at);
      if (end > 0) {
        flush();
        parts.push({ kind: "inline", value: text.slice(at + 1, end) });
        at = end + 1;
        continue;
      }
    }
    plain += text[at];
    at += 1;
  }
  flush();
  return parts;
}

/** KaTeX HTML (with MathML for screen readers); bad TeX shows as an inline error, never throws. */
export function renderTex(source: string, display = false): string {
  return katex.renderToString(source, { displayMode: display, throwOnError: false, output: "htmlAndMathml", strict: "ignore" });
}

const escape = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** Escaped text with `**bold**`, `*italic*`/`_italic_` and `` `code` ``. */
function inline(text: string): string {
  return text
    .split(/(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|_[^_\n]+_)/g)
    .map((token) => {
      if (/^`[^`]+`$/.test(token)) return `<code>${escape(token.slice(1, -1))}</code>`;
      if (/^\*\*[^*]+\*\*$/.test(token)) return `<strong>${escape(token.slice(2, -2))}</strong>`;
      if (/^(\*[^*]+\*|_[^_]+_)$/.test(token)) return `<em>${escape(token.slice(1, -1))}</em>`;
      return escape(token);
    })
    .join("");
}

/** Text with math, as HTML: math rendered, the rest escaped with light emphasis. */
export function mathInline(text: string): string {
  return splitMath(text)
    .map((part) => (part.kind === "text" ? inline(part.value) : renderTex(part.value, part.kind === "display")))
    .join("");
}

/** A checker's canonical answer is bare TeX ("\frac{3}{7} \approx 0.4286"); one already delimited (a choice) is kept. */
export function asMath(tex: string): string {
  return splitMath(tex).some((part) => part.kind !== "text") ? tex : `$${tex}$`;
}

/** A fenced code block: ```lang … ``` on lines of their own. */
const FENCE = /^```([\w+-]*)[ \t]*\n([\s\S]*?)\n?^```[ \t]*$/gm;

/**
 * A math prompt (Markdown subset): blank lines make paragraphs; math is never read as
 * Markdown; a fenced code block (a loop to count) stays verbatim, with no math in it.
 */
export function mathMarkdown(source: string): string {
  // split() with the fence's two groups gives [text, lang, code, text, lang, code, …, text].
  const parts = source.split(FENCE);
  let html = "";
  for (let index = 0; index < parts.length; index += 3) {
    html += parts[index]
      .split(/\n\s*\n/)
      .map((block) => block.trim())
      .filter(Boolean)
      .map((block) => `<p>${mathInline(block).replace(/\n/g, "<br>")}</p>`)
      .join("");
    if (index + 2 < parts.length) html += `<pre class="code"><code${parts[index + 1] ? ` data-lang="${escape(parts[index + 1])}"` : ""}>${escape(parts[index + 2])}</code></pre>`;
  }
  return html;
}

const SKIP: Record<string, true> = { CODE: true, PRE: true, SCRIPT: true, STYLE: true, TEXTAREA: true, KBD: true, SAMP: true };

/**
 * Renders the math inside already-sanitised HTML (a flashcard face) without touching its
 * markup: only text nodes outside code are read. Works on an inert template, so it is
 * safe to call before the HTML is placed with `{@html}`.
 */
export function typesetHtml(html: string): string {
  if (!/\\\(|\\\[|\$/.test(html)) return html;
  const template = document.createElement("template");
  template.innerHTML = html;
  const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => {
      for (let parent = node.parentElement; parent; parent = parent.parentElement) if (SKIP[parent.tagName]) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const texts: Text[] = [];
  while (walker.nextNode()) texts.push(walker.currentNode as Text);
  for (const node of texts) {
    const parts = splitMath(node.data);
    if (!parts.some((part) => part.kind !== "text")) continue;
    const holder = document.createElement("template");
    holder.innerHTML = parts.map((part) => (part.kind === "text" ? escape(part.value) : renderTex(part.value, part.kind === "display"))).join("");
    node.replaceWith(holder.content);
  }
  return template.innerHTML;
}
