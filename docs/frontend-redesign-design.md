# kitty! frontend redesign — design decisions

Project: kitty! (`~/projects/kitty!`), worktree `~/projects/kitty-worktrees/frontend-options-20261031`, branch `design/frontend-options-20261031`. No PR yet.

This document records the decisions from a design interview held on 2026-10-31 about redesigning the kitty! web frontend: the app frame, fonts, the kitty motif, the home page and widgets, 2D and 3D graph nodes, every study surface (study hub, flashcards, notes, mind maps, quick notes, documents), the calendar, and features that were designed or promised but never built. Every option was shown as a live, working component in a **design lab** built inside the app's own Vite project (`web/lab.html`, `web/src/lab/**`), using the real theme tokens, fonts and Bracket components. It isn't part of the production build. All 69 decisions are made. There is no exported visual; the lab itself is the visual reference (run `npm --prefix web run dev -- --port 5197` and open `/lab.html#<section>:<decision-id>`).

## Terms

- **design lab**: The live option gallery at http://127.0.0.1:5197/lab.html; every lettered card there is a working component on the real kitty! theme tokens. Avoid: mockup, wireframe.
- **motif**: kitty!'s decorative language: cherry blossoms (ASCII and drawn), petals, cat ears and paws in the pastel theme colors. Avoid: mascot, theme.
- **today's look**: The option that reproduces what the app ships now, tagged CURRENT in the lab. Avoid: default, baseline.
- **Kitty theme**: the `kitty` entry in `web/src/lib/themes.ts` (plum void `#1b151a`, panel `#241c22`, pastel pink structure `#deb2c3`, graph palette `--c1..--c6`, ASCII blossom motif). Avoid: pink theme, Harness.
- **dock**: the bottom panel holding the herdr terminal and agent activity (`Dock.svelte`). Avoid: footer, console.

## Why

In the user's words: "i want to update kitty! frontend. i want you to present with a lot of options for the nodes, 2d nodes and 3d, widgets, fonts, layout of all the study stuff, calendar, etc. basically everything unimplemented … please make sure its on theme with kitty!" The frontend had grown feature by feature. Home is a graph with a collapsed dashboard; study screens are single-column forms built from nested `<details>`; graph nodes all look the same whatever their kind; there are two competing type layers; and backend data such as FSRS memory state, streaks, availability and subject colours has no UI.

## Locked decisions

### Q1 · Which theme is the design target for the redesign?

kitty! ships nine dark themes; the default today is **Harness** (neutral grey with pink accents), while **Kitty** (plum void, pastel pink, cherry-blossom motif) is one choice in the picker. Every lab preview is drawn on the Kitty theme, and the motif options (petals, cat ears, blossom loaders) only make sense if Kitty is the look we design for. Use the theme pills at the top of the [design lab](http://127.0.0.1:5197/lab.html) to see any option on another theme.

**Decision: A · Kitty becomes the default and the design target (accepted the recommendation).**

| Option | On theme | Usability | Legibility | Runtime cost |
|---|---|---|---|---|
| **A · Kitty becomes the default and the design target** | **good** | **good** | **good** | **good** |
| B · Harness stays the default; motif extras appear only on the Kitty theme | mixed | good | good | good |
| C · Theme-neutral | poor | mixed | good | good |

Chosen option(s):
- **Kitty becomes the default and the design target** — new components are tuned for Kitty first; the other eight themes stay available and must not break.

Why: You asked for everything to be on theme with kitty, so designing for Kitty first is the direct reading; the other themes still recolor through the same CSS variables, which every lab component already uses. Best on every axis here; the only thing it gives up is Harness being the out-of-the-box look, which a one-line `DEFAULT_THEME` change controls.

Rejected options:
- **B · Harness stays the default; motif extras appear only on the Kitty theme**: like today's blossom after the wordmark, gated by `data-theme="kitty"`. Lost: mixed on On theme.
- **C · Theme-neutral**: design for all nine themes equally, no motif anywhere. Lost: poor on On theme.

### Q2 · What should the overall app frame look like?

The chrome around every page: where navigation lives, how the 28 views are reached and how much room the page keeps. It decides whether group switchers, tabs and the dock stay as they are or move, so it frames every other shell decision. Compare them live: [lab → shell-layout](http://127.0.0.1:5197/lab.html#shell:shell-layout). Letters match the lab cards.

**Decision: A · Top bar + tabs + dock.**

| Option | On theme | Usability | Legibility | Runtime cost |
|---|---|---|---|---|
| **A · Top bar + tabs + dock** | **good** | **mixed** | **good** | **good** |
| B · Icon rail | mixed | mixed | mixed | good |
| C · Sidebar tree | mixed | good | good | good |
| D · Floating dock | good | mixed | mixed | mixed |
| E · Command-first | good | mixed | good | good |
| F · Top bar + sub-nav row | good | good | good | good |

Chosen option(s):
- **Top bar + tabs + dock** (today's look) — Today's frame: wordmark, breadcrumb, four group pills, browser tabs and a bottom terminal dock.

The interview recommended F: F keeps today's look but fixes the biggest navigation gap: every view of the active group is one click away, with no 230px sidebar and no new icons to learn. Its weakest axis is ux in Job search, where 13 views overflow the row and scroll sideways. It gives up C's view of the whole app at once; C is the better pick if Job search keeps growing. The user chose differently, so the recommendation's reasoning is kept here only as context.

Rejected options:
- **B · Icon rail**: A 64px left rail of the four groups whose fly-out lists each group's views; slim header with breadcrumb and search. Lost: mixed on On theme, Usability, Legibility.
- **C · Sidebar tree**: A grouped, filterable, collapsible tree of all 28 views with badges and agent dots; collapses to an icon strip. Lost: mixed on On theme.
- **D · Floating dock**: Minimal chrome with a macOS-style glass dock of pinned views (hover magnification) and a launcher for all views. Lost: mixed on Usability, Legibility, Runtime cost.
- **E · Command-first**: One centred field shows where you are and turns into search; no other navigation chrome. Lost: mixed on Usability.
- **F · Top bar + sub-nav row**: Today's top bar plus a second row listing every view of the active group with an underline and badges. Lost: rated good throughout; it lost on preference, not on rated weakness.

Discussion:
- *user*: i like f but i dont want it looking like there is too much goingon i want it to be simplistic
- *user*: i want command first feature tho
- *agent*: Recorded today's frame (top bar, tabs, dock). I left out F's second row of views: you liked it, but it adds a line of chrome to every page. Command-first is now its own question (**Q63**), because it can sit on top of today's frame without adding clutter, and that question also covers trimming the top bar so it stays simple. See **Q63**: its options range from today's Ctrl K button to typing anywhere to open the palette, up to a top bar that is mostly one search field.

### Q3 · Which font pairing should kitty! use for titles, UI and mono?

Sets the three faces used across the whole app: h1/h2 titles, body and UI text, and the mono for chrome, kbd and code. Every page, the Atlas panel, dialogs and the dock follow it, and the wordmark and numeral choices usually build on it. Compare them live: [lab → type-pairing](http://127.0.0.1:5197/lab.html#type:type-pairing). Letters match the lab cards.

**Decision: B · Fraunces soft · Inter · JetBrains (accepted the recommendation).**

| Option | On theme | Usability | Legibility | Runtime cost |
|---|---|---|---|---|
| A · CMU · Inter · JetBrains | good | good | good | mixed |
| **B · Fraunces soft · Inter · JetBrains** | **good** | **good** | **good** | **good** |
| C · Instrument Serif · Geist · Geist Mono | mixed | good | mixed | good |
| D · Newsreader · Plex Sans · Plex Mono | mixed | good | good | mixed |
| E · Recursive casual | good | mixed | good | mixed |
| F · Fredoka · Nunito rounded | good | mixed | good | good |
| G · Space Grotesk · Space Mono | poor | good | mixed | good |
| H · All mono | mixed | poor | mixed | good |

Chosen option(s):
- **Fraunces soft · Inter · JetBrains** — Keeps the Inter UI and mono chrome but sets titles in Fraunces with its soft, rounded terminals and real italics.

Why: Fraunces keeps everything that already works (Inter UI, JetBrains chrome, the serif-title voice) and fixes CMU's real limits: it has italics and a weight range, its soft axis echoes kitty's pastel roundness without going childish, and its 121 KB Latin variable file is lighter than today's 222 KB CMU TTF. Its weakest axis is kitty-specific charm being subtle rather than loud, and the WONK leaning letters can read as a glitch to some; it gives up CMU's distinct LaTeX/academic flavour, which A keeps.

Rejected options:
- **A · CMU · Inter · JetBrains**: Today's CMU Serif Bold titles over Inter UI with JetBrains Mono uppercase chrome and Inter Tight h3. Lost: mixed on Runtime cost.
- **C · Instrument Serif · Geist · Geist Mono**: Tall, light editorial display serif with a crisp geometric Geist UI and Geist Mono chrome. Lost: mixed on On theme, Legibility.
- **D · Newsreader · Plex Sans · Plex Mono**: Bookish optical-size serif titles with IBM Plex Sans body and Plex Mono chrome. Lost: mixed on On theme, Runtime cost.
- **E · Recursive casual**: One variable family for everything: casual brushy titles, linear UI and a true mono via axes. Lost: mixed on Usability, Runtime cost.
- **F · Fredoka · Nunito rounded**: Rounded Fredoka titles, Nunito UI and heavy rounded caps for chrome; mono only for kbd. Lost: mixed on Usability.
- **G · Space Grotesk · Space Mono**: Retro-futurist grotesk for titles and UI with the wide Space Mono for chrome and figures. Lost: poor on On theme.
- **H · All mono**: Heavy narrow Iosevka titles and Commit Mono for body, chrome and numbers, lowercase chrome. Lost: poor on Usability.

Discussion:
- *user*: i want to include space grotesk
- *agent*: Recorded Fraunces + Inter + JetBrains Mono. Space Grotesk can join as a fourth face, but only in one job, or the type gets noisy. **Q64** asks which job (UI text, mid-level headings or numbers). The live specimen is pairing G in the [type lab](http://127.0.0.1:5197/lab.html#type:type-pairing).

### Q4 · How much kitty motif should the app carry overall?

Sets the overall dose of blossom and cat marks across every page: list bullets, dividers, empty states, loaders, focus rings and the background. The other motif decisions follow from it (a 'wordmark only' pick makes most of them moot). Compare them live: [lab → motif-intensity](http://127.0.0.1:5197/lab.html#motif:motif-intensity). Letters match the lab cards.

**Decision: C · Medium (accepted the recommendation).**

| Option | On theme | Usability | Legibility | Runtime cost |
|---|---|---|---|---|
| A · Wordmark only | mixed | good | good | good |
| B · Subtle accents | good | good | good | good |
| **C · Medium** | **good** | **good** | **good** | **good** |
| D · Full | good | mixed | mixed | mixed |

Chosen option(s):
- **Medium** — Subtle accents plus illustrated empty states, a petal spinner and petal-corner focus brackets.

Why: Medium puts the motif where the app is idle or waiting (empty panels, loading, focus) and leaves working content plain, so kitty! feels like itself without the motion and clutter of Full. Its weakest axis is kitty compared with D: it gives up the ambient petals and cat ears that make Full instantly recognisable, but those cost focus during study sessions and some battery, and can be added later as an opt-in background.

Rejected options:
- **A · Wordmark only**: Today's look: the tiny ASCII blossom after the wordmark is the only motif; everything else is plain chrome. Lost: mixed on On theme.
- **B · Subtle accents**: Blossom bullets that bloom when a task is ticked and petal dividers between sections, nothing else. Lost: rated good throughout; it lost on preference, not on rated weakness.
- **D · Full**: Medium plus ambient drifting petals behind the glass, cat ears on panels and a paw trail in the top bar. Lost: mixed on Usability, Legibility, Runtime cost.

### Q18 · What should kitty's home page look like?

Controls the first screen kitty opens (Plan › today). Today it is the atlas graph with a Workspace side panel, and the Dashboard (reminders, deadlines, weekly goals) is hidden inside a collapsed Today block. The answer decides whether home is a map, a dashboard or both, and which widget decisions below matter. Compare them live: [lab → home-layout](http://127.0.0.1:5197/lab.html#home:home-layout). Letters match the lab cards.

**Decision: C · Atlas centre, widget rails.**

| Option | On theme | Usability | Legibility | Runtime cost |
|---|---|---|---|---|
| A · Atlas + side panel | good | poor | good | mixed |
| B · Bento widget grid | mixed | good | good | good |
| **C · Atlas centre, widget rails** | **good** | **good** | **mixed** | **mixed** |
| D · Today timeline + small atlas | mixed | good | good | good |
| E · Atlas / Dashboard tabs | good | mixed | good | mixed |
| F · Customizable grid | mixed | mixed | mixed | good |

Chosen option(s):
- **Atlas centre, widget rails** — The atlas stays in the middle with two narrow scrolling rails of widgets on either side.

The interview recommended D: kitty is a study + planning workbench, so the first screen should answer 'what is my day' without clicks; the timeline puts events, focus done, reminders and loose tasks on one axis with a now-line, and the small atlas keeps graph navigation one click away. Its weakest axis is kitty: the atlas, the app's signature, shrinks to a thumbnail and stops being the front door, so exploring the map needs a click into it (or pair D with E's switch later). It also gives up B/F's room for many widgets: only two fit beside the timeline. The user chose differently, so the recommendation's reasoning is kept here only as context.

Rejected options:
- **A · Atlas + side panel**: Today's home: full-size atlas with the Workspace panel; Today readouts and the Dashboard sit in a collapsed details block. Lost: poor on Usability.
- **B · Bento widget grid**: Dashboard-first: a greeting strip over an uneven grid of widgets; the atlas moves to its own view. Lost: mixed on On theme.
- **D · Today timeline + small atlas**: A tall timeline of the day (events, finished focus blocks, reminder pins, unscheduled tasks, now-line) beside a small atlas and two widgets. Lost: mixed on On theme.
- **E · Atlas / Dashboard tabs**: Both full-size views behind one segmented switch (1 / 2, arrow keys) that remembers the last tab. Lost: mixed on Usability, Runtime cost.
- **F · Customizable grid**: A widget grid you arrange yourself: drag tiles by a grip or with arrow keys, cycle 1×1/2×1/1×2, hide and re-add widgets. Lost: mixed on On theme, Usability, Legibility.

### Q32 · How should the Study hub be laid out?

Controls how the Study view (Ctrl + X → Study) arranges the timer, subjects, tasks, the 14-step guide, notes and due returns. Today it is one long column with five of nine tabs hidden in 'Planning & tools'. The layout decides which of the backend's unused data (weekly streak, subject colours, spaced returns, exam plans) gets a home. Compare them live: [lab → study-layout](http://127.0.0.1:5197/lab.html#study:study-layout). Letters match the lab cards.

**Decision: B · Cockpit · three columns (accepted the recommendation).**

| Option | On theme | Usability | Legibility | Runtime cost |
|---|---|---|---|---|
| A · Single column + tabs | mixed | mixed | good | good |
| **B · Cockpit · three columns** | **good** | **good** | **mixed** | **good** |
| C · Bento desk | good | good | good | good |
| D · Focus-first | good | mixed | good | good |
| E · Day planner beside timer | good | good | mixed | good |
| F · Subject tree + content | mixed | good | good | good |

Chosen option(s):
- **Cockpit · three columns** — Subjects and tasks left, the live timer centre, guide steps, session notes and due returns right.

Why: The cockpit puts everything a session needs (what to study, the clock, how to study it, what is due) on one screen without hiding half the hub behind a disclosure, and it gives subject colours and spaced returns a permanent place. Its weakest axis is legibility: three busy columns are dense, and in a split pane it has to collapse to one column, which loses the at-a-glance advantage. It gives up the calm of D (worth keeping as the existing Focus mode toggle) and the data-dashboard feel of C.

Rejected options:
- **A · Single column + tabs**: Today's header, tab row with a 'Planning & tools' disclosure, then the timer card and guide stacked below. Lost: mixed on On theme, Usability.
- **C · Bento desk**: A grid of tiles: timer, today with subject bars, streak, current guide step, returns due and exam readiness. Lost: rated good throughout; it lost on preference, not on rated weakness.
- **D · Focus-first**: Only a large timer; tasks, guide and progress slide in from edge drawers. Lost: mixed on Usability.
- **E · Day planner beside timer**: Today's timeline of lectures, study blocks and returns next to the session timer and the next tasks. Lost: mixed on Legibility.
- **F · Subject tree + content**: A subjects → concepts tree on the left; the right shows that concept's session, plan and returns. Lost: mixed on On theme.

## Routine choices

### Shell & navigation

- **Q7 · What should the command palette (Ctrl K) look like?** (lab: `shell-palette`) → **Spotlight minimal** — macOS-style: one large rounded field with nothing else until you type, then six quiet results with the top hit emphasised; no sections, footer or hints (accepted the recommendation). Because it has no sections or hints, it depends on Ctrl K's existing ranking of views, actions and jobs.
  - User note: “i want it to look more like mac and minimalistic though”
  - Agreed handling: option F was added to the lab for this note and became the recommendation.
- **Q5 · How should the group switcher (Plan / Study / Job search / Settings) look?** (lab: `shell-groups`) → **Segmented mono pill** (today's look) — Four mono-caps segments in one hairline pill; each jumps to the group's first view.
- **Q6 · How should workspace tabs look?** (lab: `shell-tabs`) → **Underline tabs** — Flat text tabs with group-coloured icons, a glowing underline, and close on hover.
- **Q8 · Where should the herdr terminal and agent states live?** (lab: `shell-dock`) → **Bottom panel** (today's look) — A full-width glass panel with a drag divider, a Terminal/Agents toggle and a 10px handle when hidden.
  - User note: “i want floationg pill to be an option though”
  - Agreed handling: Recorded the bottom panel as the default. The floating pill becomes a dock mode you can switch to (Terminal, Agents, Floating pill, Hidden), and the choice is remembered like the dock mode is today (`kitty.dock`).
- **Q63 · How command-first should kitty! be?** (lab: `shell-layout`) → **Ctrl K button as today** (today's look) — the palette stays one button among the others.

### Fonts & type

- **Q9 · How should the "kitty!" wordmark be lettered?** (lab: `type-wordmark`) → **Rounded lowercase** — Fredoka lowercase next to the cat in a filled pastel squircle, app-icon style.
- **Q10 · How should small chrome and label text be set?** (lab: `type-chrome`) → **Serif italic eyebrows** — Pink Newsreader italic for eyebrows, tabs and field labels; Inter for buttons.
- **Q11 · What type scale and density should kitty! use?** (lab: `type-scale`) → **Compact · 13px** — 13px body, 28px rows and tighter padding for about 20% more on screen.
- **Q12 · How should numbers look in timers and stats?** (lab: `type-numerals`) → **Inter tabular** (today's look) — Inter with tabular figures for the timer, stats and columns.
- **Q64 · What job should Space Grotesk do next to Fraunces, Inter and JetBrains Mono?** (lab: `type-pairing`) → **Mid-level headings** — Space Grotesk replaces Inter Tight for h3, widget and panel titles, deck and card titles; Fraunces stays for page titles, Inter for body.

### Kitty motif

- **Q13 · How should empty states look?** (lab: `motif-empty`) → **Sleeping cat** — A line-art cat curled up asleep on a cushion with drifting z's, then title, context and actions.
- **Q14 · How should loaders and progress look?** (lab: `motif-loader`) → **Blinking cat eyes** — Glowing eyes blink and glance inside a dark cat silhouette; the bar has the kitty icon riding its tip.; **Skeleton shimmer** — Placeholder rows shimmer where content will appear, with a shimmering progress fill.
- **Q15 · Which icon style should kitty! use?** (lab: `motif-icons`) → **Thin + blossom accents** — Hairline 1.25 strokes where one detail per icon (a knob, a nose, a cursor) becomes a tiny blossom.
- **Q16 · What should sit behind the app?** (lab: `motif-background`) → **Nebula gradients** (today's look) — Today's look: four soft radial washes drawn from the graph palette.
  - User note: “a but you can set to b”
  - Agreed handling: Recorded: the nebula stays the default, and Petal drift (B) is a setting in Appearance. It's paused under reduced motion.
- **Q17 · How should keyboard focus and selection look?** (lab: `motif-focus`) → **2px accent outline** (today's look) — Today's ring: a solid 2px accent outline 4px out; selection is a wash with a tinted border.

### Home & widgets

- **Q19 · How should a home widget card be framed?** (lab: `home-widget-style`) → **Hairline minimal** — No fill or box, only a rule above each widget and a small Inter Tight title with the eyebrow on the right.
- **Q20 · Which widgets should live on home?** (lab: `home-widgets`) → **Today agenda** — Today's events with a live now-line, current-event progress and a countdown to the next one.; **Agent activity** — What each herdr workspace agent is doing now, with elapsed time and Approve for a blocked one.; **Quick note** — Capture a note from home with Ctrl+Enter; the last few notes stay visible.
  - User note: “exam countdown and what not shoyuld be ion the study part, and application and pipeline should be in apply ai session”
  - Agreed handling: Agreed and recorded: home keeps Today agenda, Agent activity and Quick note. Exam countdowns, heatmaps and study streaks move into Study; your Study picks already include countdown cards and goal rings. Application streak, weekly targets, the pipeline funnel and source health move into the job-search (applyai) section and only appear when that module is on.
- **Q21 · How should a streak be drawn?** (lab: `home-streak`) → **Segment ring** — One ring segment per day toward the next milestone, count in the centre, Log today fills the next segment.

### 2D nodes

- **Q22 · How should 2D graph nodes look?** (lab: `nodes2d-shape`) → **Mini cards** — Heptabase-like cards with a mono kind eyebrow, title, coloured spine and soft shadow.
- **Q23 · How should links between 2D nodes be drawn?** (lab: `nodes2d-edges`) → **Curved arcs** — gentle quadratic arcs with a source-to-target colour gradient and a small arrowhead (accepted the recommendation).
- **Q24 · How should 2D node labels be drawn?** (lab: `nodes2d-labels`) → **Collision-avoided** — Labels search eight positions around each node, step outward with a leader line, and hide when nothing fits.
- **Q25 · How should node groups be outlined in 2D?** (lab: `nodes2d-groups`) → **Colored labels only** — No regions; a tracked mono group name with a short underline above each cluster in the group colour.
- **Q26 · What should hovering or selecting a 2D node do?** (lab: `nodes2d-focus`) → **Neighbours lift** — Focus and neighbours grow and rise on a soft shadow with a top highlight while the rest recedes.

### 3D nodes

- **Q27 · What should 3D graph nodes be made of?** (lab: `nodes3d-material`) → **Lambert spheres** (today's look) — lit Lambert hubs, flat unlit leaves, framed sprite labels.
- **Q28 · What atmosphere should surround the 3D graph?** (lab: `nodes3d-scene`) → **Starfield nebula** — a procedural pastel nebula dome plus twinkling stars that parallax as you orbit.
  - User note: “i want whats already there for 3d but i also want the nebula option as the backgrund”
  - Agreed handling: 3D keeps today's look and gains the starfield nebula background. Its colours come from the theme graph palette (`--c1..--c6`), so it follows theme changes; the twinkle stops under reduced motion.
- **Q29 · How should links be drawn in 3D?** (lab: `nodes3d-links`) → **Hairlines** (today's look) — 1px lines in the source colour at about 20% alpha, 34% between hubs.
- **Q30 · How should node labels look in 3D?** (lab: `nodes3d-labels`) → **Sprite frames** (today's look) — canvas-drawn frames, rounded for hubs and HUD-ticked for leaves.
- **Q31 · How should the selected 3D node stand out?** (lab: `nodes3d-select`) → **Scale + dim** (today's look) — the focused node grows and non-neighbours dim.

### Study hub

- **Q33 · What should the study timer face look like?** (lab: `study-timer`) → **Progress ring** — A thin ring in the phase colour drains around the digits, with dots for completed rounds.
- **Q34 · How should the running timer show on other pages?** (lab: `study-focusbar`) → **Mini ring in top bar** — A 20px progress ring and mm:ss in the top bar that opens a popover with controls.
- **Q35 · How should the 14-step study guide be presented?** (lab: `study-guide`) → **Phase columns** — Prepare, During and Return as three columns of compact step cards you click to tick, with hints on demand.
  - User note: “d as an option houghj”
  - Agreed handling: Recorded phase columns as the default, with the checklist rail (D) as an alternate view you can switch to, remembered per user.
- **Q36 · Which progress views should the Study hub show?** (lab: `study-progress`) → **Subject-stacked bars + ring** — Week bars stacked in subject colours beside a share ring, with hover to isolate a subject or day.; **Calendar with subject tint** — A month calendar where each day is tinted by its main subject and deepened by minutes, with weekly-goal checks.; **Goal rings** — Concentric rings for today, this week and flashcard reviews with numbers beside them.
- **Q37 · How should exam plans and spaced returns be shown?** (lab: `study-planner`) → **Timeline to the exam** — Two weeks on one axis with a lane per exam, clickable blocks, the exam as a blossom and a spaced-returns lane.; **Calendar strip** — Twelve day columns with stacked blocks, exams and returns; drag blocks between days with a load warning.; **Countdown cards** — A card per exam with days left, a readiness ring, blocks left and one action.; **Returns queue** — Due / This week / Later lanes where Recalled climbs a 1·3·7·16·35-day ladder and Shaky resets it.

### Flashcards

- **Q38 · What should the flashcard review card look like?** (lab: `cards-review`) → **Stacked deck, swipe away** — A physical pile of cards: flip the top one, then swipe or press 1–4 to send it off in a grade's direction.
- **Q39 · How should you grade a card after revealing it?** (lab: `cards-grades`) → **Keyboard strip** — Real keycaps for 1–4 and Space that visibly press down, teaching the shortcuts as you use them.
- **Q40 · How should the deck overview look?** (lab: `cards-decks`) → **Tiles with due rings** — A glass tile per deck whose ring splits today's load into review, learning and new, with Review appearing on hover.
  - User note: “b with an option of e, but also make b less cluttered.”
  - Agreed handling: Recorded due-ring tiles, decluttered: one ring, deck name and due count on the tile, with retention and new-card counts on hover. The graph cluster (E) is an alternate view toggle.
- **Q41 · Which memory stats should Flashcards show?** (lab: `cards-stats`) → **Due forecast · 30 days** — Stacked bars of review and learning cards due on each of the next 30 days, with overdue folded into today.; **Retention curve** — Predicted FSRS forgetting curves for weak, medium and strong cards against the 90% target, with measured recall dots and a hover probe.; **Maturity donut** — A donut splitting the collection into new, learning, young (under 21 days) and mature cards.; **Daily review heatmap** — 26 weeks of daily review counts with the current streak, replacing today's hidden 90-day bar chart.; **Today's queue counts** — Today's learning / review / new split as a bar and counts, plus suspended, leech and buried chips and a time estimate.
- **Q42 · How should the card browser look?** (lab: `cards-browser`) → **Split list + preview** — A filterable list beside a detail pane holding a flippable preview, memory-state meters, the last reviews and Suspend/Bury actions.
  - User note: “also adding cards should be very easy”
  - Agreed handling: Recorded split list + preview. Making card creation easy is a separate choice of entry points, so **Q66** asks which ones you want.

### Notes & maps

- **Q43 · How should the Notes page be laid out?** (lab: `notes-layout`) → **Tabs + floating outline** — Browser-style page tabs, the library as a slide-over drawer, and a floating 'on this page' outline that jumps to blocks.
- **Q44 · What should a mind-map node look like?** (lab: `notes-mapnode`) → **Rounded pills** — Compact capsules with a branch-colored dot and an inverted ink root pill; about twice as dense as A.
  - User note: “but also research mind maps and hwo they are good and iw ant every apablity like, i want you to be able to easily change colors and move things around and edit it and break it”
  - Agreed handling: Recorded rounded pills. I looked at MindNode, the reference editor for keyboard-first maps ([shortcut guide](https://www.mindnode.com/support/guides/keyboard-shortcuts)). Its strengths are building without the mouse (Tab adds a child, Enter a sibling, arrows move between nodes), dragging to reconnect (press **d** mid-drag to detach a branch, Option-drag to place freely), one-step folding, cross connections, and copy/paste of style. kitty! already has collapse, drag-to-reparent, inline rename, recall mode and tree/force/radial arrange. It lacks easy recolouring, detaching ('breaking') branches, free positioning, multi-select and cross links. **Q65** lists every capability; I recommend all of them, as you asked.
- **Q45 · How should the Quick notes board look?** (lab: `notes-quick`) → **Auto-fill grid of glass cards** (today's look) — Today's 260px-wide, 230px-tall glass cards with hard-coded rose/lilac/sky/mint/honey/frost tints and fading tools.
- **Q46 · How should the canvas toolbar work?** (lab: `notes-canvas-toolbar`) → **Floating bottom pill** — A single floating dock at the bottom with tools, four inks, a stroke-size toggle and undo/redo.
  - User note: “c and you can close it in and out but pressing the botom, specifically kinda like how the terminal option i picked.”
  - Agreed handling: Recorded the floating bottom pill. It collapses to a small handle when you click its bottom edge and comes back the same way, like the dock you picked. The state is remembered.
- **Q47 · How should Documents & PDF be laid out?** (lab: `notes-docs`) → **Side-by-side source / preview** — A compact bar with a document switcher and Export PDF over a resizable, scroll-synced split of numbered Markdown source and preview.
- **Q48 · Which colors should the canvas and stickies use?** (lab: `notes-palette`) → **Hard-coded 12 swatches** (today's look) — Today's fixed hex swatches (Paper … Red) with recent colors and a hex field, plus a fixed dark mind-map sticky.
  - User note: “but also make the color selecting look better, moreover, i want the canvas background to be just black and clean.”
  - Agreed handling: Recorded the 12 swatches. A better-looking picker is **Q67**. 'Canvas background just black and clean' is **Q68**, which asks whether that means true black or kitty's near-black plum.
- **Q67 · How should picking a colour look on the canvas and stickies?** (lab: `notes-palette`) → **Round swatch row with recents** — the floating toolbar shows your last 5 colours as round dots; a + opens all 12 with names.

### Calendar

- **Q49 · How should events look in the week view?** (lab: `calendar-week`) → **Filled pastel blocks** — solid pastel blocks from the theme palette with dark text and a blossom now-marker (accepted the recommendation). Check dark-text contrast on themes with darker graph colours (Gruvbox, One Dark).
- **Q50 · What should a month-view day cell show?** (lab: `calendar-month`) → **Dots + day agenda** — A calm grid of dates with up to four calendar dots; clicking a date fills a side agenda with times and places.
- **Q51 · How should you switch views and move through time?** (lab: `calendar-switcher`) → **Floating range pill** — A glass capsule floating over the bottom of the calendar holds the range and the D W M A letters.
  - User note: “but i also want keybinds like shown in c”
  - Agreed handling: Recorded the floating range pill plus C's single-key shortcuts (D/W/M/A for views, J/K to step, T for today) while the calendar has focus.
- **Q52 · Which new calendar views should kitty! add?** (lab: `calendar-new-views`) → **Year overview heatmap** — Twelve mini months with each day tinted by booked hours, a busyness bar per month, and hover/pin readouts.; **Mini-month navigator** — A small month with ISO week numbers; hovering a week previews its load as bars and clicking jumps there.; **Today: plan split with tasks** — Today's timeline next to the task list; Plan drops a task into the next free gap as a dashed block.
- **Q53 · How should creating and editing an event work?** (lab: `calendar-editor`) → **Side drawer editor** — Clicking an event slides in a glass drawer next to the calendar; title, time steppers, color, place and reminders update the block live.
  - User note: “d but it does the natural language and picks up on it too,”
  - Agreed handling: Recorded the side drawer, and its title field parses natural language: typing 'lunch with Mira fri 12:30' fills the date, time and title, and shows what it understood before you save.
- **Q54 · How should calendars get their colors?** (lab: `calendar-colors`) → **Auto-assign by subject** — Events linked to a study subject take that subject's color, matching Study and Flashcards; unlinked events fall back to grey.
  - User note: “but if its unlinked you can still edit the color”
  - Agreed handling: Recorded auto-assigned colours by subject. A calendar or event that isn't linked to a subject keeps a colour picker, using the theme palette.

### Unbuilt features

- **Q55 · How should the application pipeline work?** (lab: `gaps-pipeline`) → **Swimlanes by track** — Rows are application tracks and columns are open stages; dragging between cells changes stage or track.
- **Q56 · How should agent activity look in the dock?** (lab: `gaps-agents`) → **Live status cards** — One card per agent with its task, last output line, a 30-second sparkline, a running timer and Open pane when blocked.
- **Q57 · What should you be able to do with a reminder?** (lab: `gaps-reminders`) → **Inline actions + swipe** — Hover reveals edit, snooze a day and delete; swiping right snoozes and left deletes, with an undo pill.
- **Q58 · How do you show and hide sections on the atlas?** (lab: `gaps-atlas-sections`) → **Drag list ↔ map** — As designed: drag a row onto the map to show it and drag a node back to hide it; hidden rows stay dimmed with a tag.
- **Q60 · How should kitty! notify you?** (lab: `gaps-notify`) → **Toast stack** — Glass toasts from every source stack bottom-right with Snooze and Open; hover fans them out and pauses the timer.; **Bell + notification center** — A bell with an unread count opens a drawer with All/Unread tabs, sources and mark all read.; **Quiet hours** — A draggable 24-hour dial that holds notifications overnight, lets accepted events through and delivers a morning digest, even when kitty is closed.
- **Q61 · How should job-search reports be shown?** (lab: `gaps-reports`) → **Narrative summary** — One card that explains the week or month in sentences, with the streak, weekly total, an inline sparkline and a next step.
- **Q62 · How should Google calendar conflicts be resolved?** (lab: `gaps-google-conflict`) → **Ghost blocks on the day** — Both versions drawn on the day grid, kitty's solid and Google's dashed; tap one to keep it while a list strikes out what you lose.
- **Q59 · What belongs in Settings → Appearance?** (lab: `gaps-appearance`) → **One panel, live preview**, with presets — theme, title font, density, motion, atlas look and blossom motif in one panel with a live preview, plus Cozy, Focused and Minimal preset buttons that set everything in one click (equivalent to option E). It also holds the Petal drift background toggle from Q16.
  - User note: “B but you can have cozy focused and minimal presets”

### Follow-ups raised during the interview

- **Q65 · Which mind-map editing capabilities should kitty! have?** → **Keyboard building** — Tab adds a child, Enter a sibling, arrows move the selection, Ctrl+↑/↓ reorder siblings, Ctrl+Enter edits.; **Drag with snap and detach** — while dragging, the drop target lights up; press D mid-drag (or the node menu's Detach) to break a branch off as its own floating map piece.; **Free positioning** — Alt-drag places a node anywhere, out of the auto layout; 'Reset position' snaps it back.; **One-click recolour** — a swatch row on the selection recolours a node or its whole branch; copy and paste style between nodes.; **Cross connections** — link any two nodes with a labelled, curved line outside the tree.; **Multi-select and bulk edit** — Shift+arrows or a drag box selects several nodes to move, recolour or delete together.; **Undo and redo everywhere** — Ctrl+Z / Ctrl+Shift+Z for every map edit, including drags and recolours.; **Split and merge maps** — turn a branch into a new map (linked back) or drop one map's root into another.
  - User note: “i also want to make it clear that mind maps will be pen compadability”
  - Agreed handling: Recorded: mind maps must work with a pen. Today pen handling exists only on the Notes canvas (its pen-mode preference); the mind-map editor is mouse and keyboard only. **Q69** lists what pen support covers. Whatever you pick, the editor will use pointer events throughout, so pen, mouse and touch share one code path.
- **Q66 · How should adding flashcards be made easy?** → **Inline add row** — a row at the top of the card browser: type the front, Tab, the back, Enter to save and start the next.; **Quick add anywhere** — N in Flashcards or 'New card' in Ctrl K opens a small front/back popover over the current page, deck preselected.
- **Q68 · What exactly should 'black and clean' mean for the canvas background?** → **kitty near-black** — the theme's flat void colour (on Kitty a very dark plum, #1b151a), no nebula, no grid, so it changes with the theme.
- **Q69 · What should a pen be able to do in mind maps?** → **Pen as a precise pointer** — tap selects, drag moves and reparents, double-tap edits; the screen ignores your palm while the pen is near.; **Ink on the map** — pressure-sensitive strokes drawn over the map, each attached to the nearest node so it moves and folds with it.; **Lasso select** — holding the barrel button and drawing a loop selects every node inside for move, recolour or delete.; **Eraser end** — flipping the pen erases ink strokes (never nodes) under it.

## Verified facts

- Home today is the Atlas graph (`GraphNavigator`) with a 280–320px Workspace panel; the Dashboard is a collapsed `<details>` inside that panel and only appears with the applyai module (`GraphNavigator.svelte:408-552`, `Dashboard.svelte`).
- There is no sidebar; navigation is the top bar's group pills (each opens only the group's first view), browser-style workspace tabs, Ctrl K, and the Atlas (`App.svelte:325-371`, `workspace.ts:52-84`).
- Two type layers fight in `styles.css`: the base layer (Inter UI, Inter Tight headings) and a later 'LearnHarness look' layer (CMU serif bold h1/h2 via `/fonts/cmunbx.ttf`, JetBrains Mono uppercase chrome at 10.5–11px, pill buttons, glass panels, nebula background) from about line 1206.
- Every graph goes through `mountGraph()` in `graph3d.ts:510-555`, which swaps a Canvas d3-force 2D renderer (`graph2d.ts`) and a three.js/3d-force-graph 3D renderer. Node kind changes only hub vs leaf; colour comes only from `group`; 2D labels have no collision handling; 3D ignores theme changes; raw group ids such as `DECK:12` reach 2D hull labels.
- The mind-map editor is a separate SVG (`SchemaMaps.svelte`) with 200×64 rect nodes, tree/force/radial arrange, collapse, recall mode and drag-to-reparent; limits are 300 nodes and 600 edges (`core/schema_maps.py`).
- The backend has FSRS-6 scheduling, per-card history, learning/review counts, weekly streak, subject colours and timer cycle/segments that the UI never shows (`core/flashcards.py`, `core/study.py:511-576`).
- Backend routes with no frontend caller: `POST /api/calendar/availability`, `GET /api/schema/export`, `GET /api/notes/pages/{id}/export`, `GET /api/notes/pages/{id}/mentions`, `POST /api/questions/refresh`.
- The application streak and weekly totals are sent in bootstrap (`web_server.py:265`, `api.ts:99-100`) but never rendered.
- Hard-coded colours outside the theme: canvas swatches in `CanvasColorMenu.svelte:14-16`, mind-map sticky `#292438`, new-calendar default `#6d5dfc`, LearnHarness iframe `#111`.
- `@nunera/bracket` exports far more than kitty uses (kitty uses Button, Panel, Badge, Popover, BarChart and applyTheme); unused relevant parts include Calendar/EventScheduler, Kanban, CalendarHeatmap, ProgressCircle, Sidebar, Tabs, Switch, Slider, EmptyState, Skeleton, ToastRegion and NotificationCenter.
- The 3D lab previews render correctly in Chromium and in a cleanly launched Firefox (checked headless). A Firefox launched from the agent's terminal inherits the kitty! desktop app's environment (`GDK_BACKEND=x11`, a bundled `LD_LIBRARY_PATH`), and WebGL fails to start in it.
- The lab type-checks clean (`npm --prefix web run check`: 0 errors) and adds 15 local `@fontsource` packages as devDependencies, imported only by `web/src/lab/type/fonts.ts`.

## Risks

- **Four typefaces**: Fraunces (titles), Space Grotesk (mid-level headings), Inter (UI, body, numbers) and JetBrains Mono, plus serif italic eyebrows (Q10). Without one documented job per face the UI turns noisy. Font payload grows by roughly the Fraunces and Space Grotesk files; CMU (222 KB) can be dropped.
- **Compact 13px scale (Q11)** with serif italic eyebrows lowers small-text legibility; check every chrome label at 13px on non-retina displays.
- **Motif at medium (Q4)**, applied as the default theme (Q1), must not leak into the other eight themes: gate motif-only elements (blinking cat eyes, thin blossom icon accents, sleeping-cat empty states) so they still look intentional off-Kitty.
- **Type-anywhere was declined (Q63 → A)**, so command-first relies on Ctrl K plus the Spotlight-style palette if Q7 lands on F; ranking quality becomes the main usability risk.
- **Mind-map scope (Q65 all eight capabilities + Q69 pen)** is the largest single build: free positioning, detach, cross links, multi-select, undo, split/merge and ink layers all touch the `SchemaMaps.svelte` data model (positions, styles, strokes) and need a backend schema change in `core/schema_maps.py`.
- **Swimlane pipeline (Q55)** with drag between stages writes application status; it must keep the append-only status history and undo the app already has, and job content stays demo-safe in the demo workspace.
- **Floating pieces** (dock floating-pill mode, canvas floating toolbar, toasts, reminders box) need one stacking order, or they will overlap on small windows.
- **3D keeps today's renderer** (Q27, Q29–Q31) and adds only the nebula background (Q28). The nebula dome has to be drawn behind the graph and recoloured on the `kitty:theme` event; today `graph3d.ts` has no theme listener, so that has to be added.
- The design lab depends on the Vite dev server; the links in this doc only work while `npm --prefix web run dev -- --port 5197` runs in the worktree.

## Deferred

None. Every question was answered.

## Open threads

- The 3D previews didn't render in the user's Firefox because that Firefox inherited the desktop app's graphics environment. The user then picked the 3D options without needing the previews (today's look plus the nebula).
- Pen support on Linux (pressure, tilt, barrel button, eraser end) was assumed to come through pointer events in both Firefox and the desktop app's webview; this was not verified on the user's tablet.
- Space Grotesk's exact weight range and whether the CMU serif file is removed once Fraunces lands were not discussed.
