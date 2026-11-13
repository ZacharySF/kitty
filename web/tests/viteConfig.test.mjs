import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import config from '../vite.config.ts';

// The dev server (`npm run dev`) forwards the API and add-on module page scripts to kitty-web.
const resolved = config({ mode: 'development', command: 'serve' });
const BACKEND = 'http://127.0.0.1:8765';

function middleware() {
  const plugin = resolved.plugins.find((item) => item?.name === 'local-api-origin');
  let handler;
  plugin.configureServer({ middlewares: { use: (fn) => { handler = fn; } } });
  return (url, origin) => {
    let status = 0;
    let passed = false;
    const headers = { host: '127.0.0.1:5173', ...(origin ? { origin } : {}) };
    handler({ url, headers }, { writeHead: (code) => { status = code; }, end() {} }, () => { passed = true; });
    return passed ? 'next' : status;
  };
}

test('module page scripts reach kitty-web with its own origin, like the API', () => {
  // A browser sends Origin with every module script import; kitty-web refuses one that is not its own.
  for (const path of ['/api', '/modules']) {
    const proxy = new EventEmitter();
    resolved.server.proxy[path].configure(proxy, {});
    const sent = {};
    proxy.emit('proxyReq', { setHeader: (name, value) => { sent[name] = value; } }, { headers: { origin: 'http://127.0.0.1:5173' } });
    assert.equal(sent.Origin, BACKEND, path);
  }
});

test('only the local dev page may reach the API or module scripts through the dev server', () => {
  const run = middleware();
  const script = '/modules/rika-math/0.1.0/ui/view.js';
  assert.equal(run(script, 'http://127.0.0.1:5173'), 'next');
  assert.equal(run(script), 'next');
  assert.equal(run(script, 'http://evil.example'), 403);
  assert.equal(run('/api/modules', 'http://evil.example'), 403);
  assert.equal(run('/src/main.ts', 'http://evil.example'), 'next');
});
