// Q4: panel fullscreen never changes native fullscreen or steals typing.
// node --experimental-strip-types --test tests/panelFullscreen.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { FULLSCREEN_ATTRIBUTE, PANEL_ATTRIBUTE, TERMINAL_ATTRIBUTE, createPanelFullscreen, focusContext } from '../src/lib/panelFullscreen.ts';

function element(tagName, { parent = null, type, editable = false, attributes = {} } = {}) {
  const attrs = new Map(Object.entries(attributes));
  return {
    tagName: tagName.toUpperCase(), type, parentElement: parent, isConnected: true, scrollTop: 0,
    get isContentEditable() { return editable || Boolean(parent?.isContentEditable); },
    hasAttribute: name => attrs.has(name),
    setAttribute(name, value) { attrs.set(name, value); },
    removeAttribute(name) { attrs.delete(name); },
    contains(other) { for (let node = other; node; node = node.parentElement) if (node === this) return true; return false; },
    focus() { this.focused = true; },
    getBoundingClientRect: () => ({ top: 0 }),
  };
}
function setup() {
  const root = element('html');
  const view = element('section', { attributes: { [PANEL_ATTRIBUTE]: 'view' } });
  const graph = element('div', { parent: view, attributes: { [PANEL_ATTRIBUTE]: 'graph' } });
  const terminal = element('section', { attributes: { [PANEL_ATTRIBUTE]: 'terminal', [TERMINAL_ATTRIBUTE]: '' } });
  const terminalInput = element('textarea', { parent: terminal });
  let scroll = [0, 240];
  const state = { blocked: false };
  const panels = createPanelFullscreen({
    root, fallbackPanel: () => view, blocked: () => state.blocked,
    scrollPosition: () => scroll,
    scrollTo: (x, y) => { scroll = [x, y]; }, activeElement: () => null,
  });
  function press(key, target = view, extra = {}) {
    const event = { key, target, ctrlKey: false, shiftKey: false, altKey: false, metaKey: false,
      repeat: false, isComposing: false, defaultPrevented: false, stopped: false, ...extra,
      preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; } };
    panels.keydownCapture(event);
    return event;
  }
  return { root, view, graph, terminal, terminalInput, panels, press, state, scroll: () => scroll };
}
const chord = { ctrlKey: true, shiftKey: true };

test('F takes the nearest focused panel; F or Esc restores the layout and scroll', () => {
  const { root, view, graph, panels, press, scroll } = setup();
  const child = element('button', { parent: graph });
  const enter = press('f', child);
  assert.equal(panels.active(), graph);
  assert.ok(graph.hasAttribute(FULLSCREEN_ATTRIBUTE));
  assert.equal(view.hasAttribute(FULLSCREEN_ATTRIBUTE), false);
  assert.equal(enter.defaultPrevented, true, 'F must not also run a page action');
  assert.equal(enter.stopped, true);
  press('f', child);
  assert.equal(panels.active(), null);
  assert.equal(root.hasAttribute(FULLSCREEN_ATTRIBUTE), false);
  press('F', element('button'));
  assert.equal(panels.active(), view, 'outside panels, use the active view');
  press('Escape');
  assert.equal(panels.active(), null);
  assert.deepEqual(scroll(), [0, 240]);
});

test('typing contexts retain F and Ctrl+Shift+F both before and during panel fullscreen', () => {
  const { view, panels, press } = setup();
  const editor = element('div', { parent: view, editable: true });
  const targets = [
    ...['text', 'search', 'email', 'number', 'password', 'url', 'tel', 'date'].map(type => element('input', { parent: view, type })),
    element('textarea', { parent: view }), element('select', { parent: view }),
    editor, element('span', { parent: editor }),
  ];
  for (const fullscreen of [false, true]) {
    if (fullscreen) press('f');
    for (const target of targets) {
      assert.equal(focusContext(target), 'text');
      for (const extra of [{}, chord]) {
        const event = press('f', target, extra);
        assert.equal(event.defaultPrevented, false, target.tagName);
        assert.equal(panels.active(), fullscreen ? view : null, target.tagName);
      }
    }
  }
});

test('terminal marker protects its inner textarea; Ctrl+Shift+F toggles and Esc exits', () => {
  const { terminal, terminalInput, panels, press } = setup();
  assert.equal(focusContext(terminalInput), 'terminal');
  assert.equal(press('f', terminalInput).defaultPrevented, false);
  assert.equal(panels.active(), null);
  assert.equal(press('F', terminalInput, chord).stopped, true);
  assert.equal(panels.active(), terminal);
  assert.equal(press('f', terminalInput).defaultPrevented, false);
  assert.equal(panels.active(), terminal, 'bare f still reaches the shell');
  press('F', terminalInput, chord);
  assert.equal(panels.active(), null);
  press('F', terminalInput, chord);
  press('Escape', terminalInput);
  assert.equal(panels.active(), null);
});

test('composition, repeat, unrelated modifiers, consumed events and dialogs retain their keys', () => {
  const { panels, press, state } = setup();
  for (const flag of ['isComposing', 'repeat', 'altKey', 'metaKey', 'ctrlKey', 'shiftKey', 'defaultPrevented']) {
    assert.equal(press('f', undefined, { [flag]: true }).stopped, false, flag);
    assert.equal(panels.active(), null, flag);
  }
  state.blocked = true;
  press('f');
  assert.equal(panels.active(), null);
  state.blocked = false;
  press('f');
  state.blocked = true;
  press('Escape');
  assert.notEqual(panels.active(), null, 'a dialog owns Escape until it closes');
});
