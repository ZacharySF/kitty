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
