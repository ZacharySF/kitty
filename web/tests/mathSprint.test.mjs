// Run with Node >=22.6: node --experimental-strip-types --test tests/mathSprint.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { choose, clock, current, finish, perMinute, remainingSeconds, score, startSprint, trend, typed } from "../src/lib/math/sprint.ts";

const PROBLEMS = [
  { q: "17 × 24", a: 408, choices: [398, 408, 418, 480] },
  { q: "93 − 41", a: 52, choices: [52, 42, 62, 53] },
  { q: "6 + 7", a: 13, choices: [12, 13, 14, 31] },
];

test("typed (Zetamac): a right answer is taken the instant it is typed, no Enter", () => {
  const run = startSprint("zetamac", 120, PROBLEMS, 1_000);
  assert.equal(current(run).q, "17 × 24");
  assert.equal(typed(run, "4", 2_000), false, "a prefix of the answer waits");
  assert.equal(typed(run, "40", 2_100), false);
  assert.equal(typed(run, "409", 2_150), false, "a wrong number neither advances nor costs");
  assert.equal(typed(run, " 408 ", 2_200), true);
  assert.equal(current(run).q, "93 − 41");
  assert.equal(typed(run, "52", 3_000), true);
  assert.deepEqual([run.right, run.wrong, score(run)], [2, 0, 2]);
});

test("typed: a typographic minus and leading zeros still match", () => {
  const run = startSprint("zetamac", 60, [{ q: "3 − 10", a: -7 }, { q: "2 + 3", a: 5 }], 0);
  assert.equal(typed(run, "−7", 10), true);
  assert.equal(typed(run, "05", 20), true);
});

test("multiple choice: +1 right, −1 wrong, and either moves on", () => {
  const run = startSprint("mc", 60, PROBLEMS, 0);
  assert.equal(choose(run, 408, 100), "right");
  assert.equal(choose(run, 42, 200), "wrong");
  assert.equal(current(run).q, "6 + 7");
  assert.equal(choose(run, 12, 300), "wrong");
  assert.deepEqual([run.right, run.wrong, score(run)], [1, 2, -1]);
});

test("problems cycle when a fast run outpaces the generated list", () => {
  const run = startSprint("zetamac", 120, PROBLEMS, 0);
  for (const problem of [...PROBLEMS, PROBLEMS[0]]) assert.equal(typed(run, String(problem.a), 10), true);
  assert.equal(current(run).q, "93 − 41");
});

test("the clock runs down to 0:00 and answers after time are ignored", () => {
  const run = startSprint("mc", 120, PROBLEMS, 0);
  assert.equal(remainingSeconds(run, 0), 120);
  assert.equal(clock(remainingSeconds(run, 18_800)), "1:42");
  assert.equal(remainingSeconds(run, 500_000), 0);
  assert.equal(clock(0), "0:00");
  assert.equal(clock(600), "10:00");
  assert.equal(choose(run, 408, 120_000), null);
  assert.equal(typed(startSprint("zetamac", 30, PROBLEMS, 0), "408", 30_001), false);
});

test("finish reports score, wrong and elapsed time, capped at the duration", () => {
  const run = startSprint("mc", 60, PROBLEMS, 1_000);
  choose(run, 408, 2_000);
  choose(run, 0, 3_000);
  assert.deepEqual(finish(run, 999_999), { score: 0, wrong: 1, elapsed_ms: 60_000 });
  const early = startSprint("zetamac", 60, PROBLEMS, 1_000);
  typed(early, "408", 5_000);
  assert.deepEqual(finish(early, 11_000), { score: 1, wrong: 0, elapsed_ms: 10_000 });
  assert.equal(typed(early, "52", 12_000), false, "a finished run takes no answers");
});

test("rate and trend", () => {
  assert.equal(perMinute(31, 120), 15.5);
  assert.equal(perMinute(5, 0), 0);
  assert.deepEqual(trend([31, 34, 33]), { best: 34, last: 33, change: -1, newBest: false });
  assert.deepEqual(trend([31, 34, 40]), { best: 40, last: 40, change: 6, newBest: true });
  assert.deepEqual(trend([12]), { best: 12, last: 12, change: 0, newBest: false });
  assert.deepEqual(trend([]), { best: 0, last: 0, change: 0, newBest: false });
});
