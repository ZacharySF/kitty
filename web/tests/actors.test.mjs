// Who made a block edit, as History and the undo plan show it: kind, agent sub-kind, name and look.
// node --test tests/actors.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { ACTOR_LOOKS, actorLook, actorName, actorStyle } from '../src/lib/actors.ts';
import { ICONS } from '../src/lib/icons.ts';

const agent = (name, session = 's1') => ({ kind: 'agent', name, session });

test('agents are told apart by the client that runs them', () => {
  assert.equal(actorLook(agent('claude-code')).id, 'claude');
  assert.equal(actorLook(agent('Claude Desktop')).id, 'claude');
  assert.equal(actorLook(agent('codex-mcp-client')).id, 'codex');
  assert.equal(actorLook(agent('omp')).id, 'omp');
  assert.equal(actorLook(agent('oh-my-pi')).id, 'omp');
  assert.equal(actorLook(agent('rika!', 'rika:conv-1')).id, 'rika');
  assert.equal(actorLook(agent('kitty notes')).id, 'agent');
  assert.equal(actorLook(agent(null)).id, 'agent');
});

test('every other kind has its own look, and a missing actor is the person', () => {
  assert.equal(actorLook({ kind: 'user', name: null, session: null }).id, 'user');
  assert.equal(actorLook({ kind: 'client', name: 'Phone', session: 'c1' }).id, 'client');
  assert.equal(actorLook({ kind: 'system', name: 'migration', session: null }).id, 'system');
  assert.equal(actorLook({ kind: 'peer', name: 'Laptop', session: null }).id, 'peer');
  assert.equal(actorLook(null).id, 'user');
});

test('each look draws its own icon in its own theme role', () => {
  const looks = Object.values(ACTOR_LOOKS);
  for (const look of looks) {
    assert.ok(ICONS[look.icon], `${look.id} uses missing icon ${look.icon}`);
    assert.deepEqual(ICONS[look.icon].accents, [], `${look.id} icon is a plain glyph`);
    assert.match(look.role, /^--br-[a-z]+$/, `${look.id} colour is a theme role`);
  }
  assert.equal(new Set(looks.map((look) => look.icon)).size, looks.length, 'icons are unique');
  const kinds = ['user', 'agent', 'client', 'system', 'peer'].map((id) => ACTOR_LOOKS[id].role);
  assert.equal(new Set(kinds).size, kinds.length, 'the five kinds use five roles');
  const agents = ['agent', 'claude', 'codex', 'rika'].map((id) => ACTOR_LOOKS[id].role);
  assert.equal(new Set(agents).size, agents.length, 'agent sub-kinds use their own roles');
  assert.equal(actorStyle(agent('claude-code')), '--actor: var(--br-caution)');
});

test('names read as the person, an agent, an app, rika! or a device', () => {
  assert.equal(actorName({ kind: 'user', name: null, session: null }), 'You');
  assert.equal(actorName(agent('claude-code')), 'Agent · claude-code');
  assert.equal(actorName(agent(null)), 'Agent · unnamed');
  assert.equal(actorName({ kind: 'client', name: 'Phone', session: 'c' }), 'Phone (connected app)');
  assert.equal(actorName({ kind: 'system', name: 'migration', session: null }), 'rika! · migration');
  assert.equal(actorName({ kind: 'peer', name: null, session: null }), 'Paired device');
});
