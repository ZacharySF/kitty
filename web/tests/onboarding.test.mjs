import test from 'node:test';
import assert from 'node:assert/strict';
import { readdir, readFile } from 'node:fs/promises';
import { tutorialSteps } from '../src/lib/onboarding.ts';

test('core-only tutorial explains disabled modules without navigating into them', () => {
  const steps = tutorialSteps([]);
  assert.deepEqual(steps.map(step => step.id), ['workspace', 'modules', 'providers', 'home', 'quick-notes', 'study', 'rika']);
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

test('the modules step points at the Brain download only while the Brain is off', () => {
  assert.match(tutorialSteps([]).find(step => step.id === 'modules').text, /Brain/);
  assert.doesNotMatch(tutorialSteps(['brain']).find(step => step.id === 'modules').text, /Brain/);
});

test('the Rika step targets markup Rika actually renders', async () => {
  const folder = new URL('../src/lib/rika/', import.meta.url);
  const sources = await Promise.all((await readdir(folder)).filter(name => name.endsWith('.svelte')).map(name => readFile(new URL(name, folder), 'utf8')));
  const step = tutorialSteps([]).find(item => item.id === 'rika');
  const token = step.selector.replace(/^\[([^=]+)="([^"]+)"\]$/, '$1="$2"');
  assert.ok(sources.some(source => source.includes(token)), `${step.selector} not rendered under src/lib/rika/`);
});

test('providers step explains paid AI is opt-in and reviewed, on the real AI settings view', () => {
  const step = tutorialSteps([]).find(item => item.id === 'providers');
  assert.equal(step.view, 'ai');
  assert.equal(step.selector, '.ai-panel');
  assert.match(step.text, /your own/i);
  assert.match(step.text + step.tip, /cost|budget/i);
  assert.match(step.text, /review/i);
});

test('tour copy stays short: no middle-dot strings, no taglines', () => {
  for (const enabled of [[], ['workspace', 'terminal', 'brain']]) {
    for (const step of tutorialSteps(enabled)) {
      assert.ok(step.title.split(' ').length <= 4, `${step.id} title is a tagline: ${step.title}`);
      assert.ok(step.text.split(' ').length <= 40, `${step.id} text is too long`);
      assert.doesNotMatch(step.tip, /·/, `${step.id} tip joins with middle dots`);
    }
  }
});
