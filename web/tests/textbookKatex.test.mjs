// Every bundled Grinstead & Snell exercise renders: each math segment found by the app's
// splitter goes through KaTeX with throwOnError. Run: node --test tests/textbookKatex.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import katex from "katex";
import { splitMath } from "../src/lib/math/tex.ts";

const book = JSON.parse(readFileSync(new URL("../../third_party/grinstead-snell/exercises.json", import.meta.url), "utf8"));

test("the bundle has exercises", () => {
  assert.ok(book.exercises.length > 600);
});

test("every exercise's math renders with KaTeX and no TeX is left in the prose", () => {
  const failures = [];
  for (const exercise of book.exercises) {
    const parts = splitMath(exercise.text_md);
    for (const part of parts) {
      if (part.kind === "text") {
        // Unrendered TeX would show up as text: a backslash command or a stray dollar.
        if (/\\[A-Za-z]|\$/.test(part.value)) failures.push(`${exercise.label}: TeX left in text ${JSON.stringify(part.value.slice(0, 80))}`);
        continue;
      }
      try {
        katex.renderToString(part.value, { displayMode: part.kind === "display", throwOnError: true, strict: "ignore" });
      } catch (error) {
        failures.push(`${exercise.label}: ${error.message}`);
      }
    }
  }
  assert.deepEqual(failures, []);
});
