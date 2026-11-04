# rika! user and operator guide

The full reference: setup, every module, the desktop app, configuration and the
database. The [README](../README.md) is the short overview.

A local workspace with three optional built-in modules: **ApplyAI** for job discovery,
application preparation and tracking, **Workspace** for notes, documents, study and
planning, and **Terminal** for rika!'s own bundled herdr terminal session. The shell
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
| Terminal (`terminal`) | rika!'s own [herdr](https://github.com/herdrdev/herdr) session (workspaces, tabs, splits, agents) streamed into the window; your installed [Stencil Tern](https://stencil.so/tern), if any, opened on a session of rika!'s own |
| Always-on core | Home, tabs, settings, module selection, shared AI-provider/budget controls and local storage infrastructure |

Use **Settings → Modules**, the terminal UI's Modules screen (`M` from the job
tracker), or `kitty modules enable/disable <id>`. The selection is the `modules`
list of the workspace's build file, stored locally (the build file is described in
`docs/platform-design.md`, Q8/Q9):

```json
{"schema": 1, "modules": ["applyai", "workspace", "terminal"]}
```

Save, then **quit and restart rika!** to activate the change. Refresh/reload does
not unload running modules, restart workers or discard drafts. Settings → Modules
marks a module whose saved choice differs from the running session with
**Restart** (its tooltip says whether it starts or stops). CLI invocations are separate
processes, so the next command uses the saved selection.

Disabled modules disappear from navigation, commands, shortcuts, atlas sections
and MCP tool listings; their routes/actions are refused and their workers do not
start. Their data, attachments and configuration are retained for re-enabling.
ApplyAI reminders work without Workspace; career calendar contributions and
cross-module results appear only while both modules are enabled.

Existing workspaces with no module selection retain every module; a pre-build
`config/modules.yaml` is moved into `config/build.json` on the first open. Fresh CLI and
desktop workspaces start core-only. The database still uses the existing global
migrations; disabling does not drop tables or run down-migrations. These are
built-in modules, not installable third-party extensions.

### Builds

`config/build.json` is the workspace's build (`docs/platform-design.md` Q8, Q9): modules,
settings (including Rika's tier models), theme tokens, custom CSS and interface preferences
(theme, graph look and layout, hidden atlas sections, pen mode, coding agent). Every window follows it live:
a save from Settings, the build editor or any text editor restyles every open window.
A save with an error changes nothing; every window shows the error's line and keeps the
last good build. Preferences a browser kept before builds move into the build once.

Settings → Build edits the build's name, author and description, exports it as
`kitty-build.json`, imports one from a file or an `https` link with a diff of what changes
(module changes are marked as needing a restart; privacy and cost changes apply only when
you tick *Also apply privacy and cost changes*), and lists earlier builds with one-click
rollback. *Open build editor* (or *Edit build* in Ctrl+K) docks the file at the right of
the window: problems are marked at their lines, theme tokens and CSS show in that window
as you type, and valid text saves itself (Ctrl+S saves at once).

Safe mode shows rika! without the build's custom CSS and theme tokens: press
Ctrl+Alt+Shift+S (again to turn them back on), open a window with `?safe`, or start
`kitty-web --safe`. A small control outside the styled page says when custom style or
safe mode is on and switches it.

### Terminal module

The desktop app bundles a pinned herdr (v0.9.5, Apache-2.0; its licence ships as
`licenses/herdr/LICENSE`) and never uses a herdr found on `PATH`. rika! runs it as
its own named session, `kitty` (never herdr's `default` session), so agents can
reach it with `herdr --session kitty`. Its sockets live where herdr keeps named
sessions, `~/.config/herdr/sessions/kitty/`: rika! creates that directory
owner-only (0700) and refuses a session whose sockets are not owner-only (0600).
The Harness colours come from rika!'s private `.private/herdr/config.toml`
(`HERDR_CONFIG_PATH`), so your own herdr configuration is neither read nor changed.
Nothing starts until a page opens a terminal. Closing the window with **Keep running
on close** leaves terminals running and reopening reattaches; **Quit rika!** (or
closing the window without that setting, or Ctrl-C on `kitty-web`) stops rika!'s
herdr session. With the module off there is no herdr process, socket or route.

The page reaches it through a WebSocket on rika!'s own loopback origin
(`/api/terminal/socket`). Browsers cannot add `X-Kitty-Request` or the UI secret's
header to an upgrade, so the upgrade requires the exact Host and Origin, the UI
secret as a WebSocket subprotocol, and a single-use token that expires after 30
seconds and is issued only by the header- and secret-protected `POST
/api/terminal/session`. A development `kitty-web` uses `KITTY_HERDR=<herdr 0.9.5
binary>`; `KITTY_HERDR_SESSION` overrides the session name.

**Terminals.** There is no terminal dock: the bottom of the window is the Rika bar
(see **Rika** below). Terminals open as native Tern windows: **Open terminal** in
Ctrl K, or ask Rika ("open a terminal"). Without a usable Tern, Open terminal
shows the **Tern** view, which says why. The **herdr** node under Plan in the atlas
opens the whole herdr session in the workspace tab, in a transparent terminal drawn
in the Harness colours. In a terminal, bare keys (including `f`) go to the shell;
**Ctrl+Shift+F** fullscreens the terminal panel and **Esc** returns. The v0.3.25
dock state (`kitty.dock` in the browser profile) is no longer read or written.

**Agents (Q2/Q9).** In the herdr view, **New Agent tab** starts the chosen
installed coding agent in a new herdr tab labelled **Agent**, in this rika!
workspace's directory. The chooser defaults to **omp**, also supports **claude**
and **codex**, and remembers the last choice in this browser profile. rika! does
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
External agents use herdr's own CLI/socket, not a rika!-specific control API:
list workspaces, create a separately labelled tab with `tab create --workspace
<id> --cwd <directory> --label <name> --no-focus`, then target the returned
`root_pane.pane_id` explicitly. Read herdr's own skill before controlling panes.
Never rely on another client's focused pane or switch to the `default` session.
Their tabs appear live in the herdr view, and Rika's history drawer (**Agents**)
shows one card per detected agent: its state, task (the pane's title), last output
line and how long it has been in that state; **Open window** focuses its Tern window.
Local CLI/socket access grants full shell access as your user.

**Tern (optional).** With the module on, the **Tern** node under Plan shows
[Stencil Tern](https://stencil.so/tern) beside herdr. herdr stays the bundled
default; rika! never bundles Tern (closed-beta, proprietary) and only runs the
`tern` you installed: the one on `PATH`, or the path saved in the view's **Tern
executable** section (`.private/tern/settings.json`, owner-only). Without it the view
says so and points to stencil.so/tern; nothing else changes. **Open in Tern** starts
rika!'s own Tern daemon on a private socket,
`$XDG_RUNTIME_DIR/kitty/tern-<workspace hash>/daemon.sock` (directory 0700, a
socket others can reach is refused), gives it a `kitty` session in the workspace
and opens a native Tern window on it (`tern --dir <workspace>`); your own Tern
daemon and sessions are never read or changed. On NixOS the AppImage runs inside
`appimage-run`'s FHS sandbox, whose `/etc/profile` would hide your programs (git,
nix, python3, agents) from Tern's login-shell panes, so there rika! starts the
daemon and windows outside it as systemd user services (`kitty-tern-<workspace
hash>`, through `systemd-run --user`) in your session's environment; without
`systemd-run` they stay rika!'s children. The view is a status panel (version,
daemon state, sessions, panes and open windows from `tern inspect --json`, which
never starts anything): Tern's web client needs WebGPU, which the desktop app's
WebKitGTK view lacks, so rika! does not embed it. Scripts reach the session with
`TERN_DAEMON_SOCKET=<socket> tern ls`. Quit closes the Tern windows rika! opened
and stops its Tern daemon (Tern restores the layout next time); closing the window
with **Keep running on close** leaves them running.

The Tern view's **Panes** section lists the panes of rika!'s Tern session (only when Tern
is installed) with their state, including exited panes and their exit status, and
recent pane starts and exits. **New pane** adds a tab to the session, **Run** types
one command line into the chosen pane (multi-line or control characters are
refused), **Focus** shows a pane in rika!'s Tern window and raises it (opening
one when none is open) and **Capture** saves the pane's text, terminal controls stripped and the
newest 60 KiB kept, as a quick note (Workspace module) that **Open note** pops out.
These act only on rika!'s own daemon and its `kitty` session
(`POST /api/terminal/tern/{new,run,focus,capture}`).

**Agent skills (docs/agent-skills-design.md).** Each agent card in Rika's history
drawer (**Agents**) shows a **now: skill · tool** chip, what the agent is using right
now (for example "now: lavish · read"; "now: —" until something answers), and a
**Skills (N)** button that lists that agent's skills in the drawer. The Rika bar's
agent chips carry the same text in their tooltip, with omp's one-line intent.
**All agent skills**, under Plan beside Tern (also in Ctrl K, or ask Rika to open
"agent skills"), catalogues every agent kind's **Agent skills**, **Tools (MCP)** and
**Plugins**, even with no agent running, with the running agents and their now
chips in a strip above. Each skill row has **Copy path** and, in the desktop app,
**Open file**, which opens that `SKILL.md` with your desktop's default opener (the
shell accepts only an absolute path to a file named `SKILL.md` that rika! listed).

- **Off until you turn it on, per agent kind.** The switch **Show agent skills and
  activity** sits in the All agent skills view, in a card's Skills list and in the
  New Agent tab chooser. It is saved owner-only in `.private/agents/settings.json`.
  While a kind is off, rika! opens no file under `~/.omp`, `~/.claude`, `~/.codex`
  (nor `~/.claude.json` or `~/.agents`) for it.
- **What it reads, names only.** `SKILL.md` frontmatter (`name`, `description`) under
  each agent's own skill folders: omp's `~/.omp/agent/skills`, its extension
  packages, managed skills and the project `.omp/skills`, `.claude/skills`,
  `.agents/skills`, `.codex/skills` and `.github/skills`; Claude's `~/.claude/skills`
  (including synced skills), its installed plugins' skills and the project
  `.claude/skills`; Codex's `~/.codex/skills` (with `.system`), `~/.agents/skills`,
  the project `.agents/skills` and its plugin cache. MCP server names and enabled
  flags come from omp's `mcp.json` files, `~/.claude.json` and the project
  `.mcp.json`, and Codex's `config.toml`; every other value (commands, arguments,
  `env`, `headers`, URLs) is dropped while the file is parsed, and the agents' CLIs
  are never run. Credentials (`~/.claude/.credentials.json`, `~/.codex/auth.json`,
  omp's `agent.db`) are never opened. Nothing is stored in the database, nothing is
  synced to peers and no ingress route serves it (`GET /api/terminal/agent-skills`,
  page guard only).
- **How "now" is found**, first source that answers: tokens that kitty!'s own
  per-launch hooks report on the agent's herdr pane (`kitty-tool`, `kitty-skill`
  through `herdr pane report-metadata`), the report of kitty!'s omp extension (which
  adds omp's intent line, at most 120 characters), then the last 64 KB of the
  agent's session log named by herdr's `agent_session` (or the newest log for the
  pane's directory), reduced to the last tool name and skill name. Session-log
  formats change between agent releases; anything unrecognised shows "—".
- **Per-launch hooks, never installed globally.** For agents rika! starts (New Agent
  tab, Rika's `start_worker`), rika! writes its hook and omp extension to
  `.private/agents/` and passes them on that launch only: Claude Code gets
  `--settings '{"hooks":{"PreToolUse":…}}'`, Codex `-c hooks.PreToolUse=[…]` and omp
  `--extension .private/agents/kitty-activity.ts`. The hook runs `python3` once per
  tool call and skips a repeat within two seconds. Codex runs a new hook only after
  you trust it in its `/hooks` screen; until then rika! reads Codex's session log
  instead and says so once.
- **Per-launch skill checklist.** With a kind on, **New Agent tab** lists its skills
  as checkboxes and shows the flags they become: omp `--skills 'a,b'` (or
  `--no-skills`), Claude `--settings '{"skillOverrides":{"x":"off"}}'`, Codex
  `-c 'skills.config=[{name="x",enabled=false}]'`. Rika's `start_worker` takes the
  same list as `skills_off`, and its approval card names the skills switched off.
  Your own agent configuration files are never edited.


## Try it on fake data first

```bash
uv run kitty --demo demo reset    # ~30 invented jobs, all labelled "(demo)"
uv run kitty --demo tui           # learn the keys without touching real data
```

`--demo` uses `data/demo.db`. `demo reset` refuses to write to any file that is
not named `demo.db`, so it cannot wipe real applications.

## Local website

The Svelte website uses the same Python core and SQLite database as the CLI/TUI.
The workbench uses `@nunera/bracket` with a compact 13px type scale and three
typefaces, each with one job: Fraunces for the page title only, Inter for the rest
of the interface (headings, labels, buttons, body) and JetBrains Mono for the
navigation chrome, key caps, code and tabular figures; Fredoka sets only the
wordmark. Labels, eyebrows and fold-out labels are Inter semibold in sentence case,
never italic. The Theme menu persists the chosen theme locally. A theme is more
than colours: each one also sets what its surfaces are made of (frosted glass,
matte paper with grain, flat sheets, or square double-ruled frames), its focus
ring (a plain ring, a soft glow, petal corners or corner brackets), how selected
options, tabs and segments look, its motion style (calm, fluid, snappy or none)
and its backdrop. Typefaces never change with the theme.
- **Kitty** (the default): frosted pink glass over a soft blossom backdrop, a
  sakura accent apart from the dusty structural pink, and six distinct data
  colours (sakura, lavender, sky, mint, peach, butter). Its motif: list dots
  become blossoms, ticked checkboxes bloom into a sakura, section rules part
  around a blossom, the active tab and group grow cat ears, keyboard focus shows
  two petals at the ring's corners, view loaders are the cat inside a petal
  spinner and empty states a pink sleeping kitty.
- **Liquid Glass**: clear, neutral glass like macOS/iOS (heavy blur and a white
  rim lit from the top-left, no tint; in the desktop window your wallpaper shows
  through), a soft glow on focus, lit selections, a glass-ring loader and a
  frosted orb for empty states.
- **Scythe**: engraved frames with corner ticks under a moonlit meadow,
  crescent bullets, blade dividers, bracket focus and a crescent loader.
- **SPEED**, the one light theme: hand-inked on off-white watercolour paper.
  Square paper sheets with an inked edge and a crimson misprint over grey-teal
  washes, teal-green and black ink splatter and drips, a white gothic cathedral,
  faint gear rings that turn slowly and a crimson sketched wheel; crimson-magenta
  for accents and a sketched double focus ring, teal washes for selection, ink-splat
  bullets, crimson checks that stamp on, brush-stroke dividers, a marker swipe
  under page titles, a ticking gear loader and a stalled-gear empty state.
- **c**: an engraving on black. Double-ruled frames tipped with blood-red
  diamonds, a rope-and-knot window frame, an etched diamond lattice, blood-red
  washes and a slowly turning wheel of sigils, with a rare muted teal; sigil
  bullets, diamond ticks that fill with blood, knotted rope dividers, a blood glow
  on focus, a sigil-ring loader and a sigil-seal empty state.
- **red**: stark black and white with one blood-red brush. Square black blocks
  inside a white paper margin set with crop marks and an issue line in tracked
  capitals, a faint stencil cat, and "rika" signed in a dripping red brush hand;
  the red ring drips under keyboard focus, the current tab and group drip, white
  rules drip once, page titles sit on a red swash, and the loader paints a stroke.
- Harness and Nord are flat, Catppuccin Mocha and Gruvbox are matte paper, and
  Dracula and Tokyo Night are glass, each with its own focus, selection and motion.
- **pretti**: glossy airbrushed graffiti on purple-black under a dark vignette:
  candy-glass panels with a magenta rim, acid-green primary pills, magenta
  selections, an acid glow on focus with a little heart, heart and star bullets,
  heart ticks, candy-chain dividers, ringed targets, hearts, stars and butterflies
  in the corners, a hypnotic spiral loader and a butterfly empty state.
- **BloodLuxe** (the light one): cream paper panels rimmed in black over a marbled
  red, white and blue sky, rose ridges, grass and flames, inside a black barbed-wire
  window frame; blood-red titles, section rules and focus drip, blood-drop bullets,
  bead ticks, a marble-orb loader and a thorn-wreathed heart for empty states.
- **half blood**: storybook heraldry in flat bold colour: navy panels edged in bone
  over an ultramarine field of white cloud squiggles with a grass hem, inside a
  bone filigree frame with crossed bones; a sun-eye and a crescent moon, sun-orange
  actions, sun bullets, green ticks, red drips on titles, rules and focus, a sun-eye
  loader and a moon asleep on a cloud for empty states.

Surfaces turn opaque under reduced transparency, and every theme's text,
selected and pressed controls stay at WCAG AA contrast over its backdrop. Graph
nodes and their edges use the active theme's graph palette.
**Settings** is a left index of short pages, each one surface: **General**
(Appearance, Desktop, Quiet hours), **Workspace** (Account, Modules, Graphs) and
**Careers & AI** (Profile & saved answers, AI & privacy, which open their own
views). **Find a setting** filters the index by name or keyword; Enter opens the
first match. Every page has its own link, `#settings/<page>` (`appearance`,
`desktop`, `quiet-hours`, `account`, `modules`, `graphs`), so Back returns to the
previous page; plain `#settings` opens Appearance. Explanations of individual
controls, such as the graph physics sliders, are tooltips on their ⓘ.
**Settings → Appearance** sets the whole look in one panel beside a live
preview: theme, title font (Fraunces or plain), density (compact 13px or
comfortable 14px), motion (full or reduced), home (Brain or 2D map), section graph look
(2D or 3D), the theme motif (turn it off to keep a theme's surfaces without its
bullets, ticks, dividers and ornaments) and the background (the theme's backdrop,
or with drifting petals). Reduce motion, here or in the system, stops every theme
animation and transition. **Cozy**, **Focused** and **Minimal** set all of them
at once; any single control still changes afterwards. Choices are saved in this
browser (`kitty.appearance`, plus `kitty.theme` and `kitty.graph.look`) and
applied before the app draws.
No Rust service is needed: the Python core
owns application behavior.

From the project root, with Node 22+ and npm available:

```bash
npm --prefix web ci
npm --prefix web run build
uv run kitty-web                 # rika! website: http://127.0.0.1:8765/?code=…
```

On NixOS without Node on PATH, prefix either npm command with
`nix shell nixpkgs#nodejs_22 --command`.
Open the printed link, `127.0.0.1` and not `localhost`: the server validates its
exact loopback Host/Origin. The link carries a one-time `code` (as Jupyter's token
link does) that the page trades for this launch's UI secret; it works once, within
ten minutes, and leaves the address bar at once. kitty-web then prints the next
link, one per browser tab (a tab keeps its secret across reloads in its
`sessionStorage`), and a fresh one whenever the current link expires. A tab opened
without a valid code, or kept from an earlier launch, is refused: "rika! did not
recognise this window". With the Vite dev server (`npm --prefix web run dev`, which
proxies `/api` to port 8765), open `http://127.0.0.1:5173/?code=<code>` with the
code from kitty-web's link. See [Local API protection](#local-api-protection-the-ui-secret).
`--port`, `--root` and `--static-dir` override the port,
workspace and built-asset directory. The refresh button and command palette reload
configuration from disk; saves in My profile reload it automatically.

The first-run welcome offers **Continue locally** or an optional Google identity
connection, followed by a skippable guided tour of workspace panels, modules,
Atlas, quick notes, Study and the Rika bar. Completion is
remembered per workspace. Reopen either flow in **Settings → Account** (**Connect
Google** or **Take the tour**; **Setup guide** links to the Google setup document).
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

### The Rika bar

**Rika** is rika!'s own agent; what it can do, its tiers and what it asks first are
under **Rika** in the AI settings section below. It lives in the **Rika bar**,
the slim bar along the bottom of the window: type a request in **Ask Rika…** and
press Enter. While Rika writes its reply the whole bar breathes with a soft ring of
light (under Reduce motion the ring stays lit and still). Each reply lands as a toast
above the bar, in the reminder toasts' look, with the reply, its tier chip (**fast**,
**standard** or **deep**: the model size Rika chose) and what it did; anything it
changed in the app carries **Undo**. Reminder toasts stack above Rika's.

**History** on the bar, or **Ctrl+J** from anywhere but a terminal, opens the history
drawer on the right: **Conversation** holds every request and reply, the action
cards Rika ran (Undo while it can still be undone) and its approvals. Anything
outward or irreversible waits for **Approve** or **Reject** on an approval card
that says what will happen and, when it has one, the estimated cost. The drawer
opens by itself when Rika asks for an approval and
closes once you decide; one you opened yourself stays open. **Agents** holds a live
card per worker agent (omp, Claude Code, Codex) in herdr or Tern: state, task, last
output line, a timer and **Open window**. Each Rika reply in the drawer ends with
its readout (tier, tool path, tokens and cost; **Direct** when no model ran), and
each email Rika drafted or holds for sending is a card with **Send**, **Undo send**
and its countdown, or **Discard**.

When today's morning brief exists, a **Morning brief · N** chip sits left of the
agent chips; its popover lists the day's finds and **Open Goals**, and opening it
marks the brief seen.

Running worker agents also show as chips on the right of the bar, a state dot and
name each (working, blocked, idle, done); a chip opens that agent's Tern window.
Past four agents the chips collapse to one count, which opens the drawer's Agents.
When Rika opens a page ("open calendar"), the workspace tab navigates there; when an
agent has no Tern window to focus, its chip opens the herdr view.

**Talk to Rika.** Hold **Space** in the bar's field for at least 0.3 s and speak;
let go and Rika gets what you said (a quick tap still types a space; words typed
first lead the spoken request). While it listens the whole bar breathes and the field
shows the words as they are heard; **Escape** drops them and nothing is sent. The
bar draws no Space keycap or hint: the breathing ring is the only sign. Keep holding
for 8 s and it becomes a **brain dump**, which keeps listening after you let go;
press Space again to finish. **Brain dump** in the history drawer or in Ctrl K starts
one directly. A dump's transcript grows live in the drawer, which opens by itself;
**Done · make plan** ends it (Cancel drops it). Rika then puts **one plan** in the
drawer, grouped by area (notes, tasks, calendar, cards, jobs, agents), every item
ticked and showing the words it came from. Untick what was misheard (or a whole
area), answer its one question if it asks one, then **Approve all** or **Approve
N of M**; **Dismiss** makes nothing. The drawer closes by itself once you decide.
Each dump is kept as a note in the **Brain dumps** notebook: the timestamped
transcript, and once the plan has run, links under **Created from this dump** to
everything it made (`[[Page]]` links, and links into Calendar, Flashcards, Study and
the herdr view).

When you spoke to Rika, it also speaks back a short summary of its reply (its first
sentences), if a local speech engine is installed: Piper with a voice model
(`KITTY_PIPER_VOICE`, or an `.onnx` voice in `data/voice/piper/`), else
espeak-ng or espeak. Without one Rika stays silent. Typed requests are never spoken.

### Controls and surfaces

- **One control height.** Buttons, fields, dropdowns, segments, menu rows and tabs
  are 28px tall (32px at the comfortable density); the one or two primary actions
  of a page are a step taller. Buttons, toggles and segments are pills; fields and
  dropdowns have softly rounded corners.
- **One surface per region.** A page's main column, a side rail or a drawer is a
  single panel; groups inside it are split by a hairline and a label, never by a
  box inside a box.
- **Status as a dot.** A view that saves (Notes today) shows a small dot on its
  workspace tab instead of "Saved" text: neutral when saved, amber with unsaved
  changes, red when saving failed. Hover it for the detail and the shortcut.
- **⋯ menus and popovers.** Rarely used actions sit in a ⋯ menu, always the last
  control of its header; small forms open in a popover anchored to the control.
  Arrow keys, Home/End and the first letter move through a menu, Enter runs an item
  and Esc closes it and returns focus.
- **Dropdowns.** Four or fewer choices are a segmented control. Longer lists open
  a themed list with a check on the current item (arrow keys, type-ahead, Enter,
  Esc); decks, notebooks and time zones open with a search field. Section graphs'
  **Browse** list is such a popover over the graph.
- **Adding and editing.** New things open a small quick-add popover from their +
  button: one phrase field and at most two chips; Enter saves, Esc keeps the
  phrase, **More** opens the full editor. Opening an existing item shows the side
  drawer with every field (the calendar's event and task drawers are the first).

### Interface copy rules

Text in the interface earns its place:

1. Helper text appears only in an empty state or directly before a consequential
   or irreversible action (deleting, sending data off this device, spending
   money), and is at most 12 words.
2. Every other explanation is deleted if it restates the label or the obvious, or
   only says where something came from; otherwise it becomes a tooltip on the
   control it explains.
3. No implementation detail (IANA, UTC, seconds, file limits, provider names) and
   no defensive negations ("never", "no … are guessed").
4. Normal state is silent: status shows only when something is unsaved, failed or
   offline, and then as a dot with a tooltip. Counts go in badges; zero is hidden.
5. Sentence case, verb first, second person, no em-dash asides.

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

### Local API protection (the UI secret)

rika!'s server listens on `127.0.0.1`, where every web page you visit (through your
browser) and every program on your machine can reach it. The page guard (exact Host
and Origin, `X-Kitty-Request` and JSON on every write) stops web pages, but a local
program can send those headers too. So every `/api` route, WebSocket upgrade and
terminal token also needs this launch's **UI secret**: a fresh 256-bit value made
at start-up and kept only in memory (never in a log, a URL or `.private/endpoint.json`).

- **Desktop app:** the backend hands the secret to the desktop shell on its private
  pipe; the shell gives it to the main window and every quick-note window before
  their scripts run (only on the main frame and only on rika!'s exact address, so the
  LearnHarness frame never sees it). Nothing to do.
- **`kitty-web` in a browser:** open the link it prints; its one-time code becomes
  the tab's secret (see [Local website](#local-website)).
- A request without the secret gets **401** with one message, "rika! did not
  recognise this window…", whatever was wrong.
- Downloads and PDFs opened in a new window (including the system browser the
  desktop app opens them in) use a single-use link valid for one minute.
- It is a header, not a cookie: browsers send a `127.0.0.1` cookie to every port on
  `127.0.0.1`, so a cookie would reach the LearnHarness server and any other local
  server; `web/tests/uiSecret.browser.test.mjs` shows this in Chromium and WebKit.
- **What it does not cover:** the CLI, TUI and MCP server still open the database
  directly, and a program running as you can still read your workspace files or
  attach a debugger to rika!. Paired programs use their own tokens (below).

### Connected apps (pairing)

Outside programs, such as the planned kitty Tern plugin and the browser extension,
reach rika! only after you approve them. They never use the page API, which needs
the UI secret only rika!'s own windows hold (see above).

1. While rika! runs (desktop app or `kitty-web`), it writes its current address to
   the owner-only `.private/endpoint.json` (0600, in a 0700 `.private`), e.g.
   `{"origin": "http://127.0.0.1:41234", "pairing": "/api/ingress/pair"}`. It never
   holds a token, and it is removed when rika! stops.
2. The app sends `POST /api/ingress/pair` with JSON
   `{"name", "kind": "tern-plugin" | "browser-extension", "scopes": ["capture", "read"]}`
   and shows the returned `code` (e.g. `CWQ-7QT`). rika! shows the request in a
   non-blocking bar and in **Settings → Connected apps**; approve only when both
   codes match. Requests expire after five minutes; at most three wait at once and
   ten are accepted per ten minutes (429 with `Retry-After`).
3. The app polls `POST /api/ingress/pair/status` with `{"request": <secret>}` until it
   gets `approved` with its token (handed out once), `denied` or `expired`.
4. Every later call sends `Authorization: Bearer <token>`:
   - `capture`: `POST /api/ingress/notes` `{text, tint?}` (a quick note),
     `POST /api/ingress/inbox` `{blocks: [{type?, text}]}` (appended to the **Inbox**
     page in the Inbox notebook, created on first use), and `POST /api/ingress/mirror`
     `{url, title, text?, selection?}` (a snapshot of the page you are reading:
     a linked callout with the selection quoted and the readable text in a collapsed
     toggle);
   - `read`: `GET /api/ingress/recent?limit=1-50` (recent page titles, open task titles
     and the Inbox page number);
   - any scope: `GET /api/ingress/client` (the client's own name and scopes).

   A token reaches nothing else: other API routes refuse any `Authorization` header.
   Captures are block transactions, so they enter the version history and appear at
   once in open windows. **Revoke** in Settings ends a token immediately.

A browser extension pairs and calls from its own extension origin
(`chrome-extension://<id>` or `moz-extension://<uuid>`); its token then works only
from that origin, and CORS (including preflight) answers only origins of live paired
extensions. Make these calls from the extension's side panel, popup or background
page; content scripts run in the web page and are not supported (Chrome's Local
Network Access rules). Web page origins are always refused.

### Keys (API keys)

rika! keeps API keys in one place (`src/kitty/core/keys.py`). Each key has a name:
`OPENROUTER`, `ANTHROPIC`, `USAJOBS`, `BRAVE`, or your own (`A-Z`, `0-9`, `_`). A key
is read from, in order:

1. the OS keyring through the freedesktop Secret Service (gnome-keyring, KeePassXC),
   when one answers on the session bus;
2. an owner-only file under `.private/` (`.private/openrouter-key` for OpenRouter, as
   before; `.private/keys/<NAME>` for the others). Files must be mode 600 and not
   symbolic links;
3. the environment, so `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `USAJOBS_API_KEY`,
   `BRAVE_SEARCH_API_KEY` (or a custom `NAME`) in `.env` keep working.

New keys go to the keyring when there is one, otherwise to the owner-only file.
**Settings → Keys** shows each key as set or unset and where it is stored, never its
value; it can set, replace and remove keys, and **Move to keyring** moves a file copy
(such as an existing `.private/openrouter-key`) into the keyring. Without a keyring it
warns that keys are stored in an owner-only file. The demo uses only its own
throwaway files.

```bash
kitty keys list                       # names, set/unset and where; never values
kitty keys set OPENROUTER             # typed without echo, or piped on stdin
kitty keys remove BRAVE               # keyring and file copy (.env is yours to edit)
kitty keys move OPENROUTER            # file copy -> OS keyring
kitty keys run +OPENROUTER -- apply-ai --help
```

`kitty keys run` gives keys to another program (apply-ai or any AI tool) without
exporting them in your shell: it starts the command with the named keys in its
environment only (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `USAJOBS_API_KEY`,
`BRAVE_SEARCH_API_KEY`, or a custom key under its own name), removes kitty's other
keys from that environment, and is replaced by the command, so the exit code is the
command's. Keys are never written into exports, builds, logs or error messages.

`KITTY_KEYRING=none` turns the keyring off for one process (the test suite does this).
On macOS, automic-vault deliberately never returns a secret to an app, so it is not a
storage backend: use it as a launch wrapper around kitty, and the variables it injects
arrive as environment variables (source 3).

### Tags and auto-tagging

A tag is a record with a name, optional **aliases** (other names: `#alias` tags too)
and **keywords**. When a block on a note page mentions a tag's name, an alias or a
keyword as a whole word (any case; no stemming), rika! notices it as you write.
Code (inline, fenced, code blocks), URLs, e-mail addresses and existing `#tag`,
`[[page]]` and `((reference))` tokens never count, and terms shorter than three
characters are ignored unless the tag allows them.

**Settings → Tags** chooses what happens:

- **Suggest** (the default): the word gets a faint dotted underline. With the caret
  on it, `Tab` (or **Tag #name** in the small pop-up) attaches the tag; `Esc` (or
  **Ignore**) declines it for that block for good.
- **Always attach**: the tag is attached as soon as the edit is saved and shown as a
  dashed chip (automatic). Removing it (`×` on the chip) is remembered, so later edits
  never attach it to that block again. An automatic tag whose word you delete goes too.

Each tag can override the setting either way (tag → **Keywords & mode**, or the tag's
row in Settings). Tags appear as chips under the block; a chip opens the tag's page in
the side panel: the blocks it is attached to (by hand, from a suggestion, or
automatically) and the remaining **unlinked mentions**, with **Link all**. Clicking a
`#name` in a note opens its tag page; a `#name` without a record offers **Make #name a
tag with keywords**. Every existing `#name` became a tag record when you updated, so
nothing written before is lost; `#name` in text keeps working as before.

Tags and attachments are blocks and block properties, so they are in the version
history, undoable from History and Trash, and appear live in every window. The mode
lives in the database for now (`tag_settings`); it moves into the build file once
builds exist. Adding a keyword does not rescan old notes: their mentions are on the
tag page, ready for **Link all**.

### Math

**Study → Math** trains the math quant interviews and tech work test. It has two
practice modes, timed mental-arithmetic **sprints** and topic **lessons**, over a
graph of 46 topics in five strands: arithmetic and estimation, counting,
probability (conditioning, Bayes, first-step analysis), expected value and
variance, and basic statistics. Every problem is made fresh by a seeded generator
(`src/kitty/core/math/templates/`) whose exact answer key is cross-checked by
enumeration or Monte Carlo over hundreds of seeds in the test suite. Topics and
generators are original and MIT-licensed. The view itself (drawers, Ctrl K, Up
next) is described under **Math** in *Start here*; this section covers how it
grades and schedules.

- **Mastery.** A topic is *locked* until every prerequisite is mastered, then
  *ready*, *learning* after your first graded answer, and *mastered* after three
  correct first tries in a row. Mastery starts a spaced-review schedule for the
  topic (FSRS, as in Flashcards); each review is a new problem, graded by
  correctness and time (wrong = Again, slower than twice the target = Hard, under
  half = Easy). A missed review is a lapse, never a relock. A correct answer also
  gives partial credit (normally 0.3, halved when not yet due) to the prerequisites
  it practises; each whole unit of credit counts as one review. Every state and
  recommendation says why, e.g. *Unlocked because you mastered Bayes' rule*.
- **Next.** Due reviews come first (most overdue), then the topic you are learning,
  then a new ready topic, preferring a different strand from your last practice.
- **Lessons.** A worked example, the same shape again with its last steps blanked
  for you to fill, then 3–8 solo problems (fewer once a topic is going well, more
  after misses); the view ends a lesson after three misses in a row.
- **Checking.** Only your first decided answer to a problem is graded; you get
  three tries, and the answer is shown when you solve it or after the third miss.
  Numbers and fractions are exact in any form (`3/7`, `6/14`, `\frac{3}{7}`,
  `0.25`, `25%`); decimals for non-terminating answers need the stated significant
  figures or tolerance. Known slips (dropped carries, $P(+\mid C)$ for $P(C\mid +)$,
  subtracting variances…) get specific feedback. Algebra answers are LaTeX, checked
  by SymPy in a separate worker process: both of its LaTeX parsers read the answer,
  `simplify` and random numeric probes compare it, and a check stops after 5 s.
  When the answer can't be read, reads two ways (`n^n^2`), lacks significant
  figures or times out, the checker says so and the try doesn't count.
- **Sprints.** Zetamac defaults: 120 s; addition (2–100) + (2–100), subtraction as
  reversed addition, multiplication (2–12) × (2–100), division as reversed
  multiplication. *Typed* takes each answer the moment it is right; *multiple
  choice* scores right − wrong. Problems come from a stored seed and are checked in
  the browser; results keep your best score per mode and a daily streak.
- **Explain my mistake.** After a wrong answer you can paste your working and ask
  AI where it went wrong (purpose `math-explain`). It is a normal AI review: you see
  exactly what is sent (the problem, your answer and working, never the correct
  answer) and its maximum cost, then approve it within your daily budget. The
  prompt forbids revealing the answer and treats your working as data. AI never
  grades. Demo mode has no AI.

API (`workspace` module): `GET /api/math/topics`, `GET /api/math/next`,
`POST /api/math/problems {topic_id, seed?}`, `POST /api/math/answers {problem_id,
answer, elapsed_ms}`, `GET /api/math/lessons/<topic_id>`, `POST /api/math/sprints
{mode: zetamac|mc, duration_s, ops?}`, `POST /api/math/sprints/<id>/result {score,
wrong, elapsed_ms}`, `GET /api/math/sprints/history?duration_s=&timezone=` and
`POST /api/math/explain {problem_id, working}` (then `POST /api/ai/run`). The graph
is authored in `kitty.core.math.topics`, synced into `math_topics`/`math_prereqs`
(a trigger rejects any prerequisite cycle), and practice lives in `math_mastery`,
`math_attempts` and `math_sprints` (migration 044).

### Brain data (`/api/brain`)

`GET /api/brain?limit=<n>` (default 20,000, at most 50,000) returns the whole
workspace as one point cloud for the Brain view: every **record** (notebooks,
pages and their blocks, decks and card notes, calendar events and tasks, study
subjects, projects, tasks, exams and sessions, quick notes, mind maps, documents,
opportunities, jobs, applications and reminders) and every **edit** as a node with
a `parent` link. Edits are the website changes captured in `record_events` plus the
histories the database already keeps (flashcard reviews, application status
changes, study sessions, calendar changes made before capture began and the block
backend's `op_log`). Note pages, documents and quick notes are blocks, so each of
their saves counts once, from `op_log`, whether it came over HTTP or the live
socket; the one-time conversion of older notes into blocks is not an edit.
Labels are titles only, never note bodies, paths or
URLs; a change to something since deleted reads e.g. *Deleted page*. Sections of
disabled modules are left out. When the limit is reached, records are kept and the
newest edits fill the rest (`truncated: true`; `total` counts everything).

### Search and the search index

**Ctrl K** searches sections and commands by name and, from the full-text index, every
note page, document, quick note, tag (name, keywords, aliases), study subject, project
and task, calendar event and task, flashcard deck and card, and (with ApplyAI) job:
titles and contents, with the matched words marked. Each word you type matches the
start of a word ("mito" finds *Mitochondria*), case and accents are ignored ("cafe"
finds *Café*), titles rank above body text, and from three characters on the text also
matches inside words and in Chinese, Japanese or Korean text ("ondri", "都庁へ"). Notes
find (the library's *Search your notes…*), the atlas section graphs' *Find a node*,
mind-map backlinks, a page's *Unlinked title mentions* and the *Connections* panel read
the same index, over every record: nothing older is left out. Where a panel shows only
the first few, it says more exist.

The index (`search_docs` with two SQLite FTS5 indexes, migration 047) is written in the
same transaction as every block edit; other records reach it through a queue their
tables' triggers fill, applied before every search. The server compares it with a full
rebuild (pages replayed from their snapshots and history) five minutes after start and
every six hours, repairs any difference and logs it.

```bash
kitty index check                 # rebuild, compare, repair; exit 1 on any difference
kitty index check --no-repair     # report only
kitty index check --from projection   # rebuild blocks from the projection, not the log
kitty index rebuild               # replace every document from the current data
```

`GET /api/search?q=<text>[&limit=1-200][&kinds=note_page,job,…]` returns
`{query, hits, total, truncated}`; each hit has `kind`, `id`, `title`, `title_marks` and
`snippet` (`[[text, is_hit], …]` runs), `view` and `match` (`word` or `substring`).
`GET /api/atlas/sections/<notes|study|flashcards|jobs>?search=<text>` returns the matching
records as graph nodes with their hubs (titles only, never bodies).

### LearnHarness extension

**Study → LearnHarness → Start LearnHarness** opens the included
[upstream LearnHarness app](https://github.com/amosblomqvist/learnharness)
(AGPL-3.0-only). Desktop installations include its production server and Node.js:
no source checkout, Git, npm, system Node, or manual service setup is required.
Teaching, practice, knowledge graph and settings remain the independent upstream
app, contained in a cross-origin frame at **http://127.0.0.1:3210**.

Start is explicit, not a login/boot service. rika! stops only the learning server
it started when that rika! instance quits. Learning records and provider
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

rika! checks availability with a bounded HEAD request to the fixed loopback
endpoint. An explicit protected `POST /api/extensions/learnharness/start` starts
only the bundled runtime; it accepts no URL, settings or credentials. There is no
data proxy or credential bridge. The server binds only IPv4 loopback, runs with
a clean environment and receives no rika! provider keys, applicant data, resumes
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
its ignored `data/kitty-extension/`, isolated HOME and no inherited rika! secrets.
It runs upstream's dev server; start again after reboot. It is not needed by the
desktop installer. rika!'s demo does not reset a separately running service.

On NixOS, build the desktop in the native toolchain shell:

```bash
nix develop 'path:./desktop#host-toolchain' --command bash scripts/build-desktop.sh
```

Keep the native executable and bundled `kitty-web` sidecar together. The desktop
uses a single instance per session: verification must use an isolated workspace,
profile and session, not relaunch or replace an owner's running instance.


### Start here

**Find jobs** has one row on top: the title and count, one segmented control for
**Map · List · Saved · Applications · Resumes · Sources**, and **Add a job link**.
Under it, an always-visible **This week** strip lists the next reminders and
deadlines (click one to open the job or Reminders), the weekly goal as sent/goal,
and **How to apply**; ⋯ at its end holds stale applications, expected openings,
search health, per-track progress, rates and rank/state totals. **How to apply**
opens a three-step guide and checks whether your name/email and a usable resume
PDF are saved. These checks describe saved data, not application readiness.

1. **My profile → My resumes:** enter your verified details in the ordinary form,
   review the save, then upload a resume PDF. Blank fields stay blank. Basic-form
   saves preserve other profile settings and reject stale edits; leaving with an
   unsaved profile, configuration or answer draft prompts before discarding it.
2. **Find jobs:** the list scrolls in 100-row batches; selections persist across
   batches and **Select all matching** covers the whole filter. Sort by best fit,
   similarity score, newest, job type (track), company or deadline; **Filters**
   (a popover) holds rank (hover Rank for what A/B/C mean), saved view, source,
   eligibility, status, preparation progress and the checkboxes. Map opens Jobs'
   own schema, filling the page with its 2D/3D, Layout and Fit controls in a
   bottom-left pill; it shows only job-type categories at the root. Open a
   category to see every matching job, without 120-item map pages (**All job
   types** returns). Every job, uncategorized ones included, is in List.
3. **Select jobs → press Shift+F:** marked jobs (otherwise the opened or focused job) go
   straight to the reviewed autofill plan. Approved preparation opens Firefox and
   fills known fields. Check each form and submit it yourself; rika! never submits.

**Notes** opens today's dated note, reusing the same page on subsequent visits.
The library (☰, top left) slides over the page as a drawer and is the one
notebook/page navigator: folders, notebooks, recent pages, tags and drag-to-reorder;
opening a page or pressing Escape closes it. The header is one row: ☰, browser-style
page tabs (a dot in the notebook's colour, × to close, + for a new page), the
**Write / Canvas / Map** switch, **Find in page** (Ctrl+F) and **⋯**, always last:
Today's page, Library overview (the folder/notebook schema), Import PDF or
Markdown…, Favourite, Preview outline, Canvas fullscreen, Privacy & storage…,
Reload saved and Delete page…. The page title is the large heading at the top of
the page, under a small line naming the notebook and when it was last edited.
Pages save as you type (Ctrl+S saves at once); the save state is the dot on the
workspace tab: neutral when saved, warning while unsaved, red when a save failed,
with **Retry save** in the error line. While writing, a floating **On this
page** outline lists the top-level blocks and the word count; click an entry to
jump to that block, or collapse the outline to a corner pill (remembered).
Text notes use stable bullet blocks: Enter adds a sibling, Tab/Shift+Tab nests or
unnests, `[[Title]]` and `#tags` collect topic mentions, and unlinked title mentions
offer a Link action. Copy a `((block-uuid))` reference or `{{embed:block-uuid}}`
live embed from a block's tools; its citation count opens referencing contexts
in the right sidebar. Referenced text follows edits to the original.
The canvas retains paper templates, ink/fill palettes, layers and handwriting, and
fills the window above the Rika bar.
A page is the theme's flat background colour with no grid; the pill's **⋯ → Paper**
adds lines, a grid, dots or Cornell margins when you want them.
Its tools sit in one floating pill at the bottom of the canvas: select, pen,
highlighter, eraser, text and sticky, your last five colours as round swatches (**+**
opens all twelve named colours and a hex field), a stroke-size toggle, undo/redo, and
**⋯** for the other tools, fill, exact stroke and text size, pen mode, snap, zoom,
paper, the text outline, **Layers…** (a side drawer) and the file actions (import an
image or PDF, restore a JSON page, export JSON, SVG or PNG). Click the handle on the
pill's bottom edge to tuck it away to a small tab and again to bring it back
(remembered); tool keys still work while it is tucked. **Canvas fullscreen** puts
only the canvas and the same pill on the laptop screen, not the whole application.
Its exit control and Escape restore the workspace.
Outside text editing, F fullscreens the focused workspace panel instead.
**Map** opens the page's own mind map in the one mind-map editor (below), inside the
page; a page without one offers **Create map**, which starts it from the page's title.

**Connections** automatically compares note and flashcard text locally:
explicit `[[Page title]]` links, unlinked title mentions and related concepts
are separate groups. Open a reference or turn a title mention into a link.
Notebook schemas derive wiki and shared-concept edges without changing your
documents. Related matches are suggestions, not claims that two concepts are
identical; no AI, external indexing or network calls are involved.

**Flashcards** opens Decks: a tile per deck whose ring splits today's load into
review, learning and new around today's count; hovering a tile shows the split,
measured recall and Review, and the tile's ⋯ holds Settings and Archive. A Tiles /
Graph toggle (remembered) swaps the tiles for the deck → note graph. The deck chip
beside the title ("All decks · 86 due") sets the scope: it opens a searchable deck
list with due counts and ends in **New deck**. There is one **New card** button
(or press N); it opens the quick-add popover for the deck in scope. The views
row's ⋯ holds New deck, practice and review options, Review history, Import /
export, AI drafts and note types. Review deals the queue as a stacked deck: tap the top card
(or press Space) to flip it, then swipe it or press a key to send it off in the
grade's direction (← Again, ↓ Hard, → Good, ↑ Easy). Below the pile a keyboard strip
shows keycaps 1–4 with each next interval and Space (flip, then Good); they sink as
you press them.
Math on a face renders with KaTeX: `\( … \)` and `$…$` inline, `\[ … \]` and
`$$…$$` as a display line, so imported Anki math decks read as typeset math. A lone
`$` before a digit or a space stays text, so "$5 and $10" is not read as math. KaTeX
(MIT) and its fonts are bundled; nothing is fetched from a CDN.
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

**Study** opens focus-first: only the timer, centred, with its session chip and
**Start session**. Two drawers slide over the page from edge tabs: **Subjects &
tasks** on the left (press **[**) picks the session's subject and concept and
quick-adds subjects, concepts and tasks; **Guide & notes** on the right (press
**]**) shows the current phase's guide steps (‹ › browse the others), session
notes with **+ Distraction**, and spaced returns due. Each drawer's open state is
remembered. **Full guide ›** opens all fourteen steps as phase columns or a rail,
the step notes, the return review and saved reflections; the guide saves itself.
The ⚙ on the timer opens timer settings: focus and break lengths in 5-minute
steps, sound and desktop-notification switches and the time zone, each saved as
you change it and applied from the next phase. The tabs are Session, Progress,
Exams & plans, Mind map and Tools (History and Helpers). Unsaved notes or a failed
save show as a dot on the workspace tab.

**Math** (Study group) opens focus-first like Study: one centred problem, lesson or
sprint, chosen by **Practice · Sprint · Lessons**. **Practice** shows the one next
thing, with why: a due topic review (a fresh generated problem) or the next lesson.
The **Topics** edge on the left (press **[**) slides in the topic graph:
prerequisites sit above what they unlock, and each topic is coloured locked (dashed,
padlock), ready (outlined), learning (filled to its progress) or mastered (filled,
ticked); a mastered topic whose spaced review is due wears an amber ring, and the
edge counts them. Click or press Enter on a topic for why it is open or locked
("Locked until you master Bayes' rule"), its next review, what it needs and
unlocks, and **Start the lesson** or **Review**. **History** and **Settings** share
the right edge (press **]**): History shows the sprint trend at the current length
and format, the daily sprint streak and recent runs; Settings sets the sprint format
(typed or multiple choice), its length (30 s to 10 min; Zetamac's 120 s by default),
the operations (+ (2–100) + (2–100), − reversed +, × (2–12) × (2–100), ÷
reversed ×) and **Start a Study focus cycle on Math when I practise**. The mode,
drawers and settings are remembered.
**Sprint** puts the problem large inside a countdown ring. Typed: the answer is
taken the instant it is right, with no Enter, and the score is the number right.
Multiple choice: four options on keys 1–4, +1 right, −1 wrong. When the time is up
the run is saved and the results card shows the score, the rate per minute, the
best and the trend line; **R** restarts. **Stop** abandons a run without saving it.
Problems come from the backend; answers are checked in the browser, so the next
problem appears at once.
A **lesson** runs **Example · Guided · Solo**: a worked example, then the same kind
of problem with the step you fill highlighted and the later ones faded, then 3–8
problems on your own. Three misses in a row end it early. Guided blanks never count
toward mastery; graded solo answers and reviews do.
**Answers**: numbers and fractions are plain text with a live echo ("3/7 ≈
0.4286", "0.43 · 2 s.f."); fractions compare exactly. Algebra goes in a MathLive
field (MIT; its sounds and the KaTeX fonts are bundled) and is checked by SymPy on
your computer. Each check adds a feedback line: correct, a named common mistake,
not equivalent, or "Couldn't decide" (nothing is marked). Only the first decided
answer is graded; you can retry until it is right or the answer is shown after the
third miss. After a wrong answer, **Explain my mistake** takes your working and
sends it through the reviewed AI flow: you see the model, the maximum cost and the
exact text, approve, and the explanation appears as a bubble. It points at the
first faulty step and never gives the answer; the checker still grades.
Launchers: Ctrl K **Mental math sprint** and **Next math lesson**, and the home **Up
next** rows **Math: next lesson** (or **Math: review due**) and, on a day without a
sprint yet, the sprint-streak nudge. While a Study cycle runs, the Math header shows
its phase, subject and time ("Focus · Math · 18:02").

**Mind maps** have one editor, reached from Notes → **Map** (the page's own map),
Study → **Mind map** and the **Mind maps** view; Ctrl K finds any map by its title and
offers **New mind map**. Its slim top bar holds the map switcher (**Maps ▾**, with
**All maps, merged** for the network of every map), **+** for a new map, the title
(edit it in place), the linked subject, **Layout ▾** (tree, radial, force), the
Map/Graph switch and **⋯** (export or import JSON, reload, linked mentions, ask AI
about the map, import tracked jobs in a jobs map, delete). The canvas fills the rest.
**+** opens a small popover: type the title, pick a starting template (Blank,
Lecture, Chapter, Exam review) and category, and press Enter; **More** adds starting
from a study subject or importing a map file. Topics are compact pills in their
branch colour around an ink root. Click a node, then Tab adds a child, Enter adds a
sibling right after it, typing or Ctrl+Enter renames, arrows move between parent,
children and siblings, Ctrl+↑/↓ reorders siblings and Space collapses a branch.
The building tools are the Notes canvas pill itself: select, **Connect topics**,
**Ink**, **+ Child** (or **+ Topic**), **+ Sibling**, the theme's six colours plus
automatic, undo/redo and **⋯** (outline, quiz, add a linked topic such as a study
class, link a note page, find a topic, and in Notes the layout and map actions).
Zoom out, the zoom level, zoom in and **Fit** sit in the corner.
A small bar rides on the selected topic: its colour (**Whole branch**, **Copy style**
/ **Paste style**, also Alt+C / Alt+V), **Rename**, **Detach**, **⋯** (Connect to…,
collapse, Reset position, Split branch into map, Merge into another map, make
flashcards, quiz this branch, start a session, open a linked page or map, and
**Details…**, a side drawer with the label, notes, reference URL, parent, linked map,
source and every connection) and × to delete.
Drag a topic (its branch travels with it): the topic it would join lights up and the
branch previews its new place; dropping elsewhere snaps it back. Press **D** mid-drag,
or **Detach** on the topic bar, to break a branch off as a floating piece that keeps
its place. Alt-drag places a topic anywhere while keeping its parent; its own children
are laid out beside it, and **Reset position** puts it back into the automatic layout.
**Connect topics** (or **Connect to…**) links the selected topic to any other one:
click (or move to and press Enter on) the other topic, then type the connection's
label. Connections are dashed curves outside the tree: they never change the layout,
hide when either end is folded away, and double-clicking one edits its label.
Shift+arrows, Shift-click or a Shift-drag box on empty space select several topics:
the colours then recolour them all, dragging one moves every selected branch
(onto a topic, freely with Alt, or detached with D), and Delete removes them together.
Edits save by themselves once the map is idle (Ctrl+S saves at once); the save state
is the dot on the workspace tab. Ctrl+Z undoes every map edit (including drags,
viewport changes and recolouring); Ctrl+Shift+Z or **Redo** reapplies it, and saving
keeps the editor's history. A new edit after undo discards the redo branch. After a
reload, Undo restores the previous saved snapshot.
**Split branch into map** moves its descendants into a new map with links in both
directions. To merge, drag a root: the other maps appear as drop targets above the
canvas; drop it on one (or use **Merge into “…”** in the topic bar's ⋯). Merging back
replaces the split placeholder; other roots stay in the source map. Transfers save
both maps atomically; undo/redo restores both sides and refuses if either map changed
in another window. The emptied source map remains available.
Mouse, touch and pen share one pointer path: tap selects, drag moves or reparents,
and a double tap (or double-click) edits a topic (or opens its linked note or map),
adds a topic on empty space or relabels a connection. With a pen, **Ink** in the
pill draws pressure-sensitive strokes (a mouse draws too; fingers keep panning).
Each stroke is smoothed when you lift the pen and sticks to the nearest topic, so it
moves with that topic, hides when its branch is folded, follows a split or merge,
and is saved with the map. Hold the pen's barrel button and draw a loop to select
every topic inside it; flip the pen to its eraser end to rub out ink (topics are
never erased).
While the pen hovers or has just been used, touches are treated as a resting palm
and ignored, and the pen's barrel button never opens the delete prompt.
**Quiz me** hides labels for recall; a branch can produce a flashcard draft.
**Linked mentions** suggests local sources for topic labels; accepting references is
revision-checked and idempotent. Kanban is removed; existing stored rows are left
untouched.

Notes once had its own concept-map mode (cards joined by connectors on a page's
canvas). On the first start after updating, rika! copies each such page's concept
map into a mind map linked to that page (opened by Map), after first writing an
owner-only JSON export of those pages to `data/exports/concept-maps-<time>.json`.
The cards themselves stay on the page's Canvas untouched.
`kitty.core.concept_maps.revert(conn, path)` removes the migrated maps again.

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
**Exams & plans**: **Add exam** is a quick-add (title, a date chip and a subject
chip that can create a subject); **More** or clicking an exam opens the side
drawer with every field. It shows exam countdown/readiness cards, a two-week timeline
with an exam lane and a spaced-returns lane, and a twelve-day strip. Select a
block to mark it done or export it explicitly to Calendar. Drag blocks between
days (or use Alt+Left/Right); days above 90 planned minutes show a load warning.
Blocks stay before their exam. **Make a plan** (a popover) saves dated blocks from your own
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
**D**ay, **W**eek, **M**onth, **A**genda, **Y**ear and day **P**lan; each letter's
tooltip names its key. While the calendar has focus the same letters switch views,
**J**/**K** step, **T** goes to today and **N** adds. Month shows up to four calendar dots per date; clicking a date fills a
side day agenda. Year tints every day by booked hours (hover to read, click to
pin, **Open day** to jump). Plan splits the day's timeline from its open tasks;
**Plan** drops a task into the next free 30-minute gap (08:00–21:00) as a dashed
block. The sidebar's mini month shows ISO weeks; hover a week to preview its
load and click a date to jump there.
**Adding.** The pill's one **+** opens a quick add: type a phrase such as
`gym tue 6pm 1h` or `lunch with Mira fri 12:30 at Café Rose every week`, pick
**Event** or **Task**, and Enter saves it; the chip shows the day and time it
understood. Clicking an empty slot (or Enter on it) opens the same quick add at
that time for an hour, dragging across slots or all-day cells prefills the range,
and **Schedule here** on Availability prefills a free interval. **More** saves
and opens the new item in its drawer. The Tasks list has its own **+ Add task**.
**Editing.** Drag an event/task to another date or time; moving an event preserves
its duration. All-day items can be moved across the all-day row. Clicking an event
opens a side drawer whose time steppers, calendar, place and reminder chips
(5m/10m/1h/1d before) update the block live until you save or press Escape; its
title field still reads natural language to retime the event. Clicking a task opens
the same side drawer for the task. Repeat rules, time zone, custom reminder offsets
and Google properties stay in the drawer's expandable sections, with their
explanations in tooltips. Saving reports through the tab's status dot.
A task's **＋ Map** action optionally adds it beneath a chosen map node.
When an event changed both in rika! and in Google Calendar, **Imports & Google**
draws both versions on a small day grid in the event's colour: rika!'s as a solid
block, Google's as a dashed ghost (a deleted side shows as an empty dashed block).
Click the block to keep; the list beside it shows each field that differs and
strikes out the values you would lose.
**Keep rika!'s** waits for a separately confirmed push, **Use Google's** applies
Google's version, and **Keep both** saves rika!'s as a copy. Calendar settings
conflicts list the differing name, timezone and colour.

The standalone **Reminders → When** dropdown offers Today, Tomorrow, one/two
weeks, 30 days or a custom date picker; no date-syntax memorization is required.

**Notifications.** Due calendar reminders, job-search reminders and Rika's urgent
goal matches (**Rika · strong match**, see **Rika** below) arrive as one
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
rika! checks reminders only while it runs: in the desktop app, **Keep running on
close** keeps it checking from the system tray; when rika! is fully closed,
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

**Opportunities & projects** opens on its category → source → opportunity map,
full-bleed in one surface (2D/3D, layout and Fit float bottom-left). One
floating row over the map holds search, **Filters** (a popover of source,
category, status, dates, remote, location, deadline and favorites), **List**,
**Add** and ⋯. **List** slides the list in as a drawer on the right (remembered
per browser); filters and search open it to show what matches. **Add** is a
quick-add popover: type a title or paste a link, pick a category or deadline
chip, Enter saves; **More** opens the full form. Opening a row or map node edits
it in the side drawer (fields, project tasks, evidence; ⋯ adds the deadline to
the calendar, drafts an AI summary or deletes). ⋯ in the row holds **Interests &
monitor** (profile, opt-in monitoring and public sources) and **Import JSON**;
**Discover from public sources** appears there outside the demo. Source hubs stay
on the map when no opportunities have been ingested.

These interactions draw on [MindNode's keyboard map-building](https://www.mindnode.com/support/guides/keyboard-shortcuts),
[Heptabase's note cards and whiteboards](https://heptabase.com/),
[Roam's linked references](https://roamresearch.com/) and
[OneNote page templates](https://support.microsoft.com/en-us/onenote).
Recall and flashcard actions complement mapping rather than treating drawing
alone as studying: [retrieval-practice research](https://pubmed.ncbi.nlm.nih.gov/21252317/)
supports actively recalling material.

**Prepare applications** also lists recommended open, unsubmitted A/B jobs.
**Preparation** (next to Fill) opens a side drawer with alternative groups, job
limits and browser progress; Fill and Shift+F open it too. With jobs selected,
**Change selected** sets their progress, rank or category (g and t open it).

Home is the **Brain** (below), every record and edit in the workspace as a
point of light in a glowing neural-network brain, or the 2D **atlas** of workspace sections; the
**Brain | 2D** switch in the stage's top-right corner, beside **View** (or
Settings → Appearance) chooses, and the choice is remembered (`kitty.home.look`;
an earlier 3D choice opens the Brain). It sits in the centre between two narrow
rails of widgets that scroll on their own. The left rail holds **Today** (today's
events with a live now-line, the current event's progress and a countdown to the
next) and **Quick note** (Ctrl+Enter saves; the last three stay visible). The
right rail holds **Agents** (what each herdr workspace agent is doing; **Approve**
opens herdr on a blocked one) and **Up next**: open dated tasks and job-search
reminders (overdue ones on top), flashcards due for review, then the next seven
days of the calendar, where deadlines and follow-ups from every module land (a
reminder already copied to the calendar shows once); a row opens its page; at most
six rows show, with the total beside the title.
The chevron on the right rail's inner edge, or **]**, folds that rail to a thin
strip so the Brain widens (and unfolds it); every home follows the one remembered
choice (`kitty.home.rail`).
**Sections**, in the stage's top-left corner, opens a popover with a section
search (`/` opens it there; Enter shows the first match in the Brain or on the
map, Enter again opens it) and the grouped section list. Each row's ⋯ opens the
section, shows it on the map, or hides it from (or shows it on) the 2D atlas;
hidden rows stay listed, dimmed. On the 2D atlas, drop a node on **Sections** to
hide it and drag a dimmed row onto the graph to show it again. Ctrl K also finds
every section.
The full job-search brief (reminders, deadlines and weekly goals) lives in **Find
jobs**. Section graphs and schemas draw in 2D or 3D (their own switch, or Settings →
Appearance); every schema starts in Tree, and an explicit layout preference can
then be changed in **Settings → Graphs**, with the graph physics and **Reset to defaults**. Home has one Study entry and only
the Jobs section entry, not separate job-action nodes.
The rika! logo returns home. Clickable schema tabs show section →
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
The **Brain** on home draws every record and every edit from `GET /api/brain` as one
point of light in a brain made of a glowing neural network, seen three-quarters from the
front and slowly turnable, over the theme's own backdrop. Its folded surface is a faint
rim of light with its sulci traced across it: two hemispheres split by the longitudinal
fissure, each with frontal, parietal, temporal and occipital lobes, a cerebellum tucked
below and behind and the brainstem. Records are its neurons, glowing in the cortex of
their group's region (a large group spans several neighbouring regions, small groups
share one); fibre tracts fan from the brain's centre out to every region and run between
them (the corpus callosum across the hemispheres, the arcuate and longitudinal fasciculi
within each), neurons reach their neighbours along short dendrites, and records that
belong together (a page in its notebook, a card in its deck) are linked by arcs. Each
group's edits travel its own fibres in time order, newest nearest the centre. Slow pulses
of light run outward along the network, and the far side of the brain is dimmer than
the near. The network grows denser and branches further as the workspace grows; even a
few hundred points still draw the whole brain. It builds itself from the centre outward.
Colours come from the active
theme and change with it; inside a group, each
record type and each kind of edit (save, create, delete, status, review…) has its
own shade. The **View** button opens its View panel (closed at first; whether it is
open is remembered, and while it is open the brain is framed beside it). It sets the
layout (Brain, Spheres, Wheel), grouping
(section, navigation group or type), labels, turning and speed, edit→record
lines, the scaffold (the brain's lit surface and folds), the background (Transparent, where the theme's backdrop
shows through unchanged, or Black for the original
night sky), point size and colours: pick any group's colour from the theme's
swatches or a hex value, or Reset colours to follow the theme again (picks are
remembered per grouping). Reset restores the view; the choices are remembered.
The legend lists each group's records + edits and, on hover or focus, the kinds
inside it; click an entry to highlight it. Hovering a sphere, sector or a region of
the brain
highlights its group; clicking it opens that group alone, laid out by kind to
fill the stage, until **← All** or Escape. Labels never overlap; ones without
room hide. Hovering a point shows its label, section, type and date. Without
WebGL home is the 2D atlas; a backend without `/api/brain` gets a notice, never
invented data. Reduced motion skips the build-up, twinkle, pulses and
turning.
Theme presets include dark cherry-pink **Cherry cat**, with blossom ASCII,
and a Scythe palette. Scythe illustration requires the actual supplied artwork;
no unrelated image is substituted. Motion respects reduced-motion preferences,
both the system's and the Appearance setting.
Top notices and the study timer bar can be dismissed.

**AI settings** provides optional OpenRouter drafts for selected text: summaries,
outlines, study plans, opportunity explanations, and source-grounded flashcards.
Add the `OPENROUTER` key (here, in **Settings → Keys** or with `kitty keys set OPENROUTER`),
refresh the model catalog, choose a model and price/latency/throughput routing,
and set an output limit and daily budget. Review the exact text, prompt and maximum
cost before explicitly approving each request. Reviews are single-use, expire
after ten minutes, and become invalid when settings change. Notes are not shared
automatically; output remains a draft. Provider requests disallow data collection,
and failed requests are not automatically retried. Paid calls are disabled in demo.

**Rika** is rika!'s own agent (design: `docs/rika-design.md`). You type to it in
the Rika bar, and it does the work through the app's own functions.

- **Tiers.** Each request goes to the smallest tier that can do it:
  - **fast** for talk, simple edits, and everything you say aloud;
  - **standard** for planning ("plan my week") and a brain dump's plan;
  - **deep** for multi-step requests (three or more steps, or a very long request) and retries after a failed reply.

  A tool call that fails validation, or a reply that comes back cut off or empty,
  moves the request up one tier, at most twice. Every reply shows its tier. You
  can override the tier for one request. Choose each tier's OpenRouter model in
  **Settings → AI**. Left empty, a tier uses a default for the main model's
  provider family (Anthropic: Haiku, Sonnet, Opus; OpenAI: GPT-5 mini, GPT-5,
  GPT-5 pro; Google: Gemini 2.5 Flash and Pro). A tier model must be in the
  refreshed catalog.
- **Simple phrases skip the model.** These run their tool directly, free and
  offline:
  - `add a node X`, `connect nodes X to Y`
  - `add a topic X [under Y]`, `rename topic X to Y`, `connect topics X to Y`
  - `new card front / back`
  - `add a task X [today|tomorrow]`, `add an event X [tomorrow] [at 4pm]`
  - `new page X`, `append text to Page`
  - `new subject X`, `study task X [for Subject]`
  - `find X`
  - `open <view>`, `open a terminal`
  - `start omp|claude|codex [to task]`
  - `delete page X`
  - ``run `command` ``
  - `remember (that) X` (saves a preference)
  - `new goal X`, `morning brief`, `what's new for my goals`

  Anything else goes to the model with Rika's tools, unless a learned shortcut
  (below) matches it.
- **What it can change.** It can:
  - create, append to, edit and find note pages and outline blocks;
  - add, move and connect canvas nodes;
  - add, connect and rename mind-map topics;
  - add flashcards;
  - create calendar events and tasks;
  - add study subjects and tasks;
  - create goals, link items to them and update their measures;
  - save preferences it should remember;
  - draft email replies;
  - open views;
  - search the whole workspace and its local memory index.

  These changes apply at once, and each one is in the history drawer with
  **Undo**. Calendar undo uses the calendar's own history and mind-map undo uses
  the map's own undo snapshot. Undo refuses, and keeps the record, when you edited
  it since.
- **What waits for one tap.** Rika asks first, and the reply pauses until you
  decide, before it:
  - deletes anything that has no Trash (note pages);
  - changes which modules load;
  - starts a worker agent;
  - runs a shell command;
  - sends an email it drafted;
  - makes a model request over today's AI budget.

  An approval expires after ten minutes, or when rika! quits.
- **Budget and privacy.** Model requests reuse the key, catalog prices, routing
  and daily budget from AI settings. Before it is sent, each request reserves its
  maximum cost in the same ledger as the reviewed requests. It is sent with those
  prices as the provider's ceiling, with no fallbacks and no retries. Providers
  must decline data collection. What you ask Rika is sent, along with what its
  tools return for that request. Note text is included only while **Allow
  selected-text AI sharing** (Notes → Privacy & storage) is on: then Rika can read
  the notes a request needs, never whole notebooks at once, and selected-text
  requests work as before; while it is off, Rika gets titles and ids only. Text that
  tools return is treated as data, never as instructions.
- **Voice, on this computer.** Speech-to-text is [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
  (OpenAI's multilingual Whisper, CTranslate2 int8 on the CPU) inside rika!'s own
  backend; audio never leaves the machine. While you talk the page streams 16 kHz
  PCM from the microphone over `/api/rika/stream`; the backend transcribes the open
  stretch every second, commits it as a segment at a pause (or every 20 s), and
  transcribes the rest when you stop, so a ten-minute dump is written out as you go.
  The first time you talk, the chosen model downloads once into the workspace's
  `data/voice/` folder, pinned to a Hugging Face revision and checked against its
  SHA-256, with its progress in the bar. **Settings → AI → Rika's voice** picks
  **Tiny** (75 MB), **Base** (145 MB, the default) or **Small** (484 MB), can
  download it ahead of time, and says which speech engine, if any, reads replies
  aloud. A command listens for at most a minute, a brain dump for 30 minutes.
- **Brain dumps.** The transcript is saved as the dump note first. The standard
  tier then answers with a JSON plan whose items may call only that area's tools
  (notes: pages, blocks, canvas nodes, mind-map topics; tasks: calendar and study
  tasks; calendar: events; cards: flashcards; jobs: tasks and events; agents: start
  a worker). Invalid items are dropped; an unreadable plan moves up one tier.
  Approving runs the ticked items through the same tools as any request, so in-app
  changes come with Undo, and approving the plan is the approval for its ticked
  outward items (starting a worker).
- **Worker agents** (Terminal module). When you approve **start**, Rika:
  1. runs rika!'s herdr session headless if no page attached it;
  2. starts omp, Claude Code or Codex in an Agent tab;
  3. hands it a task brief with `herdr agent prompt`.

  `GET /api/rika/agents` merges herdr's agent states with coding agents running
  in Tern panes. **Open** focuses the agent and shows it in a Tern window: a herdr
  agent appears through a Tern tab attached to rika!'s herdr session. Without
  Tern, the page opens the herdr view. Quit stops the session Rika started.
- **Routing memory and cost** (design Q13). Rika remembers, per kind of request,
  the cheapest tier that succeeded, the shortest tool path and how often it had to
  escalate, and starts the next request of that kind on that tier instead of
  guessing. A kind is keyed by its tool path (`search>add_card`); a request finds it
  by its shape (its first word and the object it names, e.g. "add card"). When the
  same phrasing succeeds twice with one in-app change whose arguments are words of
  the request (for example "flashcard Mitosis means Cell division" → `add_card`),
  it becomes a **learned shortcut**: the next request phrased that way runs the tool
  directly, with no model. Undoing an action a shortcut made forgets the shortcut.
  Shortcuts never cover outward tools. For Anthropic and Gemini models the stable
  part of the system prompt (instructions and preferences) carries OpenRouter's
  `cache_control` breakpoint; other providers cache repeated prefixes on their own.
  Every reply in the history drawer shows a readout: its tier, the tool path (and
  whether the path or shortcut was remembered), tokens in (cached) and out, and the
  cost, e.g. `remembered path search → add card · 1,180 in (960 cached) · 64 out ·
  $0.0002`; phrases and shortcuts show **Direct** · 0 tokens · $0. **Settings → Rika
  → Routing memory** lists what Rika learned (kind, tier, tool path, uses, average
  tokens and cost, shortcut and escalation badges) with **Forget** per row, today's
  requests, tokens, cost and cache-hit share, and a 14-day average-cost trend.
- **Memory** (design Q18). A local SQLite FTS5 index covers note pages and
  outline blocks, the edit history (the op log and Rika's actions), jobs,
  calendar events and tasks, email Rika has read, goals and preferences; it stays on
  this machine and is refreshed per source when that source changes. Each model
  request gets at most six relevant items from it, sent as data in their own
  message; page and block text stays local (titles only) while note AI sharing is
  off. **Preferences**: say "remember that I study best after 9pm", or state one in
  conversation and Rika saves it (Undo removes it). All preferences (at most 40,
  4,000 characters) go with every model request. **Settings → Rika → Memory**
  searches the index, shows each source's item count, rebuilds the index, and lists
  the preferences: edit one in place, × forgets it, and **Tell Rika something to
  remember** adds one.
- **Goals** (design Q14). **Plan → Goals** lists your goals with a progress ring,
  days left and how many new finds wait. A goal has a title, why, target date,
  status (active, paused, done, dropped) and measures (label, target, unit,
  current). A measure is set by hand or counted from linked data: **Applications
  sent** (linked jobs with an application, else applications since the goal was
  set), **Study minutes** (linked study sessions, else sessions since then),
  **Tasks done** (linked calendar tasks) or **Math topics mastered** (linked math
  topics). Progress is the average of the measures' completion. Link notes, blocks,
  calendar tasks and events, jobs, opportunities, study sessions, math topics and
  email with **Link item**. Rika's tools `create_goal`, `link_to_goal` and
  `update_measure` apply at once with Undo.
- **Morning brief and alerts** (design Q15). Once a local day, from 05:00 while
  rika! is open (or at the next launch), Rika sweeps what is new since the last
  brief: jobs, opportunities, email and the next 48 hours of calendar events, and
  matches them to your active goals by their words. In live mode the unclear ones
  go to one fast-tier request, only when it fits today's AI budget; it never asks
  for approval and is skipped with a note otherwise. The brief is a **Morning
  brief** panel at the top of home, a **Morning brief · N** chip in the Rika bar
  (its popover lists the items) and a "Good morning…" message in the history. Each
  item waits on its goal under **Rika found** with **Link** and **Not relevant** (a
  dismissed find never returns). Only a strong match whose deadline, start or
  requested reply falls within 48 hours raises an alert, through the notification
  toasts and bell (source **Rika · strong match**); quiet hours hold it like any
  job-search reminder. With no active goal and no Gmail connection there is no
  sweep.
- **Email** (design Q16). Rika reads your recent inbox (not chats, promotions or
  social), sorts each message as scheduling, confirmation, thanks, question,
  personal or automated, and drafts replies for mail that expects one, in your
  voice: one standard-tier request with your preferences, the email framed as
  untrusted data. Without a model (no key, or over budget) only scheduling,
  confirmation and thanks replies are drafted, from short templates. Drafts are
  saved as Gmail drafts and listed in **Settings → Rika → Email** and the history
  drawer; nothing is sent until you tap **Send** (or approve Rika's `send_reply`).
  **Auto-send** has one switch per kind (scheduling, confirmations, thanks), all off
  by default; turning one on asks you to confirm. Personal mail and questions are
  always drafts, and a reply auto-sends only when the keyword sort and the model
  agree on its kind. Gmail's API can't delay or undo a send, so rika! holds every
  send on this machine for the hold time you choose (10, 30, 60, 120 or 300 s;
  default 30) with **Undo send** and a countdown, then sends it; after that it
  can't be unsent. A send still held when rika! quits is not sent and stays a
  cancelled draft. Connect Gmail with **Connect** there or `uv run kitty gmail auth
  --compose`: a separate token (`data/gmail_compose_token.json`, read + compose)
  from the read-only job-status token.
- **Demo.** The demo never calls OpenRouter or Gmail, downloads no voice model and
  starts no speech engine. The simple phrases above work. Anything else gets a
  scripted reply. Holding Space "hears" the canned `Add a task Read chapter 3
  tomorrow`, and a brain dump hears a canned student's week with a scripted plan in
  every area (a typed dump is planned phrase by phrase). The Agents list shows five
  scripted agents, and an approved start adds one more. No process starts. It is
  seeded with two synthetic goals, six synthetic emails in a fake mailbox (sends are
  recorded, never delivered), a few learned routes with one shortcut and two
  preferences; its morning brief runs whenever it starts.
- **API.** These routes are header-protected like the rest of the site, and none
  is an ingress route:
  - `POST /api/rika/messages {text, tier?, spoken?}`
  - `POST /api/rika/dumps {transcript, segments, duration}`
  - `POST /api/rika/plans/<id> {decision: approve, items, choices?}` or `{decision: dismiss}`
  - `GET /api/rika/history?before=`
  - `POST /api/rika/approvals/<id> {decision}`
  - `POST /api/rika/actions/<id>/undo`
  - `GET /api/rika/agents`
  - `POST /api/rika/agents/<id>/open`
  - `GET|POST /api/rika/voice` (the voice model), `POST /api/rika/voice/model` (download it now)
  - `GET /api/rika/speech/<reply id>` (the spoken summary of a reply to a spoken request)
  - `GET /api/rika/routes`, `POST /api/rika/routes/forget {signature}`
  - `GET /api/rika/memory?q=`, `POST /api/rika/memory/rebuild`,
    `POST /api/rika/preferences {text}`, `POST /api/rika/preferences/<id> {text}`,
    `POST /api/rika/preferences/<id>/delete`
  - `GET|POST /api/goals`, `GET|POST /api/goals/<id>`, `POST /api/goals/<id>/delete`,
    `POST /api/goals/<id>/links|unlink {kind, ref}`,
    `POST /api/goals/<id>/measures/<measure> {current}`,
    `POST /api/goals/matches/<id> {decision: link|dismiss}`, `GET /api/goals/linkable?q=&kind=`
  - `GET /api/rika/brief`, `POST /api/rika/brief/seen`, `POST /api/rika/brief/run`,
    `GET /api/rika/alerts`
  - `GET /api/rika/mail`, `POST /api/rika/mail/settings {kind, auto, confirm} | {hold_seconds}`,
    `POST /api/rika/mail/check`, `POST /api/rika/mail/connect`,
    `POST /api/rika/mail/drafts/<id> {body, subject?}` and `…/send`, `…/undo`, `…/discard`

  Replies stream over the same-origin WebSocket `/api/rika/stream` as token,
  message, action, approval, plan, tier, navigate, agents, voice.model, brief and
  mail events. The page may send it only voice input: `voice.start`, binary PCM
  frames, `voice.mode`, `voice.stop` and `voice.cancel`; transcripts
  (`voice.partial`, `voice.segment`, `voice.final`) answer that page only and
  nothing is stored until the page sends the words as a request. Migration 037 keeps
  the conversation, actions with their undo payloads, and approvals; migration 045
  adds how each request arrived (typed, voice or dump), the brain-dump plans and the
  voice model setting; migration 049 adds reply tokens and routes, the routing memory
  and shortcuts, goals with their links and matches, the briefs, mail with drafts and
  auto-send switches, preferences and the FTS5 index.

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
- Reports open on a summary card that tells this week or this month (toggle) in
  sentences: applications sent against the weekly target, the streak, an inline
  sparkline of the last eight weeks, replies, assessments/interviews and offers
  from confirmed status changes, the track that hears back most, and a next step.
  Every figure comes from recorded applications and status history. **Show
  tables** reveals the outcome-rate tables; formula-safe CSV/ZIP or JSON exports
  of jobs, applications and status history stay below the card.

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
job search; Ctrl+J opens or closes Rika's history; `?` shows contextual shortcuts. Arrow keys navigate
jobs/pipeline; Space marks; Enter opens details. Job actions include `t` tier,
`k` queue state, `R` resume, `e` notes, `s` status, `u` undo, `d` deadline,
`n` next action and `o` posting. In Prepare applications, `a` records a submission,
`s` skips, `l` defers, `x` marks ineligible and `n`/`p` move between rows.
Shift+F opens the reviewed autofill plan for marked, opened or focused jobs.
Letter shortcuts pause while typing or using dialogs. Ctrl/⌘W closes the active
workspace tab using the same unsaved-edit confirmation as its close button; the
last tab returns to a fresh home tab. Ctrl/⌘T opens a new tab. Native browser
print and reload shortcuts are not intercepted.

**Panel fullscreen:** press F outside text editing to fill the rika! window with
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

- Use the dedicated **Firefox window opened by rika!**, not everyday Chrome.
  Its `data/firefox` profile persists job-site cookies between preparation runs.
- If the account is not signed in, finish Google sign-in manually in that Firefox.
  rika! can select one exact, already signed-in account on a recognized Google
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
  **rika!** application-menu entry. Launch it from that menu, not from a terminal.
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
  The out-link is a garbage-collection root. Then launch **rika!** from the menu.

The Linux installer is named **rika! Workspace** and its Debian package is
`rika-workspace` (it replaces the pre-rename `kitty-workspace` package on update),
distinct from the unrelated `kitty` terminal package.

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
image unchanged. To recover an older application, quit rika! and restore that
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

Run this with rika! closed, after installing a release containing these changes.
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
installs the icon and **rika!** application-menu entry, and creates
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
gesture while rika! is open.

Rika's voice input uses the webview's microphone. WebKitGTK refuses `getUserMedia`
unless the app answers its permission request, so the main window allows microphone
requests (`on_permission_request` in `desktop/src/main.rs`; camera, location and
the rest keep WebKit's default refusal). rika!'s loopback origin counts as secure,
and WebKitGTK 2.52 has media streams on by default. Capture goes through WebKit's
GStreamer and needs both **gst-plugins-base** and **gst-plugins-good**: with base
alone the microphone fails to start. The `.deb` depends on them, and the AppImage
bundles the build host's GStreamer plugins (`bundleMediaFramework`), because
NixOS's `appimage-run` provides only base and ugly. The backend sidecar freezes
faster-whisper with its native CTranslate2, PyAV and tokenizers libraries
(`desktop/package_backend.py`); the Whisper model itself is downloaded on first use.

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
No rika! source archive, checkout, private workspace or applicant data is uploaded
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
updates**, **Keep running on close**, **New quick note** and **Quit rika!** (which
saves and stops workers). All remain in the system-tray menu,
along with **Show rika!**. Without a system tray, keeping the app running on close
is unavailable. A browser-only website explains that desktop controls are unavailable.
The page uses the protected backend API and a fixed command allowlist over the
shell's private owner pipe; it receives no Tauri IPC permissions.

The main window and quick-note windows are transparent: the theme's backdrop is
drawn translucent, so the desktop shows through around the surfaces, and through
them on the glass themes (Kitty, Liquid Glass, Dracula, Tokyo Night).
Browser tabs keep an opaque background. All typefaces (Fraunces, Inter, Fredoka,
JetBrains Mono) are SIL OFL fonts bundled from their `@fontsource` packages, so
no font is fetched from the network.


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
announcements (off until the `USAJOBS` key and `USAJOBS_EMAIL` in `.env` are set).
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
  Cloudflare's bot check, so pages load in headless Firefox through rika!'s own driver
  with a separate profile (`data/hiringcafe-firefox`, never the form filler's
  `data/firefox` or your everyday profile). No user-agent or fingerprint changes and no
  captcha solving: a check that does not clear within 30 seconds stops the run with an
  error (429/1015: `rate_limited`), never a silent zero. Each posting links to the
  employer's own page and is deduplicated by that URL and hiring.cafe's cluster id;
  graduate-only degree requirements are dropped like Simplify's, and a stated security
  clearance becomes an eligibility note. Malformed postings make a source partial
  (or failed if none are usable), not a healthy empty result.

Brave search is **off even if a key is present**. To opt in, set the `BRAVE` key
(`kitty keys set BRAVE`, or `BRAVE_SEARCH_API_KEY` in the gitignored `.env`) and set
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
rika! opens each application form in its own Firefox window (a separate profile in
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
leaves it open; in the window list its app id is `kitty`. Before filling, rika!
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

Scoped employer email verification reuses Gmail in rika!'s dedicated Firefox
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
  fills my favorites' forms in the rika! Firefox, only those in the
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

Needs the `ANTHROPIC` key (`kitty keys set ANTHROPIC`, or `ANTHROPIC_API_KEY` in `.env`). Model, effort, prices and the hard per-run
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

Needs the `OPENROUTER` key ([Keys](#keys-api-keys)).
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

Read-only: the only scope the job-status check asks for is `gmail.readonly`.
Rika's email (drafts and sends, see **Rika** above) uses its own opt-in token,
`data/gmail_compose_token.json`, made by `uv run kitty gmail auth --compose`
(scopes `gmail.readonly` and `gmail.compose`); `gmail check` never uses it. One-time
setup:

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

Job-search tools (the applyai module): `today`, `list_jobs`, `get_job`,
`preparation_plan`, `weekly_progress`, `list_questions`, `finder_candidates`,
`list_proposals`, `list_resume_versions`, `get_resume_version`, `mark_applied`,
`set_status`, `skip_job`, `mark_not_eligible`, `confirm_proposal`, `reject_proposal`,
`run_discover`. None submits anything, writes or suggests screening answers, or writes
resume text; `mark_applied` only records an application I already submitted. Finder
approvals stay in the TUI. Each call opens and closes its own database connection. A
module that is off has no tools here: with neither applyai nor workspace on, the server
lists none.

`preparation_plan(track?, limit?)` lets an agent inspect the same favorites-first,
track-scoped batch as local autofill, with the configured hard cap and reasons for
leaving jobs out. `list_jobs`, `get_job` and selected plan jobs expose stored
preparation states, attempt/readiness timestamps and blockers, not browser-control
identifiers. This is a local snapshot, not a live browser check or a reservation:
ready means verified at the last attempt, not submitted. Planning does not open
Firefox, contact employers, fill forms or change applications/statuses; normal
workspace initialization may still migrate SQLite and sync local configuration.

### Notes tools (the workspace module)

The same server reads and writes my notes as blocks (`kitty.core.block_api`):

| Tool | What it does |
|---|---|
| `list_pages(query?, kind?, limit?)` | note pages, documents and quick notes, newest edit first |
| `get_page(page)` | one page as a block tree: `id`, `type`, `text`, `props`, `tags`, `order`, `children`, `cite` |
| `search_blocks(query, limit?)` | full-text index (as Ctrl K), one hit per block, matches in `**bold**` |
| `get_backlinks(target)` | `((references))`, `[[links]]`, unlinked title mentions, tagged blocks |
| `list_tags()` | every tag with its keywords, aliases and count |
| `create_page(title, notebook?, blocks?)` | a note page, optionally with blocks |
| `append_blocks(page, blocks, parent?, revision?)` | blocks at the end of a page or block |
| `insert_blocks(blocks, after? / before? / parent?, revision?)` | blocks next to a sibling |
| `update_block(block, text?, type?, props?, revision?)` | new text, type or `checked`/`collapsed`/`language`; on a page's own block, a new title |
| `move_block(block, after? / before? / parent? / page?, revision?)` | within a page or to another note page |
| `delete_block(block, revision?)` | to Trash |
| `add_to_inbox(blocks)` | blocks at the end of the Inbox page |
| `quick_note(text)` | a new quick note |
| `tag_block(block, tag, create?)` | attach a tag by ID or name |

A page is its block ID or a note page number; `blocks` is a list of strings (text
blocks) or `{type?, text?, props?, children?}`. Every read reports the op-log sequence
`seq` it reflects and cites each block as `((id@seq))`, the block as it was at that
moment (History shows it again). A `((id@seq))` pasted into a note is a block reference
like `((id))`: Notes previews it and it counts in backlinks. Every write is one block
transaction, exactly like typing in the editor: it is in History and Trash, appears
live in every open rika! window, and attaches *always* tags and updates the search
index. Writes return `seq` and the page's `revision`; pass the `revision` from your last
read and a page edited since is refused (409) instead of overwritten. Only note pages
are written, only with text blocks: documents keep their Markdown editor, quick notes
their own (`quick_note` only creates one) and canvas elements the canvas. The tools read
and write blocks only, never jobs, applications, the profile, answers or resumes.

The same functions are on the command line and over HTTP:

```bash
kitty notes ls [TITLE] [--kind page|document|quick_note] [--json]
kitty notes show <PAGE> [--json]           # outline; --json is get_page
kitty notes search "<text>" [--json]
kitty notes new "<title>" [TEXT...] [--notebook <title>] [--json]
kitty notes append <PAGE> TEXT... [--type to_do] [--parent <block>] [--revision N] [--json]
kitty notes inbox TEXT... [--json]
kitty notes quick "<text>" [--json]        # '-' as a TEXT reads it from stdin
```

`kitty notes` belongs to the workspace module. Over HTTP (UI secret required, like
every route): `GET /api/blocks/pages`, `/api/blocks/search?q=`, `/api/blocks/backlinks?id=`
and the existing `/api/blocks/page`; `POST /api/blocks/pages/create`, `/insert`,
`/update`, `/move`, `/delete`, `/inbox` (see `src/kitty/web_blocks.py`). The MCP server and
the CLI still open the database directly; a window sees their writes through the live
channel, which follows the log.

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
