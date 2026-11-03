// Agent skills helpers (docs/agent-skills-design.md): the now chip's text and tooltip and
// the launch flags a skill checklist shows before Start agent.
import test from 'node:test';
import assert from 'node:assert/strict';
import { launchPreview, nowText, nowTitle } from '../src/lib/agentSkills.ts';

const now = (fields) => ({ skill: null, tool: null, intent: null, via: null, label: '—', ...fields });

test('the now chip reads "now: skill · tool", or a dash when nothing answered', () => {
  assert.equal(nowText(now({ skill: 'lavish', tool: 'read', label: 'lavish · read' })), 'now: lavish · read');
  assert.equal(nowText(now({})), 'now: —');
});

test('the tooltip carries omp\'s intent and names the source', () => {
  assert.equal(nowTitle(now({ intent: 'Reading the lavish skill', via: 'extension' })), "Reading the lavish skill · What it is using now, from kitty!'s omp extension");
  assert.equal(nowTitle(now({ via: 'log' })), "What it is using now, from the agent's session log");
  assert.equal(nowTitle(now({})), 'Nothing reported yet');
});

test('an untouched checklist adds no flags', () => {
  for (const kind of ['omp', 'claude', 'codex']) assert.equal(launchPreview(kind, ['a', 'b'], new Set()), kind);
});

test('each agent gets its own checklist flags', () => {
  const skills = ['grill-me', 'lavish', 'loopy'];
  assert.equal(launchPreview('omp', skills, new Set(['lavish'])), "omp --skills 'grill-me,loopy'");
  assert.equal(launchPreview('omp', skills, new Set(skills)), 'omp --no-skills');
  assert.equal(launchPreview('claude', skills, new Set(['lavish'])), `claude --settings '{"skillOverrides":{"lavish":"off"}}'`);
  assert.equal(launchPreview('codex', skills, new Set(['lavish', 'loopy'])), `codex -c 'skills.config=[{name="lavish",enabled=false},{name="loopy",enabled=false}]'`);
});
