// Career's Overview (Q13 B): the week goal, this week's deadlines and Rika's suggestion. Synthetic data only.
// Run with Node >=22.6: node --experimental-strip-types --test tests/careerOverview.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {deadlinesThisWeek,suggestion,trackLabel,weekGoal,weekday} from '../src/lib/career/overview.ts';

const TODAY='2026-11-20';
const job=(id,company,deadline_at=null)=>({id,company,title:`Engineer ${id}`,deadline_at});
const WEEKLY=[
  {track:'software_engineering',weekly_target:3,applied_this_week:2,remaining:1},
  {track:'data',weekly_target:2,applied_this_week:3,remaining:0},
  {track:'parttime',weekly_target:0,applied_this_week:1,remaining:0},
];

test('the week goal adds up the tracks that set one; extra on one track does not cover another',()=>{
  const goal=weekGoal(WEEKLY);
  assert.deepEqual(goal.tracks.map(item=>[item.label,item.sent,item.target]),[['Software engineering',2,3],['Data',3,2]]);
  assert.equal(goal.sent,5);
  assert.equal(goal.target,5);
  assert.equal(goal.remaining,1);
  assert.deepEqual(weekGoal([]),{sent:0,target:0,remaining:0,tracks:[]});
  assert.equal(trackLabel('part-time_jobs'),'Part time jobs');
});

test('deadlines this week: postings closing within seven days and due reminders, soonest first',()=>{
  const rows=deadlinesThisWeek(TODAY,[job(1,'Halcyon','2026-11-21'),job(2,'Later','2026-11-27'),job(3,'Past','2026-11-19'),job(4,'Edge','2026-11-26T23:00:00Z')],
    [{id:7,job_id:1,label:'Meridian - Follow up',due_at:'2026-11-18T09:00:00Z'},{id:8,job_id:null,label:'Call the recruiter',due_at:'2026-11-20T15:00:00Z'}]);
  assert.deepEqual(rows.map(row=>[row.date,row.kind,row.title,row.overdue]),[
    ['2026-11-20','reminder','Meridian',true],['2026-11-20','reminder','Call the recruiter',false],
    ['2026-11-21','deadline','Halcyon',false],['2026-11-26','deadline','Edge',false]]);
  assert.equal(rows[0].detail,'Follow up · reminder');
  assert.equal(weekday('2026-11-21'),'Sat');
});

test('Rika suggests the closing strong match first, then waiting emails, reminders, the best match',()=>{
  const goal=weekGoal(WEEKLY);
  const closing=suggestion({today:TODAY,goal,matches:[job(1,'Northwind'),job(2,'Halcyon','2026-11-21')],emails:2,deadlines:[]});
  assert.equal(closing.title,'Apply to Halcyon before Sat');
  assert.deepEqual(closing.action,{label:'Open the job',job:2});
  assert.match(closing.body,/1 application short/);
  const emails=suggestion({today:TODAY,goal,matches:[job(1,'Northwind')],emails:2,deadlines:[]});
  assert.equal(emails.title,'Review 2 status emails');
  assert.equal(emails.action.view,'mail');
  const best=suggestion({today:TODAY,goal,matches:[job(1,'Northwind')],emails:0,deadlines:[]});
  assert.equal(best.title,"Look at Northwind's Engineer 1");
  const none=suggestion({today:TODAY,goal,matches:[],emails:0,deadlines:[]});
  assert.equal(none.action.view,'inbox');
  const met=suggestion({today:TODAY,goal:weekGoal([{track:'data',weekly_target:1,applied_this_week:1,remaining:0}]),matches:[job(1,'Northwind')],emails:0,deadlines:[]});
  assert.equal(met.title,"You're on track this week");
});
