// Run with Node >=22.6: node --experimental-strip-types --test tests/mathTex.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { mathMarkdown, renderTex, splitMath } from "../src/lib/math/tex.ts";

const kinds = (text) => splitMath(text).map((part) => [part.kind, part.value]);

test("Anki's \\( … \\) and \\[ … \\] delimiters split out inline and display math", () => {
  assert.deepEqual(kinds("Area \\(\\pi r^2\\) and \\[\\int_0^1 x\\,dx\\] done"), [
    ["text", "Area "], ["inline", "\\pi r^2"], ["text", " and "], ["display", "\\int_0^1 x\\,dx"], ["text", " done"],
  ]);
});

test("$…$ is inline and $$…$$ display; prices and escaped dollars stay text", () => {
  assert.deepEqual(kinds("Roll two d6: what is $\\mathbb{E}[\\max]$?"), [["text", "Roll two d6: what is "], ["inline", "\\mathbb{E}[\\max]"], ["text", "?"]]);
  assert.deepEqual(kinds("$$a^2+b^2$$"), [["display", "a^2+b^2"]]);
  // Pandoc's rule: an opening $ is not followed by a space, a closing $ is not before a digit.
  assert.deepEqual(kinds("It costs $5 and $10 today"), [["text", "It costs $5 and $10 today"]]);
  assert.deepEqual(kinds("Pay $ 3 or $x$"), [["text", "Pay $ 3 or "], ["inline", "x"]]);
  assert.deepEqual(kinds("An escaped \\$ sign"), [["text", "An escaped $ sign"]]);
  assert.deepEqual(kinds("Unclosed \\( x"), [["text", "Unclosed \\( x"]]);
});

test("renderTex draws KaTeX markup and never throws on bad TeX", () => {
  const html = renderTex("\\frac{3}{7}");
  assert.match(html, /class="katex"/);
  assert.match(html, /mfrac|frac-line/);
  assert.match(renderTex("x", true), /katex-display/);
  assert.doesNotThrow(() => renderTex("\\frac{"));
  assert.match(renderTex("\\frac{"), /katex-error|\\frac\{/);
});

test("mathMarkdown renders paragraphs, emphasis and math, escaping raw HTML", () => {
  const html = mathMarkdown("A **bag** holds 3 red.\n\nWhat is $P(\\text{red})$? <b>no</b>");
  assert.match(html, /^<p>A <strong>bag<\/strong> holds 3 red\.<\/p><p>What is <span class="katex">/);
  assert.match(html, /&lt;b&gt;no&lt;\/b&gt;<\/p>$/);
  // Underscores inside math are TeX subscripts, not emphasis.
  assert.doesNotMatch(mathMarkdown("$x_1 + x_2$"), /<em>/);
});
