// Run with Node >=22.6: node --experimental-strip-types --test tests/mapInk.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {inkOutline,lassoed,nearestBox,penAction,simplify,strokeHit} from '../src/lib/mapInk.ts';

test('pen ends and buttons choose the eraser, lasso or ink; palms are ignored while the pen is near',()=>{
  const pen=(button,buttons)=>({pointerType:'pen',button,buttons});
  assert.equal(penAction(pen(5,32),false,false),'erase');
  assert.equal(penAction(pen(0,32),true,false),'erase');
  assert.equal(penAction(pen(2,2),true,false),'lasso');
  assert.equal(penAction(pen(0,1),true,false),'ink');
  assert.equal(penAction(pen(0,1),false,false),'point');
  assert.equal(penAction({pointerType:'mouse',button:0,buttons:1},true,false),'ink');
  assert.equal(penAction({pointerType:'mouse',button:0,buttons:1},false,false),'point');
  // A finger pans or selects in ink mode, but is a palm while the pen is near.
  assert.equal(penAction({pointerType:'touch',button:0,buttons:1},true,false),'point');
  assert.equal(penAction({pointerType:'touch',button:0,buttons:1},false,true),'ignore');
  assert.equal(penAction({pointerType:'mouse',button:0,buttons:1},false,true),'point');
});

test('simplifying keeps the corners and ends of a stroke with their pressure',()=>{
  const line=Array.from({length:21},(_,i)=>[i*5,i%2?0.2:0,0.5]);
  assert.deepEqual(simplify(line,1),[[0,0,0.5],[100,0,0.5]]);
  const corner=[[0,0,0.1],[10,0,0.3],[20,0,0.5],[20,10,0.7],[20,20,0.9]];
  assert.deepEqual(simplify(corner,1),[[0,0,0.1],[20,0,0.5],[20,20,0.9]]);
  assert.deepEqual(simplify([[1,2,0.4]],1),[[1,2,0.4]]);
});

test('the outline is wider where the pen pressed harder',()=>{
  const width=(d)=>{
    const ys=[...d.matchAll(/(-?[\d.]+),(-?[\d.]+)/g)].map(m=>Number(m[2]));
    return Math.max(...ys)-Math.min(...ys);
  };
  const light=inkOutline([[0,0,0.1],[50,0,0.1]],4), heavy=inkOutline([[0,0,1],[50,0,1]],4);
  assert.match(light,/^M/);
  assert.ok(width(heavy)>width(light)*2,`${heavy} is not wider than ${light}`);
  assert.match(inkOutline([[3,4,0.5]],4),/^M.*A/,'a dot is drawn as a circle');
});

test('ink attaches to the nearest pill, lassos pick pills by centre, the eraser hits strokes',()=>{
  const boxes=[{id:'a',x:0,y:0,w:100,h:34},{id:'b',x:0,y:100,w:100,h:34}];
  assert.equal(nearestBox(boxes,[[40,20,1],[60,30,1]]),'a');
  assert.equal(nearestBox(boxes,[[150,90,1],[160,95,1]]),'b');
  assert.equal(nearestBox([],[[0,0,1]]),undefined);
  const loop=[[-10,-10],[120,-10],[120,60],[-10,60]];
  assert.deepEqual(lassoed(boxes,loop),['a']);
  assert.equal(strokeHit([[0,0,1],[100,0,1]],[50,3],4),true);
  assert.equal(strokeHit([[0,0,1],[100,0,1]],[50,30],4),false);
  assert.equal(strokeHit([[0,0,1]],[2,2],4),true);
});
