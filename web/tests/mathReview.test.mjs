// Run with Node >=22.6: node --test tests/mathReview.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { bookSuggestions, dueIn, entryLabel, entryProblem, itemEyebrow, mixedLocked, practiceOrder } from "../src/lib/math/review.ts";

const NOW = Date.parse("2026-11-04T12:00:00Z");

test("a due date reads as due now, tomorrow or in n days", () => {
  assert.equal(dueIn("2026-11-01T00:00:00Z", NOW), "due now");
  assert.equal(dueIn("2026-11-04T12:00:00Z", NOW), "due now");
  assert.equal(dueIn("2026-11-05T09:00:00Z", NOW), "in 1 day");
  assert.equal(dueIn("2026-11-10T12:00:00Z", NOW), "in 6 days");
});

test("practice opens in mixed review by the backend's default; an explicit choice wins once it is open", () => {
  const open = { default: true, mastered: 6, needed: 5 };
  const shut = { default: false, mastered: 2, needed: 5 };
  assert.equal(practiceOrder(null, null), "next");
  assert.equal(practiceOrder(null, open), "mixed");
  assert.equal(practiceOrder("next", open), "next");
  assert.equal(practiceOrder("mixed", open), "mixed");
  assert.equal(practiceOrder("mixed", shut), "next", "mixed review before enough mastery");
  assert.equal(practiceOrder("junk", open), "mixed");
  assert.equal(practiceOrder(null, { default: false, mastered: 5, needed: 5 }), "next");
  assert.equal(mixedLocked(shut), "Mixed review opens once 5 topics are mastered (2 so far)");
  assert.equal(mixedLocked({ mastered: 1, needed: 5 }), "Mixed review opens once 5 topics are mastered (1 so far)");
});

test("each mixed item says its kind and strategy", () => {
  assert.equal(itemEyebrow({ kind: "topic", strategy: "restrict-sample-space", early: false }), "Review · restrict sample space");
  assert.equal(itemEyebrow({ kind: "topic", strategy: "bayes", early: true }), "Early review · bayes");
  assert.equal(itemEyebrow({ kind: "textbook", strategy: "count-outcomes" }), "Textbook · count outcomes");
  assert.equal(itemEyebrow({ kind: "book", strategy: "combination" }), "Own book · combination");
});

test("an own-book entry is a reference: book · section · #problem", () => {
  assert.equal(entryLabel({ book: "Blitzstein & Hwang", section: "2.3", problem: "14" }), "Blitzstein & Hwang · 2.3 · #14");
  assert.equal(entryLabel({ book: "Green Book", section: "", problem: "7b" }), "Green Book · #7b");
});

test("own-book fields keep to the backend's limits", () => {
  const ok = { book: "Blitzstein & Hwang", section: "2.3", problem: "14", answer: "3/8" };
  assert.equal(entryProblem(ok), "");
  assert.equal(entryProblem({ ...ok, book: "  " }), "Name the book.");
  assert.equal(entryProblem({ ...ok, problem: "" }), "Give the problem's number.");
  assert.equal(entryProblem({ ...ok, book: "b".repeat(121) }), "The book's name is at most 120 characters.");
  assert.equal(entryProblem({ ...ok, section: "s".repeat(61) }), "The section is at most 60 characters.");
  assert.equal(entryProblem({ ...ok, problem: "p".repeat(41) }), "The problem's number is at most 40 characters.");
  assert.equal(entryProblem({ ...ok, answer: "a".repeat(201) }), "Your answer is at most 200 characters: it's a reference, not the working.");
  assert.equal(entryProblem({ ...ok, answer: "line\nbreak" }), "No line breaks or control characters.");
});

test("book suggestions match what is typed, most used first, without repeating it", () => {
  const books = ["Blitzstein & Hwang", "Green Book", "Heard on the Street", "Grinstead"];
  assert.deepEqual(bookSuggestions(books, ""), books);
  assert.deepEqual(bookSuggestions(books, "gr"), ["Green Book", "Grinstead"]);
  assert.deepEqual(bookSuggestions(books, "green book"), []);
  assert.deepEqual(bookSuggestions(books, "", 2), ["Blitzstein & Hwang", "Green Book"]);
});
