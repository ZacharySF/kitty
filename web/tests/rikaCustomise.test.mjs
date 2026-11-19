// Rika's Customise choices are preference sentences: what is chosen, and what a pick changes.
import assert from 'node:assert/strict';
import test from 'node:test';
import { GROUPS, change, chosen } from '../src/lib/rika/customise.ts';

const [length, tone] = GROUPS;
const pick = (group, id) => group.choices.find((choice) => choice.id === id);

test('with no matching sentence the plain choice is the one chosen', () => {
  assert.equal(chosen(length, []).id, 'normal');
  assert.equal(chosen(tone, [{ text: 'Put new flashcards in the Biology deck' }]).id, 'plain');
});

test('a saved sentence is recognised whatever its case or final full stop', () => {
  assert.equal(chosen(length, [{ text: 'keep replies short.' }]).id, 'short');
  assert.equal(chosen(tone, [{ text: 'Use a formal tone' }]).id, 'formal');
});

test('a pick forgets the group\'s other sentences, adds its own once and leaves other preferences alone', () => {
  const saved = [{ id: 1, text: 'Keep replies short' }, { id: 2, text: 'Use a formal tone' }, { id: 3, text: 'Call me Zach' }];
  assert.deepEqual(change(length, pick(length, 'detailed'), saved), { forget: [saved[0]], add: 'Give detailed replies and explain the reasoning' });
  assert.deepEqual(change(length, pick(length, 'short'), saved), { forget: [], add: null });
  assert.deepEqual(change(length, pick(length, 'normal'), saved), { forget: [saved[0]], add: null });
  assert.deepEqual(change(tone, pick(tone, 'friendly'), saved), { forget: [saved[1]], add: 'Use a warm, friendly tone' });
});
