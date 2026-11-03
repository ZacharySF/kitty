// Run with Node >=22.6: node --experimental-strip-types --test tests/mathAnswer.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { echo, readAnswer } from "../src/lib/math/answer.ts";

test("integers, decimals, fractions and percentages are read with their value", () => {
  assert.deepEqual(readAnswer("3/7"), { kind: "fraction", text: "3/7", value: 3 / 7, numerator: 3, denominator: 7 });
  assert.deepEqual(readAnswer(" 42 "), { kind: "integer", text: "42", value: 42, sig: 2 });
  assert.deepEqual(readAnswer("0.4286"), { kind: "decimal", text: "0.4286", value: 0.4286, sig: 4 });
  assert.deepEqual(readAnswer("42.86%"), { kind: "percent", text: "42.86%", value: 0.4286, sig: 4 });
  assert.equal(readAnswer("−3/7").value, -3 / 7, "a typographic minus reads as minus");
  assert.equal(readAnswer("-.5").value, -0.5);
  assert.equal(readAnswer("1,5").value, 1.5, "a decimal comma reads as a point");
});

test("unreadable text and a zero denominator read as nothing", () => {
  for (const text of ["", "  ", "abc", "3/0", "3//7", "1.2.3", "%"]) assert.equal(readAnswer(text), null, text);
});

test("the live echo shows the value: '3/7 ≈ 0.4286'", () => {
  assert.equal(echo(readAnswer("3/7")), "3/7 ≈ 0.4286");
  assert.equal(echo(readAnswer("6/3")), "6/3 = 2");
  assert.equal(echo(readAnswer("1/8")), "1/8 = 0.125");
  assert.equal(echo(readAnswer("42.86%")), "42.86% = 0.4286");
  assert.equal(echo(readAnswer("0.43")), "0.43 · 2 s.f.");
  assert.equal(echo(readAnswer("-12")), "−12");
  assert.equal(echo(null), "");
});
