// Page edit mode: a block's path from its view panel, and finding the block again.
import assert from 'node:assert/strict';
import test from 'node:test';
import { isStableClass, pathOf, resolvePath, stepOf } from '../src/lib/pageEdit/paths.ts';

/** A stand-in element: what a path reads, and `:scope > a > b` chains of the steps a path writes. */
function element(tag, { label = null, classes = [] } = {}, children = []) {
  const node = {
    tagName: tag.toUpperCase(),
    parentElement: null,
    children: [],
    classList: classes,
    getAttribute: (name) => (name === 'aria-label' ? label : null),
    append(child, index = node.children.length) {
      child.parentElement = node;
      node.children.splice(index, 0, child);
      return child;
    },
    querySelector(selector) {
      assert.ok(selector.startsWith(':scope > '), selector);
      let found = [node];
      for (const step of selector.slice(9).split(' > '))
        found = found.flatMap((parent) => parent.children.filter((child) => matches(child, step)));
      return found[0] ?? null;
    },
  };
  for (const child of children) node.append(child);
  return node;
}
function matches(node, step) {
  const parsed = /^([a-z0-9]+)(?:\[aria-label="([^"]*)"\])?(?:\.([A-Za-z0-9_-]+))?(?::nth-of-type\((\d+)\))?$/.exec(step);
  assert.ok(parsed, `not a step a path writes: ${step}`);
  const [, tag, label, named, nth] = parsed;
  if (node.tagName.toLowerCase() !== tag) return false;
  if (label !== undefined && node.getAttribute('aria-label') !== label) return false;
  if (named !== undefined && !node.classList.includes(named)) return false;
  return nth === undefined || node.parentElement.children.filter((child) => child.tagName === node.tagName).indexOf(node) + 1 === Number(nth);
}

function page() {
  const results = element('section', { label: 'Job results' });
  const filters = element('form', { classes: ['filters', 'svelte-1x2y3z'] });
  const first = element('article', { classes: ['svelte-abc', 'selected', 'job-row'] });
  const second = element('article', { classes: ['job-row'] });
  const plain = element('div', {}, [element('header'), results]);
  results.append(first);
  results.append(second);
  const root = element('div', {}, [element('div', { classes: ['opportunities'] }, [filters, plain])]);
  return { root, results, filters, first, second, plain };
}

test('a path leads back to the block it was taken from', () => {
  const { root, results, filters, first, second } = page();
  for (const block of [results, filters, first, second])
    assert.equal(resolvePath(root, pathOf(root, block)), block);
  assert.equal(pathOf(root, results), 'div.opportunities > div > section[aria-label="Job results"]');
  // Two rows read alike, so each says which it is.
  assert.equal(stepOf(first), 'article.job-row:nth-of-type(1)');
  assert.equal(stepOf(second), 'article.job-row:nth-of-type(2)');
  // A node outside the panel has no path.
  assert.equal(pathOf(root, element('section')), null);
});

test('a path survives a sibling arriving after its block', () => {
  const { root, results, filters, plain } = page();
  const before = [pathOf(root, results), pathOf(root, filters)];
  plain.append(element('section', { label: 'Reminders' }));
  plain.parentElement.append(element('form', { classes: ['filters'] }));
  results.append(element('article', { classes: ['job-row'] }));
  assert.equal(resolvePath(root, before[0]), results);
  assert.equal(resolvePath(root, before[1]), filters);
});

test('Svelte hashes and state classes never enter a path', () => {
  const { root, filters, first } = page();
  assert.equal(stepOf(filters), 'form.filters');
  assert.match(stepOf(first), /^article\.job-row/);
  const path = pathOf(root, first);
  first.classList.splice(first.classList.indexOf('selected'), 1);
  first.classList.unshift('svelte-zzz', 'open');
  assert.equal(pathOf(root, first), path);
  for (const name of ['svelte-1abc', 'active', 'selected', 'is-open', 'has-error', 'editing', '2col'])
    assert.equal(isStableClass(name), false, name);
  assert.equal(isStableClass('job-row'), true);
  // Only state classes: the tag alone.
  assert.equal(stepOf(element('nav', { classes: ['svelte-q', 'open'] })), 'nav');
});

test('a label that cannot sit in a selector or the build file gives way to the class', () => {
  assert.equal(stepOf(element('section', { label: 'Say "hi"', classes: ['greeting'] })), 'section.greeting');
  assert.equal(stepOf(element('section', { label: 'Café', classes: ['menu'] })), 'section.menu');
  assert.equal(stepOf(element('section', { label: 'x'.repeat(61) })), 'section');
});

test('a path too long for the build is not kept, and a broken one finds nothing', () => {
  let node = element('div');
  const root = node;
  for (let depth = 0; depth < 40; depth += 1) node = node.append(element('section', { label: 'A fairly long label here' }));
  assert.equal(pathOf(root, node), null);
  assert.equal(resolvePath({ querySelector() { throw new SyntaxError('bad selector'); } }, 'section['), null);
});
