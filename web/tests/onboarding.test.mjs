import test from 'node:test';
import assert from 'node:assert/strict';
import { tutorialSteps } from '../src/lib/onboarding.ts';

test('core-only tutorial explains disabled modules without navigating into them', () => {
  const steps = tutorialSteps([], false);
  assert.deepEqual(steps.map(step => step.id), ['workspace', 'modules', 'providers', 'atlas', 'quick-notes', 'study']);
  assert.deepEqual(steps.filter(step => ['quick-notes', 'study'].includes(step.id)).map(step => step.view), ['settings', 'settings']);
  assert.ok(steps.filter(step => ['quick-notes', 'study'].includes(step.id)).every(step => step.unavailable));
});

test('enabled study tools point at real views; terminal requires a visible dock', () => {
  const steps = tutorialSteps(['workspace', 'terminal'], false);
  assert.equal(steps.find(step => step.id === 'quick-notes').view, 'quick-notes');
  assert.equal(steps.find(step => step.id === 'study').view, 'study');
  assert.ok(!steps.some(step => step.id === 'terminal'));
  assert.equal(tutorialSteps(['workspace', 'terminal'], true).at(-1).id, 'terminal');
  assert.ok(!tutorialSteps([], true).some(step => step.id === 'terminal'));
});

test('terminal step targets markup the dock actually renders', async () => {
  const { readFile } = await import('node:fs/promises');
  const dock = await readFile(new URL('../src/lib/Dock.svelte', import.meta.url), 'utf8');
  const step = tutorialSteps(['terminal'], true).find(item => item.id === 'terminal');
  const parts = step.selector.split(',').map(part => part.trim());
  assert.ok(parts.length > 0);
  for (const part of parts) {
    const token = part.startsWith('.') ? `class="${part.slice(1)}"` : part.replace(/^\[([^=]+)="([^"]+)"\]$/, '$1="$2"');
    assert.ok(dock.includes(token), `${part} not rendered by Dock.svelte`);
  }
});

test('providers step explains paid AI is opt-in and reviewed, on the real AI settings view', () => {
  const step = tutorialSteps([], false).find(item => item.id === 'providers');
  assert.equal(step.view, 'ai');
  assert.equal(step.selector, '.ai-panel');
  assert.match(step.text, /your own/i);
  assert.match(step.text + step.tip, /cost|budget/i);
});
