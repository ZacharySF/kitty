// Find's job table: which columns show, how a column sorts, and the fit cell's words.
import assert from "node:assert/strict";
import test from "node:test";
import { COLUMNS_KEY, DEFAULT_COLUMNS, fitLabel, nextSort, readColumns, sortJobs, writeColumns } from "../src/lib/jobs/table.ts";

const job = (id, extra = {}) => ({ id, title: `Role ${id}`, company: `Company ${id}`, location: null, is_remote: false,
  posted_at: null, deadline_at: null, queue_state: "new", status: null, ...extra });
const fit = (job_id, covered, total, pay = null) => ({ job_id, covered, total, skills: [], document: null, compared_with: null, pay });

function memory(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), values };
}

test("the default columns are role, place, fit, pay and dates; queue state is hidden", () => {
  assert.deepEqual(DEFAULT_COLUMNS, ["role", "place", "fit", "pay", "dates"]);
  assert.deepEqual(readColumns(memory()), DEFAULT_COLUMNS);
});

test("chosen columns persist per browser, keep table order, and always include the role", () => {
  const storage = memory();
  writeColumns(storage, ["queue", "fit"]);
  assert.equal(storage.values.get(COLUMNS_KEY), JSON.stringify(["role", "fit", "queue"]));
  assert.deepEqual(readColumns(storage), ["role", "fit", "queue"]);
  // Unknown or broken values fall back to the defaults instead of an empty table.
  assert.deepEqual(readColumns(memory({ [COLUMNS_KEY]: "{oops" })), DEFAULT_COLUMNS);
  assert.deepEqual(readColumns(memory({ [COLUMNS_KEY]: JSON.stringify(["nope"]) })), ["role"]);
  // Storage can be unavailable; the defaults still apply.
  assert.deepEqual(readColumns({ getItem() { throw new Error("denied"); } }), DEFAULT_COLUMNS);
  assert.doesNotThrow(() => writeColumns({ setItem() { throw new Error("denied"); } }, ["fit"]));
});

test("the fit cell counts covered skills", () => {
  assert.equal(fitLabel(fit(1, 4, 6)), "4 of 6 skills");
  assert.equal(fitLabel(fit(1, 1, 1)), "1 of 1 skill");
  assert.equal(fitLabel(fit(1, 0, 0)), "No skills listed");
  assert.equal(fitLabel(undefined), "");
});

test("sorting: fit by covered skills, pay by hourly rate, dates newest first; missing values last", () => {
  const jobs = [job(1), job(2), job(3), job(4)];
  const fits = { 1: fit(1, 2, 6, { text: "$20/hr", hourly: 20 }), 2: fit(2, 4, 6), 3: fit(3, 4, 4, { text: "$90,000", hourly: 43.27 }), 4: fit(4, 0, 0) };
  const ids = (key, descending) => sortJobs(jobs, fits, key, descending).map((item) => item.id);
  assert.deepEqual(ids("fit", true), [3, 2, 1, 4]);
  assert.deepEqual(ids("fit", false), [1, 2, 3, 4]);
  assert.deepEqual(ids("pay", true), [3, 1, 2, 4]);
  assert.deepEqual(ids("pay", false), [1, 3, 2, 4]);
  assert.deepEqual(ids(null, true), [1, 2, 3, 4], "no column sort keeps the server's order");

  const dated = [job(1, { posted_at: "2026-11-01" }), job(2, { posted_at: "2026-11-10", deadline_at: "2026-12-01" }), job(3, { deadline_at: "2026-11-25" })];
  assert.deepEqual(sortJobs(dated, {}, "posted", true).map((item) => item.id), [2, 1, 3]);
  assert.deepEqual(sortJobs(dated, {}, "deadline", false).map((item) => item.id), [3, 2, 1]);

  const named = [job(1, { title: "beta" }), job(2, { title: "Alpha" }), job(3, { title: "gamma", company: "Acme" })];
  assert.deepEqual(sortJobs(named, {}, "title", false).map((item) => item.id), [2, 1, 3]);
  assert.deepEqual(sortJobs(named, {}, "company", false).map((item) => item.id), [3, 1, 2]);
});

test("a heading press sorts by it in its natural direction, then flips", () => {
  assert.deepEqual(nextSort({ key: null, descending: false }, "fit"), { key: "fit", descending: true });
  assert.deepEqual(nextSort({ key: "fit", descending: true }, "fit"), { key: "fit", descending: false });
  assert.deepEqual(nextSort({ key: "fit", descending: false }, "title"), { key: "title", descending: false });
  assert.deepEqual(nextSort({ key: null, descending: false }, "deadline"), { key: "deadline", descending: false });
  assert.deepEqual(nextSort({ key: null, descending: false }, "posted"), { key: "posted", descending: true });
});
