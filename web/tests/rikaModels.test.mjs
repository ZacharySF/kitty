import test from 'node:test';
import assert from 'node:assert/strict';
import { modelOptions, nearestThinking, shortModelLabel, thinkingChoices } from '../src/lib/rika/models.ts';

const CATALOG = {
  provider: 'omp',
  auto: ['off', 'low', 'high'],
  models: [
    { id: 'anthropic/claude-opus-5-5', label: 'Claude Opus 5.5', thinking: ['off', 'low', 'medium', 'high', 'max'] },
    { id: 'anthropic/claude-haiku-5-5', label: 'Claude Haiku 5.5', thinking: ['off', 'low', 'high'] },
    { id: 'openrouter/openai/gpt-plain', label: 'OpenAI: GPT Plain', thinking: ['off'] },
  ],
};

test('a model reads short on its chip: no vendor prefix, no "Claude"', () => {
  assert.equal(shortModelLabel('Claude Opus 5.5'), 'Opus 5.5');
  assert.equal(shortModelLabel('Anthropic: Claude Sonnet 4.5'), 'Sonnet 4.5');
  assert.equal(shortModelLabel('OpenAI: GPT-5.1'), 'GPT-5.1');
  assert.equal(shortModelLabel('Gemini 3 Pro'), 'Gemini 3 Pro');
  assert.equal(shortModelLabel('Claude'), 'Claude');
});

test('the model menu is Auto, then the catalog with each id as its hint; a pinned model missing from it stays listed', () => {
  const options = modelOptions(CATALOG, '');
  assert.deepEqual(options[0], { value: '', label: 'Auto', short: 'Auto', hint: 'by request' });
  assert.deepEqual(options[1], { value: 'anthropic/claude-opus-5-5', label: 'Claude Opus 5.5', short: 'Opus 5.5', hint: 'anthropic/claude-opus-5-5' });
  assert.equal(options.length, 4);
  const pinned = modelOptions(CATALOG, 'anthropic/claude-gone');
  assert.deepEqual(pinned.at(-1), { value: 'anthropic/claude-gone', label: 'anthropic/claude-gone', short: 'claude-gone', hint: 'not listed now' });
  assert.deepEqual(modelOptions(null, '').map((option) => option.value), ['']);
});

test('the thinking choices are the chosen model\'s levels; Auto is what the tier models share; unknown is every level', () => {
  const all = ['off', 'low', 'medium', 'high', 'max'];
  assert.deepEqual(thinkingChoices(CATALOG, 'anthropic/claude-opus-5-5', all), all);
  assert.deepEqual(thinkingChoices(CATALOG, 'openrouter/openai/gpt-plain', all), ['off']);
  assert.deepEqual(thinkingChoices(CATALOG, '', all), ['off', 'low', 'high']);
  assert.deepEqual(thinkingChoices(CATALOG, 'anthropic/claude-gone', all), all);
  assert.deepEqual(thinkingChoices(null, '', all), all);
});

test('an effort a model lacks falls back to the nearest it has, the lower one on a tie', () => {
  assert.equal(nearestThinking('max', ['off', 'low', 'medium', 'high']), 'high');
  assert.equal(nearestThinking('medium', ['off', 'low', 'high']), 'low');
  assert.equal(nearestThinking('high', ['off']), 'off');
  assert.equal(nearestThinking('low', ['off', 'low', 'high']), 'low');
  assert.equal(nearestThinking('off', ['off', 'high']), 'off');
});
