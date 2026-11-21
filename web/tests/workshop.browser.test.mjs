// Synthetic disposable backend only. Run: node --test tests/workshop.browser.test.mjs
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';
import { startHarness, startKitty } from './kittyServer.js';
import { routeBuild } from './buildRoutes.mjs';
let kitty, server, browser, base;
before(async () => {
  kitty = await startKitty();
  server = await startHarness(kitty.origin); base = server.resolvedUrls.local[0]; browser = await chromium.launch();
});
after(async () => { await browser?.close(); await server?.close(); await kitty?.stop(); });
test('create, autosave, reopen, export and preserve a conflicting draft', async () => {
  const context = await browser.newContext(); await kitty.authorize(context); await routeBuild(context);
  await context.route('**/api/account', route => route.request().method() === 'GET' ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } }) : route.continue());
  const page = await context.newPage(); const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${base}#workshop`);
  await page.getByLabel('New resume title').fill('Synthetic workshop resume');
  await page.getByRole('button', { name: 'Create resume', exact: true }).click();
  const editor = page.getByLabel('Resume source'); await editor.waitFor();
  const source = '<!doctype html><html><body><h1>Synthetic Applicant</h1><p>Verified synthetic experience.</p></body></html>';
  await editor.fill(source);
  await page.getByText('All changes saved', { exact: true }).waitFor();
  await page.reload(); await page.getByRole('button', { name: /Synthetic workshop resume/ }).first().click();
  assert.equal(await editor.inputValue(), source);
  await page.getByRole('button', { name: 'Export PDF and register', exact: true }).click();
  await page.getByText(/Registered resume #/).waitFor({ timeout: 60000 });
  await page.route('**/api/workshop/*', route => route.request().method() === 'POST' && !route.request().url().endsWith('/compile') ? route.fulfill({ status: 409, json: { error: 'Revision conflict' } }) : route.continue());
  const draft = source.replace('Verified', 'Unsaved'); await editor.fill(draft);
  await page.getByText(/Conflict: your draft is preserved/).waitFor();
  assert.equal(await editor.inputValue(), draft);
  await page.waitForTimeout(1500); assert.equal(await editor.inputValue(), draft);
  assert.deepEqual(errors, []); await context.close();
});
test('the gallery offers Classic, Modern and Compact, each sketched with a centred name over full-width heading rules', async () => {
  const context = await browser.newContext(); await kitty.authorize(context); await routeBuild(context);
  await context.route('**/api/account', route => route.request().method() === 'GET' ? route.fulfill({ json: { configured: false, identity: null, onboarding: { welcome_completed: true, tutorial_completed: true } } }) : route.continue());
  const page = await context.newPage(); const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${base}#workshop`);
  for (const label of ['Classic', 'Modern', 'Compact']) {
    await page.getByRole('radio', { name: new RegExp(`^${label}`) }).first().waitFor();
    assert.equal(await page.getByRole('radio', { name: new RegExp(`^${label}`) }).count(), 2, `${label} in LaTeX and HTML`);
  }
  for (const id of ['latex-classic', 'latex-modern', 'latex-compact', 'html-classic', 'html-modern', 'html-compact']) {
    const thumb = page.locator(`.thumb[data-look="${id}"]`);
    const [paper, name, rule] = [await thumb.boundingBox(), await thumb.locator('b').boundingBox(), await thumb.locator('s').first().boundingBox()];
    assert.ok(Math.abs(name.x + name.width / 2 - (paper.x + paper.width / 2)) < 2, `${id}: the name is centred`);
    assert.ok(rule.width > paper.width * 0.7, `${id}: headings sit on a full-width rule`);
  }
  assert.deepEqual(errors, []); await context.close();
});
