// Run with Node >=22.6: node --experimental-strip-types --test tests/workspaceModules.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {notePageLink,shortcutsFor,viewEnabled,visibleGroups,visibleNavigation,visibleRelations} from '../src/lib/workspace.ts';

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

test('the herdr, Tern and All agent skills views are Plan nodes owned by the Terminal module (Q10, Q11)',()=>{
  for(const view of ['herdr','tern','agent-skills']){
    assert.equal(viewEnabled(view,['terminal']),true);
    assert.equal(viewEnabled(view,['applyai','workspace']),false);
    assert.ok(!ids(visibleNavigation(['applyai','workspace'])).includes(view));
    assert.ok(visibleGroups(['applyai','workspace','terminal']).find(group=>group.label==='Plan').views.includes(view));
  }
  assert.deepEqual(visibleGroups(['terminal']).map(group=>[group.label,group.views]),[['Plan',['today','herdr','tern','agent-skills']],['Settings',['settings','ai']]]);
  assert.ok(visibleRelations(['terminal']).some(([a,b])=>a==='herdr'&&b==='tern'));
  assert.ok(visibleRelations(['terminal']).some(([a,b])=>a==='tern'&&b==='agent-skills'));
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
  assert.ok(titles(['workspace']).includes('Flashcards'));
  assert.ok(!titles(['workspace']).includes('Job decisions'));
  assert.ok(titles(['applyai']).includes('Job decisions'));
  assert.ok(!titles(['applyai']).includes('Study'));
  const navigate=(enabled)=>shortcutsFor(enabled).find(group=>group.title==='Navigate').shortcuts;
  assert.ok(navigate([]).some(([keys])=>keys==='Ctrl + V'));
  assert.ok(!navigate([]).some(([keys])=>keys.includes('Ctrl + X')||keys.includes('Ctrl + C')));
  assert.ok(navigate(['applyai']).some(([keys])=>keys.includes('Ctrl + C')));
});

test('a #notes/<number> link names one note page and nothing else',()=>{
  assert.equal(notePageLink('notes/12'),12);
  assert.equal(notePageLink('notes'),null);
  for(const hash of ['notes/0','notes/-1','notes/1x','notes/12/','study/3','notes/12345678901234567']) assert.equal(notePageLink(hash),null,hash);
});
