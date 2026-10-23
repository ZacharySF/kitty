// Run with Node >=22.6: node --experimental-strip-types --test tests/workspaceModules.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {shortcutsFor,viewEnabled,visibleGroups,visibleNavigation,visibleRelations} from '../src/lib/workspace.ts';

const ids=(items)=>items.map(item=>item.id);
const labels=(groups)=>groups.map(group=>group.label);

test('all modules off leaves only the core views and the Settings group',()=>{
  assert.deepEqual(ids(visibleNavigation([])).sort(),['ai','settings','today']);
  assert.deepEqual(labels(visibleGroups([])),['Settings']);
  assert.deepEqual(visibleGroups([])[0].views,['settings','ai']);
  assert.deepEqual(visibleRelations([]),[]);
});

test('each module alone exposes only its own views',()=>{
  const workspaceOnly=ids(visibleNavigation(['workspace']));
  assert.ok(workspaceOnly.includes('notes')&&workspaceOnly.includes('calendar'));
  assert.ok(!workspaceOnly.includes('jobs')&&!workspaceOnly.includes('reminders')&&!workspaceOnly.includes('profile'));
  assert.deepEqual(labels(visibleGroups(['workspace'])),['Plan','Study','Settings']);
  const applyaiOnly=ids(visibleNavigation(['applyai']));
  assert.ok(applyaiOnly.includes('jobs')&&applyaiOnly.includes('profile')&&applyaiOnly.includes('reminders'));
  assert.ok(!applyaiOnly.includes('notes')&&!applyaiOnly.includes('calendar'));
  assert.deepEqual(labels(visibleGroups(['applyai'])),['Plan','Job search','Settings']);
  assert.deepEqual(visibleGroups(['applyai'])[0].views,['today','reminders']);
  assert.equal(viewEnabled('study',['applyai']),false);
  assert.equal(viewEnabled('study',['workspace']),true);
  assert.equal(viewEnabled('ai',[]),true);
});

test('relations never cross into a disabled module',()=>{
  const all=['applyai','workspace'];
  assert.ok(visibleRelations(all).some(([a,b])=>a==='schema'&&b==='jobs'));
  for(const enabled of [['workspace'],['applyai']])
    for(const [a,b] of visibleRelations(enabled)){
      assert.ok(viewEnabled(a,enabled)&&viewEnabled(b,enabled),`${a}-${b}`);
    }
  assert.ok(!visibleRelations(['workspace']).some(([a,b])=>a==='schema'&&b==='jobs'));
  assert.ok(!visibleRelations(['applyai']).some(([a,b])=>a==='calendar'||b==='calendar'));
});

test('keyboard help lists only reachable shortcuts',()=>{
  const titles=(enabled)=>shortcutsFor(enabled).map(group=>group.title);
  assert.deepEqual(titles([]),['Navigate','Atlas home']);
  assert.ok(titles(['workspace']).includes('Study'));
  assert.ok(!titles(['workspace']).includes('Job decisions'));
  assert.ok(titles(['applyai']).includes('Job decisions'));
  assert.ok(!titles(['applyai']).includes('Study'));
  const navigate=(enabled)=>shortcutsFor(enabled).find(group=>group.title==='Navigate').shortcuts;
  assert.ok(navigate([]).some(([keys])=>keys==='Ctrl + V'));
  assert.ok(!navigate([]).some(([keys])=>keys.includes('Ctrl + X')||keys.includes('Ctrl + C')));
  assert.ok(navigate(['applyai']).some(([keys])=>keys.includes('Ctrl + C')));
});
