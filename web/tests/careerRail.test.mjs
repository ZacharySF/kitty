// The Career rail's places and counts (Q12 C, Q14 A). Synthetic counts only.
// Run with Node >=22.6: node --experimental-strip-types --test tests/careerRail.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {RAIL_GROUPS,RAIL_MORE,RAIL_VIEWS,countLabel,currentPlace,placeCount,stripDot} from '../src/lib/career/rail.ts';
import {navigationGroups,navigation} from '../src/lib/workspace.ts';

const COUNTS={new:12,queued:3,saved:0,active:9,interviews:1,reminders:0,emails:2};
const place=(key)=>[...RAIL_GROUPS.flatMap(group=>group.places),...RAIL_MORE].find(item=>item.key===key);

test('the rail lists Overview, then Find, Track and You, with the rest as quieter links',()=>{
  assert.deepEqual(RAIL_GROUPS.map(group=>group.label),['','Find','Track','You']);
  assert.deepEqual(RAIL_GROUPS.flatMap(group=>group.places.map(item=>item.label)),
    ['Overview','New jobs','All jobs','Queue','Saved','Applied','Interviews','Reminders','Resumes','Profile']);
  assert.deepEqual(RAIL_MORE.map(item=>item.label),
    ['Email updates','Application list','Resume files','Job sources','Find companies','Rank jobs','Reports','Questions']);
});

test('every Career page is on the rail and the rail opens only Career pages',()=>{
  const career=navigationGroups.find(group=>group.label==='Career').views;
  assert.deepEqual([...RAIL_VIEWS].sort(),[...career].sort());
  assert.equal(career[0],'career','Career opens on its Overview');
  for(const view of RAIL_VIEWS) assert.equal(navigation.find(item=>item.id===view)?.module,'applyai',view);
});

test('one place is current per page: Track marks Applied, the library its own link',()=>{
  assert.equal(currentPlace('career'),'overview');
  assert.equal(currentPlace('pipeline'),'applied');
  assert.equal(currentPlace('workshop'),'resumes');
  assert.equal(currentPlace('resumes'),'library');
  assert.equal(currentPlace('mail'),'mail');
  assert.equal(currentPlace('calendar'),null);
  assert.equal(place('interviews').stage,'interview');
});

test('counts read from the summary; urgent ones stand out only above zero',()=>{
  assert.deepEqual(placeCount(place('new'),COUNTS),{value:12,urgent:true});
  assert.deepEqual(placeCount(place('applied'),COUNTS),{value:9,urgent:false});
  assert.deepEqual(placeCount(place('reminders'),COUNTS),{value:0,urgent:false});
  assert.equal(placeCount(place('overview'),COUNTS),null);
  assert.equal(placeCount(place('new'),null),null);
});

test('the collapsed strip shows a numbered dot for urgent counts, a plain dot otherwise, nothing at zero',()=>{
  assert.equal(stripDot(place('new'),COUNTS),'number');
  assert.equal(stripDot(place('interviews'),COUNTS),'number');
  assert.equal(stripDot(place('queue'),COUNTS),'dot');
  assert.equal(stripDot(place('saved'),COUNTS),null);
  assert.equal(stripDot(place('reminders'),COUNTS),null);
  assert.equal(stripDot(place('profile'),COUNTS),null);
  assert.equal(countLabel(1284),'1,284');
  assert.equal(countLabel(1284,true),'999+');
  assert.equal(countLabel(12,true),'12');
});
