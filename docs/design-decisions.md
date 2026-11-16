# Design decisions (from the design-lab session)

## Round 1: default theme (2026-11-13)
- Default theme = Apple product-page look, **always black**: #000000 ground, #161617 surfaces, #F5F5F7 text.
- **No accent colour**: pure black and white; primary buttons and the active state are inverted.
- **No blur anywhere except the top bar**, which gets Apple's frosted bar; everything else flat (hairlines, one-step greys).
- Type: **Inter**, weights 300–600, larger sizes, **no monospace**.
- Other themes stay as options; today's pink Kitty becomes "Blossom". No petals/blossom backdrop in the default.

## Inspiration: ginseng / ginseng-tui (github.com/ZacharySF)
- Dot-matrix point fields on near-black (ginseng-tui "liquid study", "ambient geometry"): basis for 2D nodes as stippled,
  star-like points and for the brain loader as a point cloud (braille/dot rendering: engine/ginseng/braille.py).
- Palette: brand cobalt #0100F4 on paper #F7F7F2, ink #070C56. Type: Archivo Narrow. Layout: numbered sections,
  quiet technical labels.

## Module picker (first run)
- **B: sets first** (Student, Job search, Builder, Minimal) fill the switches; fine-tune in the list below. Sets shared with Templates.
- After picking: **restart automatically on first run**; later, one "Restart now" button.

## From the labs page (2026-11-13) — full wording in lab-answers.md
- **Type:** TeX Gyre Heros (open Helvetica; supersedes Round 1's Inter). Titles in the text face, light/regular, no Fraunces.
  Mono only inside code blocks and the terminal. Density switch becomes Standard / Larger. (Heros has only 400/700: no thin weight.)
- **Top bar:** C, centred groups with a tab switcher, one row; *no Settings or anything unnecessary in the bar, only
  important things*. Theme picker lives in Settings (+ "Theme" in search). Breadcrumb only inside nested schemas. In the
  desktop app the row is the window title bar.
- **Module picker:** C, one question then the list (supersedes B). Defaults: every module without a download on, downloads
  off. One component for first run and Settings. Names bundled; packs and sizes from the release's signed packs.json,
  cached, offline falls back. Sizes on downloading modules + a total. Restart now; first run restarts itself. Lines say
  what you do ("Review cards on a schedule").
- **Templates:** Student, Job search, Builder, Minimal. Rika suggests one. A template arranges, then offers each missing module.
- **Widgets:** edit mode (Edit shows handles/sizes/library, Done locks); free resize by grid cells; library = drawer beside
  Home; Pomodoro = a single line with a hairline bar; one shared timer with Study.
- **2D nodes:** constellation points with spikes on hubs; one-hue ramp in lightness steps; names only on hover; links within
  a group at rest; 2D map is the default Brain look.
- **Loader:** point-cloud brain everywhere (start, views, Brain, buttons); captions name the thing ("Opening Brain…"); no
  skeleton rows; the Brain turns after 30 s untouched, at 0.5× (a turn every 57 s).
- **Notifications:** Linux notifications + an in-app centre from search; events: calendar soon, agent waiting, study timer
  end, Rika strong match; click opens, buttons Snooze 10 min and Done; quiet hours: desktop DND + rika!'s dial; nothing
  critical; a systemd user service keeps the core running when the window is closed; one card per item updated in place;
  a Recent list in Settings (24 h).
- **Agents:** ambient: one number + a notification; on both surfaces plus a pinnable per-session widget; numbers: context
  %, new tokens, cache reads apart, tool calls, files changed; price from a model table; read the CLIs' own logs locally,
  opt-in per agent kind, keep only per-session totals; watch files, push at most every 5 s; notify only when waiting for input.

## Round of 15 Nov 2026 (the owner's feedback on the first attempts; these override anything above)
- **2D map points carry their names** (over the point), always; not only on hover.
- **Job list:** no wide bar above the list. Actions on ticked rows live in a bar at the bottom that exists only while rows are ticked.
- **Widget colours:** the theme's own six data colours as dark glass panes; no outlines, no off-theme hues.
- **Kitty theme:** art-directed from the owner's references (Shiina Ringo subway photograph, Placebo "Without You I'm Nothing B-Sides", a blue car park): cross-processed film stills, flat square panes, heavy grotesque type, tube yellow and signal orange. No ASCII art, no glow, no aqua glass ("AI ugly").
- **Animations** must be seen: page turns, sliding markers, springs. Fades alone read as "flashing".
- **Any page can be rearranged** with Ctrl+Shift+Space (move and resize blocks).
- **Calendar:** Notion Calendar / Apple Calendar as the bar: hairlines, one clear now-line, soft event blocks, no floating pill, no cards in cards.

## Module downloads (15 Nov 2026)
- One full-window, opaque download surface covers the title bar and any onboarding dialog, whether a download starts in Settings, onboarding, or a theme choice. Reuse the turning point-cloud brain, with a short pack label and aggregate byte progress; no blur.
- Use the native modal top layer: keep background controls inert and keyboard focus inside until the download succeeds or fails, then return focus to its caller (or the next available control). Escape does not hide a running download. Reduced motion keeps the brain still.
