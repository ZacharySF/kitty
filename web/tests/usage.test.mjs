// Agent usage helpers (usage/usage.ts): how counts and cost are written, a card's one
// line, the stacked bars' geometry, the sparkline's points and the pinned session.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  SOURCES, axisTop, dayReadout, money, pinSession, pinnedSession, sparkPoints, stackBars, tokens, usageLine,
} from '../src/lib/usage/usage.ts';

const sums = (input = 0, cache_read = 0, cache_write = 0, output = 0, cost_usd = 0) => ({ input, cache_read, cache_write, output, cost_usd });
const day = (name, sources = {}) => ({ day: name, sources: Object.fromEntries(SOURCES.map((source) => [source.id, sources[source.id] ?? sums()])) });
const session = (fields = {}) => ({
  key: 'a'.repeat(16), agent: 'omp', label: 'omp', cwd_name: 'parser', first_at: null, last_at: null, model: 'm', context_tokens: 420000,
  context_window: 1000000, context_pct: 42, requests: 9, input: 100, cache_read: 3920000, cache_write: 200, output: 61100, new_tokens: 61400,
  tool_calls: 118, files_changed: 9, cost_usd: 3.41, cost_source: 'reported', ...fields,
});

test('token counts are at most three figures, and never "1000k"', () => {
  assert.deepEqual([0, 7, 940, 999, 1000, 1234, 9949, 9950, 18400, 999400, 999600, 1200000, 3920000, 12345678, 2.5e9].map(tokens),
    ['0', '7', '940', '999', '1k', '1.2k', '9.9k', '10k', '18k', '999k', '1M', '1.2M', '3.9M', '12M', '2.5B']);
  assert.equal(tokens(-5), '0');
  assert.equal(tokens(NaN), '0');
});

test('cost: an estimate is marked, a reported one is not, none is no text', () => {
  assert.equal(money(3.41, 'reported'), '$3.41');
  assert.equal(money(3.41, 'priced'), '≈$3.41');
  assert.equal(money(0.004, 'priced'), 'under $0.01');
  assert.equal(money(0), '$0');
  assert.equal(money(null, 'none'), '');
  assert.equal(money(1.5, 'none'), '');
});

test('a card says context, new tokens, cache reads apart, tool calls, files and cost on one line', () => {
  assert.equal(usageLine(session()), '42% context · 61k new · 3.9M cached · 118 tools · 9 files · $3.41');
  // No window known, nothing cached, one of each, no price: those parts go or read in the singular.
  assert.equal(usageLine(session({ context_pct: null, cache_read: 0, tool_calls: 1, files_changed: 1, cost_usd: null, cost_source: 'none' })),
    '61k new · 1 tool · 1 file');
  assert.equal(usageLine(session({ files_changed: 0, cost_usd: 0.39, cost_source: 'priced' })), '42% context · 61k new · 3.9M cached · 118 tools · 0 files · ≈$0.39');
});

test('the axis tops out at a round number', () => {
  assert.deepEqual([0, 1, 3, 5, 7, 40, 180, 2400000, 5000000, 5000001].map(axisTop), [1, 1, 5, 5, 10, 50, 200, 5000000, 5000000, 10000000]);
});

test('bars stack every source from the bottom, scaled to the axis', () => {
  const { top, bars } = stackBars([
    day('2026-11-09'),
    day('2026-11-10', { omp: sums(10, 60, 10, 20), rika: sums(50), claude: sums(0, 0, 0, 50) }),
  ], 100);
  assert.equal(top, 200);
  assert.deepEqual(bars[0], { day: '2026-11-09', total: 0, parts: [] });
  assert.equal(bars[1].total, 200);
  // omp (100 tokens) sits on the baseline, Claude Code on it, Rika on top; cache reads count.
  assert.deepEqual(bars[1].parts, [
    { source: 'omp', y: 50, height: 50, value: 100 },
    { source: 'claude', y: 25, height: 25, value: 50 },
    { source: 'rika', y: 0, height: 25, value: 50 },
  ]);
  assert.deepEqual(stackBars([], 100), { top: 1, bars: [] });
});

test('a day reads as its date, total, cost and each source', () => {
  assert.equal(dayReadout(day('2026-11-10', { omp: sums(0, 2400000, 0, 0, 2.5), claude: sums(800000, 0, 0, 0, 0.6) })),
    'Nov 10 · 3.2M tokens · $3.10 · omp 2.4M, Claude Code 800k');
  assert.equal(dayReadout(day('2026-01-03')), 'Jan 3 · nothing');
  // Reviewed AI requests have a cost and no token count.
  assert.equal(dayReadout(day('2026-11-10', { ai: sums(0, 0, 0, 0, 0.06) })), 'Nov 10 · 0 tokens · $0.06');
});

test('the sparkline spans its box and needs two steps', () => {
  const step = (input, output) => ({ t: '', input, output, cache: 999999 });
  assert.equal(sparkPoints([], 100, 20), '');
  assert.equal(sparkPoints([step(1, 1)], 100, 20), '');
  // Cache reads do not move the line; the highest step touches the top, zero the bottom.
  assert.equal(sparkPoints([step(0, 0), step(30, 20), step(100, 0)], 102, 22), '1.0,21.0 51.0,11.0 101.0,1.0');
  assert.equal(sparkPoints([step(0, 0), step(0, 0)], 102, 22), '1.0,21.0 101.0,21.0');
});

test('the pinned session is a key or nothing', () => {
  const store = new Map();
  const storage = { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, value), removeItem: (key) => store.delete(key) };
  assert.equal(pinnedSession(storage), null);
  pinSession('0123456789abcdef', storage);
  assert.equal(pinnedSession(storage), '0123456789abcdef');
  pinSession(null, storage);
  assert.equal(pinnedSession(storage), null);
  store.set('rika.usage.pinned', '../etc/passwd');
  assert.equal(pinnedSession(storage), null);
  assert.equal(pinnedSession(undefined), null);
});
