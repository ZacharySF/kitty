// Add-on (external) module views next to the built-in ones. Synthetic module ids only.
// Run with Node >=22.6: node --test tests/externalViews.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  externalViewId, groupOf, isExternalViewId, isView, navItem, shortcutsFor, viewEnabled, viewFromHash,
  visibleGroups, visibleNavigation,
} from '../src/lib/workspace.ts';
import {atlasGraph, atlasSections, hideableSection} from '../src/lib/atlasNodes.ts';
import {modulePath, parseRepository} from '../src/lib/externalModules.ts';

const view=(overrides={})=>({module:'demo-mod',version:'0.1.0',id:'demo',label:'Demo',icon:'study',group:'study',first:false,ui:'element',
  script:'/modules/demo-mod/0.1.0/ui/view.js',tag:'demo-mod-view',...overrides});
const ALL=['applyai','workspace','terminal'];
const groupViews=(enabled,external,label)=>visibleGroups(enabled,external).find(group=>group.label===label)?.views;

test('external view ids live under m/ and can never equal a built-in view id',()=>{
  assert.equal(externalViewId(view()),'m/demo-mod/demo');
  // A module whose view is named like a built-in still gets its own id.
  const clash=view({module:'notes',id:'notes'});
  assert.equal(externalViewId(clash),'m/notes/notes');
  assert.equal(isView('m/notes/notes'),false);
  assert.equal(isExternalViewId('m/notes/notes'),true);
  for(const value of ['notes','m','m/','m/demo-mod','m/Demo/x','m/demo-mod/demo/extra','m//x']) assert.equal(isExternalViewId(value),false,value);
});

test('with no add-on views the built-in navigation is exactly as before',()=>{
  for(const enabled of [[],['workspace'],['applyai'],['terminal'],ALL]){
    assert.deepEqual(visibleGroups(enabled,[]),visibleGroups(enabled));
    assert.deepEqual(visibleNavigation(enabled,[]).map(item=>item.id),visibleNavigation(enabled).map(item=>item.id));
    assert.deepEqual(shortcutsFor(enabled,[]),shortcutsFor(enabled));
  }
});

test('a view placed first leads its group; otherwise it is appended at the end',()=>{
  const first=view({module:'first-mod',id:'one',first:true});
  const last=view({module:'last-mod',id:'two'});
  const study=groupViews(ALL,[last,first],'Study');
  const builtIn=groupViews(ALL,[],'Study');
  assert.deepEqual(study,['m/first-mod/one',...builtIn,'m/last-mod/two']);
});

test('each group key maps to its header group and built-ins keep their order',()=>{
  const external=[
    view({module:'plan-mod',group:'plan',first:true}),
    view({module:'jobs-mod',group:'jobs'}),
    view({module:'set-mod',group:'settings'}),
  ];
  // The atlas home stays the Plan group's entry point: "first" means its first section.
  assert.deepEqual(groupViews(ALL,external,'Plan').slice(0,2),['today','m/plan-mod/demo']);
  assert.equal(groupViews(ALL,external,'Job search').at(-1),'m/jobs-mod/demo');
  assert.equal(groupViews(ALL,external,'Settings').at(-1),'m/set-mod/demo');
  assert.deepEqual(groupViews(ALL,external,'Study'),groupViews(ALL,[],'Study'));
  assert.equal(groupOf('m/jobs-mod/demo',external)?.label,'Job search');
  assert.equal(groupOf('notes',external)?.label,'Study');
  assert.equal(groupOf('m/unknown/x',external),undefined);
});

test('an add-on view opens its group even when every built-in module is off',()=>{
  const external=[view()];
  assert.deepEqual(visibleGroups([],external).map(group=>group.label),['Plan','Study','Settings']);
  assert.deepEqual(groupViews([],external,'Study'),['m/demo-mod/demo']);
  assert.equal(viewEnabled('m/demo-mod/demo',[],external),true);
  assert.equal(viewEnabled('m/demo-mod/demo',ALL,[]),false);
  assert.equal(viewEnabled('study',[],external),false);
  const navigate=shortcutsFor([],external).find(group=>group.title==='Navigate').shortcuts;
  assert.ok(navigate.some(([keys])=>keys==='Ctrl + Alt / Ctrl + X / Ctrl + V'));
});

test('navigation items carry the label and fall back to a generic icon for unknown names',()=>{
  const external=[view({icon:'math'}),view({module:'odd-mod',icon:'not-an-icon'})];
  const items=visibleNavigation([],external);
  assert.deepEqual(items.map(item=>item.id),['today','goals','ai','settings','m/demo-mod/demo','m/odd-mod/demo']);
  assert.equal(navItem('m/demo-mod/demo',external)?.icon,'math');
  assert.equal(navItem('m/odd-mod/demo',external)?.icon,'atlas');
  assert.equal(navItem('m/odd-mod/demo',external)?.label,'Demo');
  assert.equal(navItem('notes')?.label,'Notes & canvas');
  assert.equal(navItem('m/odd-mod/demo',[]),undefined);
});

test('a hash names a built-in view or an add-on view; anything else names nothing',()=>{
  assert.equal(viewFromHash('settings/modules'),'settings');
  assert.equal(viewFromHash('notes'),'notes');
  assert.equal(viewFromHash('m/demo-mod/demo'),'m/demo-mod/demo');
  assert.equal(viewFromHash('m/demo-mod/demo/page/2'),'m/demo-mod/demo');
  for(const hash of ['','bogus','m','m/demo-mod','m/Demo/x']) assert.equal(viewFromHash(hash),null,hash);
});

test('the atlas lists add-on sections in their group, never hidden, including under Job search',()=>{
  const external=[view({module:'jobs-mod',group:'jobs'}),view()];
  const lookup=id=>navItem(id,external);
  const sections=atlasSections(visibleGroups(ALL,external),lookup);
  const jobs=sections.find(group=>group.label==='Job search').items.map(item=>item.id);
  assert.deepEqual(jobs,['jobs','m/jobs-mod/demo']);
  assert.ok(sections.find(group=>group.label==='Study').items.some(item=>item.id==='m/demo-mod/demo'));
  assert.equal(hideableSection('m/demo-mod/demo'),false);
  assert.equal(hideableSection('notes'),true);
  // Even a stray stored id cannot hide an add-on node.
  const graph=atlasGraph(sections,new Set(['m/demo-mod/demo']),[],new Map());
  const node=graph.nodes.find(item=>item.id==='view:m/demo-mod/demo');
  assert.equal(node?.href,'#m/demo-mod/demo');
  assert.ok(graph.links.some(link=>link.source==='view:study'&&link.target==='view:m/demo-mod/demo'));
});

test('a GitHub repository is owner/repo; a pasted GitHub link is accepted too',()=>{
  assert.equal(parseRepository('octo-org/rika-demo'),'octo-org/rika-demo');
  assert.equal(parseRepository('  octo/demo.js  '),'octo/demo.js');
  assert.equal(parseRepository('https://github.com/octo/demo'),'octo/demo');
  assert.equal(parseRepository('https://github.com/octo/demo.git'),'octo/demo');
  assert.equal(parseRepository('github.com/octo/demo/'),'octo/demo');
  for(const text of ['','octo','octo/','/demo','octo/demo/extra','-octo/demo','octo/..','oc to/demo','https://gitlab.com/octo/demo'])
    assert.equal(parseRepository(text),null,text);
});

test('a module view talks only to its own API path',()=>{
  assert.equal(modulePath('demo-mod',''),'/modules/demo-mod');
  assert.equal(modulePath('demo-mod','items'),'/modules/demo-mod/items');
  assert.equal(modulePath('demo-mod','/items/2'),'/modules/demo-mod/items/2');
});
