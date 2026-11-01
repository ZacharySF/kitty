// The one validation and duplicate check behind both ways of adding a flashcard:
// the inline add row in the card browser and the quick-add popover (N / Ctrl K).
// node --experimental-strip-types --test tests/newCard.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { addCard, basicType, checkCard, normalizeFront, preselectDeck, rememberDeck, rememberedDeck } from '../src/lib/flashcards/newCard.ts';

const decks = [
  { id: 1, name: 'Default', archived: 0 },
  { id: 2, name: 'Spanish', archived: 0 },
  { id: 3, name: 'Old', archived: 1 },
];
const types = [
  { id: 7, kind: 'cloze', fields: ['Text', 'Extra'] },
  { id: 8, kind: 'basic', fields: ['Front', 'Back', 'Notes'] },
];
const memory = () => {
  const items = new Map();
  return { getItem: (key) => items.get(key) ?? null, setItem: (key, value) => items.set(key, String(value)) };
};

test('an empty front or back is rejected and names the field to fix', () => {
  assert.deepEqual(checkCard({ front: '   ', back: 'Lima' }, []), { problem: 'Write the front (question) first.', field: 'front', duplicate: false });
  assert.deepEqual(checkCard({ front: 'Capital of Peru?', back: '\n' }, []), { problem: 'Write the back (answer) too.', field: 'back', duplicate: false });
  assert.deepEqual(checkCard({ front: 'Capital of Peru?', back: 'Lima' }, []), { problem: '', field: null, duplicate: false });
});

test('a front already in the deck is flagged, ignoring case, spacing and markup', () => {
  const fronts = ['Capital of <b>Peru</b>?', 'Other'];
  assert.equal(checkCard({ front: '  capital   of PERU? ', back: 'Lima' }, fronts).duplicate, true);
  assert.equal(checkCard({ front: 'Capital of Peru?', back: '' }, fronts).duplicate, true, 'flagged while the back is still empty');
  assert.equal(checkCard({ front: 'Capital of Chile?', back: 'Santiago' }, fronts).duplicate, false);
  assert.equal(normalizeFront('A&nbsp;&amp;&nbsp;B'), 'a & b');
});

test('the Basic Front/Back type fills the save body', () => {
  assert.equal(basicType(types)?.id, 8);
  assert.equal(basicType([types[0]]), undefined);
});

test('the deck is preselected: the current deck, else the last used, else Default', () => {
  assert.equal(preselectDeck(decks, 2, 1), 2);
  assert.equal(preselectDeck(decks, 0, 2), 2);
  assert.equal(preselectDeck(decks, 3, 99), 1, 'archived and missing decks are skipped');
  assert.equal(preselectDeck([{ id: 5, name: 'Only', archived: 0 }], null, null), 5);
  assert.equal(preselectDeck([], 2, 2), 0);
  const storage = memory();
  assert.equal(rememberedDeck(storage), null);
  rememberDeck(2, storage);
  assert.equal(rememberedDeck(storage), 2);
});

test('addCard saves a valid card, refuses invalid ones and asks once before a duplicate', async () => {
  const calls = [];
  const call = async (path, body) => { calls.push([path, body]); return { note: { id: 41 } }; };
  const storage = memory();
  const base = { deckId: 2, types, fronts: ['Capital of Peru?'], storage };

  let result = await addCard(call, { ...base, draft: { front: '', back: 'x' } });
  assert.equal(result.saved, null);
  assert.equal(result.check.field, 'front');

  result = await addCard(call, { ...base, draft: { front: 'capital of peru?', back: 'Lima' } });
  assert.equal(result.saved, null, 'a duplicate is not saved without confirmation');
  assert.equal(result.check.duplicate, true);
  assert.equal(calls.length, 0);

  result = await addCard(call, { ...base, draft: { front: 'capital of peru?', back: 'Lima' }, allowDuplicate: true });
  assert.deepEqual(result.saved, { id: 41 });

  result = await addCard(call, { ...base, draft: { front: ' Capital of Chile? ', back: 'Santiago ' } });
  assert.deepEqual(result.saved, { id: 41 });
  assert.deepEqual(calls.at(-1), ['/flashcards/note', { deck_id: 2, type_id: 8, fields: { Front: 'Capital of Chile?', Back: 'Santiago', Notes: '' }, tags: [] }]);
  assert.equal(rememberedDeck(storage), 2, 'the saving deck becomes the last used');

  await assert.rejects(addCard(call, { ...base, types: [types[0]], draft: { front: 'a', back: 'b' } }), /Basic/);
  await assert.rejects(addCard(call, { ...base, deckId: 0, draft: { front: 'a', back: 'b' } }), /deck/);
});
