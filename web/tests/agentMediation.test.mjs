import test from 'node:test';
import assert from 'node:assert/strict';
import { GUARANTEE_LABELS, canApply, flipSummary, guaranteeOf, isLive, moreJournal, shortId, within } from '../src/lib/agents/agents.ts';

test('guarantee levels read as the herdr tab labels; anything unknown is not mediated', () => {
  assert.deepEqual(GUARANTEE_LABELS, { full: 'Full', attribution: 'Attribution only', none: 'Not mediated' });
  assert.equal(guaranteeOf('full'), 'full');
  assert.equal(guaranteeOf('attribution'), 'attribution');
  for (const value of ['none', undefined, null, 'FULL', 3]) assert.equal(guaranteeOf(value), 'none');
});

test('End applies while the agent may still run', () => {
  assert.deepEqual(['pending', 'confined', 'unconfined', 'refused', 'expired', 'ended'].filter(isLive), ['pending', 'confined', 'unconfined']);
});

test('Apply needs a simulation of exactly the edited source, and a change to the policy in force', () => {
  const active = 'mode: enforce\n';
  assert.equal(canApply('mode: observe\n', null, active), false);
  assert.equal(canApply('mode: observe\n', 'mode: observe\n', active), true);
  assert.equal(canApply('mode: observe\n ', 'mode: observe\n', active), false);
  assert.equal(canApply(active, active, active), false);
});

test('older journal pages append below, never repeating a row', () => {
  const row = (id) => ({ id });
  assert.deepEqual(moreJournal([row(5), row(4)], [row(4), row(3), row(2)]).map((item) => item.id), [5, 4, 3, 2]);
  assert.deepEqual(moreJournal([], [row(1)]).map((item) => item.id), [1]);
});

test('a simulation reads as replayed rows and the flips each way', () => {
  assert.equal(flipSummary({ replayed: 1256, counts: { allow_to_deny: 2, deny_to_allow: 1 } }), '1,256 replayed · 2 allow → deny · 1 deny → allow');
  assert.equal(flipSummary({ replayed: 4, counts: { allow_to_deny: 0, deny_to_allow: 3 } }), '4 replayed · 3 deny → allow');
  assert.equal(flipSummary({ replayed: 0, counts: { allow_to_deny: 0, deny_to_allow: 0 } }), '0 replayed · no decision changes');
  assert.equal(shortId('8f2b5b19-a9f7-4046-b42b-95e9571e0268'), '8f2b5b19');
  assert.equal(shortId(null), '');
});

test('planted files read relative to the session folder they are in', () => {
  assert.equal(within('/work/parser/.envrc', '/work/parser'), '.envrc');
  assert.equal(within('/work/parser/.git/hooks/pre-commit', '/work/parser/'), '.git/hooks/pre-commit');
  assert.equal(within('/work/parserx/.envrc', '/work/parser'), '/work/parserx/.envrc');
});
