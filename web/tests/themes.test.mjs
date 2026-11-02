// Run with Node >=22.6: node --experimental-strip-types --test tests/themes.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import {DEFAULT_THEME, THEMES, getTheme} from '../src/lib/themes/catalog.ts';
import {FOCUS_STYLES, MIN_SURFACE_ALPHA, MOTION_STYLES, SELECTION_STYLES, SURFACE_STYLES} from '../src/lib/themes/contract.ts';

/* WCAG relative luminance and contrast of two opaque hex colours. */
const channels = (hex) => [1, 3, 5].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255);
const linear = (value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
const luminance = (hex) => {
  const [r, g, b] = channels(hex).map(linear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};
/* OKLab, for perceptual distance between data colours. */
const oklab = (hex) => {
  const [r, g, b] = channels(hex).map(linear);
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
};
const distance = (a, b) => Math.hypot(...oklab(a).map((value, index) => value - oklab(b)[index]));
const chroma = (hex) => Math.hypot(oklab(hex)[1], oklab(hex)[2]);

test('a first launch with no saved theme opens on Kitty', () => {
  assert.equal(DEFAULT_THEME, 'kitty');
  assert.equal(getTheme(null).id, 'kitty');
  assert.equal(getTheme(undefined).id, 'kitty');
});

test('an unknown saved theme falls back to Kitty, a known one is kept', () => {
  assert.equal(getTheme('no-such-theme').id, 'kitty');
  assert.equal(getTheme('harness').id, 'harness');
  assert.equal(getTheme('nord').id, 'nord');
});

test('every theme owns a surface, focus, selection and motion style, not just colours', () => {
  assert.equal(new Set(THEMES.map((theme) => theme.id)).size, THEMES.length);
  for (const theme of THEMES) {
    assert.ok(SURFACE_STYLES.includes(theme.surface.style), `${theme.id} surface ${theme.surface.style}`);
    assert.ok(FOCUS_STYLES.includes(theme.focus), `${theme.id} focus ${theme.focus}`);
    assert.ok(SELECTION_STYLES.includes(theme.selection), `${theme.id} selection ${theme.selection}`);
    assert.ok(MOTION_STYLES.includes(theme.motion), `${theme.id} motion ${theme.motion}`);
    assert.ok(theme.surface.radius >= 0 && theme.surface.radius <= 20, `${theme.id} radius`);
    for (const role of ['panel', 'inset', 'raised']) {
      const alpha = theme.surface.alpha[role];
      assert.ok(alpha >= MIN_SURFACE_ALPHA && alpha <= 100, `${theme.id} ${role} alpha ${alpha} below the ${MIN_SURFACE_ALPHA}% floor`);
    }
    // Typography stays global (Q11-B was rejected): tokens carry no typeface.
    assert.ok(!JSON.stringify(theme).match(/font|family/i), `${theme.id} must not set a typeface`);
  }
  // The engine is more than a tint: the themes do not all share one treatment.
  for (const key of ['surface', 'focus', 'selection', 'motion']) {
    const styles = new Set(THEMES.map((theme) => (key === 'surface' ? theme.surface.style : theme[key])));
    assert.ok(styles.size >= 3, `only ${[...styles]} ${key} styles across the themes`);
  }
});

test('Liquid Glass is offered as a theme with see-through glass surfaces', () => {
  const theme = getTheme('liquid-glass');
  assert.equal(theme.label, 'Liquid Glass');
  assert.equal(theme.surface.style, 'glass');
  // Glass needs much more of the backdrop to show through than the paper and flat themes.
  assert.ok(theme.surface.alpha.panel <= 35, `panel alpha ${theme.surface.alpha.panel}`);
});

test('Scythe draws frames, not glass', () => {
  assert.equal(getTheme('scythe').surface.style, 'frame');
  assert.equal(getTheme('scythe').focus, 'bracket');
});

test('Kitty is frosted pink glass with petal focus and calm motion', () => {
  const kitty = getTheme('kitty');
  assert.equal(kitty.surface.style, 'glass');
  assert.equal(kitty.focus, 'petal');
  assert.equal(kitty.motion, 'calm');
});

test('Kitty has a real palette: lifted panels, an accent apart from structure, six distinct data colours', () => {
  const {roles, graph} = getTheme('kitty');
  // Today: panel on void 1.08:1, accent vs structure 1.11:1, four of six graph colours pink.
  assert.ok(contrast(roles.panel, roles.void) >= 1.2, `panel/void ${contrast(roles.panel, roles.void).toFixed(2)}`);
  assert.ok(contrast(roles.signal, roles.structure) >= 1.25, `accent/structure ${contrast(roles.signal, roles.structure).toFixed(2)}`);
  assert.ok(chroma(roles.signal) - chroma(roles.structure) >= 0.05, 'the accent is a saturated sakura, structure stays dusty');
  for (const [a, b] of [['signal', 'go'], ['signal', 'caution'], ['signal', 'danger'], ['go', 'caution'], ['caution', 'danger'], ['go', 'reference']])
    assert.ok(distance(roles[a], roles[b]) >= 0.08, `${a} and ${b} read alike (${distance(roles[a], roles[b]).toFixed(3)})`);
  const pinks = graph.filter((color) => {
    const [, a, b] = oklab(color);
    const hue = (Math.atan2(b, a) * 180) / Math.PI;
    return hue > -40 && hue < 25;
  });
  assert.ok(pinks.length <= 1, `${pinks.length} pink data colours`);
  for (let i = 0; i < graph.length; i += 1)
    for (let j = i + 1; j < graph.length; j += 1)
      assert.ok(distance(graph[i], graph[j]) >= 0.07, `data colours ${graph[i]} and ${graph[j]} are too close (${distance(graph[i], graph[j]).toFixed(3)})`);
  for (const color of graph) assert.ok(chroma(color) >= 0.06, `${color} is washed out`);
  // Body and secondary text pass AA on the solid panel colour.
  for (const role of ['ink', 'mute', 'dim']) assert.ok(contrast(roles[role], roles.panel) >= 4.5, role);
});

/* OKLab hue in degrees, for "this colour reads as green / pink / blue" checks. */
const hue = (hex) => {
  const [, a, b] = oklab(hex);
  return (Math.atan2(b, a) * 180) / Math.PI;
};

test('pretti, BloodLuxe and half blood keep six distinct data colours and AA text on their panels', () => {
  for (const id of ['pretti', 'bloodluxe', 'halfblood']) {
    const theme = getTheme(id);
    assert.equal(theme.id, id, `${id} is in the catalog`);
    const {roles, graph} = theme;
    for (let i = 0; i < graph.length; i += 1)
      for (let j = i + 1; j < graph.length; j += 1)
        assert.ok(distance(graph[i], graph[j]) >= 0.07, `${id}: data colours ${graph[i]} and ${graph[j]} are too close`);
    for (const role of ['ink', 'mute', 'dim']) assert.ok(contrast(roles[role], roles.panel) >= 4.5, `${id} ${role} on panel`);
    // Status colours stay apart (BloodLuxe's accent is itself blood red, so danger may share its hue).
    for (const [a, b] of [['signal', 'go'], ['go', 'caution'], ['caution', 'danger'], ['go', 'danger']])
      assert.ok(distance(roles[a], roles[b]) >= 0.08, `${id}: ${a} and ${b} read alike`);
  }
});

test('pretti is glossy graffiti glass: acid green accent, magenta and purple data, a dark vignette ground', () => {
  const {roles, graph, surface, focus, motion, scheme} = getTheme('pretti');
  assert.equal(scheme, 'dark');
  assert.equal(surface.style, 'glass');
  assert.equal(focus, 'glow');
  assert.equal(motion, 'fluid');
  assert.ok(hue(roles.signal) > 100 && hue(roles.signal) < 150, `accent ${roles.signal} is acid green`);
  assert.ok(graph.some((color) => hue(color) > -20 && hue(color) < 10), 'a magenta/pink data colour');
  assert.ok(graph.some((color) => hue(color) < -50 && hue(color) > -100), 'a purple data colour');
  assert.ok(luminance(roles.void) < 0.02, 'a dark ground');
});

test('BloodLuxe is a light luxe theme with readable cream surfaces and red, blue and grass green', () => {
  const {roles, graph, surface, scheme} = getTheme('bloodluxe');
  assert.equal(scheme, 'light');
  // Maximal art behind, but the panels stay nearly solid.
  for (const role of ['panel', 'inset', 'raised']) assert.ok(surface.alpha[role] >= 88, `${role} alpha ${surface.alpha[role]}`);
  assert.ok(luminance(roles.panel) > 0.85, 'cream panels');
  assert.ok(hue(roles.signal) > 0 && hue(roles.signal) < 40, `accent ${roles.signal} is blood red`);
  assert.ok(graph.some((color) => hue(color) > -110 && hue(color) < -80), 'a royal blue data colour');
  assert.ok(graph.some((color) => hue(color) > 120 && hue(color) < 150), 'a grass green data colour');
});

test('half blood is flat heraldic colour on an ultramarine field with a sun-orange accent', () => {
  const {roles, graph, surface, scheme} = getTheme('halfblood');
  assert.equal(scheme, 'dark');
  assert.equal(surface.style, 'flat');
  assert.deepEqual(surface.alpha, {panel: 100, inset: 100, raised: 100});
  assert.ok(hue(roles.void) < -70 && hue(roles.void) > -110 && chroma(roles.void) > 0.15, `field ${roles.void} is ultramarine`);
  assert.ok(hue(roles.signal) > 50 && hue(roles.signal) < 80, `accent ${roles.signal} is sun orange`);
  assert.ok(graph.some((color) => hue(color) > 120 && hue(color) < 150), 'a grass green data colour');
  assert.ok(graph.some((color) => hue(color) < -90 && hue(color) > -120 && luminance(color) > 0.4), 'a pale moon blue data colour');
});
