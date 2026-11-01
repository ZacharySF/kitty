# kitty! user and operator guide

The full reference: setup, every module, the desktop app, configuration and the
database. The [README](../README.md) is the short overview.

A local workspace with three optional built-in modules: **ApplyAI** for job discovery,
application preparation and tracking, **Workspace** for notes, documents, study and
planning, and **Terminal** for kitty!'s own bundled herdr terminal session. The shell
and settings work with any combination of modules, or none.

**Two rules the code enforces, not just documents:**

1. It never submits an application. It opens pages; I submit.
2. It never invents qualifications or facts about the applicant. Screening facts
   come from `config/answers.yaml` and the saved profile. Recruitment-source choices
   may use a plain offered source when the saved wording is unavailable. The local
   resume editor creates bounded, fact-preserving candidates for explicit review;
   it never silently promotes them.

Stages 1-7 are built: tracker, discovery, LLM triage, the daily flow and apply
session, a niche-company finder, Gmail status proposals with reports and export,
and an MCP server for my agent.

## Layout

```
config/           yaml I edit; *.example files are committed, the real ones are gitignored
data/             apply.db (real) and demo.db (throwaway) - gitignored
resumes/          my resume files - gitignored
src/kitty/
  core/           all behaviour, no UI imports - the CLI, TUI and MCP server call this
  db/             schema migrations and the connection
  tui/            Textual screens
  cli.py          the command line
  mcp_server.py   the MCP server (`kitty mcp`), a thin layer like the CLI
  web_server.py   loopback-only website adapter (`kitty-web`)
tests/            pytest, including headless TUI tests
web/              Svelte 5 / TypeScript website; built assets in ignored web/dist/
```

The layout rule: nothing under `core/` imports Textual or Typer, so the CLI, the
TUI and the MCP server all call the same functions.

## Setup

```bash
uv sync                         # create .venv and install
uv run kitty init --yes          # new workspaces start with the core only
uv run kitty modules list
uv run kitty modules enable applyai workspace
uv run kitty doctor             # check the enabled modules
```

`init` never overwrites existing configuration. To enable modules during initial
setup, use `kitty init --module applyai --module workspace`; `--yes` skips profile
questions. Enabling ApplyAI installs missing generic configuration defaults;
applicant fields remain blank until you supply facts.

Only generic `config/*.yaml.example` templates are shared. Existing local YAML is
preserved. Keep applicant facts, preferences, answers, watchlists and source
selections in ignored `config/*.yaml`.
`PRIVATE_CONTEXT.md` is an optional ignored local notebook for personal context;
it is not application configuration. Never commit it, `.env`, resumes, databases,
exports, cached responses, discovered jobs or application history.

```bash
source .venv/bin/activate    # then just `kitty ...`
```

## Modules

| Module | Features |
| --- | --- |
| ApplyAI (`applyai`) | Jobs, discovery, companies, sources, profile, resumes, screening questions, preparation/recovery, applications, career reminders, mail and reports |
| Workspace (`workspace`) | Notes, documents/PDFs, canvas, mind maps, backlinks, flashcards, study, calendar, tasks and projects/opportunities |
| Terminal (`terminal`) | kitty!'s own [herdr](https://github.com/herdrdev/herdr) session (workspaces, tabs, splits, agents) streamed into the window; your installed [Stencil Tern](https://stencil.so/tern), if any, opened on a session of kitty!'s own |
| Always-on core | Home, tabs, settings, module selection, shared AI-provider/budget controls and local storage infrastructure |

Use **Settings → Modules**, the terminal UI's Modules screen (`M` from the job
tracker), or `kitty modules enable/disable <id>`. Selection is stored locally:

```yaml
# config/modules.yaml
enabled: [applyai, workspace, terminal]
```

Save, then **quit and restart Kitty** to activate the change. Refresh/reload does
not unload running modules, restart workers or discard drafts. Settings shows
the active and saved selections separately. CLI invocations are separate
processes, so the next command uses the saved selection.

Disabled modules disappear from navigation, commands, shortcuts, atlas sections
and MCP tool listings; their routes/actions are refused and their workers do not
start. Their data, attachments and configuration are retained for re-enabling.
ApplyAI reminders work without Workspace; career calendar contributions and
cross-module results appear only while both modules are enabled.

Existing workspaces without `modules.yaml` retain every module. Fresh CLI and
desktop workspaces start core-only. The database still uses the existing global
migrations; disabling does not drop tables or run down-migrations. These are
built-in modules, not installable third-party extensions.

### Terminal module

The desktop app bundles a pinned herdr (v0.9.5, Apache-2.0; its licence ships as
`licenses/herdr/LICENSE`) and never uses a herdr found on `PATH`. kitty! runs it as
its own named session, `kitty` (never herdr's `default` session), so agents can
reach it with `herdr --session kitty`. Its sockets live where herdr keeps named
sessions, `~/.config/herdr/sessions/kitty/`: kitty! creates that directory
owner-only (0700) and refuses a session whose sockets are not owner-only (0600).
The Harness colours come from kitty!'s private `.private/herdr/config.toml`
(`HERDR_CONFIG_PATH`), so your own herdr configuration is neither read nor changed.
Nothing starts until a page opens a terminal. Closing the window with **Keep running
on close** leaves terminals running and reopening reattaches; **Quit kitty!** (or
closing the window without that setting, or Ctrl-C on `kitty-web`) stops kitty!'s
herdr session. With the module off there is no herdr process, socket or route.

The page reaches it through a WebSocket on kitty!'s own loopback origin
(`/api/terminal/socket`). Browsers cannot add `X-Kitty-Request` to an upgrade, so
the upgrade requires the exact Host and Origin plus a single-use token that expires
after 30 seconds and is issued only by the header-protected `POST
/api/terminal/session`. A development `kitty-web` uses `KITTY_HERDR=<herdr 0.9.5
binary>`; `KITTY_HERDR_SESSION` overrides the session name.

**Dock.** With the module on, the bottom of the window holds the dock: herdr's
client in a transparent terminal drawn in the Harness colours, **Agents** (one card
per detected herdr agent with its working / blocked / done / idle state, read from
`GET /api/terminal/activity`, which never starts herdr), or nothing but a thin
handle that brings it back. Drag the divider (or focus it and use the arrow keys) to
resize it; the state and height are remembered. The first size is the empty area
below the main panel. **Float** detaches the dock into a glass card in the
bottom-right corner that leaves the page its full height; hiding it there leaves a
small pill, and **Dock** returns it to the bottom. **Open herdr view**, or the **herdr** node under Plan in the
atlas, opens the whole session in the workspace tab. In a terminal, bare keys
(including `f`) go to the shell; **Ctrl+Shift+F** fullscreens the terminal panel and
**Esc** returns.

**Agents (Q2/Q9).** In either terminal surface, **New Agent tab** starts the chosen
installed coding agent in a new herdr tab labelled **Agent**, in this kitty!
workspace's directory. The chooser defaults to **omp**, also supports **claude**
and **codex**, and remembers the last choice in this browser profile. kitty! does
not install agents, configure their provider credentials, send them work, or
bypass their approval prompts. Open the terminal before starting an agent.
The header-protected `POST /api/terminal/agents` accepts only the selected kind;
herdr creates the tab and starts the agent in the exact returned pane. Failed or
blocked startup keeps the tab and reports its IDs rather than retrying or killing it.

The skill comes from the bundled binary's `--skill` output, saved owner-only in
`.private/herdr/SKILL.md`. omp and Claude receive it through their native appended
system-instruction file option; Codex receives a developer instruction to read and
follow that file. Only that tab's `PATH` gets a private `herdr` alias to the bundled
binary, so the skill's commands work without a separate herdr installation. No
global skill, agent configuration or existing project instruction file is changed.

**Attach external agent** shows the actual bundled binary, named session and
workspace directory (`GET /api/terminal/agents`; reading never starts herdr).
External agents use herdr's own CLI/socket, not a kitty!-specific control API:
list workspaces, create a separately labelled tab with `tab create --workspace
<id> --cwd <directory> --label <name> --no-focus`, then target the returned
`root_pane.pane_id` explicitly. Read herdr's own skill before controlling panes.
Never rely on another client's focused pane or switch to the `default` session.
Their tabs appear live in the terminal and herdr view, and **Agents** in the dock
shows one card per detected agent: its state, task (the pane's title), last output
line, a 30-second output sparkline and how long it has been working or blocked. A
blocked agent's **Open pane** focuses that pane and switches the dock to the
terminal. Local CLI/socket access grants full shell access as your user.

**Tern (optional).** With the module on, the **Tern** node under Plan shows
[Stencil Tern](https://stencil.so/tern) beside herdr. herdr stays the bundled
default; kitty! never bundles Tern (closed-beta, proprietary) and only runs the
`tern` you installed: the one on `PATH`, or the path saved in the view's **Tern
executable** section (`.private/tern/settings.json`, owner-only). Without it the view
says so and points to stencil.so/tern; nothing else changes. **Open in Tern** starts
kitty!'s own Tern daemon on a private socket,
`$XDG_RUNTIME_DIR/kitty/tern-<workspace hash>/daemon.sock` (directory 0700, a
socket others can reach is refused), gives it a `kitty` session in the workspace
and opens a native Tern window on it (`tern --dir <workspace>`); your own Tern
daemon and sessions are never read or changed. The view is a status panel (version,
daemon state, sessions, panes and open windows from `tern inspect --json`, which
never starts anything): Tern's web client needs WebGPU, which the desktop app's
WebKitGTK view lacks, so kitty! does not embed it. Scripts reach the session with
`TERN_DAEMON_SOCKET=<socket> tern ls`. Quit closes the Tern windows kitty! opened
and stops its Tern daemon (Tern restores the layout next time); closing the window
with **Keep running on close** leaves them running.


## Try it on fake data first

```bash
uv run kitty --demo demo reset    # ~30 invented jobs, all labelled "(demo)"
uv run kitty --demo tui           # learn the keys without touching real data
```

`--demo` uses `data/demo.db`. `demo reset` refuses to write to any file that is
not named `demo.db`, so it cannot wipe real applications.

## Local website

The Svelte website uses the same Python core and SQLite database as the CLI/TUI.
The workbench uses `@nunera/bracket` with a compact 13px type scale: Fraunces page
titles, Space Grotesk panel and card headings, Inter UI text and numbers, pastel
Newsreader italic labels, a Fredoka wordmark and JetBrains Mono for code and data
chrome. The Theme menu persists one of ten dark
themes locally: **Kitty** (the default: plum, pastel pink and a cherry-blossom
motif), Harness, Dracula, Catppuccin Mocha, Tokyo Night, Nord, Gruvbox, One Dark,
Scythe and **Liquid Glass** (clear, neutral glass like macOS/iOS: heavy blur and a
white rim lit from the top-left, no tint; in the desktop window your wallpaper shows
through; panels turn opaque under reduced transparency). Graph nodes and their
edges use the active theme's graph palette.
**Settings → Appearance** sets the whole look in one panel beside a live
preview: theme, title font (Fraunces or plain), density (compact 13px or
comfortable 14px), motion (full or reduced), atlas look (2D, 3D or Brain), the blossom
motif and the background (the nebula, or the nebula with drifting petals, which
rest under reduced motion). **Cozy**, **Focused** and **Minimal** set all of them
at once; any single control still changes afterwards. Choices are saved in this
browser (`kitty.appearance`, plus `kitty.theme` and `kitty.graph.look`) and
applied before the app draws.
No Rust service is needed: the Python core
owns application behavior.

From the project root, with Node 22+ and npm available:

```bash
npm --prefix web ci
npm --prefix web run build
uv run kitty-web                 # http://127.0.0.1:8765
```

On NixOS without Node on PATH, prefix either npm command with
`nix shell nixpkgs#nodejs_22 --command`.
Use the printed `127.0.0.1` address, not `localhost`: the server validates its
exact loopback Host/Origin. `--port`, `--root` and `--static-dir` override the port,
workspace and built-asset directory. The refresh button and command palette reload
configuration from disk; saves in My profile reload it automatically.

The first-run welcome offers **Continue locally** or an optional Google identity
connection, followed by a skippable guided tour of workspace panels, modules,
Atlas, quick notes and Study (plus the terminal dock when present). Completion is
remembered per workspace. Reopen either flow in **Settings → Welcome & account**.
Google is not required for local/offline use and does not secure the local API or
sync data. For the owner-created Desktop OAuth client, `KITTY_GOOGLE_CLIENT_ID`,
`KITTY_GOOGLE_CLIENT_SECRET`,
system-browser/PKCE flow and privacy boundaries, see
[Google connection and onboarding setup](docs/GOOGLE_ONBOARDING.md).

For safe exploration without touching real jobs, runtime YAML or resumes:

```bash
uv run kitty-web --demo --port 8766
```

Unlike CLI `--demo`, the website demo creates a disposable temporary workspace
from public examples; edits disappear when the process exits. External discovery,
browser filling, Gmail, notifications and paid operations are disabled. Synthetic
text resume placeholders correctly appear as unavailable PDFs; real test PDFs can
be registered inside the disposable workspace.

### Quick notes

**Study → Quick notes** is a board of small translucent scratch notes. Each note
saves as you type (no save button; `Ctrl+S` forces it), can be tinted, pinned to the
top, searched and deleted. Notes live in the workspace database (`quick_notes`),
separate from Notes & canvas pages.

**↗ Floating note** (or the pop-out icon on a note) opens one note on its own: in
the desktop app that is a small transparent, borderless, always-on-top window.
Its editor reveals the desktop with only a faint color wash near the top;
controls and save status keep a local backing for readability. Board cards and
browser popups retain their denser backgrounds. `Esc` closes it (already saved).
If the same note is edited in two windows, the stale one stops and offers
*Load latest* or *Keep mine* instead of overwriting.

In the desktop app, the tray menu has **New quick note**, and
`kitty-workspace --quick-note` opens one in the running app (or starts it), so a
compositor key binding can summon a note, e.g. in niri:
`Mod+N { spawn "kitty-workspace" "--quick-note"; }`. Note windows have no title
bar; move them with the compositor (niri: `Mod`+drag).

### Brain data (`/api/brain`)

`GET /api/brain?limit=<n>` (default 20,000, at most 50,000) returns the whole
workspace as one point cloud for the Brain view: every **record** (notebooks,
pages and their blocks, decks and card notes, calendar events and tasks, study
subjects, projects, tasks, exams and sessions, quick notes, mind maps, documents,
opportunities, jobs, applications and reminders) and every **edit** as a node with
a `parent` link. Edits are the website changes captured in `record_events` plus the
histories the database already keeps (flashcard reviews, application status
changes, study sessions, calendar changes made before capture began and, with the
block backend, its `op_log`). Labels are titles only, never note bodies, paths or
URLs; a change to something since deleted reads e.g. *Deleted page*. Sections of
disabled modules are left out. When the limit is reached, records are kept and the
newest edits fill the rest (`truncated: true`; `total` counts everything).

### LearnHarness extension

**Study → LearnHarness → Start LearnHarness** opens the included
[upstream LearnHarness app](https://github.com/amosblomqvist/learnharness)
(AGPL-3.0-only). Desktop installations include its production server and Node.js:
no source checkout, Git, npm, system Node, or manual service setup is required.
Teaching, practice, knowledge graph and settings remain the independent upstream
app, contained in a cross-origin frame at **http://127.0.0.1:3210**.

Start is explicit, not a login/boot service. Kitty stops only the learning server
it started when that Kitty instance quits. Learning records and provider
connections persist separately in the selected workspace's `data/learnharness/`
(`auth.json` and an isolated `home/`); nothing is imported from another checkout.
The bundled app in `kitty-web --demo` uses disposable demo storage. A healthy
independently started service on port 3210 is reused without modifying its storage
or stopping it. **Reconnect** only probes availability and reloads the frame.

The production payload is built from upstream **0.3.8**
(`3dc88472eea41eed0d5e5f29c2f16f8d063c415b`) in a fresh public temporary clone,
never a developer's learning checkout. `scripts/build-desktop.sh` runs
`desktop/package_learnharness.py`, applies the reviewed dependency-lock patch,
uses `npm ci`, builds Next's standalone output, and runs upstream's payload
guard against private data/auth/env files and missing prompt assets.
The desktop resources under `licenses/learnharness/` include the AGPL license,
corresponding `source.tar.gz`, exact dependency-lock patch and Node's licenses.
App source and `package.json` stay unchanged. The dependency patch resolves the
original critical package findings within upstream's manifest ranges, without
`--force`: Next.js 16.4.3, DOMPurify 3.4.17, Sharp 0.35.5, Zod 4.6.7 and related
transitives.
**Remaining upstream risk:** npm still reports two high package findings:
`ansi-to-react@6.2.6` pulls `linkify-it@3.0.3` with quadratic text/`mailto:`
scanning denial-of-service advisories
([GHSA-22p9-wv53-3rq4](https://github.com/advisories/GHSA-22p9-wv53-3rq4),
[GHSA-v245-v573-v5vm](https://github.com/advisories/GHSA-v245-v573-v5vm)).
The suggested `ansi-to-react@6.1.6` downgrade is breaking and is not applied.
Loopback and iframe containment do not make upstream content-processing risks
disappear; avoid untrusted imports and do not expose the server on a network.
These audit results describe the reviewed installation on 2026-10-27, not a
future guarantee.

Kitty checks availability with a bounded HEAD request to the fixed loopback
endpoint. An explicit protected `POST /api/extensions/learnharness/start` starts
only the bundled runtime; it accepts no URL, settings or credentials. There is no
data proxy or credential bridge. The server binds only IPv4 loopback, runs with
a clean environment and receives no Kitty provider keys, applicant data, resumes
or browser profiles. The iframe cannot navigate the parent or open provider-login
popups; use **Open in new tab** for provider connections.

No provider credentials/models are configured by installation. Generating lessons
requires your own LearnHarness provider key or supported connection and model
selection; requests can send learning context externally and may cost money.
Availability is not provider readiness. Installation and smoke verification make
no paid requests. Do not enter private applicant data.

Source development:

```bash
npm --prefix web ci
npm --prefix web run build
uv run python desktop/package_learnharness.py  # build the included learning runtime
uv run kitty-web --demo --port 8766            # disposable synthetic workspace
```

An independent source service remains optional:

```bash
./scripts/learnharness.sh setup
./scripts/learnharness.sh start
./scripts/learnharness.sh status
./scripts/learnharness.sh stop
# Without systemd: ./scripts/learnharness.sh run (Ctrl+C to stop)
```

That developer launcher uses `~/Projects/learnharness` with persistent storage in
its ignored `data/kitty-extension/`, isolated HOME and no inherited Kitty secrets.
It runs upstream's dev server; start again after reboot. It is not needed by the
desktop installer. Kitty demo does not reset a separately running service.

On NixOS, build the desktop in the native toolchain shell:

```bash
nix develop 'path:./desktop#host-toolchain' --command bash scripts/build-desktop.sh
```

Keep the native executable and bundled `kitty-web` sidecar together. The desktop
uses a single instance per session: verification must use an isolated workspace,
profile and session, not relaunch or replace an owner's running instance.


### Start here

**How to apply** under **Find jobs → Reminders, deadlines & goals** (a section
that stays closed until you open it, and remembers that) opens a three-step guide and checks
whether your name/email and a usable resume PDF are
saved. These checks describe saved data, not application readiness.

1. **My profile → My resumes:** enter your verified details in the ordinary form,
   review the save, then upload a resume PDF. Blank fields stay blank. Basic-form
   saves preserve other profile settings and reject stale edits; leaving with an
   unsaved profile, configuration or answer draft prompts before discarding it.
2. **Find jobs:** the list scrolls in 100-row batches; selections persist across
   batches and **Select all matching** covers the whole filter. Sort by best fit,
   similarity score, newest, job type (track), company or deadline. Jobs opens its
   own schema, showing only job-type categories at the root. Open a category to
   see every matching job, without 120-item map pages; its searchable Browse list
   progressively reveals rows. Uncategorized jobs remain available in List.
3. **Select jobs → press Shift+F:** marked jobs (otherwise the opened or focused job) go
   straight to the reviewed autofill plan. Approved preparation opens Firefox and
   fills known fields. Check each form and submit it yourself; kitty! never submits.

**Notes** opens today's dated note, reusing the same page on subsequent visits.
The library (☰, top left) slides over the page as a drawer and is the one
notebook/page navigator: folders, notebooks, recent pages, tags and drag-to-reorder;
opening a page or pressing Escape closes it. The header row holds browser-style
page tabs (a dot in the notebook's colour, × to close, + for a new page), the save
state, **Views** (saved maps, the folder/notebook **Library overview** schema,
Today) and canvas fullscreen. One editor toolbar holds the
Writing/Canvas/Mind map switch, page title, Save (Ctrl+S) and Find in page;
**More** holds outline preview, add block, paper, favorite, reload, delete and the
keyboard help. Escape closes Views and More. While writing, a floating **On this
page** outline lists the top-level blocks and the word count; click an entry to
jump to that block, or collapse the outline to a corner pill (remembered).
Text notes use stable bullet blocks: Enter adds a sibling, Tab/Shift+Tab nests or
unnests, `[[Title]]` and `#tags` collect topic mentions, and unlinked title mentions
offer a Link action. Copy a `((block-uuid))` reference or `{{embed:block-uuid}}`
live embed from a block's tools; its citation count opens referencing contexts
in the right sidebar. Referenced text follows edits to the original.
The canvas retains paper templates, ink/fill palettes, layers and handwriting.
A page is the theme's flat background colour with no grid; **More → Paper** adds
lines, a grid, dots or Cornell margins when you want them.
Its tools sit in one floating pill at the bottom of the canvas: select, pen,
highlighter, eraser, text and sticky (mind maps: select, sticky, connect, pen,
eraser and **+ Concept**), your last five colours as round swatches (**+** opens all
twelve named colours and a hex field), a stroke-size toggle, undo/redo, and **⋯**
for the other tools, fill, exact stroke and text size, pen mode, snap, zoom and
the text outline. Click the handle on the pill's bottom edge to tuck it away to
a small tab and again to bring it back (remembered); tool keys still work while it
is tucked. **Canvas fullscreen** (⛶) puts only the canvas and the same pill on the
laptop screen, not the whole application. Its exit control and Escape restore the
workspace.
Outside text editing, F fullscreens the focused workspace panel instead.

**Connections** automatically compares note and flashcard text locally:
explicit `[[Page title]]` links, unlinked title mentions and related concepts
are separate groups. Open a reference or turn a title mention into a link.
Notebook schemas derive wiki and shared-concept edges without changing your
documents. Related matches are suggestions, not claims that two concepts are
identical; no AI, external indexing or network calls are involved.

**Flashcards** opens Decks: a tile per deck whose ring splits today's load into
review, learning and new around today's count; hovering a tile shows the split,
measured recall and Review. A Tiles / Graph toggle (remembered) swaps the tiles for
the deck → note graph. **Add card** is available immediately,
including from All decks. Review deals the queue as a stacked deck: tap the top card
(or press Space) to flip it, then swipe it or press a key to send it off in the
grade's direction (← Again, ↓ Hard, → Good, ↑ Easy). Below the pile a keyboard strip
shows keycaps 1–4 with each next interval and Space (flip, then Good); they sink as
you press them.
**Stats** shows memory statistics for the selected scope: today's queue (learning,
review and new, with suspended, leech and buried cards and a time estimate), a
30-day due forecast with overdue cards in today, FSRS forgetting curves for weak,
medium and strong cards against your target retention with measured recall, a
maturity donut (new, learning, young under 21 days, mature) and a 26-week daily
review heatmap with your streak.
**Cards** is a split browser: a filterable card list (search plus one-click due, new,
learning, review, leech and suspended filters) beside the selected card's flippable
preview, its FSRS stability, difficulty and recall now, its last reviews, and
Suspend, Bury, Edit, Archive and Delete. Its top row adds cards inline: type the
front, Tab, the back, and Enter saves it into the deck in scope (with All decks in
scope the row has its own deck picker) and leaves the front ready for the next card.
**Quick add** works from any page: press N on Flashcards (outside a text field) or
choose **New card** in Ctrl K to open a small front/back popover over the current
page with the deck in view, or the last one you used, preselected. Enter or
Ctrl+Enter saves and readies the next card; Esc closes it. Both refuse an empty
front or back and flag a front already in that deck; Enter again adds it anyway.
FSRS scheduling, cloze/reversed cards, media and portable imports are supported.
This is not full Anki parity: no AnkiWeb sync/add-ons, personalized FSRS optimizer,
or arbitrary template JavaScript/CSS. Legacy imported schedules are approximated.
Likewise, Notes implements the workflows above, not the Obsidian plugin ecosystem.

**Study** opens a three-column cockpit: subjects (with this week's minutes in
their colours), concepts and tasks on the left pick what to study; the session
timer sits in the centre; the fourteen-step guide, session notes and due spaced
returns sit on the right. Under 900px wide it stacks into one column, and
**Focus mode** keeps only the timer and the next tasks. Progress, History,
Exams & plans, Mind map, Helpers and Timer settings are tabs beside it.
In **Mind map**, create Lecture, Chapter or Exam review maps,
or create one from a subject. Topics are compact pills in their branch colour around
an ink root. Click a node, then Tab adds a child, Enter adds a sibling right after it,
typing or Ctrl+Enter renames, arrows move between parent, children and siblings,
Ctrl+↑/↓ reorders siblings and Space collapses a branch. Drag a topic (its branch
travels with it): the topic it would join lights up and the branch previews its new
place; dropping elsewhere snaps it back. Press **D** mid-drag, or **Detach branch**
in the node panel, to break a branch off as a floating piece that keeps its place.
Alt-drag places a topic anywhere while keeping its parent; its own children are laid
out beside it, and **Reset position** puts it back into the automatic layout.
A swatch row floats above the selected topic: pick one of the theme's six colours
(or automatic) for the topic, or turn on **Whole branch** to paint its descendants
too; **Copy style** / **Paste style** (Alt+C / Alt+V) carry a colour to other topics.
**Connect to…** in the node panel links the topic to any other one: click (or move
to and press Enter on) the other topic, then type the connection's label. Connections
are dashed curves outside the tree: they never change the layout, hide when either
end is folded away, and double-clicking one edits its label.
Shift+arrows, Shift-click or a Shift-drag box on empty space select several topics:
the swatch row then recolours them all, dragging one moves every selected branch
(onto a topic, freely with Alt, or detached with D), and Delete removes them together.
Ctrl+Z undoes every map edit (including drags, viewport changes and recolouring);
Ctrl+Shift+Z or **Redo** reapplies it. Saving keeps the current editor's history.
A new edit after undo discards the redo branch. Reloading starts a fresh local
history; **Undo last save** still restores the durable previous saved snapshot.
**Split branch into map** moves its descendants into a new map with links in both
directions. To merge, drag a root onto another map's named drop target above the
canvas (or use **Merge into map → Merge root**). Merging back replaces the split
placeholder; other roots stay in the source map. Transfers save both maps
atomically; undo/redo restores both sides and refuses if either map changed in
another window. The emptied source map remains available.
Mouse, touch and pen share one pointer path: tap selects, drag moves or reparents,
and a double tap (or double-click) edits a topic (or opens its linked note or map),
adds a topic on empty space or relabels a connection. With a pen, **Ink** in the
map toolbar draws pressure-sensitive strokes (a mouse draws too; fingers keep
panning). Each stroke is smoothed when you lift the pen and sticks to the nearest
topic, so it moves with that topic, hides when its branch is folded, follows a
split or merge, and is saved with the map. Hold the pen's barrel button and draw a
loop to select every topic inside it; flip the pen to its eraser end to rub out
ink (topics are never erased).
While the pen hovers or has just been used, touches are treated as a resting palm
and ignored, and the pen's barrel button never opens the delete prompt.
Tree/radial layouts, an outline, linked note cards and links between maps help
organize material. **Quiz me** hides labels for recall; a branch can produce a
flashcard draft. **Auto-links** also suggests local sources for node labels;
accepting references is revision-checked and idempotent. Save graph edits
explicitly. Kanban is removed; existing stored rows are left untouched.

**Start session** opens the fourteen-step preparation/connection/recall guide
and a five-minute priming timer, then repeats 25-minute focus / five-minute break
phases. Start unlocks the audible phase-change cue. Only completed focus and
explicit manual entries count toward weekly/monthly totals; priming, breaks and
pauses do not. The guide's checklist and reflections stay with the session.
**Progress** stacks each day of the selected week or month in subject colours
beside a share ring (hover or click a subject to isolate it), tints a month
calendar by each day's main subject and deepens it by minutes, with a ✓ for each
week that met the weekly goal (or had any study when no weekly goal is set), and
draws goal rings for today, this week and today's flashcard reviews.
The segmented streak ring counts consecutive days from saved study time toward
the next milestone. **Log today** opens the manual session editor; saving actual
time fills the segment, rather than adding a cosmetic check-in.
**Exams & plans** shows exam countdown/readiness cards, a two-week timeline
with an exam lane and a spaced-returns lane, and a twelve-day strip. Select a
block to mark it done or export it explicitly to Calendar. Drag blocks between
days (or use Alt+Left/Right); days above 90 planned minutes show a load warning.
Blocks stay before their exam. Make a plan saves dated blocks from your own
dates and durations. Returns are grouped Due / This week / Later: Recalled
advances through 1, 3, 7, 16 and 35 days; Shaky restarts at one day.
**Calendar** opens one week with half-hour slots and a day-plan sidebar; events
are filled blocks in the theme palette. An event or calendar linked to a study
subject takes that subject's Study colour, shown in the editor as a locked
**Auto · Subject** chip; unlinked ones pick one of the theme's six palette
colours (new calendars start on the next one), which recolour with the theme.
Colours picked before keep their hex, and tasks linked to a subject carry its
colour too. A floating range pill at the bottom of
the calendar steps back/forward, returns to today, jumps to a date and switches
**D**ay, **W**eek, **M**onth, **A**genda, **Y**ear and day **P**lan. While the
calendar has focus the same letters switch views, **J**/**K** step and **T** goes
to today. Month shows up to four calendar dots per date; clicking a date fills a
side day agenda. Year tints every day by booked hours (hover to read, click to
pin, **Open day** to jump). Plan splits the day's timeline from its open tasks;
**Plan** drops a task into the next free 30-minute gap (08:00–21:00) as a dashed
block. The sidebar's mini month shows ISO weeks; hover a week to preview its
load and click a date to jump there.
Click a slot to schedule a time, or drag an event/task to another date or time. Drops open the
editor for review before saving; moving an event preserves its duration.
All-day items can be moved across the all-day row. Clicking an event opens a side
drawer whose time steppers, calendar, place and reminder chips (5m/10m/1h/1d
before) update the block live until you save or press Escape. Its title field
reads natural language: `lunch with Mira fri 12:30 at Café Rose every week` fills
the date, time, place and repeat and shows what it understood before you save.
Repeat rules, time zone, custom reminder offsets and Google properties stay in
the drawer's expandable sections.
A task's **＋ Map** action optionally adds it beneath a chosen map node.

The standalone **Reminders → When** dropdown offers Today, Tomorrow, one/two
weeks, 30 days or a custom date picker; no date-syntax memorization is required.

**Notifications.** Due calendar reminders and job-search reminders arrive as one
stack of glass toasts in the bottom-right corner. Hovering (or tabbing into) the
stack fans it out and pauses every countdown. **Snooze** brings a calendar
reminder back in ten minutes and moves a job-search reminder a day; **Open**
jumps to the calendar, the job or Reminders. A toast that times out is not lost:
the bell in the top bar counts unread notifications and opens the notification
center, with All/Unread tabs, a source filter, **Mark all read**, and
**Dismiss**/**Done** on each item. Its footer switches system alerts on or off.
**Settings → Quiet hours** sets a window on a 24-hour dial (drag either end, or
use the arrow keys for 15-minute steps). Inside it nothing toasts; reminders are
held (shown as Held in the center), calendar events can be let through, and when
the window ends the held items arrive together as one **Morning digest**.
kitty checks reminders only while it runs: in the desktop app, **Keep running on
close** keeps it checking from the system tray; when kitty is fully closed,
reminders that fell due meanwhile arrive the next time it opens. Read, held and
snoozed state is remembered per browser profile.

**Documents & PDF** authors portable Markdown (numbered source) or rich headings,
emphasis, nested lists and quotes beside a live preview that scrolls with the
source; drag or arrow-key the divider to resize the split (remembered per browser).
A compact bar switches documents, saves, makes a note page and exports a real
selectable-text PDF; its settings button chooses the format and notes
folder/notebook. Below the editor, import PDF/Markdown/text as new notes or
append extracted content to the saved document. Original files remain downloadable.
Clipboard files/text and relative Markdown-folder structure are supported.
Topic matching can stay inside the document or the selected folder, locally;
scanned-PDF OCR and proprietary notebook formats are not supported.

**Opportunities** has a separate category → source → opportunity schema and a
List view. Source hubs remain visible when no opportunities have been ingested;
Discover explicitly fetches enabled public sources, and is disabled in demo.

These interactions draw on [MindNode's keyboard map-building](https://www.mindnode.com/support/guides/keyboard-shortcuts),
[Heptabase's note cards and whiteboards](https://heptabase.com/),
[Roam's linked references](https://roamresearch.com/) and
[OneNote page templates](https://support.microsoft.com/en-us/onenote).
Recall and flashcard actions complement mapping rather than treating drawing
alone as studying: [retrieval-practice research](https://pubmed.ncbi.nlm.nih.gov/21252317/)
supports actively recalling material.

**Prepare applications** also lists recommended open, unsubmitted A/B jobs.
Alternative groups, job limits and browser progress remain under preparation
options. **More filters** exposes ranking, source and other filters; selected-job
actions and job details have their own expandable advanced controls.

Home is the **atlas**, containing workspace sections rather than individual jobs
or note pages. It sits in the centre between two narrow rails that scroll on
their own. The left rail holds **Today** (today's events with a live now-line,
the current event's progress and a countdown to the next) and **Quick note**
(Ctrl+Enter saves; the last three stay visible). The right rail holds **Agents**
(what each herdr workspace agent is doing; **Approve** opens herdr on a blocked
one) and **Workspace** with the 2D/3D switch, **Find a section** (`/`) and the
Sections list. Job-search reminders, deadlines and weekly goals live in **Find
jobs**, not on home. Every schema
starts in Tree; an explicit layout preference can
then be changed in **Settings → Workspace**. Home has one Study entry and only
the Jobs section entry, not separate job-action nodes.
The kitty logo returns home. Clickable schema tabs show section →
notebook/type → page/job/node; the Back control returns through that hierarchy.
Advanced layout/physics controls live in Settings, not over the graph.
Dense graphs retain every node, batch leaves with Three.js InstancedMesh and
edges with LineSegments, and limit labels rather than dropping source records.
This keeps the existing [3d-force-graph](https://github.com/vasturiano/3d-force-graph)
interaction model while using [instancing](https://threejs.org/docs/pages/InstancedMesh.html).
Browse lists remain available when labels overlap or WebGL is unavailable.
Left or middle-button drag rotates; the wheel zooms; right-button drag pans.
Ctrl + Left/Right rotates the visible 3D schema in ten-degree steps, except while
editing text, selecting text, or using a dialog.
**Brain** is a third home look beside 2D and 3D (Workspace panel or Settings →
Workspace). It draws every record and every edit from `GET /api/brain` as one
point of light on black: records form a colourful cerebrum (one lobe per section)
and cerebellum, edits run down a glowing spinal cord with the newest nearest the
brain, and a faint halo of edits surrounds the records they changed. Its View
panel sets the layout (Anatomy, Spheres, Wheel), grouping (section, navigation
group or type), labels, turning and speed, edit→record lines, the body scaffold
and point size; Reset restores the defaults and the choices are remembered. The
legend lists each group's records + edits; click one, or a point, to highlight
it. Hovering a point shows its label, section, type and date. Section schemas
have no Brain and draw it as 3D. A backend without `/api/brain` gets a notice,
never invented data. Reduced motion skips the build-up, twinkle and turning.
Theme presets include dark cherry-pink **Cherry cat**, with blossom ASCII,
and a Scythe palette. Scythe illustration requires the actual supplied artwork;
no unrelated image is substituted. Motion respects reduced-motion preferences,
both the system's and the Appearance setting.
Top notices and the study timer bar can be dismissed.

**AI settings** provides optional OpenRouter drafts for selected text: summaries,
outlines, study plans, opportunity explanations, and source-grounded flashcards.
Add a key locally (owner-only `.private/openrouter-key`, or `OPENROUTER_API_KEY`),
refresh the model catalog, choose a model and price/latency/throughput routing,
and set an output limit and daily budget. Review the exact text, prompt and maximum
cost before explicitly approving each request. Reviews are single-use, expire
after ten minutes, and become invalid when settings change. Notes are not shared
automatically; output remains a draft. Provider requests disallow data collection,
and failed requests are not automatically retried. Paid calls are disabled in demo.

The website supports:

- Structured daily brief: track goals, deadlines, follow-ups, quiet applications,
  expected openings, source problems, mail proposals and outstanding questions.
- Inbox, ranked opportunities, A/B apply queue, saved views, source/eligibility/
  status/date filters, favorites and selection across pages.
- Manual posting entry, optional public-page metadata lookup, track/tier decisions,
  deadline overrides, persistent notes, queue/skip/later and durable ineligibility.
- Pipeline swimlanes by track (drag or Shift+arrows between stages and tracks),
  reminders you can edit, snooze a day or delete (hover, or swipe right/left, with
  undo), explicit recording of an already-submitted application,
  configured follow-ups, dated evidence and append-only status undo.
- Resume registration/upload, vocabulary coaching with posting evidence, audited
  candidate generation, exact diff/fact/PDF review, promotion and rollback.
  Historical application resumes stay pinned.
- Browser application preparation for selected jobs, the A/B queue or capped
  favorites. Preflight shows the chosen resumes and blockers. Approval is bound
  to the reviewed job/configuration/PDF identities; changes require another review.
  Hash-addressed approved PDF copies stay in private runtime data so later human
  submission uses the approved bytes.
- Screening-question logging/collection and user-authored saved answers. Unknown
  answers remain blank. No applicant facts or screening responses are invented.
- Editable basic profile details without YAML, plus validated, stale-safe advanced
  configuration editing, previewed CSV import, local diagnostics, schedule
  generation and explicitly authorized notifications.
- Source health, run-one/discover, enable/disable and bounded posting refresh.
  Discovery is restricted to enabled free adapters; paid web search is excluded.
- Company-finder proposals and explicit watchlist approval/rejection.
- Read-only Gmail authorization/checking and explicit status-proposal decisions.
- Rules-only triage, fresh cost estimates, capped paid immediate/batch runs and
  collection of existing batch results. Paid operations require explicit approval.
- Outcome reports and formula-safe CSV/ZIP or JSON exports of jobs, applications
  and status history.

**Job selection:** Ctrl+F (⌘F on macOS) searches; Space marks focused rows,
Ctrl-click toggles and Shift-click selects a range. **Save selected** favorites
the selection. Right-click now requests confirmed deletion, not favoriting.
Submitted applications and actively prepared jobs are protected; discovery can
re-import a deleted source posting. Selection is limited to 10,000 results.
Notes pages/blocks, flashcard notes, map nodes and calendar items also have
confirmed right-click deletion; removing a map node does not delete its source.

**Keyboard:** Ctrl+Alt opens Plan, Ctrl+X Study, Ctrl+C Job search and Ctrl+V
Settings outside editors, selections and dialogs. Native cut/copy/paste remains
unchanged while editing. F1–F10 navigation is removed. Ctrl/⌘K opens command and
job search; `?` shows contextual shortcuts. Arrow keys navigate
jobs/pipeline; Space marks; Enter opens details. Job actions include `t` tier,
`k` queue state, `R` resume, `e` notes, `s` status, `u` undo, `d` deadline,
`n` next action and `o` posting. In Prepare applications, `a` records a submission,
`s` skips, `l` defers, `x` marks ineligible and `n`/`p` move between rows.
Shift+F opens the reviewed autofill plan for marked, opened or focused jobs.
Letter shortcuts pause while typing or using dialogs. Ctrl/⌘W closes the active
workspace tab using the same unsaved-edit confirmation as its close button; the
last tab returns to a fresh home tab. Ctrl/⌘T opens a new tab. Native browser
print and reload shortcuts are not intercepted.

**Panel fullscreen:** press F outside text editing to fill the kitty! window with
the focused view or graph. Click a graph to focus its panel first. F or Esc
restores the layout; changing workspace tabs or navigating also exits panel
fullscreen. The window's own fullscreen state never changes. Inputs, textareas,
selects and contenteditable editors retain their keys; dialogs own Escape first.
Terminal views reserve bare f for the shell and use Ctrl+Shift+F to toggle panel
fullscreen (Esc exits). Terminal integrations mark their root with
`data-kitty-terminal`, including around their hidden input, and mark the
focusable panel with `data-kitty-panel`.

**Preparation is not submission:** Firefox fills supported fields with your
verified profile/answers and attaches the approved resume. Review every answer
and press the employer's final Submit yourself. MFA, CAPTCHAs, consent and unknown
questions remain manual. The latest preparation results show filled fields and
remaining blockers directly, rather than hiding them under advanced options.
A later tab/browser failure preserves earlier job results.

**Employer accounts:** save the account email and password in
`.private/application-credentials.env` (owner-only mode `600`). The reviewed
website plan automatically detects recognized sign-in/account-creation forms;
there is no existing/new-account selector. Saved credentials enable account
access for the reviewed run and exact HTTPS destinations, with an opt-out.
Credentials are never displayed. Changes to credentials or destinations require
a new review. Unknown forms, verification, non-routine consent, CAPTCHA and MFA stay manual.
Firefox launches without inherited desktop/PyInstaller library overlays;
visible preparation requires a desktop session. Supported Phenom posting links
open their exact matching Apply page. Unsupported Next/Submit boundaries stop
with the retained tab rather than risking an application submission.

Account fields use the saved application alias, independently of the Profile contact
email. Quoted credential values preserve literal spaces and `#`. Loading shells do
not count as usable account forms; guarded account controls use Firefox WebDriver
BiDi pointer input, not Jev. Approved account creation includes a required routine
terms/privacy checkbox only when it is explicitly bound to that account request;
marketing, biometric consent and factual certifications stay manual. Covered
controls are not force-clicked. After a manual step, **Continue filling** reviews
that report's job, independent of the current category filter. A new reviewed run
can replace idle monitoring without overlapping active filling.

**Google sign-in:** the dedicated Firefox profile preserves manually established
Google sessions. Advanced integrations can explicitly review Google reuse with
`authentication_mode: google`; the identity comes from **My profile → email**,
and this mode never loads or sends the private employer password.

- Use the dedicated **Firefox window opened by kitty!**, not everyday Chrome.
  Its `data/firefox` profile persists job-site cookies between preparation runs.
- If the account is not signed in, finish Google sign-in manually in that Firefox.
  kitty! can select one exact, already signed-in account on a recognized Google
  chooser; it never adds an account, enters a Google password, or accepts permissions.
- After a manual handoff, return to the same employer application tab and review a
  new plan. Retained fields are reused only when the application identity can be
  verified; a different posting or an unidentifiable changed URL is not adopted.

Both same-tab flows and new popups explicitly associated with the employer tab
are supported. Unrelated tabs are never selected. Unrecognized chooser markup,
Google security blocks, verification and consent remain manual. Google Drive's
resume-picker button is not account sign-in; the filler attaches the approved
local PDF instead. Google reuse is authorized through the website, not by setting
`account_mode: google` alone in CLI/TUI configuration.

The optional watcher records only supported confirmation evidence after your own
submission; stopping releases browser control without closing your prepared tabs.
Ctrl+C waits for background work so accepted paid batch IDs/results can be
persisted before the server exits.

The server binds only to `127.0.0.1`, checks Host/Origin and browser fetch metadata,
requires JSON plus a custom header for mutations, denies framing/CORS, and only
serves registered, hash-verified PDFs. This is a trusted-user local application,
not a remotely deployable multi-user service.

For frontend development, keep the API at port 8765 and run
`npm --prefix web run dev` in a second terminal. Vite proxies `/api` only after
validating the local browser origin. `npm --prefix web run check` checks Svelte
and TypeScript. Backend verification uses isolated synthetic workspaces:

```bash
uv run pytest -q tests/test_web_server.py tests/test_web_jobs.py tests/test_web_tasks.py \
  tests/test_web_automation.py tests/test_web_resumes.py tests/test_web_profile.py \
  tests/test_web_insights.py
```

For browser smoke checks, use the disposable demo, not real application records.
Check simple profile save and discard protection, PDF upload, the home setup
checklist, Ctrl+F, cross-page selection/right-click, preparation-plan review,
disclosure hotkeys, append-only status undo and desktop/mobile layouts.
Demo restrictions intentionally prevent testing live employer, Gmail or paid calls.

## Desktop app (Linux)

### Download, install, and launch

Open the **[latest desktop release](https://github.com/ZacharySF/kitty-releases/releases/latest)**.
Downloads are published in a separate public, releases-only repository; application
source is not included. The supported downloadable platform is **Linux x86_64**,
built on Ubuntu 22.04 (glibc 2.35); older distributions, ARM, Windows and macOS
are not supported by these installers.

- **Ubuntu 22.04+ / compatible Debian-based desktops:** download the `.deb`,
  open it with your graphical software/package installer, and approve installation.
  The package installs its GTK/WebKit, tray and GStreamer dependencies and a
  **kitty!** application-menu entry. Launch it from that menu, not from a terminal.
- **Other compatible Linux desktops:** download the `.AppImage`, move it to a
  permanent writable folder such as `~/Applications`, enable **Allow executing
  file as program** in the file's Properties, and double-click it. Your desktop
  needs AppImage/FUSE support (on Ubuntu 22.04, package `libfuse2`) and compatible
  audio support. A desktop AppImage integrator can add it to your application menu.
  Do not run it as root or keep it in a read-only/system-owned directory.
- **NixOS:** the `.deb` is not a NixOS package. Install the published AppImage
  through `appimage-run` (an FHS runtime that keeps signed self-updates working):
  ```bash
  nix build nixpkgs#appimage-run --out-link ~/.local/share/kitty/appimage-run
  python3 scripts/install-desktop.py --appimage ~/Downloads/kitty_<version>_amd64.AppImage \
    --appimage-runner ~/.local/share/kitty/appimage-run/bin/appimage-run
  ```
  The out-link is a garbage-collection root. Then launch **kitty!** from the menu.

The Linux installer is named **kitty! Workspace** and its Debian package is
`kitty-workspace`, distinct from the unrelated `kitty` terminal package.

No Python, Node, Rust, repository checkout, or build shell is needed to run the
downloadable app. The app starts and stops its own backend on a random loopback
port. Its default workspace is `~/.local/share/kitty/workspace` (or the native
XDG data directory); configuration, databases, resumes and other workspace files
live **outside the application**, are reused in place, and are not overwritten by
application updates. Back up that workspace as you would other personal data.
Replacing/uninstalling the application does not require deleting it.

### Signed in-app updates

In **Settings → Desktop → App updates**, choose **Check for updates** (also in the
system-tray menu), then **Download update** and **Install and restart**. No manual
download, quit, replacement or relaunch is needed for routine signed updates.
The native updater checks the public release feed over HTTPS and verifies the
installer and announced version against the signing key embedded in the app.
The website receives no updater permissions. Downloading leaves your workspace
available; installation waits for accepted work, saves, and restarts automatically.

Debian-package updates require administrator authentication through your desktop's
PolicyKit authentication agent, and the new package's dependencies must already be
satisfiable. If package installation reports missing dependencies, open the new
`.deb` from the release page in your graphical package manager.

AppImage updates require a writable installation directory and space for both the
replacement and one prior image. After signature verification, the app stages the
replacement beside the installed image, syncs and closes it, then renames it into
place. This avoids cross-mount rename failures inside NixOS `appimage-run`. The
previous executable remains beside it as `kitty.AppImage.previous` (or the original
AppImage name plus `.previous`). Failed staging/replacement leaves the installed
image unchanged. To recover an older application, quit kitty! and restore that
previous image to the original filename; do not delete or replace the workspace.
A network/signature/install failure is not a completed update.

Only packaged `.deb` and AppImage installations support native self-installation.
Loose source-binary installations use the public downloads link instead; rebuild
and rerun the local installer to replace those binaries. Installing a published
AppImage with the launcher below retains AppImage self-update support.

**NixOS launcher migration for the next desktop release:** existing launchers need
the current installer once to enable restart outside the old AppImage/FHS process.
Use the same installed image; there is no need to hunt for another download:

```bash
python3 scripts/install-desktop.py \
  --appimage ~/.local/share/kitty/application/kitty.AppImage
```

Run this with kitty! closed, after installing a release containing these changes.
The installer retains the generated launcher's working runtime environment,
configured `appimage-run` link and selected workspace, even when invoked from a
different development dependency shell. Explicit `--workspace` or
`--appimage-runner` options change those selections; ambient `KITTY_HOME` and
`KITTY_DB` never select the installed workspace. Future updates reuse that launcher
automatically. Custom launchers are not silently rewritten.

### Build and install a local launcher (including NixOS)

For development/build machines, the existing packaging script builds the locked
website and freezes the locked Python backend into a relocatable `kitty-web`
sidecar, stages the pinned herdr release as the `kitty-herdr` sidecar
(`desktop/fetch_herdr.py` downloads it and refuses any binary whose SHA-256 differs
from its pin; `--from <downloaded asset>` verifies an already downloaded file
instead), then builds the Tauri app. It requires Python 3.12, uv, Node/npm, Rust,
GTK3/WebKitGTK 4.1 development libraries, tray/media libraries and packaging tools.
The frontend CLI comes from `web/package-lock.json`; no global CLI is needed.

```bash
# NixOS: reuse the installed cargo/rustc with pinned GTK/WebKit dependencies.
nix develop 'path:./desktop#host-toolchain' --offline --max-jobs 0 --command bash scripts/build-desktop.sh
# Install under the same dependency environment, once (no shell needed at launch).
nix develop 'path:./desktop#host-toolchain' --offline --command \
  uv run --locked python scripts/install-desktop.py
# To keep using an existing workspace, add: --workspace /absolute/path/to/workspace
```

The installer copies the built app and its `kitty-web` and `kitty-herdr` sidecars to
`$XDG_DATA_HOME/kitty/application` (default `~/.local/share/kitty/application`),
installs the icon and **kitty!** application-menu entry, and creates
`$XDG_BIN_HOME/kitty-workspace` (default `~/.local/bin/kitty-workspace`).
It captures only runtime library/schema/media settings from the build environment,
not secrets, and does not depend on the repository at launch. Keep those Nix
runtime store paths available. The launcher does not run `nix develop` or need an
open terminal. The installer never copies over or deletes workspace files.
`--binary`, `--sidecar`, `--herdr`, and `--icon` can select explicit build artifacts.
Alternatively, `--appimage /absolute/path/to/downloaded.AppImage` installs a
published AppImage rather than loose binaries. Its writable installed location
remains fixed across signed replacements, with an adjacent `.previous` recovery
copy. Reinstalling an AppImage preserves the original generated launcher's runtime
settings instead of capturing a different build shell's library paths.
The AppImage launcher sets `APPIMAGE_EXTRACT_AND_RUN=1` to avoid requiring FUSE and
waits for the app; an update restart returns through the original runner with its
original environment. NixOS AppImages use `--appimage-runner` for their FHS runtime;
loose source binaries still need the captured dependency-shell libraries.
Neither a checkout nor a dependency shell is needed for subsequent launches.

NixOS defaults to a `.deb` build artifact because Tauri's AppImage assembly expects
GTK in a single library directory, unlike the Nix store. The official downloadable
AppImage is built on Ubuntu CI. On other build hosts, `KITTY_BUNDLES=deb` or
`KITTY_BUNDLES=appimage,deb` explicitly selects bundles.
`bash scripts/build-desktop.sh --prepare-only` prepares website/backend artifacts
for the official Tauri release action without running a second native build.
AppImage builds (the script and the release workflow) set `PATCHELF` to
`desktop/appimage_patchelf.py`: the AppImage tool adds a library path to every
bundled executable, which crashes the static herdr binary, so the wrapper leaves
executables without a dynamic loader byte-identical to the pinned release.

The dependency environment exposes matching GStreamer base/good plugins for WebKit
timer audio. Sound also needs an available output device and a Start/Enable sound
gesture while kitty! is open.

### Publish a desktop release (maintainers)

The private source repository's **Desktop release (Linux x86_64)** workflow runs
on a `v*` tag or manual dispatch. The application version in
`desktop/tauri.conf.json` must match the tag exactly (for example, `v0.1.1`).
Manual dispatch uses that configuration's version. Commit the version and lockfiles
first, then push the matching tag, or dispatch the workflow on that commit.

Configure repository secrets `TAURI_SIGNING_PRIVATE_KEY`,
`TAURI_SIGNING_PRIVATE_KEY_PASSWORD` (empty when the key is unencrypted), and
`RELEASE_TOKEN`. The latter must grant release/contents write access **only** to
`ZacharySF/kitty-releases`; the source workflow itself has read-only contents
permissions. Keep a secure backup of the signing key: replacing it breaks trust
for already-installed applications. Never commit the private key or password.

CI uses Ubuntu 22.04, Python 3.12 with `uv --locked`, npm's lockfile and locked
Rust dependencies. It checks the frontend, isolated backend/runtime regressions
and native Rust tests before the official
[Tauri release action](https://github.com/tauri-apps/tauri-action) signs and uploads
the `.deb`, `.AppImage`, their `.sig` files and `latest.json` to a **draft** in the
public releases repository. A guard downloads the actual uploaded files and
validates the version/tag, nonempty complete assets, signatures and installer-specific
`linux-x86_64-deb` / `linux-x86_64-appimage` metadata before publishing as latest.
A failed guard leaves a draft, not a new public updater feed.
No Kitty source archive, checkout, private workspace or applicant data is uploaded
by the workflow. Bundles do include LearnHarness's public corresponding source and
licenses; GitHub's automatic source archives cover only the releases repository.

The signed updater feed is
[`latest.json`](https://github.com/ZacharySF/kitty-releases/releases/latest/download/latest.json).
Metadata generation is owned by the official action, not a separate custom feed
generator; the executable guard is covered by `tests/test_release_packaging.py`.
For local verification of release/install tooling:

```bash
uv run --locked pytest -q tests/test_release_packaging.py tests/test_desktop_install.py
```

On Wayland the app runs natively, so text and graphs stay sharp on fractional-scale
(e.g. 125%) screens; it no longer routes through XWayland, which upscaled the whole
window. Wayland gives GTK no DPI setting, and WebKitGTK turns that into a negative
device-pixel ratio (a blank, compressed page), so the app sets a neutral 96 DPI when
none is present; a DPI you set yourself is kept. An explicit `GDK_BACKEND` is respected.
The window has no native menu strip. **Settings → Desktop** provides **Check for
updates**, **Keep running on close (monitoring / reminders)**, **New quick note**,
and **Quit kitty! (save and stop workers)**. All remain in the system-tray menu,
along with **Show kitty!**. Without a system tray, keeping the app running on close
is unavailable. A browser-only website explains that desktop controls are unavailable.
The page uses the protected backend API and a fixed command allowlist over the
shell's private owner pipe; it receives no Tauri IPC permissions.

The main window and quick-note windows are transparent: the default **Kitty**
theme (plum near-black with pastel pink accents) and every other theme
draw translucent glass panels over a soft glow, so the desktop shows through.
Browser tabs keep an opaque background. All typefaces (Fraunces, Space Grotesk,
Inter, Newsreader, Fredoka, JetBrains Mono) are SIL OFL fonts bundled from
their `@fontsource` packages, so no font is fetched from the network.


## Finding jobs (Stage 2)

```bash
uv run kitty discover                       # every enabled source
uv run kitty discover -s greenhouse -s nuft2027   # just these
uv run kitty discover -s employers -s workable    # direct engineering/employer boards
uv run kitty discover -s research -s yc_internships -s websearch
uv run kitty discover -s local_employers -s starbucks # configured nearby part-time employers
```

`discover` pulls the Simplify, NUFT, vansh (`vanshb03/Summer2027-Internships`
listings.json; postings older than 180 days skipped), speedyapply (the USA
internship tables in the READMEs of `speedyapply/2027-SWE-College-Jobs` and
`2027-AI-College-Jobs`; fragile, a changed layout shows as partial/error) and derec4
(every listing in `DereC4/internships-and-newgrad`: README.md internships, NEWGRAD.md
new grad and go/UNFILTERED.md, deduplicated by URL, including URLs with parentheses;
🛂/🇺🇸/🎓 markers become eligibility notes and 🔒 closes a posting. Missing files,
changed tables or malformed rows report partial/error rather than a complete
snapshot) repos,
hiring.cafe (see below), every watchlist board on Greenhouse, Lever, Ashby,
SmartRecruiters, Workday and Workable (public widget API, one request per board),
Indeed (via JobSpy),
the monthly Hacker News "Who is hiring" thread, and USAJOBS student/intern
announcements (off until `USAJOBS_API_KEY` and `USAJOBS_EMAIL` are in `.env`).
It keeps internship/co-op/student titles, routes each posting by title and additional
description evidence, runs the rules-only eligibility filter, and stores it. Previously
seen postings can gain newly matched tracks without resetting hand decisions. It prints
new jobs by source, track and eligibility,
the top filtered-out reasons with an example each, and every `[F]` company with
an open posting.

Additional channels:

- `employers`: Texas Instruments and Dell (Oracle), AMD and Schneider Electric
  (Jibe), Lockheed Martin, Northrop Grumman and Eaton (Eightfold), and Siemens'
  public careers pages. The verified portal URLs and request limits live under
  `sources.yaml` → `employers.options.boards`; these do not require new watchlist
  ATS kinds. A blocked or truncated board remains partial/error, not a clean zero.
- Workday searches `intern`, `co-op`, and `student`, paging past unrelated results.
  `max_results_per_query` caps each board/query. JobSpy searches every configured
  internship term when `max_terms_per_track: 0`; result caps, lookback windows and
  locations are configured locally.
- `research`: a separate, rules-only track for verified paid program applications,
  not fabricated individual lab vacancies or employment claims. TAMU S-REU and DOE
  SULI require a published open application window and funding evidence. GPA, credits
  and other unverified prerequisites keep the record **ambiguous**. Each program
  cycle has its own identity and expires against its current published source deadline,
  not a personal reminder date. NASA deadlines and NSF directory responses alone are
  not proof of an open paid opportunity.
- `yc_internships`: public YC directory candidates checked against their actual
  detail pages for US location, internship type, application availability and expired
  cycles. Old activity is filtered; an absent activity date is an explicit uncertainty,
  not proof of closure. Published minimum school standing reaches the eligibility rules.
- `websearch`: configured public `seed_urls` can be fetched without a search key.
  Only fetched JSON-LD `JobPosting` records enter discovery—not search
  snippets or program landing pages. Employer identity, qualifications, deadlines
  and query/page provenance are retained. Shared URLs and sources still deduplicate.
- `hiringcafe`: page 1 only of hiring.cafe's public `/jobs/<topic>` landing pages,
  one per track search term (`software engineer intern` → `/jobs/software-engineer-intern`),
  at most `max_topics` (40) per run, 5+ seconds apart. robots.txt disallows the
  `?page=`/`?searchState=` URLs and the terms forbid republishing, so nothing past
  page 1 is read and postings stay in the local database. The site sits behind
  Cloudflare's bot check, so pages load in headless Firefox through kitty's own driver
  with a separate profile (`data/hiringcafe-firefox`, never the form filler's
  `data/firefox` or your everyday profile). No user-agent or fingerprint changes and no
  captcha solving: a check that does not clear within 30 seconds stops the run with an
  error (429/1015: `rate_limited`), never a silent zero. Each posting links to the
  employer's own page and is deduplicated by that URL and hiring.cafe's cluster id;
  graduate-only degree requirements are dropped like Simplify's, and a stated security
  clearance becomes an eligibility note. Malformed postings make a source partial
  (or failed if none are usable), not a healthy empty result.

Brave search is **off even if a key is present**. To opt in, put
`BRAVE_SEARCH_API_KEY` in the gitignored `.env` and set
`websearch.options.enable_search: true`. Defaults cap a run at four API queries,
five results/query and twenty fetched posting pages; changing these may change
provider charges. No key is needed for seed-only runs. Public fetching respects
robots, redirects, response-size limits and rate limits; private-network URLs and
credential forwarding are rejected. Robots delays are per host; waits over 60 seconds
are deferred and reported rather than blocking the entire run.

Some sources require credentials or interactive access. USAJOBS needs its own API
key/email; authenticated campus portals and robots-disallowed directories are not
bypassed or counted as imported jobs. Source-run evidence distinguishes future,
closed, unknown and blocked program states from open opportunities.

Location and pay rules are user configuration, not repository-wide preferences.
Tracks can limit cities, allow specific city/employer combinations, impose minimum
hourly pay, and rank preferred locations or remote work. JobSpy can search nearby
locations through `also_near`; the `starbucks` source can fetch store jobs and pay
ranges from its public careers search. Track-specific application cities come from
`profile.yaml` (`city_by_track`); answer scopes use `tracks` / `exclude_tracks`.

`local_employers` is a separately selectable, robots-aware direct source using
configured Jibe/Oracle boards, including supported grocery and pet-retail portals.
It restricts cities before fetching details, retains store addresses and
published pay, excludes explicitly full-time-only records, and labels unknown hours
and continuous-acceptance listings rather than promising immediate vacancies.
Jibe's structured US hourly salary ranges are retained. Set a board's
`part_time_only: true` to exclude records without explicit part-time title/schedule
evidence. The pay filter compares the highest stated hourly rate; a passing range
does not guarantee that starting wage. Review tip-dependent pay and schedule
uncertainties before applying.
Configure `options.boards` and `options.allow_cities` in `sources.yaml`; the shipped
example is disabled and has no personal geography. City allowlists and search miles
are commute proxies, not measured drive times. Existing active jobs gain newly
matching tracks on discovery; widening geography alone never reopens a closed job.

In **Jobs (`F4`)**, saved view **6 — Nearby part-time - all posting dates** shows
active, not-filtered, unapplied local jobs in `new`/`queued` state without the default
14-day cutoff. Older but still-open store listings otherwise disappear from the
recent view. `c` still narrows the current saved view; clear another track selection
if necessary. In Inbox, `w` shows older new jobs.

To keep finding jobs without me, run discovery on a timer:

```bash
uv run kitty schedule print                  # home-manager block (NixOS), 07:00 and 17:00
uv run kitty schedule print --format systemd # or plain systemd user units / --format cron
uv run kitty schedule print --with-triage    # opt into capped paid triage with the configured provider
```

The generated schedule runs every enabled discovery source, including DereC4 and
hiring.cafe when enabled in `config/sources.yaml`; it never installs itself.
Without `--with-triage`, newly generated schedules omit paid Jev/OpenRouter and
Anthropic requests. Discovery itself needs no paid inference; an existing schedule
that explicitly opts into capped triage remains a separate authorized step.

It is slow on purpose: at least 2 s between requests to any host (5 s for the
repos), and raw responses are cached under `.cache/http/` for each source's TTL.
Cache hits avoid repeated page requests; failed requests are not cached as successes.
A 429 stops requests to the affected host; retained successes are reported as partial.

Filtered-out jobs are kept (queue state `not_eligible`, visible in "All jobs");
everything else lands in the Inbox (`F2`). The Sources screen (`F6`) shows each
adapter's last run and error, and `enter` runs one source.
`partial` is a problem, not a healthy empty result: Today, push notifications,
Doctor and Sources show it with targeted recovery guidance. DNS failures call
for resolver/provider investigation; robots restrictions stay restricted; capped
queries need narrower coverage; rate limits need cooldown, not a full rerun.

A job closes (leaves the queues, stays in "All jobs") when Simplify marks it
inactive, or when every source that listed it has answered cleanly
`close_after_missed_runs` runs in a row (`config/sources.yaml`, default 3)
without it. A board that errored, was rate limited or suddenly returned 0 does
not count as a miss. Bounded searches (Workday, JobSpy and the new channels) set
`complete_snapshot: false`: absence from a search never closes a posting. Explicit
source closure/expiry and published research-program deadlines still close records.

### Refresh the shortlist, not the source count

```bash
uv run kitty refresh --limit 10 --dry-run  # inspect favorites-first selection
uv run kitty refresh --limit 10           # bounded authoritative detail checks
uv run kitty refresh --job <ID> --limit 1  # use a local stored job ID; repeat --job
```

Selection prefers favorites, then missing/stale descriptions within best-first
ranking; `--track` narrows it. Checks use single-posting ATS details or an exact
public JobPosting record, with robots restrictions, pacing and caching. Workday
CXS and native Oracle Candidate Experience details must identify the exact
tenant, site and posting. Unknown vanity-domain mappings, empty details and failed
sources remain uncertain, not closed. No paid services or submissions.

Inbox, Jobs and Job Detail distinguish **verified**, **stale**, **unverified**
and **closed**. Successful live evidence expires after seven days; a discovery
sighting or failed check does not renew it. A generic HTTP error, absent search
hit or different posting is not evidence of closure.

Source facts retain source, URL, observation time and authority. Newer
same-authority evidence can replace old qualifications; weaker or empty evidence
cannot erase them. Manual queue decisions, notes, favorites, tiers, resumes and
reminder dates remain separate. Changed descriptions invalidate old model tiers;
late batch answers with changed inputs are cached but not applied.


The Inbox's "opens soon" line lists watchlist companies with no open posting
whose opening is within 30 days either side of today: from
`expected_open_months` in `watchlist.yaml` when set, otherwise from the day
their first posting appeared last cycle (cycles start July 1). It needs a year
of history, so until then only the overrides produce anything.

## Every day (Stage 4)

```bash
uv run kitty today           # due/overdue reminders, deadlines this week, new A/B per track,
                               # weekly progress + streak, failing sources, quiet applications
uv run kitty today --notify  # also push it (ntfy or Telegram, set in .env; off by default)
```

The TUI opens on **Today** (`F1`), the same digest with every line a link.
**Apply** (`F3`) is the apply session: one job at a time from the queue, A tier
first, then B, on one track (`t`) or all. It shows the posting, the resume to
send, the edit notes, my saved answers, preparation state and freshness. `f` fills the
form, I check it and click submit, then `a` records the application (resume,
time) and creates follow-up reminders at +7 / +14 / +21 days (`config/apply.yaml`).
`s` skips, `l` puts it off, `x` marks it not eligible by hand (discover never
undoes that), `q` logs a screening question I could not answer. On the Profile
tab (`F8`) `w` answers a logged question once; the answer goes into `answers.yaml`
exactly as I type it. `kitty status <id> applied` also creates the follow-ups.

**Inbox company search:** press **Ctrl+F**, type a company name or part of one,
then **Enter**. Matching is case-insensitive and company-only, not title/location
search. It preserves the selected track, favorites filter and 14-day/all window;
the counts line shows the active company query. **Escape** clears only that search.
Hidden marked rows are cleared when the results narrow, so batch actions cannot
silently affect a company outside the displayed results.

## Finding companies (Stage 5)

```bash
uv run kitty finder          # propose up to 15 firms that match config/skills.yaml
uv run kitty finder --list   # what is waiting for a decision
```

Sources: the YC companies list (hiring), HN "Who is hiring" posters, sponsor lists
in `config/conferences.yaml`, GitHub organisations in my skill areas, and small
firms with SBIR/STTR awards since three years ago (the bulk CSV without abstracts,
~85 MB, downloaded once and cached 30 days; the awards API answers 403). Each
firm is scored against the keywords of each area in `skills.yaml` (at least two
must match), with a one-line reason quoting what matched; each area stops at its
`per_run_quota` and a run never proposes more than 15. For each one it checks
Greenhouse / Lever / Ashby / SmartRecruiters for a board. On the **Finder** tab
(`F9`) `a` approves (the firm joins `watchlist.yaml`, so discover polls it) and
`r` rejects (never proposed again). SBIR only has award titles, so few SBIR firms
reach two keywords.

## Applying (the tool fills, I submit)

```bash
uv run kitty apply 1 2                       # example local job IDs
uv run kitty apply --top 10 --track embedded # the 10 best new jobs in a track
uv run kitty apply --queued                  # everything already in the apply queue
```

In the TUI: mark rows in the Inbox with `space` and press `f` (or `f` on any job).
kitty opens each application form in its own Firefox window (a separate profile in
`data/firefox/`, so logins stick and my everyday Firefox is never touched), uploads
the track's resume, fills contact and education fields from `profile.yaml` and
screening questions from `answers.yaml`, and stops. **It never submits.** The window stays open; I check
each tab, fill what is left and click submit. Attempted forms with fields enter
the "Apply queue" view (Jobs, key 5); being queued does not mean ready.

Preparation states are **unattempted**, **attempted**, **partial**, **blocked** and
**ready**. Only verified required fields, completed uploads and a recognized
review/submit boundary permit ready; detection of fields alone does not.
Old fill markers migrate to attempted, not ready. Missing answers can be saved
on Profile and retried, including the first answer in an empty answers file.

Explicit job IDs do not bypass eligibility or availability checks. Excluded jobs
return their reasons without opening tabs or accessing credentials. CLI
`kitty apply <ID> --include-ineligible` is deliberate compatibility research:
it cannot authenticate, become ready or register submission watching. Preparation
reports distinguish ineligible/unavailable jobs, human boundaries, missing facts,
unsupported controls and browser errors instead of counting touched fields as success.

A valid retained application tab is resumed without navigating away or replacing
manual choices/attachments. Per-tab browser-storage markers bridge Firefox's
session-scoped tab IDs; origin and posting checks still apply. A matching URL alone
never adopts another tab, and ambiguous copied markers are rejected. Monitoring
uses the same ownership checks and preserves the original attachment identity.
If ownership cannot be verified, the tool explicitly restarts only saved facts.
A new upload on a populated retained form stays manual because employer
resume parsing can overwrite your entries. Login, verification, visible CAPTCHA,
unsupported controls, validation errors and incomplete uploads remain blockers.

The window is its own program (a separate systemd scope), so closing the terminal
leaves it open; in the window list its app id is `kitty`. Before filling, kitty
switches Firefox's remote control to its non-automation mode, so pages see
`navigator.webdriver = false` (bot checks such as reCAPTCHA read it). That needs
`--remote-allow-system-access`: while the window is open, any program on this
computer that reaches 127.0.0.1:9333 can run code inside that Firefox, so keep only
job-site logins in it.

After filling it keeps watching the tabs (`watch_hours` in `config/apply.yaml`,
default 4; `--no-watch` skips it). When a tab shows its board's confirmation page
(Greenhouse `.../confirmation`, Lever `.../thanks` with "Application submitted!",
Ashby's success message), it records the application with the resume and the
follow-ups, exactly like `kitty status <id> applied` or `a` in the Apply tab.
It only reads the tab's address and text; it never clicks. Other boards (Workday,
company sites) are not watched: mark those by hand. In the terminal Ctrl-C stops
watching; in the TUI watching goes on across tabs, `w` on the Apply tab stops it,
and pressing `f` again keeps watching the earlier tabs too.

Firefox permits one remote-control session at a time. Stop the existing worker
before starting another. Session release is confirmed, not inferred from a closed
socket; an unconfirmed cleanup is reported separately without erasing completed
job outcomes. An empty process-holder diagnostic is not proof that restarting is
safe. Preserve unsaved forms before restarting Firefox or reloading an employer
page. Normal Reload affects that tab, not unrelated application tabs.

Applicant facts are never guessed: an unanswered screening question stays blank
and goes into the Profile screen's unanswered list, so I answer it once. Facts
persist in the private local configuration across sessions. `match:` phrases
make a saved answer apply to differently worded
versions of the same question ("authorized to work", "sponsorship"); "U.S.",
"USA" and "United States" count as the same words. `choices:` records my exact
wording for dropdowns that offer no plain "Yes". `companies:` and
`exclude_companies:` keep prior-interview or employment answers from leaking
between companies. A graduation dropdown listing terms ("2032 - Spring") gets
the term of the configured graduation month (Jan-May Spring, Jun-Aug Summer, Sep-Dec Fall).
Country-bound work-authorization and sponsorship answers are scoped by the
country in their saved question or choice aliases. A relative question requires
a proven matching job jurisdiction; mixed, conflicting or unknown locations stay
unanswered. Explicit field-country evidence is checked too. Citizenship, residence
and birthplace are not substitutes for permission to work in another country.

College enrollment does not establish age; a single citizenship or no previously
held citizenship does not establish the absence of another current citizenship.
An employer missing from a resume does not prove no employment or contractor
history. Explicitly confirmed answers take precedence over profile inference.
Current study and expected completion must not be recorded as an earned degree.

Recruitment-source selectors keep an offered saved source first. Otherwise they
prefer a plain company/careers website or online source from the actual options;
referral, recruiter, agency and "Other—specify" choices are excluded. Learning
questions and source-code questions are not recruitment-source selectors.

Autonomy improvements follow the same-case loop: observe a real failure, isolate
its cause, implement a reusable repair, add a consumer-visible regression check,
then rerun the original case without submission. One-off browser scripts are
verification work, not evidence that the application implements that capability.
Oracle split month/year controls now retain receipts for observed native option
selections even when the shared date row lacks its generic filled marker; a
typed query alone remains insufficient. Required education/experience records
are reported explicitly, but their editors still need additional workflow support.
Verification must match the owned posting, account, employer sender and a fresh
message; a query containing an old thread is not sufficient. CAPTCHA, unknown
facts, sensitive consents and final submission remain explicit human boundaries.
The persistent flowcharts and action gates are in
[AUTONOMY_PROTOCOL.md](AUTONOMY_PROTOCOL.md), linked from the repository's
agent instructions. Private checkpoints preserve evidence and next actions, not
credentials or new authorization.

The preparation worker has a bounded, local recovery agent. Each tool gets a fresh
structural diagnosis and an observed outcome. It can wait for loading, restore its
owned tab's focus, recommit invalid text only when it exactly matches a saved answer,
retry freshly detected supported blank fields, or reload a proven pristine loading
page. Different/manual values, consent and unsupported controls stay untouched.
The agent cannot replay login/account creation, uploads or Submit. Failed/no-effect
strategies are excluded on unchanged evidence; step/action limits, cycles, Stop and
deadlines never reset. See `recovery` in `config/apply.yaml.example` and protocol
section 8 for limits.

Optional model-directed tool selection uses the configured OpenRouter model only
after a fresh review and explicit per-run approval. Structural metadata and typed
action/outcome history only; no page text, labels, answers, URLs, resumes, credentials or mail. Request/token/
dollar caps share the existing daily AI ledger. Local guards still decide whether
each proposed tool may execute. No API key or paid approval is needed locally.

The UI exposes the current job/action, streams completed job reports and lists
fixer receipts. SQLite `application_recovery_events` persists categorized actions
and outcomes. `settings.data_dir/application-recovery/PROTOCOL_FEEDBACK.md` is an
atomically regenerated, owner-only evidence report, not self-modifying code,
executable instructions or model training.

Recovery distinguishes inspection (`observed`), verified improvement (`completed`),
and an action that returned without improving its target (`no_effect`). DOM signature
churn or a nonempty write log does not prove acceptance. Recommitting invalid saved
text is an agent strategy, not an unconditional second pass in the normal filler.

Oracle choice-loading skeletons are not resume-upload indicators. Explicit
resume-analysis indicators and unknown progress remain blocking; excluding a
known lookup spinner does not waive required-field validation.

Generic comboboxes first open their existing option list and select an exact
matching value (including configured/profile aliases), then fall back to search
where supported. Asynchronous location searches retain their loading wait.
Native Workday lists use their owned list control and inspect the bounded
hierarchy before selecting one uniquely matching leaf. Ambiguous or incomplete
lists stop with a selection-specific reason, without repeated answer clicks or
search fallback. Radio decorations are allowed only inside the uniquely owned
single-select portal; unrelated controls stay blocked. A committed selection
after blur is required, not just a click.
Text writes also require a fresh native value after blur. A cleared, replaced or
rounded-away value is not credited as filled. Safe telephone formatting and
native newline normalization retain equivalent-value receipts.
Verified acronym decorations are equivalent to the full option name, so
`ECE - Electrical and Computer Engineering` can match the saved full major.
Ambiguous/disabled choices are refused; a major never matches a different program
merely because its name starts the same way. Editable search text is not proof of
a selection. Failed typed searches discard their automation-owned query; native
Workday navigation preserves existing manual search text.
Recognized Workday pages can follow guarded Apply/Apply Manually entry and
non-final Next/Continue steps to Review. Account requirements vary by employer.
Unknown structures and final application actions are never clicked. Known Oracle
email-first entry can use the saved account alias and approved routine terms;
its standard application sections are checked together, not advanced by guessing
from the numeric URL. Missing required profile records still prevent readiness.
Loaded embedded application forms take precedence over lingering parent Apply
buttons. Separate Oracle resume attachments can be added without invoking the
profile-import widget; ambiguous retained uploads remain deferred to protect answers.
Required native resume uploads inside labelled ARIA upload groups are supported.
When Greenhouse removes the file input after upload, its visible filename/removal
receipt can verify the retained attachment. Pending, failed or incomplete receipts
cannot establish readiness.
An unrelated unsupported required widget no longer prevents known fields and the
resume from being filled, but it still blocks advancing or claiming readiness.
Job-description pages without a recognized application entry remain manual;
opening a tab alone is not reported as successful preparation.

Sign-in automation is off by default. The website can authorize one reviewed run
as described above. For CLI/TUI policy, put the account in
`.private/application-credentials.env` (`APPLICATION_ACCOUNT_EMAIL=...` and
`APPLICATION_ACCOUNT_PASSWORD=...`, `chmod 600`) and list each site's exact HTTPS
origin in `config/apply.yaml`:

```yaml
authentication:
  enabled: true
  allow_account_creation: false   # true also lets it fill Create account / Sign up forms
  allow_account_terms: false     # true authorizes required routine terms/privacy during sign-in too
  account_mode: recognized       # login or create explicitly chooses an account action
  approved_origins:
    - https://example.wd5.myworkdayjobs.com
  existing_account_origins: []   # never create another account on these origins
  manual_account_origins: []     # no automatic credential writes on these origins
```

`recognized` only handles an already displayed account form. Explicit `login` or
`create` can first follow one unambiguous approved account-entry link or
non-submitting button. `create` also requires `allow_account_creation: true`.
Auto mode sends an initial recognized credential action and switches flows only
after an explicit account-not-found or already-registered error. An incorrect
password or a conditional "Already have an account?" hint never triggers a retry.
A sent action is not proof an account was created; the filler checks the next
page and stops for email activation, verification or MFA.
Account stages have bounded semantic waits and fresh tree, document and frame
receipts before each credential write. Empty provisional navigation metadata is
waited out read-only; a real unapproved document still fails closed. Email-first
and password-next flows do not require a whole new account attempt. A rejected
credential pair is not replayed by the same controller.
Workday sign-in redirects retain the reviewed application identity across locale
insertion and location-label punctuation changes. The HTTPS tenant, career site
and full requisition slug must still match before credentials are written;
different postings, nested redirects and cross-origin targets are rejected.

Scoped employer email verification reuses Gmail in kitty's dedicated Firefox
profile. If its Gmail tab was closed, preparation opens a normal background tab
and waits for the saved session; it never copies everyday-browser cookies or
requires OAuth status-mail setup. A signed-out or ambiguous mailbox is reported
specifically. Only the approved sender, exact recipient and fresh request window
are searched; delivery headers must qualify before reading a message body.


Employer-password mode recognizes email/password account forms, confirmation
fields and accessible component controls. Native credential submits require POST.
A recognized component proxy with an omitted method gets a POST-only native
fallback before any credential is populated; explicit GET and unsafe submit
overrides are rejected. In password forms, CAPTCHA widgets, extra fields,
cross-origin frames and boxes already holding other values prevent credential
writes. Oracle email entry distinguishes empty CAPTCHA placeholders and passive
badges from an actual challenge; it never solves a challenge. Account-creation
approval covers required routine sign-up terms. `allow_account_terms` separately
authorizes required routine terms/privacy during sign-in; reviewed website auto,
login and create runs include that permission. Consent is classified from its own
label and directly bound descriptions, not every purpose named in an expanded
privacy notice. Optional marketing stays unselected; certifications, screening
permissions and sensitive declarations stay manual. Checkbox acknowledgment is
observed before sending one account action; no blind double-click. Known cookie
notices may be rejected, never accepted. Credentials are redacted from reports,
stored notes and browser errors.

Posting entry waits for Oracle's visible application-loading overlay to clear
before clicking Apply. A visible interactive DataDome challenge, including one
embedded in SmartRecruiters, stops preparation for manual completion; hidden
widgets and passive collection frames do not authorize or require challenge
interaction. Supported but unanswered required radio groups remain questions to
complete, not evidence that the control type is unsupported.

Saved country and state fields are filled before dependent city choices, even when
the form displays City first. Missing personal facts and unavailable choices remain
unanswered; the filler does not invent values to complete the address.
After Oracle email verification, preparation resumes the owned application tab
across its email and section routes. Recovery requires the same employer origin,
candidate site and exact job ID; it never substitutes another application's tab.

The [application autonomy and privacy research](APPLICATION_AUTONOMY_RESEARCH.md)
records provider account capabilities, implemented preparation/resumption
boundaries and remaining tenant-specific gaps. There is no promise of unattended
support for every site.
Browser-signal suppression is not anonymity; verification, CAPTCHAs, non-routine
consent, unknown answers and final submission still need an appropriate handoff.

The window is system Firefox (`$KITTY_FIREFOX` or `firefox` on PATH) started with
remote control (WebDriver BiDi) on `127.0.0.1:9333` (`KITTY_BROWSER_PORT`). While
it runs, Firefox shows the striped "under remote control" address bar, and any program
on this computer could drive that window, which is why it uses its own profile.
Closing and reopening it reuses that profile, but employers can expire cookies or
require sign-in or email verification again. Keep application tabs open to preserve
in-progress browser values; restoring a login does not restore an unsaved form.
New device-location permission requests are blocked by default in this application
profile, not in everyday Firefox. Typed address fields and job-location filters
continue to work normally.

To answer questions before the filler meets them, `kitty questions collect`
reads the forms of the queue's jobs without a browser (default: active, `new` and
`queued`, tiers A and B; `--states`, `--tiers`, `--limit`). Greenhouse comes from
its public board API, Lever from the job's `/apply` page; Ashby (its robots.txt
disallows the form API), Workday (sign-in) and other sites are skipped with the
reason in the summary. One request per second per host, cached for a week, and
each question counts a job once, so re-running changes no counts. The company's
own name becomes `{company}`, so "Why Example Robotics?" and "Why Example Systems?" merge.
`kitty questions list` prints what is still unanswered, most-shared first, by
kind (choice, factual), with job counts, example companies and the dropdown
choices; `--include-essays`, `--include-demographic`, `--all`, `--min-jobs N`,
`--json`. Questions profile.yaml fills and file uploads are left out, and
questions the filler logged are listed too.

### Favorites and autofill

- Star jobs I like: `*` in the Inbox, Jobs, Apply and Job Detail screens (★ marks
  them), or `kitty fav <ids...>` / `kitty unfav <ids...>`. `kitty favorites`
  lists them best first; `kitty list --favorites --best` works too. `v` in Inbox /
  Jobs / Apply shows favorites only.
- More like these: best-first ordering (Inbox, Jobs, Apply, `kitty list --best`,
  MCP list_jobs) ranks jobs that look like my favorites (title, company, tracks,
  description words) above ones that look like jobs I skipped or marked not eligible
  by hand. It comes after eligibility, tier and watchlist priority and before place
  and age, so it never overrides a rule. With no favorites it changes nothing. Job
  Detail and Apply show "like your favorites (+0.45): fpga, track cpu, ...".
- `kitty autofill [--limit N] [--track T] [--no-watch] [--detach] [--dry-run]` (or `F`
  on the Apply tab, `f` on a ★ job there, or `f` on Inbox / Jobs with nothing marked)
  fills my favorites' forms in the kitty Firefox, only those in the
  track the screen is filtered on (`c` on Inbox / Jobs / Apply; `--track`),
  best first, at most `autofill_cap` per run (config/apply.yaml, default 10), waiting
  `autofill_pause_seconds` (default 15) between jobs. **It never submits.** It skips
  closed, applied, skipped / not eligible and verified ready-for-review favorites,
  and says why. Partial/blocked attempts can resume; Workday is inspected rather than
  skipped by hostname. `--detach` keeps it running after the terminal
  closes (own systemd scope; output in data/autofill.log). With ntfy or Telegram set
  up in .env it pushes the summary. Afterwards it watches the tabs like
  `kitty apply` and records each confirmation page as applied.
  Filling temporarily selects each employer tab so Firefox delivers real dropdown
  input. The previous tab is restored only when it remains in the same browser
  window and the user has not selected another tab during preparation.

## Triage (Stage 3, optional Anthropic or OpenRouter API)

```bash
uv run kitty triage                 # what a run would send and cost; changes nothing
uv run kitty triage --now --limit 5 # small run now, one request at a time
uv run kitty triage --batch         # everything, as one Message Batch (half price, < 24 h)
uv run kitty triage --collect       # apply finished batches
```

Needs `ANTHROPIC_API_KEY` in `.env`. Model, effort, prices and the hard per-run
cost cap (`max_cost_usd_per_run`, default $5) are in `config/triage.yaml`. Every
paid run prints the job count, estimated tokens and dollars first and asks to
confirm (`--yes` skips it, for the schedule). A run stops before it could pass the
cap, and a batch is trimmed to fit; the run's cost and cap hits are in `runs`.

Anthropic remains the default (`provider: anthropic`). For OpenRouter Jev, set
the following in `config/triage.yaml`:

```yaml
provider: openrouter
model: typesafe/jev-router
openrouter_max_input_price: 2.0   # USD per million input tokens
openrouter_max_output_price: 10.0 # USD per million output tokens
max_cost_usd_per_run: 1.0
openrouter_daily_budget_usd: 1.0  # optional shared UTC-day reservation ceiling
```

Provide `OPENROUTER_API_KEY` or the local `.private/openrouter-key` file.
Jev is a model router, not a job-fetching service: the deterministic DereC4
importer still fetches and parses the repository, then Jev tiers eligible
uncached jobs. No applications are submitted. OpenRouter supports `--now` only;
`--batch` and `--collect` require Anthropic. A flagless preview sends nothing.
Dynamic router prices are bounded by the configured input/output ceilings and
token limits, with worst-case reservations against the per-run cap (including
failed requests); a cheaper response does not authorize extra requests beyond
that reserved budget. Requests require supported parameters and deny provider
data collection; providers that cannot honor the restrictions are not a fallback.
Redacted resume and posting text still leave the machine when paid triage runs.
When a daily ceiling is set, reservations survive failures, process restarts and
overlapping runs; they are not recycled after cheaper responses. The day boundary
is UTC, independent of the scheduler's local-time trigger.

Each paid run requires approval unless `--yes` is explicitly supplied.
`schedule print --with-triage` is a separate unattended-spending opt-in: it emits
`triage --now --yes` for OpenRouter, or collect/batch commands for Anthropic,
and never installs anything. Without that flag scheduling stays free.
The generators accept a single time (for example `times=("07:00",)`) for daily
discovery, optional triage and the digest; the CLI default remains twice daily.

For each job the model returns, as schema-checked JSON: a tier (A careful review,
B fast queue, C kept with a reason), one registered resume with a one-sentence
reason, and up to three edit notes. A note is kept only if its JD phrase is
really in the posting and its resume line is really in that resume; notes say
what to check, never resume text to paste. Answers are cached per (job, model,
prompt), so rerunning costs nothing until a posting or resume changes.

Free, no model call: every discover gives each kept job a rules tier straight
away (A open to underclassmen, B eligible or no bar found, C filtered out with
the reason), so the tier column is never empty; `kitty triage --rules` does the
same on demand. Part-time jobs (triage_depth `rules`) keep that tier; on other
tracks `triage` replaces it with the model's. A tier I set by hand is never
overwritten. My name, email, phone and profile links are removed from resume
text before it is sent.

## Replies from Gmail (Stage 6)

Read-only: the only scope asked for is `gmail.readonly`. One-time setup:

1. Open https://console.cloud.google.com/ and create a project (e.g. "kitty").
2. APIs & Services > Library: search "Gmail API" and click Enable.
3. APIs & Services > OAuth consent screen: choose External, fill in the app name
   and your email, then under Audience > Test users add your own Gmail address.
   Leave the app in Testing.
4. APIs & Services > Credentials > Create credentials > OAuth client ID, type
   "Desktop app". Download the JSON and save it as `config/gmail_client.json`
   (gitignored).
5. `uv run kitty gmail auth`: a browser opens; allow read-only access. The token
   goes to `data/gmail_token.json` (gitignored). While the app is in Testing,
   Google expires it after 7 days; run `gmail auth` again when a check says so.

```bash
uv run kitty gmail status            # set up? last check, proposals waiting
uv run kitty gmail check --days 30   # read recent recruiting mail, propose status changes
uv run kitty proposals               # what is waiting for me
uv run kitty proposals confirm 12    # or: proposals reject 12
```

Rules only, no model: a message is classified as rejection, offer, interview or
OA from explicit phrases ("unfortunately", "online assessment", "pleased to
offer"...; phrases inside "if ..." sentences don't count, so "we received your
application" is ignored), and matched to one open application by company name.
Anything ambiguous is skipped and counted. Each message is looked at once
(`gmail_messages` table). A proposal changes nothing until I confirm it: on the
**Pipeline** tab `v` lists proposals (`y` confirm, `n` decline) and `G` checks
Gmail in the background.

## Reports and export (Stage 6)

```bash
uv run kitty report                  # response rate by resume, track, source and tier
uv run kitty export                  # data/export/<date>/{jobs,applications,status_events}.csv
uv run kitty export -f json          # data/export/kitty-<date>.json
```

"Heard back" = OA, interview, offer or rejection; "OA+" = OA or further. The
**Reports** tab (`F10`) shows the same tables; `e` / `j` write the CSV / JSON
export. CSV cells that start with `= + - @` get a leading `'`, so a spreadsheet
never runs them as formulas.

## MCP server for my agent (Stage 7)

`uv run kitty mcp` serves the core over stdio (`--demo mcp` uses the demo DB).
Register it in an MCP client:

```json
{
  "mcpServers": {
    "kitty": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/kitty!", "kitty", "mcp"]
    }
  }
}
```

Tools: `today`, `list_jobs`, `get_job`, `preparation_plan`, `weekly_progress`,
`list_questions`, `finder_candidates`, `list_proposals`, `list_resume_versions`,
`get_resume_version`, `mark_applied`, `set_status`, `skip_job`, `mark_not_eligible`,
`confirm_proposal`, `reject_proposal`, `run_discover`. None submits anything,
writes or suggests screening answers, or writes resume text; `mark_applied` only
records an application I already submitted. Finder approvals stay in the TUI.
Each call opens and closes its own database connection.

`preparation_plan(track?, limit?)` lets an agent inspect the same favorites-first,
track-scoped batch as local autofill, with the configured hard cap and reasons for
leaving jobs out. `list_jobs`, `get_job` and selected plan jobs expose stored
preparation states, attempt/readiness timestamps and blockers, not browser-control
identifiers. This is a local snapshot, not a live browser check or a reservation:
ready means verified at the last attempt, not submitted. Planning does not open
Firefox, contact employers, fill forms or change applications/statuses; normal
workspace initialization may still migrate SQLite and sync local configuration.

## Resumes and the resume coach

Resumes live in `resumes/` (gitignored) and are registered by label; each track
sends its `default_resume`. Add one from the Profile screen (`F8`, `n`) or with
`kitty resumes add <file> --label <label>`.
Choose labels and track mappings for your own files in local configuration.
Resume sources, generated documents and any rendering scripts containing personal
content must remain in the ignored `resumes/` directory.

**Resume (`F7`) opens the resume library:** all active registered files, including
variants that are not a track default. Select a row to see its full local path and
description; `Enter` or `o` opens that PDF in the local viewer. The table shows
effective default-track assignments and missing files. `r` refreshes the library;
browsing or opening a PDF never changes a default or an application attachment.


The **Posting evidence** tab within Resume (`F7`, or `kitty coach [--track T]`)
counts, per track, how often postings mention each term in
`config/resume_terms.yaml`, with resume-line and job-description evidence.
Counts are vocabulary measurements, not an ATS score or proof of experience.
`t` / `T` changes the coach/candidate track, not the all-resumes library.
The coach remains read-only; candidate generation is separate.

### Local deterministic editing

```bash
uv run kitty resumes improve --track <TRACK> --source resumes/source.html
uv run kitty resumes versions --track <TRACK>
uv run kitty resumes review <VERSION> --open
uv run kitty resumes promote <VERSION>
uv run kitty resumes reject <VERSION>
uv run kitty resumes rollback --track <TRACK>
```

`improve` creates a candidate HTML/PDF and an audit manifest, not a new default.
`--source` can be omitted only when the source is unambiguous; `--min-postings`
sets the minimum cohort size (default 5). Review includes exact patches and diff,
existing-fact anchors, quoted JD evidence, cohort/source coverage, measurements,
warnings, missing-fact review items and PDF/layout validation. Open the PDF and
inspect it before approving.

On **Resume (`F7`)**, `g` generates a candidate, `v` opens version history/review,
and `b` requests rollback. The exact review screen uses `o` to open the local PDF,
`p` to explicitly approve/promote, and `x` to reject. Promotion and rollback require
confirmation. Rejection retains the audit artifacts.

The editor is local and deterministic: **no hosted AI, no uploads and no paid AI
calls**. It supports narrowly audited wording substitutions and evidence-ranked
ordering of already-supported skills/bullets within their semantic sections.
It does not write arbitrary new prose or add a skill because employers mention it.
Contact, education, dates, metrics and project status remain protected; unsupported
facts become review items. Source/artifact hashes and validation guard promotion.
Originals and immutable version artifacts are retained.

Promotion changes only the default for **future unchosen jobs**. Existing job
choices, open application attachments and historical application resume associations
are not rewritten. Rollback restores the prior default, not historical rows.

The [resume improver research](RESUME_IMPROVER_RESEARCH.md) retains primary-source
comparisons of Jobscan, Teal, Rezi, Resume Worded, Reactive Resume and Google Docs.
Open-ended rewriting, a visual source editor and cloud-document integration remain
outside the implemented bounded editor.

## Daily use in Stage 1

```bash
uv run kitty add https://jobs.example.com/postings/123 --track quant
uv run kitty add <url> --fetch                 # fetch the page title to prefill, still editable
uv run kitty status 12 oa --when 2026-10-01    # or press s in the TUI
uv run kitty list --track quant
uv run kitty resumes add resumes/quant.pdf     # stores path + hash + your description
uv run kitty import csv old-applications.csv   # preview, confirm, then write
uv run kitty tui
```

### Getting around

The tabs at the top of every screen follow a job's life, left to right, and can
be clicked: **Today** `F1` (what is due, what is new) → **Inbox** `F2` (decide on
new postings) → **Apply** `F3` (the apply session) → **Jobs** `F4` (everything
kept) → **Pipeline** `F5` (applications by stage) → **Sources** `F6` (where
postings come from), then **Resume** `F7` (resume library and posting evidence), **Profile**
`F8` (facts, resumes, preferences, answers), **Finder** `F9` (companies to
watch) and **Reports** `F10` (who hears back, export). The line under the tabs says what the current screen is for. On Today
every line that needs action is a link to the screen that handles it.

The look is a dark-fantasy theme (red sky, black keep, torchlight). Colours keep
one meaning everywhere: ember = keys and things you can act on, green = good,
gold = look closer, red = problem, grey = done or unimportant. The castle banner
on Today hides itself on terminals shorter than 36 rows.

### Keys

| key | does |
| --- | --- |
| `?` | help overlay, built from the current screen's real bindings |
| `ctrl+p` | command palette: screens, actions, and jobs by company/title |
| `F1`-`F10` | Today, Inbox, Apply, Jobs, Pipeline, Sources, Resume, Profile, Finder, Reports (or click the tabs) |
| `v` `G` | Pipeline: review Gmail proposals (`y` confirm, `n` decline), check Gmail now |
| `a` `r` `R` | Finder: approve into the watchlist, reject for good, run the finder |
| `e` `j` | Reports: export CSV / JSON to data/export/ |
| `a` `s` `l` `x` `q` | Apply session: applied (after I submit), skip, later, not eligible, log a question |
| `w` | Profile: answer a logged screening question (saved to answers.yaml) |
| `f` | fill & open the application form(s): marked Inbox rows, or the selected job. On Inbox / Jobs with nothing marked, and on Apply when the job shown is ★ (or `v` is on), every waiting favorite in the current track instead (like `F`). On Apply a plain fill moves to the next job; filled jobs wait at the end of the queue |
| `*` `v` | Inbox / Jobs / Apply / Job Detail: star or unstar (marked rows or the selected job); show favorites only |
| `F` | Apply: autofill the current track's favorites, temporarily selecting each employer tab (never submits) |
| `c` | Inbox / Jobs / Apply: choose one track (quant, parttime, ...) or every track; Escape keeps the current selection. On Jobs it narrows the saved view; on Apply it also scopes favorite filling |
| `esc` | back from Job Detail |
| `space` | Inbox: mark a row; `t` tier, `g` add track, `x` skip act on every marked row |
| `w` | Inbox: last 14 days / all new jobs |
| `1`-`9` | switch saved view (titles are on the views line) |
| `/` | filter bar; `escape` clears it |
| `a` | add a job by pasting a URL |
| `enter` | open Job Detail |
| `s` | change status (records the application if there is none yet) |
| `u` | undo the last status change (status changes only) |
| `n` `d` | next action date+note, deadline |
| `k` `t` `R` `e` | queue state, tier, resume pick, my notes |
| `t` `T` | Resume screen: next / previous coach and candidate track; the library still lists every resume |
| `g` `v` `b` | Resume screen: generate local candidate, versions/exact review, rollback default |
| `enter` `o` | Resume library: open the selected local PDF without changing its default or attachments |
| `o` `p` `x` | Resume review: open PDF, explicitly approve/promote, reject |
| `e` `n` | Profile screen: edit a config file in `$EDITOR`, add a resume |
| `o` | open the posting in my normal browser |
| `left` `right` | move between pipeline columns |
| `ctrl+r` | reload the yaml config and sync it |
| `q` | quit |

## Config

| file | committed? | what it is |
| --- | --- | --- |
| `profile.yaml` | example only | applicant facts; nothing is invented. `discipline` fills Major / Discipline / Field of study; `discipline_by_track` supplies track-specific values |
| `tracks.yaml` | example only | tracks: search terms, sources, location and eligibility rules, default resume, triage depth, weekly target |
| `sources.yaml` | example only | discovery adapters, rate limits, cache TTLs |
| `watchlist.yaml` | example only | company boards (`workday`: `tenant/wdN/site`; `workable`: account slug); `ats: none` keeps unpollable companies with reasons in `notes`; optional tags describe programs or locations |
| `answers.yaml` | example only | user-authored screening answers; optional `match`, `choices`, company and track scopes |
| `resume_terms.yaml` | example only | terms counted by the resume coach |
| `triage.yaml` | example only | triage model, effort, prices and per-run cost cap |
| `apply.yaml` | example only | follow-up reminder days, archive suggestions, deadline window |
| `skills.yaml` | example only | focus areas and per-run quotas for the Stage 5 finder |
| `conferences.yaml` | example only | exhibitor/sponsor lists for the Stage 5 finder |
| `gmail_client.json` | no | the Desktop OAuth client for Gmail (Stage 6), downloaded from Google Cloud |
| `views.yaml` | example only | saved views for the list screens |

The table names runtime files under `config/`; Git contains only their
`.yaml.example` counterparts. Example applicant details are fictional and must be
replaced locally before filling real forms.

Every file is validated on load, and errors point at the line:

```
config/tracks.yaml:6  tracks.1.weekly_targt: Extra inputs are not permitted
    fix: unknown key - remove it or check the spelling
```

## Database

SQLite at `data/apply.db` is the single source of truth. Schema changes are
numbered `.sql` files under `src/kitty/db/migrations/`, applied in order and
recorded in `schema_version`; `kitty init` migrates in place.

Two design points worth knowing:

* `status_events` is **append-only**. Undo appends a reverting row that points at
  the row it undoes, so the history shows both steps. Gmail (Stage 6) writes rows
  with `state='proposed'`, which change nothing until I confirm them.
* `jobs.queue_state` (new / queued / later / skipped / not_eligible / applied) is
  separate from `applications.status`. Skipping a job never creates an
  application row.
* `record_events` is **append-only** too: every successful content change made in
  the website (saving a page, creating a quick note or calendar event, editing a
  deck…) appends one row with only its section, entity, entity id, action and
  route, never a title or body. Settings, previews, timer and practice controls,
  background imports and changes already logged elsewhere (`flash_reviews`,
  `status_events`, `study_sessions`) are not captured; the route lists live in
  `kitty.core.record_events`.

## Tests

```bash
uv run pytest
```

The TUI tests drive the real app through Textual's pilot, so a broken key binding
fails in CI rather than in front of me.

## Stages

1. **done** - tracker: config, schema, core, CLI, Dashboard / Jobs / Pipeline / Job Detail, CSV import, demo data
2. **done** - discovery: repo feeds, watchlist ATS boards, Indeed, USAJOBS and HN; direct engineering employers, research programs, YC internships and bounded opt-in web search; dedupe, cross-track routing, rules-only eligibility, Inbox + Sources screens
3. **done** - LLM triage: tiers, resume pick and checked edit notes; estimate + confirm + hard cost cap; `--now` and `--batch`; cached per job
4. **done** - Today digest (TUI landing tab + `kitty today`, optional ntfy/Telegram push), Apply session, follow-up reminders, hand "not eligible", answering logged questions, weekly targets + streak, `kitty schedule print`
5. **done** - niche-company finder: YC, HN, conferences, GitHub, SBIR awards; skills.yaml scoring, per-area quotas, hard cap 15, approve/reject on the Finder tab
6. **done** - Gmail read-only status proposals (confirm with a key), response rates by resume / track / source / tier (`kitty report`, Reports tab), `kitty export` (CSV/JSON, formula-safe)
7. **done** - MCP server over `core/` (`kitty mcp`); own Firefox form filler (`kitty apply`; fills, never submits) and the resume coach
- **2026-10-03** - favorites (`*`), "more like these" best-first ranking, `kitty autofill` for favorites (fills, never submits)
- **Local resume editing** - deterministic candidate HTML/PDF, exact evidence-backed review, immutable versions, explicit promotion/rejection and rollback; no hosted AI or invented facts.
