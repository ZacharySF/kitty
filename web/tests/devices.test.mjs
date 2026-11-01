// Settings → Devices client (platform design Q16): routes, mutation headers, bodies and the
// pure helpers behind the panel (code formatting, countdown, status and time labels).
// node --test tests/devices.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  cancelPairing, codeComplete, countdown, deviceName, discoveryLabel, enterPairingCode, formatCode, listenLabel,
  loadDevices, pairable, peerAddress, pendingLabel, relativeTime, renameDevice, secondsLeft, setPaused, setPeerAddress,
  showPairingCode, statusLabel, statusTone, syncNow, unpair,
} from '../src/lib/devices.ts';

const STATE = {
  device: { id: 'dev-a', name: 'Synthetic desk', fingerprint: '3f2a 9c01 77be 0d4e', port: 47800, addresses: ['192.0.2.10:47800'],
    listening: true, error: null, paused: false, discovery: 'on', discovery_error: null },
  pairing: null, discovered: [], peers: [],
};

function fakeFetch(calls, reply = { ok: true, status: 200, body: STATE }) {
  return async (url, init = {}) => {
    calls.push({ url, method: init.method, headers: init.headers, body: init.body });
    return { ok: reply.ok, status: reply.status, json: async () => reply.body };
  };
}

test('a typed code is upper-cased, stripped and split into two halves', () => {
  assert.equal(formatCode('abcdef'), 'ABC-DEF');
  assert.equal(formatCode(' ab c-d e f '), 'ABC-DEF');
  assert.equal(formatCode('ab'), 'AB');
  assert.equal(formatCode('abc'), 'ABC');
  assert.equal(formatCode('abcd'), 'ABC-D');
  assert.equal(formatCode('ABC-DEF'), 'ABC-DEF');
  assert.equal(formatCode('abcdefgh'), 'ABC-DEF');
  assert.equal(formatCode('a1b2c3'), 'A1B-2C3');
  assert.equal(codeComplete('abcdef'), true);
  assert.equal(codeComplete('ABC-DE'), false);
  assert.equal(codeComplete(''), false);
});

test('device names and addresses are trimmed like the server expects', () => {
  assert.equal(deviceName('  Laptop  '), 'Laptop');
  assert.equal(deviceName('   '), '');
  assert.equal(deviceName('x'.repeat(64)), 'x'.repeat(64));
  assert.equal(deviceName('x'.repeat(65)), '');
  assert.equal(peerAddress(' laptop.tailnet.ts.net '), 'laptop.tailnet.ts.net');
  assert.equal(peerAddress('   '), null);
  assert.equal(peerAddress(null), null);
});

test('relative times and the pairing countdown', () => {
  const now = Date.parse('2026-11-01T12:00:00Z');
  assert.equal(relativeTime(null, now), 'never');
  assert.equal(relativeTime('not a date', now), 'never');
  assert.equal(relativeTime('2026-11-01T11:59:30Z', now), 'just now');
  assert.equal(relativeTime('2026-11-01T12:00:05Z', now), 'just now');
  assert.equal(relativeTime('2026-11-01T11:59:00Z', now), '1 minute ago');
  assert.equal(relativeTime('2026-11-01T11:55:00Z', now), '5 minutes ago');
  assert.equal(relativeTime('2026-11-01T09:00:00Z', now), '3 hours ago');
  assert.equal(relativeTime('2026-10-30T12:00:00Z', now), '2 days ago');
  assert.equal(secondsLeft('2026-11-01T12:05:00Z', now), 300);
  assert.equal(countdown('2026-11-01T12:05:00Z', now), '5:00');
  assert.equal(countdown('2026-11-01T12:00:09.2Z', now), '0:10');
  assert.equal(countdown('2026-11-01T12:04:05Z', now), '4:05');
  assert.equal(countdown('2026-11-01T11:59:00Z', now), 'expired');
  assert.equal(secondsLeft('2026-11-01T11:59:00Z', now), 0);
});

test('peer status badges read like the panel shows them', () => {
  const now = Date.parse('2026-11-01T12:00:00Z');
  const peer = { status: 'idle', error: null, last_sync_at: '2026-11-01T11:55:00Z' };
  assert.equal(statusLabel(peer, now), 'Synced 5 minutes ago');
  assert.equal(statusLabel({ ...peer, last_sync_at: null }, now), 'Not synced yet');
  assert.equal(statusLabel({ ...peer, status: 'syncing' }, now), 'Syncing…');
  assert.equal(statusLabel({ ...peer, status: 'offline' }, now), 'Offline');
  assert.equal(statusLabel({ ...peer, status: 'paused' }, now), 'Paused');
  assert.equal(statusLabel({ ...peer, status: 'error', error: 'Fingerprint changed' }, now), 'Error: Fingerprint changed');
  assert.equal(statusLabel({ ...peer, status: 'error' }, now), 'Error: sync failed');
  assert.deepEqual(['idle', 'syncing', 'offline', 'paused', 'error', 'unknown'].map(statusTone), ['ok', 'busy', 'muted', 'muted', 'bad', 'muted']);
  assert.equal(pendingLabel(0), '');
  assert.equal(pendingLabel(1), '1 change from this device not received yet');
  assert.equal(pendingLabel(12), '12 changes from this device not received yet');
});

test('this device: where it listens and whether it looks for others', () => {
  assert.equal(listenLabel({ listening: true, addresses: ['192.0.2.10:47800', '100.64.0.2:47800'], error: null }),
    'Listening at 192.0.2.10:47800, 100.64.0.2:47800');
  assert.equal(listenLabel({ listening: true, addresses: [], error: null }), 'Listening');
  assert.equal(listenLabel({ listening: false, addresses: [], error: 'Port 47800 is in use' }), 'Not listening: Port 47800 is in use');
  assert.equal(listenLabel({ listening: false, addresses: [], error: null }), 'Not listening');
  assert.equal(discoveryLabel({ discovery: 'on', discovery_error: null }), 'Finding devices on this network');
  assert.equal(discoveryLabel({ discovery: 'off', discovery_error: null }), 'Not looking for devices on this network');
  assert.equal(discoveryLabel({ discovery: 'error', discovery_error: 'multicast blocked' }), 'Finding devices on this network failed: multicast blocked');
});

test('only nearby devices showing a code and not yet paired are offered', () => {
  const found = [
    { device_id: 'b', name: 'Laptop', address: '192.0.2.11:47800', pairing: true, paired: false },
    { device_id: 'c', name: 'Old phone', address: '192.0.2.12:47800', pairing: true, paired: true },
    { device_id: 'd', name: 'Tablet', address: '192.0.2.13:47800', pairing: false, paired: false },
  ];
  assert.deepEqual(pairable(found).map((device) => device.device_id), ['b']);
});

test('every route, method, header and body matches the contract', async () => {
  const calls = [];
  globalThis.fetch = fakeFetch(calls);
  assert.deepEqual(await loadDevices(), STATE);
  await renameDevice('  Laptop ');
  await showPairingCode();
  await cancelPairing();
  await enterPairingCode('abc def', ' laptop.tailnet.ts.net ');
  await syncNow('dev-b');
  await syncNow();
  await unpair('dev-b');
  await setPeerAddress('dev-b', ' 100.64.0.2:47800 ');
  await setPeerAddress('dev-b', '');
  await setPeerAddress('dev-b', null);
  await setPaused(true);
  assert.deepEqual(calls.map((call) => [call.url, call.method]), [
    ['/api/devices', 'GET'],
    ['/api/devices/name', 'POST'],
    ['/api/devices/pair/show', 'POST'],
    ['/api/devices/pair/cancel', 'POST'],
    ['/api/devices/pair/enter', 'POST'],
    ['/api/devices/sync', 'POST'],
    ['/api/devices/sync', 'POST'],
    ['/api/devices/unpair', 'POST'],
    ['/api/devices/address', 'POST'],
    ['/api/devices/address', 'POST'],
    ['/api/devices/address', 'POST'],
    ['/api/devices/pause', 'POST'],
  ]);
  assert.equal(calls[0].body, undefined);
  assert.equal(calls[0].headers['X-Kitty-Request'], undefined);
  for (const call of calls.slice(1)) {
    assert.equal(call.headers['X-Kitty-Request'], '1');
    assert.equal(call.headers['Content-Type'], 'application/json');
  }
  assert.deepEqual(calls.slice(1).map((call) => JSON.parse(call.body)), [
    { name: 'Laptop' },
    {},
    {},
    { code: 'ABC-DEF', address: 'laptop.tailnet.ts.net' },
    { device_id: 'dev-b' },
    {},
    { device_id: 'dev-b' },
    { device_id: 'dev-b', address: '100.64.0.2:47800' },
    { device_id: 'dev-b', address: null },
    { device_id: 'dev-b', address: null },
    { paused: true },
  ]);
});

test('a refused pairing surfaces the server error text', async () => {
  globalThis.fetch = fakeFetch([], { ok: false, status: 400, body: { error: 'Wrong code or the code expired' } });
  await assert.rejects(enterPairingCode('abcdef', '192.0.2.11'), { message: 'Wrong code or the code expired', status: 400 });
  globalThis.fetch = fakeFetch([], { ok: false, status: 504, body: { error: 'No kitty! answered at that address' } });
  await assert.rejects(enterPairingCode('abcdef', '192.0.2.11'), { message: 'No kitty! answered at that address', status: 504 });
});
