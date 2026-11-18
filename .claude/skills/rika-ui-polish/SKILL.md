---
name: rika-ui-polish
description: Applies rika!'s locked design system to Svelte 5 UI changes and proves the result on screen. Checks theme tokens, keyboard-first behaviour, the copy budget, reduced motion and accessibility, with screenshots at 1568x942 and 390px. Use when creating or changing .svelte components, web/src/styles.css or theme files, or when asked to polish, review or fix how a screen looks, reads or feels.
paths:
  - "web/src/**/*.svelte"
  - "web/src/**/*.css"
  - "web/src/lib/themes/**"
---

# rika! UI polish

rika! already has a design system. Apply it exactly and prove it on screen; do not invent a new look.
On product screens this file beats any generic design skill. Where frontend-design, Impeccable or a
taste skill says "take a risk" or "avoid Inter", ignore it: the owner chose these fonts and rules.

## Sources of truth: read the relevant part before editing

- `docs/frontend-fidelity-design.md`: locked decisions (surfaces, type, copy budget, controls, themes, per-screen layouts)
- `docs/frontend-redesign-design.md`: earlier decisions still in force (focus ring, keyboard building, flashcard keys)
- `web/src/lib/themes/contract.ts`: what a theme may and may not do
- `web/src/styles.css`, `web/src/lib/themes/engine.css`: the tokens
If this file disagrees with those docs, the docs win. Say so in your report.

## Plan first: three lines in your reply, before code

1. Surface: which page region changes, and which existing parts you reuse (`web/src/lib/ui/*`: Select,
   Popover, QuickAdd, EditDrawer, OverflowMenu, StatusDot, ToastStack; EmptyState; Loader; Bracket
   Button, Panel, Badge, ToggleGroup). Add a new component only when two places need it.
2. Keyboard path: how the task is done without a mouse, key by key.
3. Words: every new visible string, with its word count.

## Rules

### Tokens
- Colour, size, radius and motion only via `var(--…)`: `--ink --muted --subtle --accent --surface
  --surface-raised --border --hairline --success --warning --error`, `--control-h --control-h-lg`,
  `--r-control --r-field --r-menu --r-item`, `--fs-label --fs-control --fs-chrome`,
  `--motion-ms --motion-ease`, `--focus-ring-*`. No raw hex, rgba or one-off px for these. 1px hairlines are fine.
- Each face has one job. Fraunces: page titles (H1) only. Inter: all other UI. JetBrains Mono:
  navigation chrome, keys, code, tabular numbers. Fredoka: the wordmark only. Themes never set `font-family`.
- Theme CSS is scoped to `:root[data-theme="<id>"]`; decorative motif rules also `:not([data-motif="off"])`.

### Layout and controls
- One surface per page region. Inside it, separate groups with a full-width hairline and a label.
  Never put a bordered box inside a bordered box.
- Controls use `--control-h` (28px). At most two primary actions per page use `--control-h-lg`.
- No native dropdown menus. 4 or fewer choices: ToggleGroup. More: the themed Select (searchable for long lists).
- Rare actions go in the ⋯ OverflowMenu, always the last control in its header.
- Create in a popover anchored to the + button: one natural-language field plus at most two
  essentials, Enter saves. Edit in the EditDrawer.
- Status is a StatusDot with a tooltip, never text like "Saved locally".

### Keyboard first
- Every action works from the keyboard. Tab order follows reading order. No positive `tabindex`.
- Keep the shared `:focus-visible` ring from `engine.css`. No `outline: none` without an equal replacement.
- Popovers, menus and drawers close on Escape and return focus to the control that opened them.
  Dialogs use `<dialog>` with `showModal()`.
- Register new shortcuts where `shortcutsFor` (`web/src/lib/workspace.ts`) reads them, so Keyboard
  shortcuts lists them. Letter shortcuts pause while typing or inside a dialog. Check for clashes,
  including Ctrl Space.
- Show the key on the control (tooltip or `<kbd>`), as flashcard grading does.

### Words (no fluff)
- Inline help only in an empty state or right before a consequential or irreversible action, at most
  12 words. Anything else is deleted or becomes a tooltip on the control it explains.
- Labels: Inter 600, sentence case. No ALL-CAPS eyebrows, no italics or serif in chrome.
- Buttons say what happens ("Save changes", not "Submit"). An action keeps one name through the flow
  ("Archive", then "Archived").
- Errors say what failed and what to do next. No apologies, no "Oops", no exclamation marks.
- Every label, caption, heading and duplicate button must earn its place. Delete text that restates
  the obvious or says where something came from.

### Motion
- Only `transform` and `opacity` animate. Nothing blocks input.
- Timing comes from `--motion-ms` and `--motion-ease`. Entering: ease-out. Moving on screen: ease-in-out.
- Everything stops under `prefers-reduced-motion: reduce` and `:root[data-motion="reduced"]`. JS
  animations (`element.animate`, requestAnimationFrame loops) go through `web/src/lib/motion.ts` and do not run then.

### Accessibility floor (WCAG 2.2 AA)
- Text contrast at least 4.5:1 (3:1 for large text) on every theme, over the brightest backdrop.
- Targets at least 24×24px. Icon-only controls have an accessible name. Fields have labels. Async
  results use a live region.
- No horizontal scrolling at 390px. Zoom stays enabled.

### Svelte 5
- Runes only. Prefer `$derived` to `$effect`. Never write `$state` inside `$effect` unless it cannot loop.

### Tells to remove
Nested cards; decorative gradients; one radius and one grey shadow on everything; ALL-CAPS eyebrows;
"01 / 02" on content that is not a sequence; fade-up on every section; "A · B · C" meta strings; "→"
appended to buttons; paragraphs of helper text; small grey text on grey.

## Verify: required before you say done

1. Static checks: `npm --prefix web run check`, plus for each changed .svelte file
   `npx @sveltejs/mcp svelte-autofixer <file> --svelte-version "$(node -p "require('./web/node_modules/svelte/package.json').version")"`.
2. Demo (synthetic data only):
   ```bash
   npm --prefix web run build
   uv run kitty-web --demo --port 8890   # prints a one-use link; later runs need the "next link" it prints
   ```
   On NixOS, set `PLAYWRIGHT_BROWSERS_PATH` and `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS=true` as in
   `scripts/screens/README.md`. Do not rebuild while the script runs.
3. Shots and checks:
   ```bash
   node .claude/skills/rika-ui-polish/scripts/shots.mjs "<link>" <route> [route …]
   ```
   Routes are hash names: today, notes, calendar, jobs, rika, documents, … For each route the script
   saves 1568×942 and 390×844 screenshots with and without reduced motion, crops of the first 15 Tab
   stops, and report.json. Exit code 2 means a hard failure: sideways scroll, an animation still running
   under reduced motion, a focus stop with no visible ring, or a serious or critical axe finding.
   `wordyText` and `smallTargets` in report.json are leads to check by eye.
4. Open every PNG with the Read tool and check it against the rules above: one surface per region,
   28px controls, each face doing its job, the word budget, a visible ring on every focus crop, and
   nothing clipped or overlapping at 390px. Fix and rerun. After three rounds, stop and report what is left.
5. If tokens or a theme changed, also run `node --test web/tests/themes.browser.test.mjs`.
6. Build the owner's gallery: `python3 scripts/screens/gallery.py <out dir> "<change title>"`.

## Report

- What changed, one line per file.
- Screenshot folder and gallery path.
- Every check you ran, with its PASS/FAIL line.
- What you did not verify (for example the Tauri window, pen input, screen-reader speech).
