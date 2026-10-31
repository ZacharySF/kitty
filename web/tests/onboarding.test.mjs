import test from 'node:test';
import assert from 'node:assert/strict';
import { tutorialSteps } from '../src/lib/onboarding.ts';

test('core-only tutorial explains disabled modules without navigating into them', () => {
  const steps = tutorialSteps([], false);
  assert.deepEqual(steps.map(step => step.id), ['workspace', 'modules', 'atlas', 'quick-notes', 'study']);
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
