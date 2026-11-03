import test from 'node:test';
import assert from 'node:assert/strict';
import { agentChips, costLabel, entryKey, mergeEntries, rikaSocketUrl } from '../src/lib/rika/rika.ts';

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
