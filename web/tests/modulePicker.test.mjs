// The module picker's rules (modulePicker.ts) and its store (modulePicker.svelte.ts, compiled
// here as vite-plugin-svelte compiles it, over a stand-in for rika-server's routes).
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { transform } from 'esbuild';
import { compileModule } from 'svelte/compiler';
import {
  megabytes, moduleObtainable, moduleReady, preselect, renewals, sameSelection, totalLine, totals, withoutDownload,
} from '../src/lib/modulePicker.ts';

const registry = JSON.parse(readFileSync(new URL('../../src/kitty/modules.json', import.meta.url), 'utf8'));
const available = registry.modules.map(({ id, label, description, packs }) => ({ id, label, description, packs }));
const MB = 1e6;
const SIZES = { discovery: 168.5 * MB, math: 29.6 * MB, learnharness: 270 * MB, herdr: 29 * MB, voice: 344.9 * MB, brain: 1.6 * MB };
/** `GET /api/packs` with every pack downloadable, but for the states given. */
const listing = (states = {}, error) => ({
  version: '0.3.36', index: error ? { error } : {},
  packs: registry.packs.map((pack) => ({
    ...pack, modules: [], state: states[pack.id] ?? 'available', size: states[pack.id] === 'unavailable' ? null : SIZES[pack.id],
    installed_size: null, installed_version: null, progress: null, error: null,
  })),
});
const byId = (id) => available.find((item) => item.id === id);

test('every module says what you do in one short line', () => {
  for (const item of available) {
    assert.match(item.description, /^[A-Z][^.]{10,60}$/, `${item.id}: ${item.description}`);
    assert.doesNotMatch(item.description, /,.*,.*,/, `${item.id} lists what it contains`);
  }
});

test('before anything is touched every module without a download is on and downloads stay off', () => {
  assert.deepEqual(withoutDownload(available, listing()), ['workspace']);
  assert.deepEqual(withoutDownload(available, listing({ math: 'built-in', brain: 'installed' })), ['workspace', 'math', 'brain']);
  // Until the list answers nothing is known to be here.
  assert.deepEqual(withoutDownload(available, null), ['workspace']);
  assert.deepEqual(preselect(available, listing(), ['workspace'], []), ['workspace']);
});

test('an answer adds its modules; a switch is the last word; the order is the registry\'s', () => {
  const packs = listing();
  assert.deepEqual(preselect(available, packs, ['workspace'], ['math', 'workspace']), ['workspace', 'math']);
  assert.deepEqual(preselect(available, packs, ['workspace'], ['brain', 'terminal', 'applyai']), ['applyai', 'workspace', 'terminal', 'brain']);
  assert.deepEqual(preselect(available, packs, ['workspace'], ['math'], { math: false, voice: true }), ['workspace', 'voice']);
  assert.deepEqual(preselect(available, packs, ['workspace'], [], { workspace: false }), []);
});

test('an answer cannot choose a module whose download is not offered; a saved one stays', () => {
  const offline = listing({ math: 'unavailable', brain: 'unavailable' }, 'rika! could not reach GitHub to check for downloads');
  assert.equal(moduleObtainable(byId('math'), offline), false);
  assert.equal(moduleObtainable(byId('workspace'), offline), true);
  assert.equal(moduleObtainable(byId('voice'), offline), true);
  assert.equal(moduleObtainable(byId('math'), null), false);
  assert.deepEqual(preselect(available, offline, ['workspace'], ['math', 'brain', 'terminal']), ['workspace', 'terminal']);
  assert.deepEqual(preselect(available, offline, ['workspace', 'math'], []), ['workspace', 'math']);
});

test('ready means built in or installed; a download in flight is not ready yet', () => {
  assert.equal(moduleReady(byId('workspace'), null), true);
  for (const [state, ready] of [['built-in', true], ['installed', true], ['downloading', false], ['outdated', false], ['available', false], ['unavailable', false]])
    assert.equal(moduleReady(byId('brain'), listing({ brain: state })), ready, state);
});

test('after an update the packs an older version left are wanted again: the saved modules\' and those no module owns', () => {
  const packs = listing({ math: 'outdated', voice: 'outdated', brain: 'outdated', themes: 'outdated', herdr: 'installed', animations: 'installed' });
  const ids = (configured) => renewals(available, configured, packs).map((pack) => pack.id);
  // Math is off: its old download waits until Math is turned on again.
  assert.deepEqual(ids(['workspace', 'terminal', 'voice', 'brain']), ['voice', 'brain', 'themes']);
  assert.deepEqual(ids(['workspace']), ['themes']);
  assert.deepEqual(renewals(available, ['workspace', 'brain'], listing({ brain: 'available' })), [], 'a pack never downloaded is not fetched by itself');
  assert.deepEqual(renewals(available, ['workspace', 'brain'], null), []);
});

test('the total counts each pack once and names what is unsized', () => {
  const packs = listing();
  const none = totals(available, ['workspace'], packs);
  assert.deepEqual([none.on, none.of, none.bytes, none.downloads.length], [1, 7, 0, 0]);
  assert.equal(totalLine(none), '1 of 7 on, nothing to download');
  const some = totals(available, ['workspace', 'math', 'brain'], packs);
  assert.deepEqual(some.downloads.map((pack) => pack.id), ['math', 'brain']);
  assert.equal(totalLine(some), '3 of 7 on, 31.2 MB to download');
  assert.equal(totalLine(totals(available, ['workspace', 'math', 'brain'], listing({ math: 'installed' }))), '3 of 7 on, 1.6 MB to download');
  const all = totals(available, available.map((item) => item.id), packs);
  assert.equal(totalLine(all), '7 of 7 on, 844 MB to download');
  const unsized = listing();
  unsized.packs.find((pack) => pack.id === 'brain').size = null;
  assert.equal(totalLine(totals(available, ['math', 'brain'], unsized)), '2 of 7 on, 29.6 MB plus 1 download');
});

test('sizes read as megabytes, whole above a hundred, then gigabytes', () => {
  assert.deepEqual([null, 0, 4_000, 1.6 * MB, 29 * MB, 275 * MB, 1.2e9].map(megabytes), ['', '0 MB', '0.1 MB', '1.6 MB', '29.0 MB', '275 MB', '1.2 GB']);
});

test('a selection is the same whatever its order', () => {
  assert.ok(sameSelection(['workspace', 'brain'], ['brain', 'workspace']));
  assert.ok(!sameSelection(['workspace'], ['workspace', 'brain']));
  assert.ok(!sameSelection(['workspace', 'math'], ['workspace', 'brain']));
});

// The store: its runes compiled for the server (plain fields), `./api` replaced by a stand-in
// that answers from `server`, a small rika-server: modules, packs with downloads that take
// two polls, and a desktop shell.
const scratch = fileURLToPath(new URL(`../node_modules/.kitty-module-store-${process.pid}/`, import.meta.url));
let server;

async function loadStore() {
  mkdirSync(scratch, { recursive: true });
  const source = readFileSync(new URL('../src/lib/modulePicker.svelte.ts', import.meta.url), 'utf8')
    // The polls are real timers; nothing here needs to wait half a second for one.
    .replace('const POLL_MS = 500;', 'const POLL_MS = 5;');
  const { code } = await transform(source, { loader: 'ts', format: 'esm', target: 'esnext' });
  const compiled = compileModule(code, { generate: 'server', filename: 'modulePicker.svelte.js' }).js.code
    .replace(/from\s+["']\.\/api["']/, 'from "./api.mjs"')
    .replace(/from\s+["']\.\/modulePicker["']/, `from ${JSON.stringify(pathToFileURL(fileURLToPath(new URL('../src/lib/modulePicker.ts', import.meta.url))).href)}`);
  writeFileSync(`${scratch}api.mjs`, `
    export class ApiError extends Error { constructor(message, status) { super(message); this.status = status; } }
    export async function api(path, body) { return globalThis.__kittyModuleServer(path, body, ApiError); }
  `);
  writeFileSync(`${scratch}store.mjs`, compiled);
  globalThis.__kittyModuleServer = (path, body, ApiError) => server.answer(path, body, ApiError);
  return import(pathToFileURL(`${scratch}store.mjs`).href);
}

function fakeServer({ configured = ['workspace'], states = {}, desktop = false, failing = '', offline = '' } = {}) {
  const state = { configured: [...configured], enabled: [...configured], calls: [], packs: { ...states }, polls: {} };
  const modules = () => ({ enabled: state.enabled, configured: state.configured, available, restart_required: !sameSelection(state.configured, state.enabled), missing: [] });
  const packs = () => {
    const now = listing(state.packs, offline || undefined);
    for (const pack of now.packs) {
      if (pack.state !== 'downloading') continue;
      state.polls[pack.id] += 1;
      if (pack.id === failing) { state.packs[pack.id] = 'available'; Object.assign(pack, { state: 'available', error: 'The download was cut off' }); }
      else if (state.polls[pack.id] > 2) { state.packs[pack.id] = 'installed'; pack.state = 'installed'; }
      else pack.progress = { received: pack.size / 2, total: pack.size };
    }
    return now;
  };
  state.answer = (path, body, ApiError) => {
    state.calls.push(body === undefined ? path : [path, body]);
    if (path === '/modules') {
      if (body) {
        const lacking = body.enabled.find((id) => !moduleReady(byId(id), listing(state.packs)));
        if (lacking) throw new ApiError(`${lacking} needs its download first`, 409);
        state.configured = body.enabled;
      }
      return modules();
    }
    if (path === '/packs') return packs();
    if (path === '/packs/install') { state.packs[body.id] = 'downloading'; state.polls[body.id] = 0; return packs(); }
    if (path === '/packs/uninstall') { state.packs[body.id] = 'available'; return packs(); }
    if (path === '/desktop') return { available: desktop };
    if (path === '/desktop/command') {
      if (!desktop) throw new ApiError('Desktop controls are only available in the rika! desktop app', 409);
      return { ok: true };
    }
    throw new ApiError(`no route ${path}`, 404);
  };
  return state;
}

test('the store', async (t) => {
  const { moduleStore: store } = await loadStore();
  t.after(() => rmSync(scratch, { recursive: true, force: true }));
  const fresh = (options) => {
    server = fakeServer(options);
    Object.assign(store, { modules: null, packs: null, error: '', packsError: '', phase: '', progress: null, desktop: null, restarting: false });
  };
  const posts = () => server.calls.filter((call) => Array.isArray(call)).map(([path, body]) => `${path} ${JSON.stringify(body)}`);

  await t.test('loads the bundled list, the packs and whether the desktop app is there', async () => {
    fresh({ desktop: true });
    await store.load();
    await store.loadPacks();
    assert.deepEqual(store.modules.available.map((item) => item.id), available.map((item) => item.id));
    assert.equal(store.packs.packs.length, registry.packs.length);
    assert.equal(store.desktop, true);
    assert.equal(store.busy, false);
    assert.equal(store.downloading, false);
  });

  await t.test('apply downloads what the selection lacks, one pack at a time, then saves it', async () => {
    fresh();
    const seen = [];
    const watch = setInterval(() => { if (store.progress) seen.push({ ...store.progress, phase: store.phase }); }, 1);
    const saved = await store.apply(['workspace', 'math', 'brain']);
    clearInterval(watch);
    assert.deepEqual(posts(), ['/packs/install {"id":"math"}', '/packs/install {"id":"brain"}', '/modules {"enabled":["workspace","math","brain"]}']);
    assert.deepEqual(saved.configured, ['workspace', 'math', 'brain']);
    assert.equal(saved.restart_required, true);
    assert.equal(store.modules, saved);
    assert.deepEqual([store.phase, store.progress, store.busy], ['', null, false]);
    // One measure over both packs: it never restarts for the second.
    assert.ok(seen.length && seen.every((step) => step.total === SIZES.math + SIZES.brain && step.phase === 'downloading'));
    assert.ok(seen.every((step, index) => index === 0 || step.received >= seen[index - 1].received));
    assert.ok(seen.some((step) => step.label === 'Brain' && step.received >= SIZES.math), 'the second pack starts where the first ended');
  });

  await t.test('apply with nothing to download only saves', async () => {
    fresh({ states: { math: 'installed' } });
    await store.apply(['workspace', 'math']);
    assert.deepEqual(posts(), ['/modules {"enabled":["workspace","math"]}']);
  });

  await t.test('a failed download saves nothing, says which pack and leaves the store usable', async () => {
    fresh({ failing: 'brain' });
    await assert.rejects(store.apply(['workspace', 'math', 'brain']), /^Error: Brain: The download was cut off$/);
    assert.deepEqual(posts(), ['/packs/install {"id":"math"}', '/packs/install {"id":"brain"}']);
    assert.deepEqual(server.configured, ['workspace']);
    assert.equal(server.packs.math, 'installed', 'what already arrived stays');
    assert.deepEqual([store.phase, store.progress], ['', null]);
    await store.apply(['workspace', 'math']);
    assert.deepEqual(server.configured, ['workspace', 'math']);
  });

  await t.test('a second apply is refused while one runs', async () => {
    fresh();
    const first = store.apply(['workspace', 'brain']);
    await assert.rejects(store.apply(['workspace']), /already being saved/);
    await first;
    assert.deepEqual(server.configured, ['workspace', 'brain']);
  });

  await t.test('enable adds one module to the saved selection, in the registry\'s order', async () => {
    fresh({ configured: ['workspace', 'brain'], states: { brain: 'installed' } });
    const saved = await store.enable('applyai');
    assert.deepEqual(posts(), ['/packs/install {"id":"discovery"}', '/modules {"enabled":["applyai","workspace","brain"]}']);
    assert.deepEqual(saved.configured, ['applyai', 'workspace', 'brain']);
  });

  await t.test('remove uninstalls one pack', async () => {
    fresh({ states: { brain: 'installed' } });
    await store.remove('brain');
    assert.equal(store.packs.packs.find((pack) => pack.id === 'brain').state, 'available');
    assert.equal(store.packsError, '');
  });

  await t.test('restart asks the desktop app; a plain browser answers no and nothing is claimed', async () => {
    fresh({ desktop: true });
    assert.equal(await store.restart(), true);
    assert.equal(store.restarting, true);
    assert.deepEqual(posts(), ['/desktop/command {"command":"restart"}']);
    fresh();
    assert.equal(await store.restart(), false);
    assert.deepEqual([store.restarting, store.desktop, store.error], [false, false, '']);
  });

  await t.test('renew downloads again what an older version left, as one measure, and saves no selection', async () => {
    fresh({ configured: ['workspace', 'voice', 'brain'], states: { math: 'outdated', voice: 'outdated', brain: 'outdated', herdr: 'installed' } });
    const seen = [];
    const watch = setInterval(() => { if (store.progress) seen.push({ ...store.progress, phase: store.phase }); }, 1);
    assert.equal(await store.renew(), true);
    clearInterval(watch);
    assert.deepEqual(posts(), ['/packs/install {"id":"voice"}', '/packs/install {"id":"brain"}']);
    assert.deepEqual([server.packs.voice, server.packs.brain, server.packs.math], ['installed', 'installed', 'outdated']);
    assert.ok(seen.length && seen.every((step) => step.total === SIZES.voice + SIZES.brain && step.phase === 'downloading'));
    assert.ok(seen.some((step) => step.label === 'Brain' && step.received >= SIZES.voice), 'the second pack starts where the first ended');
    assert.deepEqual([store.phase, store.progress, store.packsError], ['', null, '']);
  });

  await t.test('renew with nothing left from an older version downloads nothing and never shows a download', async () => {
    fresh({ configured: ['workspace', 'brain'], states: { brain: 'installed' } });
    const phases = new Set();
    const watch = setInterval(() => phases.add(store.phase), 1);
    assert.equal(await store.renew(), false);
    clearInterval(watch);
    assert.deepEqual(posts(), []);
    assert.deepEqual([...phases].filter(Boolean), []);
  });

  await t.test('renew tries nothing while this version\'s download list cannot be reached', async () => {
    fresh({ configured: ['workspace', 'brain'], states: { brain: 'outdated' }, offline: 'rika! could not reach GitHub to check for downloads' });
    assert.equal(await store.renew(), false);
    assert.deepEqual(posts(), []);
    assert.equal(store.phase, '');
  });

  await t.test('a renewal that fails says which pack, reports nothing to restart for and leaves the store usable', async () => {
    fresh({ configured: ['workspace', 'voice', 'brain'], states: { voice: 'outdated', brain: 'outdated' }, failing: 'brain' });
    assert.equal(await store.renew(), false);
    assert.equal(store.packsError, 'Brain: The download was cut off');
    assert.equal(server.packs.voice, 'installed', 'what already arrived stays');
    assert.deepEqual([store.phase, store.progress], ['', null]);
  });
});
