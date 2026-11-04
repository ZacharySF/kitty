import test from 'node:test';
import assert from 'node:assert/strict';
import { agentChips, costLabel, entryKey, holdLeft, holdClock, mergeEntries, mailLine, rikaSocketUrl, usageReadout, usd } from '../src/lib/rika/rika.ts';
import { compactTokens, trendPath, trendRange } from '../src/lib/rika/settings/settings.ts';

const agent = (id, status) => ({ id, kind: 'omp', name: `omp ${id}`, status, task: '', line: '', since: 0, where: {} });

test('up to four running agents get a chip each; past four they collapse to one count', () => {
  const four = ['a', 'b', 'c', 'd'].map((id) => agent(id, 'working'));
  assert.deepEqual(agentChips(four), { kind: 'agents', agents: four });
  assert.deepEqual(agentChips([]), { kind: 'agents', agents: [] });
  const five = [...four, agent('e', 'done')];
  assert.deepEqual(agentChips(five), { kind: 'count', count: 5, status: 'working' });
});

test('the count chip takes the most urgent state: blocked, then working, idle, done', () => {
  const many = (states) => agentChips(states.map((status, index) => agent(String(index), status)));
  assert.equal(many(['done', 'idle', 'working', 'blocked', 'done']).status, 'blocked');
  assert.equal(many(['done', 'idle', 'idle', 'working', 'done']).status, 'working');
  assert.equal(many(['done', 'idle', 'done', 'done', 'done']).status, 'idle');
  assert.equal(many(['done', 'done', 'done', 'done', 'done']).status, 'done');
});

test('the stream socket stays on the page origin; only the scheme changes', () => {
  assert.equal(rikaSocketUrl({ protocol: 'http:', host: '127.0.0.1:8980' }), 'ws://127.0.0.1:8980/api/rika/stream');
  assert.equal(rikaSocketUrl({ protocol: 'https:', host: 'rika.local' }), 'wss://rika.local/api/rika/stream');
});

test('history pages and live events merge oldest first with no duplicates; a newer copy replaces the old', () => {
  const user = { kind: 'message', id: 1, role: 'user', text: 'Add a task', created_at: '2026-11-02T10:00:00Z' };
  const reply = { kind: 'message', id: 2, role: 'rika', text: 'Adding', status: 'pending', created_at: '2026-11-02T10:00:01Z' };
  const action = { kind: 'action', id: 2, tool: 'add_task', summary: 'Added a task', reversible: true, status: 'done', message_id: 2, created_at: '2026-11-02T10:00:02Z' };
  const older = { kind: 'message', id: 0, role: 'rika', text: 'Morning', created_at: '2026-11-01T08:00:00Z' };
  const merged = mergeEntries([user, reply], [action, older]);
  assert.deepEqual(merged.map(entryKey), ['message:0', 'message:1', 'message:2', 'action:2']);
  const done = mergeEntries(merged, [{ ...reply, text: 'Added it', status: 'done' }]);
  assert.equal(done.length, 4);
  assert.equal(done.find((entry) => entryKey(entry) === 'message:2').text, 'Added it');
});

test('costs read as dollars; without an estimate there is no cost line', () => {
  assert.equal(costLabel(0.4213), '$0.42');
  assert.equal(costLabel(0.004), 'under $0.01');
  assert.equal(costLabel(0), 'free');
  assert.equal(costLabel(null), '');
  assert.equal(costLabel(undefined), '');
});

import { approveLabel, downloadLabel, planGroups, talkClock } from '../src/lib/rika/rika.ts';
import { HOLD_MS, LATCH_MS, SpaceHold } from '../src/lib/rika/hold.ts';
import { Downsampler, littleEndian, VOICE_RATE } from '../src/lib/rika/mic.ts';

/** A SpaceHold on a fake clock: `advance(ms)` fires what is due. */
function fakeHold() {
  const calls = [];
  let now = 0;
  let timers = [];
  const hold = new SpaceHold(
    { start: () => calls.push('start'), latch: () => calls.push('latch'), stop: () => calls.push('stop'), tap: () => calls.push('tap') },
    { set: (run, ms) => { const timer = { at: now + ms, run }; timers.push(timer); return timer; }, clear: (timer) => { timers = timers.filter((item) => item !== timer); } },
  );
  /** Runs due timers in time order, each at its own moment, so a timer it sets counts from then. */
  const advance = (ms) => {
    const until = now + ms;
    for (;;) {
      const due = timers.filter((timer) => timer.at <= until).sort((a, b) => a.at - b.at)[0];
      if (!due) break;
      timers = timers.filter((timer) => timer !== due);
      now = due.at;
      due.run();
    }
    now = until;
  };
  return { hold, calls, advance };
}

test('a Space tap types a space; a hold of at least 0.3 s listens and its release sends', () => {
  assert.equal(HOLD_MS, 300);
  const { hold, calls, advance } = fakeHold();
  hold.down(false);
  advance(299);
  hold.up();
  assert.deepEqual(calls, ['tap']);
  hold.down(false);
  advance(300);
  assert.deepEqual(calls, ['tap', 'start']);
  hold.down(true); // auto-repeat while held does nothing
  advance(2000);
  hold.up();
  assert.deepEqual(calls, ['tap', 'start', 'stop']);
  assert.equal(hold.state, 'idle');
});

test('a long hold latches into a brain dump that outlasts the release; the next Space ends it', () => {
  const { hold, calls, advance } = fakeHold();
  hold.down(false);
  advance(LATCH_MS);
  assert.deepEqual(calls, ['start', 'latch']);
  hold.up();
  assert.equal(hold.state, 'latched');
  advance(60_000);
  hold.down(false);
  assert.deepEqual(calls, ['start', 'latch', 'stop']);
  hold.up();
  assert.equal(hold.state, 'idle');
  hold.adopt(); // a dump started from the drawer ends the same way
  hold.down(false);
  assert.deepEqual(calls.at(-1), 'stop');
});

test('reset forgets a hold without typing or sending anything', () => {
  const { hold, calls, advance } = fakeHold();
  hold.down(false);
  hold.reset();
  advance(10_000);
  hold.up();
  assert.deepEqual(calls, []);
});

test('microphone audio becomes 16 kHz little-endian PCM, across frames of any size', () => {
  const resample = new Downsampler(48_000);
  const second = Float32Array.from({ length: 48_000 }, (_, index) => Math.sin((2 * Math.PI * 440 * index) / 48_000) * 0.5);
  let samples = 0;
  for (let start = 0; start < second.length; start += 4096) samples += resample.push(second.subarray(start, start + 4096)).length;
  assert.ok(Math.abs(samples - VOICE_RATE) <= 1, `${samples} samples`);
  const pcm = new Downsampler(16_000).push(Float32Array.from([0, 0.5, -1, 2]));
  assert.deepEqual([...pcm], [0, 16383, -32768, 32767]);
  assert.deepEqual([...new Uint8Array(littleEndian(Int16Array.from([1, -2])))], [1, 0, 254, 255]);
});

test('a plan groups its items by area in the fixed order and labels its approval', () => {
  const item = (id, area) => ({ id, area, title: id, detail: '', source: '', count: 1, outward: false, question: null, choice: null, result: null });
  const groups = planGroups([item('a', 'agents'), item('t', 'tasks'), item('n', 'notes'), item('t2', 'tasks')]);
  assert.deepEqual(groups.map((group) => [group.area, group.items.map((entry) => entry.id)]), [['notes', ['n']], ['tasks', ['t', 't2']], ['agents', ['a']]]);
  assert.equal(approveLabel(9, 9), 'Approve all 9');
  assert.equal(approveLabel(7, 9), 'Approve 7 of 9');
  assert.equal(talkClock(612.4), '10:12');
  assert.equal(talkClock(3725), '1:02:05');
  assert.equal(downloadLabel(72_608_766, 145_217_532), '50% of 145 MB');
});

const usage = (extra = {}) => ({ tokens_in: 1180, tokens_cached: 960, tokens_out: 64, path: ['find_deck', 'add_card'], signature: 'add-card', remembered: true, shortcut: false, ...extra });

test('a model reply reads tier, remembered path, tokens in (cached), out and cost', () => {
  assert.deepEqual(usageReadout({ tier: 'fast', model: 'demo/fast', cost_usd: 0.0002, usage: usage() }),
    { tier: 'fast', label: 'Fast', detail: 'remembered path find_deck → add_card · 1,180 in (960 cached) · 64 out · $0.0002' });
  // A first-time kind of request has no remembered path; nothing cached prints no cached part.
  assert.deepEqual(usageReadout({ tier: 'standard', model: 'demo/standard', cost_usd: 0.0041, usage: usage({ remembered: false, tokens_in: 6240, tokens_cached: 0, tokens_out: 610 }) }),
    { tier: 'standard', label: 'Standard', detail: '6,240 in · 610 out · $0.0041' });
  // No cost reported yet: the tokens still show.
  assert.equal(usageReadout({ tier: 'deep', model: 'demo/deep', cost_usd: null, usage: usage({ remembered: false, tokens_cached: null }) }).detail, '1,180 in · 64 out');
});

test('deterministic and shortcut replies read Direct · path · 0 tokens · $0', () => {
  const direct = { tokens_in: 0, tokens_cached: 0, tokens_out: 0, path: ['move_event'], signature: 'move', remembered: false, shortcut: false };
  assert.deepEqual(usageReadout({ tier: 'fast', model: 'deterministic', cost_usd: null, usage: direct }), { tier: 'direct', label: 'Direct', detail: 'move_event · 0 tokens · $0' });
  assert.deepEqual(usageReadout({ tier: 'fast', model: 'shortcut', cost_usd: null, usage: { ...direct, path: ['find_deck', 'add_card'], shortcut: true } }),
    { tier: 'direct', label: 'Direct', detail: 'find_deck → add_card · 0 tokens · $0' });
  assert.equal(usageReadout({ tier: 'fast', model: 'deterministic', cost_usd: null, usage: { ...direct, path: [] } }).detail, '0 tokens · $0');
  // Without usage (a user message, an older reply) there is no readout.
  assert.equal(usageReadout({ tier: 'fast', model: 'demo/fast', cost_usd: 0.001, usage: null }), null);
  assert.equal(usageReadout({ tier: 'fast', model: 'demo/fast' }), null);
});

test('a scripted demo reply says so with no tier; the morning brief has no token readout', () => {
  const none = { tokens_in: 0, tokens_cached: 0, tokens_out: 0, path: [], signature: '', remembered: false, shortcut: false };
  assert.deepEqual(usageReadout({ tier: 'fast', model: 'demo', cost_usd: null, usage: none }), { tier: 'direct', label: 'Demo', detail: 'Scripted demo reply · 0 tokens · $0' });
  // The brief is written without a model by default: no readout, so only a cost it reports shows.
  assert.equal(usageReadout({ tier: null, model: 'brief', cost_usd: null, usage: null }), null);
  assert.equal(usageReadout({ tier: 'fast', model: 'brief', cost_usd: 0.0003, usage: none }), null);
});

test('readout dollars keep sub-cent precision', () => {
  assert.equal(usd(0), '$0');
  assert.equal(usd(0.0002), '$0.0002');
  assert.equal(usd(0.031), '$0.031');
  assert.equal(usd(0.5), '$0.50');
  assert.equal(usd(1.234), '$1.23');
  assert.equal(usd(0.00002), 'under $0.0001');
  assert.equal(usd(null), '');
});

const draft = (extra = {}) => ({ kind: 'mail', id: 'a'.repeat(32), status: 'scheduled', auto: false, reply_kind: 'scheduling', to: 'Maya Chen <maya@example.test>', subject: 'Re: Onsite', body: 'Hi Maya,\n\nTuesday after 1pm works.\n\nBest,\nAlex', send_after: '2026-11-03T10:00:30.000Z', sent_at: null, error: null, ...extra });

test('a held send counts down to its send time, then nothing is left to undo', () => {
  const at = (iso) => Date.parse(iso);
  assert.equal(holdLeft(draft(), at('2026-11-03T10:00:00.000Z')), 30);
  assert.equal(holdLeft(draft(), at('2026-11-03T10:00:29.400Z')), 1);
  assert.equal(holdLeft(draft(), at('2026-11-03T10:00:31.000Z')), 0);
  assert.equal(holdLeft(draft({ status: 'sent' }), at('2026-11-03T10:00:00.000Z')), 0);
  assert.equal(holdLeft(draft({ send_after: null }), at('2026-11-03T10:00:00.000Z')), 0);
  assert.equal(holdClock(30), '0:30');
  assert.equal(holdClock(125), '2:05');
  assert.equal(holdClock(0), '0:00');
});

test('a mail item names the kind of reply and whether Rika sends it on its own', () => {
  assert.equal(mailLine(draft({ status: 'sent', auto: true, sent_at: '2026-11-03T10:00:30Z' })), 'Auto-sent · scheduling');
  assert.equal(mailLine(draft({ status: 'scheduled', auto: true })), 'Auto-send · scheduling');
  assert.equal(mailLine(draft({ status: 'scheduled', auto: false, reply_kind: 'question' })), 'Reply · question');
});

test('routing memory numbers: compact tokens and a 14-day trend line', () => {
  assert.equal(compactTokens(0), '0');
  assert.equal(compactTokens(640), '640');
  assert.equal(compactTokens(1180), '1.2k');
  assert.equal(compactTokens(12_400), '12k');
  const trend = [0.006, 0.004, 0.002].map((avg_cost_usd, index) => ({ day: `2026-11-0${index + 1}`, requests: 3, avg_cost_usd }));
  // From zero to the highest day, so a falling cost is drawn to scale.
  assert.equal(trendPath(trend, 200, 48), 'M0,4 L100,18 L200,32');
  assert.equal(trendPath([], 200, 48), '');
  // Days without requests are gaps, not free days; one day draws a level line.
  assert.equal(trendPath([{ day: '2026-11-01', requests: 0, avg_cost_usd: 0 }], 200, 48), '');
  assert.equal(trendPath([{ day: '2026-11-01', requests: 0, avg_cost_usd: 0 }, { day: '2026-11-02', requests: 2, avg_cost_usd: 0.001 }], 200, 48), 'M0,4 L200,4');
  assert.equal(trendPath([{ day: 'a', requests: 1, avg_cost_usd: 0.002 }, { day: 'b', requests: 0, avg_cost_usd: 0 }, { day: 'c', requests: 1, avg_cost_usd: 0.001 }], 200, 48), 'M0,4 L200,25');
  // Days without requests are skipped for the from → to range.
  assert.deepEqual(trendRange([{ day: 'a', requests: 0, avg_cost_usd: 0 }, ...trend]), { from: 0.006, to: 0.002 });
  assert.equal(trendRange([]), null);
});
