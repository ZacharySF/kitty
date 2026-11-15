// Page edit mode: reading and changing the build's ui.pages.widgets list.
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { MAX_WIDGETS, WIDGET_PATTERN, WIDGET_WIDTH, addWidget, formatWidget, moveWidget, parseWidget, removeWidget, resizeWidget, widgetsFor, withoutViewWidgets } from '../src/lib/pageEdit/widgets.ts';

test('an entry reads back as it was written', () => {
  const item = { view: 'jobs', widget: 'clock', x: 640, y: 96, w: 360 };
  assert.equal(formatWidget(item), 'jobs|clock|640,96,360');
  assert.deepEqual(parseWidget('jobs|clock|640,96,360'), item);
  assert.deepEqual(parseWidget('agent-skills|note|0,0,1200'), { view: 'agent-skills', widget: 'note', x: 0, y: 0, w: 1200 });
  for (const bad of ['', 'jobs|clock', 'jobs|clock|1,2', 'Jobs|clock|0,0,360', 'jobs|Clock|0,0,360', 'jobs|clock|-8,0,360', 'jobs|clock|0,0,36', 'jobs|clock|0,0,12000', 'jobs|clock|0,0,360|more'])
    assert.equal(parseWidget(bad), null, bad);
  // A width the schema lets through but no pane takes is brought into range.
  assert.equal(parseWidget('jobs|clock|0,0,100').w, WIDGET_WIDTH.min);
  assert.equal(parseWidget('jobs|clock|0,0,9999').w, WIDGET_WIDTH.max);
});

test('what is written always fits the build schema, on the 8px grid', () => {
  const schema = JSON.parse(readFileSync(new URL('../../src/kitty/core/builds/kitty-build.schema.json', import.meta.url), 'utf8'));
  const widgets = schema.properties.ui.properties.pages.properties.widgets;
  assert.equal(widgets.items.pattern, WIDGET_PATTERN.source);
  assert.equal(widgets.maxItems, MAX_WIDGETS);
  assert.equal(formatWidget({ view: 'jobs', widget: 'clock', x: 13.4, y: -40, w: 100 }), 'jobs|clock|16,0,240');
  assert.equal(formatWidget({ view: 'jobs', widget: 'clock', x: 1e9, y: 3, w: 1e9 }), 'jobs|clock|99992,0,1200');
  for (const entry of [formatWidget({ view: 'jobs', widget: 'clock', x: 1e9, y: -1e9, w: 1e9 }), formatWidget({ view: 'calendar', widget: 'note', x: 0.2, y: 0.2, w: 0 })])
    assert.match(entry, WIDGET_PATTERN);
});

test('a page holds one of each widget: adding it again moves it, and other pages are left alone', () => {
  let entries = addWidget(['calendar|clock|8,8,360', 'kept-from-a-newer-rika'], 'jobs', 'clock', 643, 99);
  assert.deepEqual(entries, ['calendar|clock|8,8,360', 'kept-from-a-newer-rika', 'jobs|clock|640,96,360']);
  entries = addWidget(entries, 'jobs', 'note', 0, 0, 480);
  entries = addWidget(entries, 'jobs', 'clock', 16, 16);
  assert.deepEqual(entries, ['calendar|clock|8,8,360', 'kept-from-a-newer-rika', 'jobs|note|0,0,480', 'jobs|clock|16,16,360']);
  assert.deepEqual(widgetsFor(entries, 'jobs').map((item) => item.widget), ['note', 'clock']);
  assert.deepEqual(widgetsFor(entries, 'notes'), []);
  // A name the schema would refuse is never written.
  assert.deepEqual(addWidget([], 'jobs', 'Clock', 0, 0), []);
});

test('moving and resizing change one widget in place; one that is not there changes nothing', () => {
  const entries = ['jobs|note|0,0,480', 'jobs|clock|16,16,360', 'calendar|clock|8,8,360'];
  assert.deepEqual(moveWidget(entries, 'jobs', 'clock', 203, -5), ['jobs|note|0,0,480', 'jobs|clock|200,0,360', 'calendar|clock|8,8,360']);
  assert.deepEqual(resizeWidget(entries, 'jobs', 'clock', 517), ['jobs|note|0,0,480', 'jobs|clock|16,16,520', 'calendar|clock|8,8,360']);
  assert.deepEqual(resizeWidget(entries, 'jobs', 'clock', 90), ['jobs|note|0,0,480', 'jobs|clock|16,16,240', 'calendar|clock|8,8,360']);
  assert.deepEqual(resizeWidget(entries, 'jobs', 'clock', 5000)[1], 'jobs|clock|16,16,1200');
  assert.deepEqual(moveWidget(entries, 'notes', 'clock', 0, 0), entries);
  // "clock" is not a prefix match for another widget.
  assert.deepEqual(moveWidget(['jobs|clock-big|8,8,360'], 'jobs', 'clock', 0, 0), ['jobs|clock-big|8,8,360']);
});

test('removing takes one widget off one page, or every widget off it; the list never outgrows the build', () => {
  const entries = ['jobs|note|0,0,480', 'jobs|clock|16,16,360', 'calendar|clock|8,8,360', 'jobs-archive|clock|8,8,360'];
  assert.deepEqual(removeWidget(entries, 'jobs', 'clock'), ['jobs|note|0,0,480', 'calendar|clock|8,8,360', 'jobs-archive|clock|8,8,360']);
  assert.deepEqual(withoutViewWidgets(entries, 'jobs'), ['calendar|clock|8,8,360', 'jobs-archive|clock|8,8,360']);
  let many = [];
  for (let index = 0; index < MAX_WIDGETS + 3; index += 1) many = addWidget(many, `view-${index}`, 'clock', 0, 0);
  assert.equal(many.length, MAX_WIDGETS);
  assert.equal(many.at(-1), `view-${MAX_WIDGETS + 2}|clock|0,0,360`);
});
