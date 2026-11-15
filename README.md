<p align="center">
  <img src="docs/assets/rika-hero.svg" width="846" alt="rika! — a home for your beautifully tangled mind. A local-first workspace for connected ideas.">
</p>

<p align="center">
  Notes become connections. Connections become something you can build.<br>
  A local-first desktop workspace for your notes, documents, study and plans.
</p>

<p align="center">
  <b><a href="https://github.com/ZacharySF/kitty-releases/releases/latest">Download for Linux ↗</a></b>
  &nbsp; / &nbsp; <a href="#quick-start">Take it for a spin</a>
  &nbsp; / &nbsp; <a href="docs/GUIDE.md">Read the field guide</a><br>
  <sub>AppImage + .deb · Signed updates · MIT licensed</sub>
</p>

<p align="center">
  <a href="#your-workspace">Your workspace</a> ·
  <a href="#rika">Rika</a> ·
  <a href="#make-it-yours">Make it yours</a> ·
  <a href="#modules">Modules</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#reference">Reference</a>
</p>

<br>

<p align="center"><b>A little less scattered. A lot more connected.</b></p>

<p align="center">
  <img src="docs/screenshots/readme/home.jpg" width="100%" alt="The rika! home dashboard on black: today, a quick note and a focus timer on the left; up next and an agent session in the middle; and the Rika panel on the right with three drafted replies waiting for Send.">
</p>
<p align="center"><sub>Home: today, what's next, and Rika on the right with your replies already drafted.</sub></p>

<br>

## Your workspace

### Your mind isn't a filing cabinet.

A paragraph can belong to a project, a question and next Tuesday's study session.
rika! gives those connections somewhere to live: one SQLite database on your
machine, with an atlas instead of another pile of tabs.

### 01 &nbsp; Catch the thought

Write in blocks. Link a page with `[[wiki]]` links or refer to the exact passage
with `((block))` references. Follow backlinks, spread an idea across a canvas,
or turn it into a mind map.

Documents, PDFs and floating quick notes stay in the same workspace.
You don't have to know where an idea belongs before you write it down.

### 02 &nbsp; Give it somewhere to go

Keep flashcards and study beside the material you're learning. Put the next
step on your calendar or task list, set a goal and watch what moves it.
Open another tab, or use the command palette to move without losing your place.

<img src="docs/screenshots/readme/calendar.jpg" width="100%" alt="The calendar's week view: one header row with the range, Today, and Day, Week, Month, Agenda, Year and Plan; a hairline grid with soft colour-barred events, today's date in a filled circle and a line marking the time now; a small month and the selected day's agenda on the right.">
<p align="center"><sub>The calendar: one quiet row of controls, a line for now, and the day you picked beside it.</sub></p>

### 03 &nbsp; Follow the thread

Every record and every edit becomes a glowing point in the **Brain**: a
neural-network map of everything you've written, studied and planned. Prefer
flat ground? Switch to the **2D** map, where every point carries its name.
Same records, different ways to find your bearings.

<img src="docs/screenshots/readme/brain.jpg" width="100%" alt="The Brain view: a glowing neural-network brain threaded in red, orange, green and blue, above a legend counting applications, jobs, reminders, notes, calendar and more.">
<p align="center"><sub>The Brain as its own view. Demo workspace, synthetic data.</sub></p>

<br>

## Rika

### Someone to hand the loose ends to.

Rika reads what changed overnight and tells you in a few lines. She drafts the
replies; **you** press Send. Ask her for something by typing, or by talking if
you turn Voice on.

She has a page of her own for everything else: the coding agents working for
you (what each is doing, how full its context is, what it has cost), the skills
you can drag onto one, what she remembers, and what all of it has used.

<img src="docs/screenshots/readme/rika.jpg" width="100%" alt="Rika's page: the conversation with three drafted email replies on the left, and on the right four coding agents with their task, context, tokens, cost and state.">
<p align="center"><sub>Rika's page: the conversation on the left, your agents on the right.</sub></p>

<br>

## Make it yours

### Your desk, not ours.

Press **Ctrl + Shift + Space** on any page. Drag a block where you want it, pull
an edge to resize it, and open **Widgets** to drag in a clock, a quick note or
your goals. Press Done and the page stays that way.

Home goes further: eighteen templates to start from, widgets in the order you
like, and six quiet colours to tell them apart.

<img src="docs/screenshots/readme/edit.jpg" width="100%" alt="Edit mode on the Find jobs page: a clock and a quick note dragged onto the page, a dashed outline with a grip around the quick note, and a small bar at the bottom with Widgets, Remove widget, Reset page and Done.">
<p align="center"><sub>Edit any page: move it, size it, drag a widget in.</sub></p>

### Black by default. Loud when you want it.

rika! opens in **Black**: white type, hairlines, nothing glowing. Turn Themes on
for seventeen more, from Nord and Gruvbox to **Kitty**, a record sleeve from
about 1999 with a different film still behind it every time you open it.

Turn **Animations** on and pages turn instead of blinking: they slide in the
order of the bar, grow out of what you pressed, and the marker of where you are
glides over. Everything stops if your system asks for less motion.

<img src="docs/screenshots/readme/kitty.jpg" width="100%" alt="The Kitty theme: the same home in heavy capitals and tube yellow over a green, grainy film still of a corridor, with the page's name running down the edge.">
<p align="center"><sub>The Kitty theme. Same home, different night.</sub></p>

<br>

## Modules

### A workspace, not a fixed collection of tools.

Keep the parts you use. Turn off the parts you don't.
Add a terminal when you're building, enable ApplyAI when you're looking for work,
or download the Brain when you want to see it all at once.

<p align="center">
  <img src="docs/screenshots/readme/modules.jpg" width="72%" alt="Settings › Modules: switches for ApplyAI, Workspace, Math, LearnHarness, Terminal, Voice, Brain, Themes, Animations and Icons, each with one line saying what it does.">
</p>

| Module | What it brings |
| --- | --- |
| **Workspace** | Notes and block references, documents and PDFs, canvas, mind maps, flashcards, study, calendar, tasks, goals and floating quick notes. |
| **Terminal** | A pinned [herdr](https://github.com/herdrdev/herdr) session in a resizable dock. Open omp, Claude or Codex in labelled agent tabs with live activity. |
| **ApplyAI** · optional | Job search in four steps: Find, Saved, Apply, Track. It fills forms but **never submits an application** and never invents facts about the applicant. |
| **Math**, **LearnHarness**, **Voice** · download | Checked math practice, the LearnHarness app inside Study, and talking to Rika. Each fetches what it needs when you turn it on. |
| **Brain** · download | Your workspace as a 3D brain or a 2D map, as its own view. |
| **Themes**, **Animations** · download | Seventeen more looks, and motion for pages, widgets and menus. Off until you ask. |
| **Icons** | An icon beside every name, if you like them there. |
| **Core** · always on | Home, Rika, tabs, command palette, settings and module selection. |

### Small to install. Yours to grow.

The installer carries the window, Rika and the core. Everything heavier is a
**pack**: downloaded from the release when you switch its module on, checked
against a signed list, and removed again when you switch it off and say so.

### From a thought to a terminal

The Terminal module streams herdr into the window over a same-origin WebSocket.
Keep a session in the dock or open the full herdr view, with coding agents in
their own tabs.

If you separately install [Stencil Tern](https://stencil.so/tern), rika! can
also drive its own Tern session: list live and exited panes, run a command,
focus a pane, or capture one to a quick note.

### Looking for work? Four steps, one bar.

<img src="docs/screenshots/readme/jobs.jpg" width="100%" alt="The Apply page of job search: Find, Saved, Apply and Track across the top, a list of ranked jobs with two ticked, and a small bar at the bottom offering Save, Change, Check and Fill in 2 forms.">
<p align="center"><sub>Tick the jobs, press Fill. rika! stops before Submit, every time.</sub></p>

### Leave the clutter. Keep the data.

Disabled modules disappear from navigation, routes, workers and MCP tools;
**their data stays intact**. Install add-ons from a GitHub release in
**Settings → Modules**, checked against the release's SHA-256.
The [module guide](docs/MODULES.md) explains the format and how to publish one.

<br>

## Quick start

**Just want the desktop app?** [Download the latest Linux release](https://github.com/ZacharySF/kitty-releases/releases/latest).
Choose the AppImage or .deb; signed updates are built in.

<p align="center">
  <img src="docs/screenshots/readme/welcome.jpg" width="62%" alt="The rika! welcome card: a glowing neural-network brain above the words 'Welcome to rika!', with Continue with Google and Start locally buttons">
</p>
<p align="center"><sub>First launch: one button to start locally. No account needed.</sub></p>

### Run from source

Requirements: Git, Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22+ and Rust (cargo).

```bash
git clone https://github.com/ZacharySF/kitty.git
cd kitty
uv sync
npm --prefix web ci
npm --prefix web run build
cargo build --release --manifest-path server/Cargo.toml   # rika-server, the local front
uv run kitty-web --demo
```

The demo uses a **disposable workspace with synthetic data**. Open the printed
`http://127.0.0.1:…` address, not `localhost`: the server checks the exact Host.

For a real workspace:

```bash
uv run kitty init
uv run kitty-web
```

The [full guide](docs/GUIDE.md) covers modules, the desktop build, configuration
and every command. The repository and CLI still use the `kitty` name.

## Architecture

```mermaid
flowchart LR
  subgraph Desktop["Tauri shell (Rust)"]
    Updater["Signed updates"]
  end
  UI["Svelte 5 + TypeScript UI"]
  Front["rika-server (Rust)<br/>loopback HTTP + WebSocket,<br/>the routes already moved"]
  Worker["Python worker<br/>the routes still to move"]
  CLI["CLI (Typer)"]
  TUI["TUI (Textual)"]
  MCP["MCP server"]
  Core["Python core<br/>(no UI imports)"]
  DB[("SQLite<br/>numbered migrations")]
  Herdr["herdr sidecar"]
  Packs["Signed module packs<br/>(release downloads)"]

  Desktop --> UI
  Desktop --> Front
  UI --> Front
  Front --> DB
  Front -->|"everything else"| Worker
  Front -.->|"on request"| Packs
  Worker --> Core
  CLI --> Core
  TUI --> Core
  MCP --> Core
  Core --> DB
  Worker -->|"terminal socket"| Herdr
```

Two rules hold the layout together. Nothing under `src/kitty/core/` imports a UI
framework, so the website, desktop app, CLI, TUI and MCP server all call the
same functions against the same database. And the core is moving to Rust one
route at a time: a route moves only with a test that it answers exactly as the
Python it replaces ([how, and what stays in Python](docs/rust-core-design.md)).

## Engineering notes

- **Edits are never silently lost.** Pages, folders and block outlines carry a revision;
  a save against a stale revision is rejected, and a second window editing the
  same note offers *Load latest* or *Keep mine* instead of overwriting.
- **References point at rows, not copies.** Block references resolve the
  authoritative block at read time, with parent-cycle checks and bounded
  expansion.
- **History is append-only where it matters.** Application status changes are
  events; undo appends a reverting event instead of rewriting history.
- **The local server is not an open door.** It binds to loopback, checks the
  exact Host and Origin, requires a custom header on every mutation, and requires
  a per-launch secret only rika!'s own windows hold on every API call and
  WebSocket, so a local program that forges the headers still gets nothing.
- **Rust where it pays.** Calendar, search, goals, study, flashcards, mind maps
  and settings are answered by rika-server; the flashcard scheduler is an exact
  port, checked against the Python one card by card. What leans on Python's own
  libraries (math checking, job scraping, form filling) stays there.
- **Downloads are checked twice.** A pack is fetched only from the release of the
  running version, against a list signed with the updater key, then against its
  size and SHA-256; a failed download changes nothing.
- **It ships.** Tauri builds signed Linux installers in CI and the app updates
  itself, including through an `appimage-run` launcher on NixOS.
- **Browser automation without a framework.** ApplyAI drives Firefox through its
  own WebDriver BiDi client and stops at every human boundary: sign-in
  challenges, consent, unknown answers and the final submit.

## Develop

```bash
uv run pytest -n auto                           # Python core, web adapter, TUI
cargo test --manifest-path server/Cargo.toml    # rika-server
cargo test --manifest-path desktop/Cargo.toml   # the desktop shell
npm --prefix web run check                      # svelte-check / TypeScript
```

The browser tests in `web/tests/*.browser.test.mjs` drive a real Chromium against
the demo; run them one file at a time. CI runs the Python tests, the frontend
check and the web unit tests on every pull request.

## Reference

| Looking for… | Start here |
| --- | --- |
| Setup, configuration and everyday use | [Full guide](docs/GUIDE.md) |
| Exact behaviour and contracts | [Behaviour spec](docs/SPEC.md) |
| Building and publishing an add-on | [Module guide](docs/MODULES.md) |
| The move to Rust: rules, what is built, what stays | [Rust core design](docs/rust-core-design.md) |
| Rika: how she is put together | [Rika design](docs/rika-design.md) |
| Linux installers and release notes | [Desktop releases](https://github.com/ZacharySF/kitty-releases/releases/latest) |

## Built on

rika! integrates these projects rather than reimplementing them; credit for
their features belongs to their authors.

- [herdr](https://github.com/herdrdev/herdr) (Apache-2.0): the terminal
  multiplexer behind the Terminal module, bundled at a pinned version.
- [LearnHarness](https://github.com/amosblomqvist/learnharness) (AGPL-3.0): the
  upstream learning app opened from Study, run as a separate local server in a
  cross-origin frame. Its licence and corresponding source ship with the desktop
  app.
- [Grinstead and Snell's *Introduction to Probability*](https://math.dartmouth.edu/~doyle/docs/prob/)
  (GNU FDL): its exercises, used as a textbook source in Math, ship with their
  licence and source note in `third_party/grinstead-snell/`.
- [Tauri](https://tauri.app), [Svelte](https://svelte.dev),
  [Textual](https://textual.textualize.io) and
  [3d-force-graph](https://github.com/vasturiano/3d-force-graph).

The README wordmark uses [Fraunces](https://fraunces.undercase.xyz), by Undercase
Type, with a subset embedded under the [SIL Open Font License](docs/assets/OFL-Fraunces.txt).

## License

[MIT](LICENSE). Bundled third-party components keep their own licences.
