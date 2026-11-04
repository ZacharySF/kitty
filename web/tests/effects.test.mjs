// The pending-approvals list's text: what a proposed effect would do and the values it is re-checked against.
// node --test tests/effects.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { conditionText, previewLines } from '../src/lib/effects.ts';

const effect = (preview) => ({ preview, preconditions: {} });

test('the preview lists what would happen, in a fixed order, without empty fields', () => {
  const lines = previewLines(effect({
    text: 'drop this', action: 'Move this block and its children to Trash', page: 'Plans',
    reason: '', child_count: 1, children: ['and its child'],
  }));
  assert.deepEqual(lines.map((line) => line.label), ['Does', 'Page', 'Text', 'Children']);
  assert.equal(lines[2].value, 'drop this');
});

test('an application proposal shows the posting and where you submit it yourself', () => {
  const lines = previewLines(effect({
    action: 'Submit this application (you do it, in the browser)', company: 'Synthetic Labs', title: 'Intern',
    apply_url: 'https://example.com/jobs/1/apply', notice: 'rika! never submits applications.',
  }));
  assert.deepEqual(lines.map((line) => line.label), ['Does', 'Company', 'Role', 'Application']);
  assert.equal(previewLines(effect(null)).length, 0);
});

test('precondition values read as text', () => {
  assert.equal(conditionText(null), 'none');
  assert.equal(conditionText('sha256:ab'), 'sha256:ab');
  assert.equal(conditionText({ blocks: 1 }), '{"blocks":1}');
});
