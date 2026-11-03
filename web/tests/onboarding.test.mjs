import test from 'node:test';
import assert from 'node:assert/strict';
import { tutorialSteps } from '../src/lib/onboarding.ts';

test('core-only tutorial explains disabled modules without navigating into them', () => {
  const steps = tutorialSteps([]);
  assert.deepEqual(steps.map(step => step.id), ['workspace', 'modules', 'providers', 'atlas', 'quick-notes', 'study', 'rika']);
  assert.deepEqual(steps.filter(step => ['quick-notes', 'study'].includes(step.id)).map(step => step.view), ['settings', 'settings']);
  assert.ok(steps.filter(step => ['quick-notes', 'study'].includes(step.id)).every(step => step.unavailable));
});

test('enabled study tools point at real views; the Rika step ends every tour and names Tern only with Terminal on', () => {
  const steps = tutorialSteps(['workspace', 'terminal']);
  assert.equal(steps.find(step => step.id === 'quick-notes').view, 'quick-notes');
  assert.equal(steps.find(step => step.id === 'study').view, 'study');
  assert.ok(!steps.some(step => step.id === 'terminal'), 'the dock tour step is back');
  assert.equal(steps.at(-1).id, 'rika');
  assert.match(steps.at(-1).text, /Tern windows/);
  assert.doesNotMatch(tutorialSteps([]).at(-1).text, /Tern/);
  assert.match(steps.at(-1).tip, /Ctrl \+ J/);
});

test('the Rika step targets markup the Rika bar actually renders', async () => {
  const { readFile } = await import('node:fs/promises');
  const bar = await readFile(new URL('../src/lib/rika/RikaBar.svelte', import.meta.url), 'utf8');
  const step = tutorialSteps([]).find(item => item.id === 'rika');
  const token = step.selector.replace(/^\[([^=]+)="([^"]+)"\]$/, '$1="$2"');
  assert.ok(bar.includes(token), `${step.selector} not rendered by RikaBar.svelte`);
});

test('providers step explains paid AI is opt-in and reviewed, on the real AI settings view', () => {
  const step = tutorialSteps([]).find(item => item.id === 'providers');
  assert.equal(step.view, 'ai');
  assert.equal(step.selector, '.ai-panel');
  assert.match(step.text, /your own/i);
  assert.match(step.text + step.tip, /cost|budget/i);
});
