// Goals (docs/rika-design.md Q14 A) and the morning brief (Q15 A): the pure helpers.
// node --experimental-strip-types --test tests/goals.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  amount, daysLeftLabel, fitLabel, goalFromHash, goalHash, mailLine, matchLine, measureFraction, measureInputs, measureTarget, measureText,
  pace, percent, statusLabel, targetLabel,
} from '../src/lib/goals/goals.ts';
import { navigation, navigationGroups, viewEnabled } from '../src/lib/workspace.ts';

test('a measure reads "have / want unit" and its bar never overflows', () => {
  assert.equal(measureText({ current: 18, target: 40, unit: '' }), '18 / 40');
  assert.equal(measureText({ current: 33, target: 60, unit: 'min' }), '33 / 60 min');
  assert.equal(measureText({ current: 64, target: 100, unit: '%' }), '64 / 100%');
  assert.equal(measureText({ current: 2.25, target: 5, unit: ' h ' }), '2.3 / 5 h');
  assert.equal(measureTarget({ target: 60, unit: 'min' }), '60 min');
  assert.equal(measureTarget({ target: 100, unit: '%' }), '100%');
  assert.equal(measureFraction({ current: 18, target: 40 }), 0.45);
  assert.equal(measureFraction({ current: 9, target: 5 }), 1);
  assert.equal(measureFraction({ current: -3, target: 5 }), 0);
  assert.equal(measureFraction({ current: 3, target: 0 }), 0);
  assert.equal(amount(Number.NaN), '0');
  assert.equal(percent(0.636), 64);
  assert.equal(percent(1.4), 100);
  assert.equal(percent(Number.NaN), 0);
});

test('days left read as a countdown, today or overdue', () => {
  assert.equal(daysLeftLabel(133), '133 days left');
  assert.equal(daysLeftLabel(1), '1 day left');
  assert.equal(daysLeftLabel(0), 'due today');
  assert.equal(daysLeftLabel(-1), '1 day overdue');
  assert.equal(daysLeftLabel(-4), '4 days overdue');
  assert.equal(daysLeftLabel(null), '');
  assert.equal(targetLabel({ target_date: '2027-03-15', days_left: 133 }, 'en-US'), 'by Mar 15, 2027 · 133 days left');
  assert.equal(targetLabel({ target_date: null, days_left: null }, 'en-US'), '');
});

test('pace compares progress with the time gone since the goal was set', () => {
  const goal = { status: 'active', target_date: '2027-01-01', created_at: '2026-01-01T12:00:00Z', progress: 0.5, measures: [{}] };
  const midway = new Date(2026, 6, 2, 12);
  assert.equal(pace(goal, midway), 'on track');
  assert.equal(pace({ ...goal, progress: 0.2 }, midway), 'behind');
  assert.equal(pace({ ...goal, progress: 0 }, new Date(2026, 0, 1, 18)), 'on track');
  assert.equal(pace({ ...goal, status: 'paused' }, midway), null);
  assert.equal(pace({ ...goal, target_date: null }, midway), null);
  assert.equal(pace({ ...goal, measures: [] }, midway), null);
});

test('matches, mail counts and statuses read as short lines', () => {
  const match = { detail: 'posted 2 h ago', score: 0.914, classified_by: 'model', goal_title: 'Summer internship' };
  assert.equal(fitLabel(match), '91% fit');
  // A keyword match's score is no measured fit, so it never claims a percentage.
  assert.equal(fitLabel({ score: 1, classified_by: 'keywords' }), 'keyword match');
  assert.equal(matchLine(match), 'posted 2 h ago · 91% fit · Summer internship');
  assert.equal(matchLine({ ...match, classified_by: 'keywords' }), 'posted 2 h ago · keyword match · Summer internship');
  assert.equal(matchLine({ ...match, detail: '' }, false), '91% fit');
  assert.equal(mailLine({ drafted: 2, scheduled: 1, sent: 0 }), '2 replies drafted · 1 scheduled');
  assert.equal(mailLine({ drafted: 1, scheduled: 0, sent: 3 }), '1 reply drafted · 3 sent');
  assert.equal(mailLine({ drafted: 0, scheduled: 0, sent: 0 }), '');
  assert.equal(mailLine(null), '');
  assert.equal(statusLabel('dropped'), 'Dropped');
});

test('the measures editor sends clean measures or names the first problem', () => {
  const rows = [
    { id: 'm1', label: ' Applications sent ', target: '40', unit: '', current: '18', auto: 'applications' },
    { label: 'Portfolio projects', target: '3', unit: ' projects ', current: '2', auto: null },
    { label: '', target: '', unit: '', current: '0', auto: null },
  ];
  assert.deepEqual(measureInputs(rows), {
    error: '',
    measures: [
      { id: 'm1', label: 'Applications sent', target: 40, unit: '', auto: 'applications' },
      { label: 'Portfolio projects', target: 3, unit: 'projects', current: 2, auto: null },
    ],
  });
  assert.equal(measureInputs([{ label: '', target: '4', unit: '', current: '0', auto: null }]).error, 'Every measure needs a name.');
  assert.equal(measureInputs([{ label: 'Runs', target: '0', unit: '', current: '0', auto: null }]).error, '“Runs” needs a target above 0.');
  assert.equal(measureInputs([{ label: 'Runs', target: 'x', unit: '', current: '0', auto: null }]).error, '“Runs” needs a target above 0.');
  assert.equal(measureInputs([{ label: 'Runs', target: '4', unit: '', current: '-1', auto: null }]).error, '“Runs” needs a current amount of 0 or more.');
});

test('a goal deep link names its goal and round-trips', () => {
  assert.equal(goalHash(12), '#goals/12');
  assert.equal(goalFromHash(goalHash('a b')), 'a b');
  assert.equal(goalFromHash('#goals/12'), '12');
  assert.equal(goalFromHash('#goals/a%20b'), 'a b');
  assert.equal(goalFromHash('#goals'), null);
  assert.equal(goalFromHash('#settings/modules'), null);
});

test('Goals is a core Plan view right after the atlas home', () => {
  const goals = navigation.find(item => item.id === 'goals');
  assert.deepEqual(goals && { label: goals.label, module: goals.module }, { label: 'Goals', module: null });
  assert.equal(viewEnabled('goals', []), true);
  const plan = navigationGroups.find(group => group.label === 'Plan').views;
  assert.equal(plan.indexOf('goals'), plan.indexOf('today') + 1);
});
