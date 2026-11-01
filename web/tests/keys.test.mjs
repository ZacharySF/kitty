// Settings → Keys client: names, labels, and that a value is only ever sent, once.
// node --experimental-strip-types --test tests/keys.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { backendLabel, keyName, loadKeys, moveKey, removeKey, setKey } from '../src/lib/keys.ts';

const STATUS = { keys: [{ name: 'DUMMY', label: 'DUMMY', env: 'DUMMY', custom: true, set: true, backend: 'file', in_file: true, error: null }],
  keyring: false, keyring_label: null, store: 'file' };

function fakeFetch(calls) {
  return async (url, init = {}) => {
    calls.push({ url, method: init.method, headers: init.headers, body: init.body });
    return { ok: true, status: 200, json: async () => STATUS };
  };
}

test('key names are upper-cased and validated like the server', () => {
  assert.equal(keyName(' my_tool_key '), 'MY_TOOL_KEY');
  assert.equal(keyName('openrouter'), 'OPENROUTER');
  for (const bad of ['', '1KEY', 'MY-KEY', 'A B', '_X', 'X'.repeat(65)]) assert.equal(keyName(bad), '');
});

test('backends are named for people, never with a value', () => {
  assert.equal(backendLabel('keyring'), 'OS keyring');
  assert.equal(backendLabel('file'), 'owner-only file');
  assert.equal(backendLabel('env'), 'environment (.env)');
  assert.equal(backendLabel(null), 'not set');
});

test('set posts the value once behind the mutation header; other calls never carry one', async () => {
  const calls = [];
  globalThis.fetch = fakeFetch(calls);
  await loadKeys();
  await setKey('DUMMY', 'synthetic-dummy-value');
  await removeKey('DUMMY');
  await moveKey('DUMMY');
  assert.deepEqual(calls.map((call) => [call.url, call.method]), [
    ['/api/keys', 'GET'], ['/api/keys/set', 'POST'], ['/api/keys/remove', 'POST'], ['/api/keys/move', 'POST']]);
  assert.equal(calls[1].headers['X-Kitty-Request'], '1');
  assert.deepEqual(JSON.parse(calls[1].body), { name: 'DUMMY', value: 'synthetic-dummy-value' });
  assert.equal(calls.filter((call) => String(call.body).includes('synthetic-dummy-value')).length, 1);
});
