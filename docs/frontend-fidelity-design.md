# kitty! frontend fidelity and themes: design

The shipped redesign (v0.3.21–v0.3.24) did not come together the way the first design interview and its lab promised. On Notes, six typefaces render at once. One CSS rule makes every small label and every fold-out italic serif. Panels nest inside panels, 100 dropdowns use the browser's own menu, helper paragraphs read like AI, and the default Kitty theme is nearly a single pink. This interview settled 23 decisions to fix that. They were taken in the order of a new design lab, "design lab 2 · fidelity" (branch `design/fidelity-lab`, `web/lab.html`), where every question has a card with a "today" mock and each option. The decisions cover:
- **Controls:** one control height system, one surface per region, status as dots.
- **Type:** Inter sentence-case labels, three typefaces.
- **Fold-outs, dropdowns and adding:** ⋯ menus instead of boxed fold-outs, themed dropdowns with search, popover-to-create and drawer-to-edit.
- **Words:** a strict word budget.
- **Themes:** themes that own surfaces, motifs, backgrounds, loaders and motion, a richer Kitty theme, and six new themes.
- **Per-screen layouts:** for Home, Study, Flashcards, Notes, mind maps, Calendar, Opportunities, Find jobs and Settings.

Evidence for every "today" claim is in the audit that preceded the interview (v0.3.21 measured on the Liquid Glass and Kitty themes). Its summary is reproduced under Verified facts.

## Terms

- **Control**: any clickable or editable element: button, toggle, input, dropdown. *Avoid:* widget.
- **Surface**: a filled, bordered area that groups content (what the code calls a panel or card). *Avoid:* box, slab.
- **Status text**: words that report a state nobody acts on, such as "Saved locally", "Map saved", "Guide saved" or "0/0 blocks done". *Avoid:* none.
- **Fold-out**: a `<details>/<summary>` disclosure. *Avoid:* well, accordion (unless it really is one).
- **Design lab 2**: the per-interview mock site at `web/lab.html` on branch `design/fidelity-lab`. Card anchors are `#<section>:q<N>`.

## Why

In the user's words:
- "the main appearance of how it's supposed to look hasn't shipped"
- "the home page looks really ugly and not how we planned, notes section doesn't look good"
- "fonts in general are off and unprofessional and it doesn't come together as well as the grill-with-ui session laid it out"
- "I don't like the cursive looking fonts"
- "adding stuff is ugly in general"
- "the grey for unassigned isn't on theme"
- "the main study page is too long and not organized"
- "this drop down is so ugly"
- "in a lot of sections there's too many words and it looks AI"
- "things that are there should always serve a purpose"
- "the kitty theme is just very basic and doesn't have much to it, and it's not pretty"
- "make the current themes more than just cover overlays"

## Locked decisions

### Q2 (Shell): one surface per region, hairlines inside

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. One surface per page region; sections inside split by hairlines, no nested boxes** | **good** | **good** | **good** | **good** |
| B. Glass cards everywhere (today's look, cleaned up) | mixed | mixed | good | mixed |
| C. No surfaces at all; hairline minimal app-wide | mixed | good | mixed | good |

**Decision: A.** Each page region (a page's main column, a side rail, a drawer) is exactly one surface. Groups inside it are separated by a full-width hairline rule and a label, never another bordered box. That removes, for example, the three concentric borders on Opportunities (page panel > "Opportunities 12/12" panel > canvas), the bordered canvas inside a bordered Notes workspace, and calendar trays drawn as cards.

- **B lost** because glass cards everywhere is the box-in-box clutter the user called ugly.
- **C lost** because, with no surfaces, themes have nothing to render onto. That works against Q11 (themes own surfaces and borders).

### Q4 (Type): labels in Inter semibold, sentence case

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| A. JetBrains Mono caps, small and spaced | good | good | good | good |
| **B. Inter semibold, sentence case** | **good** | **good** | **good** | **good** |
| C. Inter semibold small caps | mixed | good | good | good |
| D. Mono caps for section labels, Inter for form field labels | good | good | good | good |

**Decision: B** (the user chose B over the recommended D). Every eyebrow, section title, field label and fold-out label uses Inter 600, sentence case, at the compact label size. No italics and no serif anywhere in chrome. This reverses the first design's Q10-E (pink Newsreader italic eyebrows), which the user rejected on sight.

- **A and D lost** to the user's preference for a quieter, uniform label voice.
- **C lost** because small caps read as a stylistic flourish.
- Mono stays for code, keys, numbers in tables and the navigation chrome that already uses it (Q5).

### Q5 (Type): three typefaces

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. Fraunces for page titles only + Inter + JetBrains Mono; drop Newsreader and Space Grotesk** | **good** | **good** | **good** | **good** |
| B. A plus Space Grotesk for widget and section headings | mixed | good | mixed | good |
| C. Inter + JetBrains Mono only | mixed | good | good | good |

**Decision: A.** Each face has one job:
- **Fraunces** for page titles (H1) only.
- **Inter** for everything else in the UI.
- **JetBrains Mono** for navigation chrome, keyboard keys, code and tabular figures.
- **Fredoka** remains only in the wordmark.

Newsreader and Space Grotesk are removed from the bundle.

- **B lost:** a fourth face without a strict job is what made the app noisy.
- **C lost:** without Fraunces the soft title voice the Kitty theme relies on is gone.

### Q6 (Type): fold-outs become ⋯ menus or popovers

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| A. Unboxed "More options ›" row with chevron and hairline | good | good | good | good |
| B. Section header row, label left, chevron right, hairline, no fill | good | good | good | good |
| **C. Replace most fold-outs with a ⋯ menu or popover** | **mixed** | **mixed** | **mixed** | **good** |
| D. Secondary settings in a side drawer per page | mixed | mixed | mixed | good |

**Decision: C** (the user chose C over the recommended B). The 99 `<summary>` fold-outs are triaged:
- **Rare actions:** import, advanced map actions, "More layouts", note types and templates, study tools. These move into a ⋯ menu on the owning header.
- **Small forms:** "Start from a subject", reminders and goals. These become popovers anchored to the control that needs them.
- **Long settings content:** follows Q23 (Settings becomes an index with short pages).
- **Read-only explanation:** "Return review & reflection", provenance notes. These are deleted under Q9 or become a tooltip.

The rating is mixed because hidden things are harder to discover; the ⋯ menu must be consistent (always the last control in a header, same icon, keyboard reachable). A and B lost because the user prefers fewer visible rows over unboxed rows. D lost because a drawer per page is heavier than a menu.

### Q8 (Type): popover to create, drawer to edit

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| A. Quick-add popover only | good | mixed | good | good |
| B. Side drawer everywhere | mixed | good | good | good |
| C. Inline add row at the top of each list | good | good | good | good |
| **D. Popover to create, side drawer to edit details** | **good** | **good** | **good** | **good** |

**Decision: D.**
- **Creating** anything (exam, deck, task, event, card, subject, opportunity) opens a small popover anchored to the + button. It holds one natural-language field plus at most two essentials (for example a date and a subject chip); Enter saves.
- **Opening** an existing item shows the side drawer with every field.
- The calendar's existing drawer and phrase parser are the reference implementation.
- Flashcards' inline add row (v0.3.22) stays inside the card browser as that list's create affordance.

A lost because editing many fields in a popover is cramped. B lost because a drawer for a one-line create is slow. C lost because not every page is a list.

### Q9 (Type): strict copy budget

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. Strict: help only in empty states and before consequential actions, 12 words max** | **good** | **good** | **mixed** | **good** |
| B. Moderate: one help line per panel, 20 words max | mixed | good | good | good |
| C. No inline help; tooltips + a global ? overlay | mixed | good | mixed | good |

**Decision: A.** Inline helper text appears only in two places, each at most 12 words:
- in an empty state;
- directly before a consequential or irreversible action.

Every other explanation:
- is deleted if it restates the obvious or describes where something came from;
- otherwise moves into a tooltip on the control it explains.

Each screen also gets a purpose check: every label, caption, heading and duplicate button must earn its place or go. The risk is mixed because a rare control can lose its explanation; tooltips cover it. B lost because it keeps the AI-sounding paragraphs. C lost because empty states need words.

### Q11 (Themes): what a theme owns

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. Surfaces and borders (glass, paper, flat, frames)** | **good** | **good** | **good** | **good** |
| B. Title face and label accents | good | good | mixed | good |
| **C. Motif layer: icons, dividers, bullets, frame ornaments** | **good** | **good** | **good** | **good** |
| **D. Background art and texture** | **good** | **good** | **good** | **mixed** |
| **E. Loaders, empty-state art, focus and selection styles** | **good** | **good** | **good** | **good** |
| **F. Motion style (calm, snappy, none)** | **mixed** | **good** | **mixed** | **good** |

**Decision: A, C, D, E and F** (multi-select; the user swapped the recommended B for F). A theme is a world, not a tint. Beyond its colour tokens, each theme ships:
- its surface treatment and border style (A);
- a motif layer of icons, dividers, bullets and frame ornaments (C);
- background art and texture (D);
- its loader, empty-state art, focus ring and selection style (E);
- a motion style (F).

Two constraints:
- **Motion:** theme motion always yields to the user's Reduce motion setting and the OS preference.
- **Typography stays global** (B was not chosen). Themes do not change faces, which keeps Q4/Q5 one type system across themes.

Backgrounds must stay below a contrast budget so text on surfaces keeps WCAG AA.

### Q13 (Home): widgets only, Sections as a popover, collapsible rail

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. Widgets only; Sections becomes a popover over the Brain** | **good** | **good** | **mixed** | **good** |
| B. Keep the Sections list, collapsed to one header by default | mixed | good | good | good |
| C. One left rail of widgets and a wider Brain | good | good | good | good |
| D. Two rebalanced rails: Agenda + Quick note, Agents + Up next | good | good | good | good |

**Decision: A, plus a rail collapse** (the user chose A over the recommended D).
- The old Workspace/Sections list leaves the home rail. Sections become a popover opened from the Brain's corner, with section search and the section list; Ctrl K also finds sections.
- The rails hold only widgets (Today agenda, Agents, Quick note, Up next).
- From the discussion: "I want an option to hide the right side so the brain can collapse to be bigger." The widget rail gets a collapse control: a chevron on its inner edge plus a keyboard shortcut. Collapsed, the rail becomes a thin edge strip and the Brain widens to fill the space. The choice is remembered.

The risk is mixed because section navigation is one click deeper. B lost because it keeps the old layout. C and D lost to the user's preference for a clean Brain-first home.

### Q14 (Study): focus-first with drawers

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| A. Right column shows only the current phase; full guide in its own tab | good | good | good | good |
| B. Timer and tasks on top, phase columns below, notes as a side card | mixed | good | good | good |
| **C. Focus-first: timer alone, everything else in drawers** | **mixed** | **good** | **mixed** | **good** |
| D. A with tabs cut to Session/Plan/Progress/Tools and timer settings as a popover | good | good | good | good |

**Decision: C** (the user chose C over the recommended D). The Study page opens on the timer alone, centred. Subjects and tasks, the guide, session notes, distractions and spaced returns live in drawers that slide over the page. This replaces the 1,986px-tall cockpit.

- How the drawers split was asked as a follow-up (Q24) and left open at Finish. See Deferred, which records the default to build.
- UX and risk are mixed because everything except the timer is one click away.
- A and D lost to the user's preference for a calm first view. B lost because it keeps the page long.

### Q19 (Notes & maps): one mind-map editor

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| **A. One editor, reached from Notes' Map mode, Study and Ctrl K; the Notes concept canvas folds into Canvas** | **good** | **good** | **mixed** | **good** |
| B. Keep both, rename Notes' mode "Concept map" | mixed | mixed | good | good |
| C. A standalone Mind maps view only; remove the Study tab | mixed | good | mixed | good |

**Decision: A.** There is one mind-map editor (today's `SchemaMaps.svelte` with the pill nodes, keyboard building, drag, free positioning, recolour, cross links, multi-select, undo, split/merge and pen). It opens from Notes → Map, Study → Mind map and Ctrl K. Notes' separate concept-map mode folds into Canvas.

The risk is mixed because existing Notes concept maps need a one-way migration into the shared editor, done with a reversible owner-only JSON export first. B lost because two editors split every feature. C lost because Study needs maps in context.

### Q21 (Other): Opportunities map-first

| Option | User experience | Correct | Risk | Cost |
|---|---|---|---|---|
| A. Board like the pipeline: stage columns, compact cards, map as a toggle | good | good | good | good |
| **B. Map first, full-bleed, list as a side drawer** | **mixed** | **good** | **good** | **good** |
| C. Table first with filters, map as a toggle | mixed | good | good | good |

**Decision: B** (the user chose B over the recommended A).
- Opportunities & projects opens as a full-bleed map, with no nested panels.
- The list slides in as a side drawer.
- Filters and Add (via the Q8 popover) sit in one floating control row over the map.

UX is mixed because scanning many items is slower on a map; the list drawer covers that. A and C lost to the user's preference for the spatial view.

## Routine choices

- **Q1, Shell (control height):** C. Every control is 28px tall with one radius per control type. The one or two primary actions on a page may be 32px. This replaces the measured 26/33/34/39/42px mix.
- **Q3, Shell (status text):** B. Status becomes a 6px dot on the tab or title, with a tooltip for detail. Neutral when saved; warning colour when unsaved; danger when failed. "Saved locally", "Map saved", "Guide saved" and "0/0 blocks done" are removed as text.
- **Q7, Type (dropdowns):** C.
  - Choices of 4 or fewer become segmented buttons.
  - Longer lists use the themed glass listbox.
  - Decks, notebooks and timezones get a searchable combobox.
  - The browser's native dropdown menu is gone. All 100 `<select>`s migrate, using the app's existing `SelectListbox`, `Combobox`, `TimeZoneSelect` and `ToggleGroup`.
- **Q10, Themes (Kitty direction):** A + B + C. The Kitty theme becomes:
  - frosted pink glass surfaces over a soft blossom backdrop;
  - a real palette with distinct accent, success, warning and data colours (no longer four pinks);
  - the motif layer from the first interview: blossom bullets that bloom on tick, petal dividers, cat marks, a petal spinner.
- **Q12, Themes (new themes):** all six are built as full themes under Q11:
  - **SPEED:** watercolour paper, teal/black splatter and drips, crimson sketch lines, white gothic line art, gear rings, a stamped sigil.
  - **c:** near-black with etched white line art, knot borders, diamond tiling, blood-red washes, sigils.
  - **pretti:** acid green, magenta and purple, glossy graffiti airbrush, hypnotic spirals, hearts, stars, a dark vignette.
  - **BloodLuxe:** marbled red/white/blue swirls, pink, cream and grass green, a black thorn frame, dripping red lettering accents.
  - **half blood:** an ultramarine field with cloud squiggles, grass green and sun orange, a white bone filigree frame, red drip accents.
  - **red:** stark black and white, a single dripping blood-red accent, editorial small caps.
  - Reference images: `/tmp/kitty-shots/theme-refs/`. All motifs are original drawings in each style; the album artwork is never embedded or traced.
- **Q15, Study (timer settings):** A. A popover from the timer with:
  - minute steppers;
  - sound and system-notification switches;
  - the themed timezone picker.
  The 68-word paragraph and the seconds fields go.
- **Q16, Flashcards:** A. The deck scope becomes a compact searchable chip in the page header. There is one New card button (shortcut N); the duplicates are removed.
- **Q17, Notes (header):** A.
  - One row: library ☰, page tabs, and the Write/Canvas/Map segmented switch on the right.
  - The page title is a Fraunces H1 inside the page, with a small meta line.
  - Import moves into ⋯ (Q6).
  - Nothing else stays in the bar.
- **Q18, Mind maps (toolbar):** D.
  - A slim top bar holds the map switcher, title, undo/redo and layout.
  - Building tools sit in the exact floating bottom pill the Notes canvas uses.
  - A small contextual bar appears next to the selected topic.
  - The help paragraph and the separate "Map title" bar go, and the canvas fills the page.
- **Q20, Calendar (adding):** A. One + on the floating range pill opens the quick-add popover with a typed phrase ("gym tue 6pm 1h"). Clicking a slot prefills it, and the drawer is only for editing. The three duplicate Add task/Add event spots go.
- **Q22, Find jobs (navigation):** A. One segmented control for Map, List, Saved, Applications, Resumes and Sources. Reminders, deadlines and goals become an unboxed strip.
- **Q23, Settings:** B. Settings becomes a left index with short pages. Module rows print one description, slider explanations become tooltips, and Google setup is one line plus a docs link.

## Verified facts

These were established by the read-only audit of v0.3.21 (`origin/main` @ `dcf6a7e`). Its screenshots and measurement scripts were kept in the session that produced it.
- **Italics:** `web/src/styles.css:1314-1322` sets `font-family: var(--font-label)` (Newsreader) and `font-style: italic` on `:root body :is(.eyebrow, .br-section-title, .br-field__label, summary)`. That covers 99 `<summary>` elements in 30 files and 59 `.eyebrow` uses in about 35 files. `Notes.svelte:2445` adds uppercase on top, producing the italic-caps "VIEWS ▾ / MORE ▾".
- **Typefaces:** six render on Notes/Writing: Inter, JetBrains Mono, Newsreader, Space Grotesk, Fraunces and Fredoka.
- **Fold-outs:** the global `summary` style (`styles.css:218-238`) draws each as a 34px full-width bordered box.
- **Notes header:** one bar has controls at 26, 33, 34, 39 and 42px with radii 999, 6 and 5.
- **Dropdowns:** 100 native `<select>` elements in 29 files, styled `appearance:auto` (`styles.css:126-183`). The app already ships unused `SelectListbox`, `Combobox`, `TimeZoneSelect`, `DurationInput` and `ToggleGroup`.
- **Study page:** 1,986px tall on Liquid Glass. Timer settings ask for seconds and an IANA timezone above a 68-word paragraph.
- **Helper text** (words per screen):

  | Screen | Words |
  |---|---|
  | Notes/Mind map | 250 |
  | Settings | about 170 |
  | Study session | 125 |
  | AI | 123 |
  | Helpers | 100 |
  | Notes/Canvas | 95 |
  | Calendar | 92 |
  | Timer settings | 80 |

- **Duplicated actions:** Calendar's Add task/Add event appear 3 times, Flashcards' New card 2–3 times, Study's Start session twice.
- **Kitty theme:**
  - panel vs background contrast is 1.08:1;
  - accent vs structural pink is 1.11:1;
  - 4 of 6 graph colours are pinks at 26–46% saturation;
  - there are 0 lines of theme-specific CSS (Liquid Glass has 157);
  - the planned motif accents never shipped.
- **Two mind-map editors exist:** Study's `SchemaMaps.svelte` (old toolbar) and Notes' Map mode (about 150 words of disclaimers).
- **The pipeline (My applications) is the screen closest to the original lab.** Use its label voice and density as the reference.

## Risks

- **Fold-outs → menus (Q6):** discoverability drops. Mitigation: the ⋯ menu is always the last control in its header, has the same icon everywhere, is keyboard reachable and is listed in Ctrl K.
- **Focus-first Study (Q14):** a returning user may not find the guide. Mitigation: drawer edge tabs stay visible, keyboard shortcuts exist, and the drawer state is remembered.
- **Mind-map consolidation (Q19):** one-way migration of Notes concept maps. Mitigation: a reversible owner-only JSON export and a synthetic-data migration test, as with the blocks migration.
- **Home without the Sections list (Q13):** navigation is one click deeper. Mitigation: a Sections popover on the Brain, plus Ctrl K.
- **Themes with background art (Q11-D, Q12):**
  - Contrast on busy art. Mitigation: surfaces keep a minimum opacity, and an automated contrast check runs per theme in the browser tests.
  - Asset weight. Mitigation: SVG/CSS motifs, lazy-loaded per theme.
  - Copyright. Mitigation: original motifs only, never the reference artwork.
- **Migrating 100 dropdowns (Q7):** regression surface. Mitigation: each screen slice migrates its own selects with its tests.
- **Strict copy (Q9):** an important caveat could be deleted. Mitigation: consequential-action text is explicitly allowed. Each removal is reviewed in its screen slice.

## Deferred

- **Q24, Study: what goes in the focus-first drawers?** This was asked after Q14 and left unanswered when the interview finished. The options were:
  - A: two drawers. Left holds subjects and next tasks; right holds the current phase steps and session notes. Progress, Plans, Mind map and Tools stay as tabs above.
  - B: one right drawer with sections; everything else under ⋯.
  - C: a bottom sheet with tabs.

  The recommendation was **A**, and A is what gets built unless the user says otherwise. It mirrors the old cockpit's sides, so muscle memory carries over, and it keeps whole pages out of drawers. Its weakest point is two drawers to learn; the `[` and `]` shortcuts mitigate that. The question reopens if the Study slice shows A crowding the timer, or the user picks differently on review.

## Open threads

- **Q13 rail collapse:** the user asked for a way to hide the right side so the Brain can grow. It is recorded as part of the Q13 decision. The Home slice chose **]** as the shortcut: it is free on home, needs no modifier, and fits the `[`/`]` drawer keys planned for Study (Q24).
- **Implementation slicing (from the audit):**
  1. Global type, tokens, shared controls (fold-out menu, dropdowns, popover/drawer pattern, status dot).
  2. Theme engine (Q11) + Kitty (Q10).
  3. Home.
  4. Notes + mind maps.
  5. Study.
  6. Flashcards.
  7. Calendar.
  8. Other screens.
  9. The six new themes.

  Slices 1 and 2 unblock the rest. Each screen slice applies Q9's copy rules and purpose checks to its own files.
