// Run with Node >=22.6: node --experimental-strip-types --test tests/notesCanvas.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {CANVAS_PAPER,clone,displayColor,drawingInput,emptyDocument,endpoints,expandGroups,exportSVG,finishGesture,finishStroke,fit,hit,hitTop,lassoSelection,removeElements,resizeElement,world,zoomAt} from '../src/lib/notesCanvas.ts';

const stroke=(id='stroke')=>({id,kind:'pen',layer:'default',x:-100,y:20,w:100,h:50,color:'#344054',fill:'none',width:8,points:[[0,0,.1],[100,50,.9]]});
const rect=(id,x,y)=>({id,kind:'rect',layer:'default',x,y,w:100,h:100,color:'#344054',fill:'none',width:2});
const document=(...elements)=>({...emptyDocument(),elements});

test('palm contacts never become drawing or pinch input during or immediately after pen strokes',()=>{
  const touch={pointerType:'touch',button:0,buttons:1};
  assert.equal(drawingInput(touch,'pen',false,true,false),'ignore');
  assert.equal(drawingInput(touch,'pen',false,false,true),'ignore');
  assert.equal(drawingInput(touch,'pen',true,false,false),'pan');
  assert.equal(drawingInput(touch,'pen',false,false,false),'pen');
  const pen={pointerType:'pen',button:0,buttons:1};
  assert.equal(drawingInput(pen,'highlighter',true,false,false),'highlighter');
  assert.equal(drawingInput({...pen,buttons:3},'pen',true,false,false),'eraser');
  assert.equal(drawingInput({...pen,button:5,buttons:32},'pen',true,false,false),'eraser');
  assert.equal(drawingInput(pen,'pen',true,false,true),'pen');
});

test('pointercancel discards provisional ink, erase, and moves without mutating the saved snapshot',()=>{
  const before=document(stroke());
  for(const changed of [document(),document(stroke(),stroke('partial')),document({...stroke(),x:50})]){
    const restored=finishGesture(before,changed,true);
    assert.deepEqual(restored,before);
    assert.notEqual(restored,before);
    restored.elements[0].points[0][0]=999;
    assert.equal(before.elements[0].points[0][0],0);
  }
  const completed=document(stroke(),stroke('complete'));
  assert.equal(finishGesture(before,completed,false),completed);
});

test('zoom and pan do not change document contents or erase world coordinates',()=>{
  const doc=document(stroke());
  const original=clone(doc);
  const view={x:350,y:-200,zoom:.5};
  const p=[-50,45,1];
  const screen=[p[0]*view.zoom+view.x,p[1]*view.zoom+view.y];
  assert.deepEqual(world(...screen,view),p);
  assert.equal(hit(doc.elements[0],world(...screen,view),4/view.zoom,doc.elements),true);
  const zoomed=zoomAt(view,...screen,4);
  assert.deepEqual(world(...screen,zoomed),p);
  assert.deepEqual(doc,original);
  assert.equal(hit(doc.elements[0],[200,300,1],1,doc.elements),false);
});

test('stroke finalization preserves pressure, reverse directions, and negative world positions',()=>{
  const input={...stroke(),x:20,y:30,points:[[0,0,.2],[-40,-70,.8],[-20,10,.6]]};
  const e=finishStroke(input);
  assert.equal(e.x,-20);assert.equal(e.y,-40);assert.equal(e.w,40);assert.equal(e.h,80);
  assert.deepEqual(e.points,[[40,70,.2],[0,0,.8],[20,80,.6]]);
  assert.equal(input.points[1][0],-40);
});

test('lasso and hit testing operate in world coordinates and respect locked/hidden layers',()=>{
  const doc=document(rect('selected',-100,-100),rect('outside',400,400));
  doc.layers.push({id:'locked',name:'Reference',visible:true,locked:true});
  doc.elements.push({...rect('locked-item',-100,-100),layer:'locked'});
  const polygon=[[-150,-150,1],[100,-150,1],[100,100,1],[-150,100,1]];
  assert.deepEqual(lassoSelection(doc,polygon),['selected']);
  assert.equal(hitTop(doc,[-50,-50,1],3)?.id,'selected');
  doc.layers[0].visible=false;
  assert.deepEqual(lassoSelection(doc,polygon),[]);
  assert.equal(hitTop(doc,[-50,-50,1],3),undefined);
});

test('group selection, resizing, and connectors survive JSON reload',()=>{
  const a={...rect('a',10,20),group:'g'};
  const b={...rect('b',210,220),group:'g'};
  const connector={...rect('join',0,0),kind:'connector',from:'a',to:'b',points:[[0,0,1],[1,1,1]]};
  const doc=document(a,b,connector);
  assert.deepEqual(expandGroups(doc,['a']),['a','b']);
  const resized=resizeElement(a,{x:10,y:20,w:300,h:300},{x:10,y:20,w:600,h:150});
  assert.deepEqual([resized.x,resized.y,resized.w,resized.h],[10,20,200,50]);
  const reloaded=JSON.parse(JSON.stringify(doc));
  assert.deepEqual(endpoints(reloaded.elements[2],reloaded.elements),[[60,70,1],[260,270,1]]);
  reloaded.elements[0].x+=100;
  assert.deepEqual(endpoints(reloaded.elements[2],reloaded.elements)[0],[160,70,1]);
  assert.deepEqual(removeElements(doc,new Set(['a'])).elements.map(e=>e.id),['b']);
});

test('fit handles empty pages, point strokes, and widely spaced drawings within zoom limits',()=>{
  assert.deepEqual(fit([],1000,800),{x:0,y:0,zoom:1});
  const fitted=fit([rect('a',-5000,-4000),rect('b',6000,7000)],1000,800);
  assert.ok(fitted.zoom>=.05&&fitted.zoom<=3);
  const point=fit([{...stroke(),w:0,h:0}],1000,800);
  assert.ok(Number.isFinite(point.x)&&Number.isFinite(point.y));
  assert.equal(zoomAt({x:0,y:0,zoom:19},0,0,10).zoom,20);
  assert.equal(zoomAt({x:0,y:0,zoom:.1},0,0,.001).zoom,.05);
});

test('undo/redo snapshots are independent JSON documents and include layers and pressure',()=>{
  const before=document(stroke());
  const after=clone(before);after.layers[0].locked=true;after.elements.push(rect('next',100,100));
  const undo=clone(before),redo=clone(after);
  assert.deepEqual(undo,before);assert.deepEqual(redo,after);
  redo.elements[0].points[0][2]=.8;
  assert.equal(before.elements[0].points[0][2],.1);
  assert.equal(after.elements[0].points[0][2],.1);
  assert.equal(clone(new Proxy(before,{})).elements[0].id,'stroke');
});

test('SVG export contains real pressure geometry and escaped text, not executable content',async()=>{
  const text={...rect('text',40,70),kind:'text',text:'<script>alert("x")</script> & recall',fontSize:20,w:800};
  const doc=document(stroke(),text);
  const svg=await exportSVG(doc);
  assert.ok(svg.startsWith('<svg '));
  assert.ok(svg.includes('stroke-width="4"'));
  assert.ok(svg.includes('&lt;script&gt;'));
  assert.ok(svg.includes('&amp; recall'));
  assert.equal(svg.includes('<script>'),false);
  assert.ok(svg.includes('<tspan'));
});

test('dark ink from earlier notes stays legible on the dark paper while sticky text keeps its colour',async()=>{
  const luminance=hex=>{const [r,g,b]=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(c=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4);return .2126*r+.7152*g+.0722*b;};
  for(const dark of ['#344054','#000000','#1d3a8a']) assert.ok(luminance(displayColor(dark))>luminance(CANVAS_PAPER)+.3,dark);
  assert.equal(displayColor('#e7dfef'),'#e7dfef');
  assert.equal(displayColor('#fff2a8'),'#fff2a8');
  const sticky={...rect('sticky',0,0),kind:'sticky',text:'recall',fill:'#fff2a8'};
  const svg=await exportSVG(document(stroke(),sticky));
  assert.ok(svg.includes(`fill="${CANVAS_PAPER}"`));
  assert.equal(svg.includes('stroke="#344054" stroke-width="4"'),false);
  assert.ok(svg.includes('fill="#344054">'),'sticky text keeps its dark colour on the light sticky');
});
