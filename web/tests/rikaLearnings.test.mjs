import test from 'node:test';
import assert from 'node:assert/strict';
import { chipSize, editLine, evidenceJobs, grouped, skillLine, topSkills } from '../src/lib/rika/learnings.ts';

const item = (id, kind, text, detail = {}, extra = {}) =>
  ({ id, kind, key: text, text, weight: 1, evidence: [], detail, pinned: false, created_at: 't', updated_at: 't', ...extra });

test('learnings split into the Memory groups in server order', () => {
  const items = [
    item(1, 'posting_skill', 'Python'), item(2, 'posting_pattern', 'Summer 2027'),
    item(3, 'resume_edit', 'Lead with the result', { accepted: 2, rejected: 1 }),
    item(4, 'resume_edit', 'Drop the article', { accepted: 0, rejected: 2 }), item(5, 'note', 'Remote first'),
    item(6, 'posting_skill', 'SQL'),
  ];
  const groups = grouped(items);
  assert.deepEqual(groups.skills.map((x) => x.id), [1, 6]);
  assert.deepEqual(groups.patterns.map((x) => x.id), [2]);
  assert.deepEqual(groups.accepted.map((x) => x.id), [3]);
  assert.deepEqual(groups.rejected.map((x) => x.id), [4]);
  assert.deepEqual(groups.notes.map((x) => x.id), [5]);
  assert.deepEqual(topSkills(items, 1).map((x) => x.text), ['Python']);
});

test('chip sizes scale with weight against the heaviest skill', () => {
  assert.equal(chipSize(10, 10), 4);
  assert.equal(chipSize(5, 10), 2);
  assert.equal(chipSize(0.1, 10), 1);
  assert.equal(chipSize(-1, 10), 1);
  assert.equal(chipSize(3, 0), 1);
});

test('lines say postings, skips, facts and review counts', () => {
  assert.equal(skillLine(item(1, 'posting_skill', 'Python', { postings: 1, against: 0, fact: true })), '1 posting · confirmed fact');
  assert.equal(skillLine(item(1, 'posting_skill', 'SQL', { postings: 3, against: 2, fact: false })), '3 postings · 2 skipped · no confirmed fact');
  assert.equal(editLine(item(3, 'resume_edit', 'x', { accepted: 2, rejected: 1 })), 'Accepted 2 of 3');
});

test('evidence names the posting jobs that still exist, never proposals', () => {
  const jobs = { 4: { title: 'Engineer', company: 'Synthetic' }, 9: { title: 'Intern', company: '' } };
  assert.deepEqual(evidenceJobs(item(1, 'posting_skill', 'Python', {}, { evidence: [4, 7, 9] }), jobs),
    [{ id: 4, label: 'Engineer · Synthetic' }, { id: 9, label: 'Intern' }]);
  assert.deepEqual(evidenceJobs(item(3, 'resume_edit', 'x', {}, { evidence: ['4'] }), jobs), []);
});
