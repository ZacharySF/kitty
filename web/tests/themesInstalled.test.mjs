// The Themes module: which themes Appearance offers, and how picks are kept.
import assert from 'node:assert/strict';
import test from 'node:test';
import { DEFAULT_THEME, THEMES } from '../src/lib/themes/catalog.ts';
import { EXTRA_THEMES, STARTER_THEMES, shownThemes, toggleTheme } from '../src/lib/themes/installed.ts';

const ids = (themes) => themes.map((theme) => theme.id);

test('with the module off only the default theme is offered, plus the one in use', () => {
  assert.deepEqual(ids(shownThemes([], DEFAULT_THEME)), [DEFAULT_THEME]);
  assert.deepEqual(new Set(ids(shownThemes([], 'nord'))), new Set([DEFAULT_THEME, 'nord']));
});

test('picked themes are offered in the catalogue order, and an unknown pick is not', () => {
  const order = ids(THEMES).filter((id) => [DEFAULT_THEME, 'nord', 'blossom'].includes(id));
  assert.deepEqual(ids(shownThemes(['blossom', 'from-a-newer-rika', 'nord'], DEFAULT_THEME)), order);
});

test('a pick toggles, keeps the catalogue order and keeps names this build does not know', () => {
  const picked = toggleTheme(['nord', 'from-a-newer-rika'], 'dracula');
  assert.deepEqual(picked, [...ids(THEMES).filter((id) => ['dracula', 'nord'].includes(id)), 'from-a-newer-rika']);
  assert.deepEqual(toggleTheme(picked, 'nord'), ['dracula', 'from-a-newer-rika']);
});

test('the default theme is not one of the extras, and the starter set is', () => {
  assert.ok(!ids(EXTRA_THEMES).includes(DEFAULT_THEME));
  assert.deepEqual(STARTER_THEMES.filter((id) => !ids(EXTRA_THEMES).includes(id)), []);
});
