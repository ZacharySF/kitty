# Terminal dock, herdr view and 2D atlas — design

kitty! gains a **dock** in the bottom part of its window that holds a transparent terminal backed by a bundled, real **herdr runtime**, an agent-activity strip, or nothing (hidden behind a thin handle). The same herdr session is also a **herdr view**: a node under Plan in the atlas that opens the full session in an ordinary workspace tab. You and agents share it: an Agent tab runs a coding agent inside kitty!, and agent sessions outside kitty! can attach and open their own labelled tab. The terminal is a new built-in **Terminal** module; its bytes travel over a WebSocket on kitty!'s existing loopback origin, so the page still has no native permissions. F gives **panel fullscreen**. The native menu strip leaves the window; its controls move to Settings → Desktop. Separately, the atlas lets you drag sections in and out to choose which nodes show, and every graph gains a 2D, LearnHarness-style look in kitty!'s theme (default), with 3D one click away. No visual was produced for this grill.

## Terms

- **dock** — The bottom panel of the kitty! window that holds the terminal or another view. Avoid: drawer, footer, console.
- **panel fullscreen** — One panel (a view, the dock, the graph, the terminal) filling the whole kitty! window until you press F or Esc again. Avoid: zoom, maximize.
- **herdr runtime** — herdr's background server, which owns real terminals (workspaces, tabs, panes) that keep running when no client is attached. Avoid: tmux.
- **herdr view** — The full herdr session shown as a normal kitty! view: a node in the atlas that opens in a workspace tab at full size. Avoid: terminal page, console.

## Why

In the user's words: *"in the bottom half of this i want to be able to access the terminal (it is also transparent) and i want you to be able to access the terminal and actually have your own tab if you want and it has all the features of herdr. keep the same UI aesthetic we have. but also you can have an option if you don't want to keep the terminal there, then something else will go there."* Later additions: *"press f to fullscreen into everything"*, *"get rid of this [native menu strip] and put it somewhere else maybe in settings"*, *"as long as it all runs in kitty"*, *"i want you to be able to open a tab and run the entire herdr session in there. so herdr should be its own node"*, *"drag and drop nodes in so you can choose how many nodes show"*, and *"reference the learning harness and have an option to switch to that look, so you can have the 2d nodes that look just like that but make it look more kitty theme."*

## Locked decisions

### Q1 — The dock gets herdr's features by running real herdr

**Decision: run the real herdr runtime and show its own client in a transparent browser terminal view in the dock, coloured with the Harness theme.** Every herdr feature (workspaces, tabs, splits, agent states, detach/restore, its CLI and socket API) comes from herdr itself; sessions outlive the kitty! window when allowed (Q7).

| Option | User experience | Technical correctness | Risk | Cost |
| --- | --- | --- | --- | --- |
| **A. Real herdr client in a transparent terminal view** | **mixed** | **good** | **mixed** | **good** |
| B. kitty!-drawn tabs/splits attached via herdr's socket API | good | mixed | poor | good |
| C. kitty!'s own pty terminal, no herdr | mixed | poor | mixed | good |

Weakest axis of A is user experience: herdr's sidebar and tabs are drawn as text inside the terminal rather than as kitty! pill controls. Harness is already monospace/uppercase and herdr's `[theme]` config can carry the Harness colours, so the gap is small. Risk is "could break" on herdr version changes, mitigated by bundling a pinned version (Q6).

Rejected:
- **B** looks best but is riskiest: the socket API is designed for scripting and it was not confirmed that it streams live pane output, so B could stall on a missing API, and kitty! would have to track herdr's API changes.
- **C** means rebuilding herdr's features, contradicting "all the features of herdr".

### Q2 — Agents use herdr's own CLI and socket

**Decision: agents drive kitty!'s herdr session through herdr's own CLI and socket, each in its own labelled tab; everything they do is visible live in the dock or herdr view.** The user added: *"as long as it all runs in kitty"* — kitty! ships herdr, starts its server under kitty!'s own named session, and shows every tab in kitty!.

| Option | User experience | Technical correctness | Risk | Cost |
| --- | --- | --- | --- | --- |
| **A. herdr CLI/socket, agent tab visible in kitty!** | **good** | **good** | **mixed** | **good** |
| B. kitty!-specific agent terminal API forwarding to herdr | good | mixed | mixed | good |
| C. Agent only types into a pane the user gives it | poor | good | good | good |

Weakest axis of A is risk ("could break"): the socket grants full shell access as the user, so kitty! must run herdr under a **fixed named session of its own** (never the user's default herdr session) with a user-private socket.

Rejected:
- **B** duplicates herdr's API and must follow its changes.
- **C** gives the agent no tab of its own, which was explicitly asked for.

### Q6 — herdr is bundled, pinned, inside kitty!'s installers

**Decision: bundle a pinned herdr binary in kitty!'s .deb and AppImage; kitty!'s signed updates bring herdr upgrades.**

| Option | User experience | Technical correctness | Risk | Cost |
| --- | --- | --- | --- | --- |
| **A. Bundle pinned herdr** | **good** | **good** | **mixed** | **good** |
| B. Require separate herdr install; install button when missing | mixed | good | good | good |
| C. kitty! downloads herdr on first use | good | mixed | poor | mixed |

Weakest axis of A is risk in the hard-to-undo sense: kitty! owns herdr's update cadence, so a herdr fix waits for a kitty! release. The herdr licence (Apache-2.0) permits bundling with its licence/NOTICE included.

Rejected:
- **B** breaks download-and-go for people without herdr.
- **C** fetches code outside kitty!'s signed update trust.

### Q7 — Terminal lifetime follows the existing close/quit distinction

**Decision: with "Keep running on close (monitoring / reminders)" on, closing the window leaves terminals running and reopening kitty! reattaches; "Quit kitty! (save and stop workers)" also stops kitty!'s herdr server.** With the setting off, closing the window is a quit.

| Option | User experience | Technical correctness | Risk | Cost |
| --- | --- | --- | --- | --- |
| A. Quit always stops herdr | mixed | good | mixed | good |
| B. herdr always survives quit | mixed | good | mixed | mixed |
| **C. Follow the existing Keep-running setting; Quit stops herdr** | **good** | **good** | **good** | **good** |

C is best on every axis and adds no setting.

Rejected:
- **A** kills long agent runs when the window is closed out of habit.
- **B** leaves invisible shells running after the user meant to stop everything.

### Q11 — Terminal is a new built-in module

**Decision: a new built-in Terminal module, on by default in the user's workspace; when off, no herdr process, dock terminal option, atlas node, WebSocket route or socket exists.** It uses the existing module system (restart-only selection in `config/modules.yaml`, no routes/workers when disabled).

| Option | User experience | Technical correctness | Risk | Cost |
| --- | --- | --- | --- | --- |
| A. Core, always on | mixed | mixed | mixed | mixed |
| **B. New built-in Terminal module** | **good** | **good** | **good** | **good** |
| C. Part of the Workspace module | mixed | mixed | good | good |

B is best on every axis. Default for fresh downloads follows the existing fresh-workspace module policy (fresh CLI/desktop workspaces start core-only); the user's existing workspace enables it.

Rejected:
- **A** forces a shell runtime on users who only want notes.
- **C** couples terminals to unrelated notes/study features.

### Q12 — Terminal transport is a WebSocket on the existing loopback origin

**Decision: kitty!'s backend runs the herdr client in a pty and streams it over a WebSocket on the same loopback origin, behind the same Host / Origin / required request-header checks as every API route.**

| Option | User experience | Technical correctness | Risk | Cost |
| --- | --- | --- | --- | --- |
| **A. Backend pty + same-origin WebSocket** | **good** | **good** | **good** | **good** |
| B. Native shell pty + Tauri IPC | good | mixed | poor | good |
| C. Separate loopback server/port for terminals | good | mixed | mixed | good |

A is best on every axis. It works identically in the desktop app and a browser tab. Because browsers cannot set custom headers on WebSocket upgrades, the upgrade must be authorised by an equivalent same-origin proof (exact Origin + Host and a per-page token obtained from a header-protected API call), not by the header itself.

Rejected:
- **B** grants the page native IPC for the first time, letting a page compromise reach the machine.
- **C** adds a second origin to protect for no benefit.

### Q14 — 2D LearnHarness-style look, written fresh

**Decision: add a switchable 2D look beside the existing 3D layouts, written fresh for kitty!: soft region hulls per group (Plan, Study, Job search, Settings, …) in the theme's group colours, zoom-faded node labels in kitty!'s fonts, hover glow, Harness pill labels. The choice is remembered.**

| Option | User experience | Technical correctness | Risk | Cost |
| --- | --- | --- | --- | --- |
| **A. Switchable 2D look, written fresh** | **good** | **good** | **good** | **good** |
| B. Replace 3D entirely with 2D | mixed | good | mixed | good |
| C. Embed LearnHarness's graph component | good | poor | poor | good |

A is best on every axis. LearnHarness (upstream `amosblomqvist/learnharness`, `components/graph-canvas.ts`) is **AGPL-3.0**; kitty! is MIT. Only the look is referenced: 2D canvas, force layout, cluster hull fills/strokes with region labels, label fade by zoom (Obsidian-like), constant-radius hover halo, d3-zoom pan/zoom-to-cursor/pinch. No code is copied.

Rejected:
- **B** removes the working 3D views the user did not ask to lose.
- **C** would put kitty! under AGPL.

### Q17 — Stencil Tern is an optional second terminal, shown as status and opened natively

**Decision: with the Terminal module on, a Tern node under Plan shows the user's installed [Stencil Tern](https://stencil.so/tern) running kitty!'s own daemon and `kitty` session; Open in Tern opens Tern's native window on it. herdr stays the bundled default.** The owner asked: *"add stencil tern to kitty"*.

| Option | User experience | Technical correctness | Risk | Cost |
| --- | --- | --- | --- | --- |
| A. Embed `tern web serve`'s client in a view | good | poor | mixed | mixed |
| **B. Status panel + native window on kitty!'s daemon** | **mixed** | **good** | **good** | **good** |
| C. Bundle Tern like herdr | good | mixed | poor | good |

Weakest axis of B is user experience: the terminal opens in Tern's own window, not inside kitty!. The spike (0.9.8) showed why A fails where it matters: the web client requires WebGPU; Chromium with a hardware adapter renders it and takes input, but kitty!'s desktop WebKitGTK 2.54 has no `navigator.gpu` (its WebGPU feature is "unstable" and stays absent even when forced on), so the page only says "this browser has no WebGPU". The view never draws a fake terminal.

Rejected:
- **A** works only in a Chromium tab, not in the desktop app, and loads its wasm from build.stencil.so.
- **C** is not allowed: Tern is closed-beta proprietary software; kitty! runs only the user's `tern` (PATH or a saved path) and behaves exactly as before without it.

Lifecycle follows Q7 and Q2: nothing starts until Start or Open; the daemon lives on a private socket (`$XDG_RUNTIME_DIR/kitty/tern-<hash>/daemon.sock`, directory 0700, refused unless owner-only) so the user's own Tern daemon and sessions are never touched; every `tern` kitty! runs gets `TERN_DAEMON_SOCKET` for that socket and none of an outer Tern pane's variables. Quit closes the windows kitty! opened and stops its daemon (by its socket's peer pid); a window would otherwise restart a daemon on its own. Status comes from `tern inspect --json` (sessions, panes, working state, attached windows) polled like the activity strip.

**Panes in the dock.** The dock's **Agents** view adds a Tern section (only when the Terminal module is on and Tern is installed; nothing at all otherwise) listing the panes of kitty!'s `kitty` session with their live state (working, idle, or exited with its status; Tern keeps exited panes listed) and recent pane starts and exits from `tern events --filter pane_spawned,pane_exited`, followed while kitty!'s daemon runs and stopped at Quit. Its actions reach only kitty!'s daemon socket and only blocks listed in the `kitty` session, addressed as `kitty/<id>` so Tern itself refuses any other session: **New pane** (`tern new tab kitty --cwd <workspace>`, starting the session first if needed), **Run** one command line in the chosen pane (`tern run kitty/<id> -- <line>`; a newline, escape or other control character is refused with 400 before Tern runs), **Focus** (`tern focus kitty/<id>`, which only changes what the window shows, then `tern --dir <workspace>`: Tern 0.9.8 hands that to a window already on kitty!'s daemon and raises it, else it opens one) and **Capture** (`tern capture kitty/<id> --scrollback`, terminal controls stripped, newest 60 KiB kept) into a quick note that **Open note** pops out; capture needs the Workspace module. A block that is not a pane of kitty!'s session is 404; actions while kitty!'s daemon is not running are 409. Routes: `POST /api/terminal/tern/{new,run,focus,capture}`.

## Routine choices

- **Q3 — Dock contents:** three states: **terminal**, **agent-activity strip** (herdr's own agent states: working / blocked / done / idle, per workspace), and **hidden** (main view takes the full height; a thin handle brings the dock back). Not arbitrary kitty! views.
- **Q4 — F key:** panel fullscreen only; the kitty! window's own fullscreen state is unchanged. Bare F works when focus is not in a text field or the terminal; in the terminal/herdr view use Ctrl+Shift+F (a bare f must reach the shell). Esc or the same key exits.
- **Q5 — Native menu strip:** removed from the window (no `app.set_menu`). Its controls move to **Settings → Desktop**: Check for updates, Keep running on close, New quick note, Quit. The page asks the backend, which passes these fixed commands to the desktop shell over its existing private owner pipe; no page-to-native permission is added. The tray menu keeps all of them.
- **Q8 — Dock size:** divider is draggable and the height is remembered. The first size is the space the dock fills in the current layout (the empty area below the main panel in the current window), not exactly half.
- **Q9 — Where agents run:** both: an **Agent** tab in the dock/herdr view that runs the chosen coding agent (omp by default) inside herdr and controls its panes from inside with herdr's skill; and agent sessions outside kitty! may attach to kitty!'s herdr session and open their own labelled tab.
- **Q10 — herdr node placement:** the herdr view is a node under **Plan** in the atlas.
- **Q13 — Choosing atlas nodes:** drag a section from the Sections list onto the graph to show it; drag a graph node back onto the list to hide it. Hidden sections stay in the list, dimmed. Remembered for the atlas in every tab. Each row's menu also offers Show/Hide for keyboard and touch users.
- **Q15 — 2D scope:** every graph drawn by `web/src/lib/graph3d.ts` (atlas, section graphs, schema network, mind-map graph view). One 2D/3D switch in the existing graph preferences (`graphPreferences.ts`, Settings) plus a quick toggle on each graph's controls.
- **Q16 — Default look:** 2D (LearnHarness-style); 3D one click away.

## Verified facts

- kitty! has no terminal today; herdr is not installed on this machine.
- herdr is a runtime: a background server owns panes; clients attach to render. With multiple clients, each can view its own workspace and tab; when clients view the same tab, the last to focus/select/interact controls its pane sizes (herdr docs, Concepts → Client and server). This is what lets the dock and a herdr view tab show different things at once.
- herdr has a CLI, a local socket API, named sessions (`herdr session attach <name>`), agent detection (states working/blocked/done/idle/unknown, `herdr agent list`), `[theme]` config, and ships an agent skill (`skills/herdr/SKILL.md`). Nested launches inside a herdr pane are blocked by design (`HERDR_ENV=1`).
- herdr licence: Apache-2.0; latest release v0.9.5 with `herdr-linux-x86_64` and `herdr-linux-aarch64` binaries.
- LearnHarness 0.3.8 (commit `3dc88472`) graph: `components/graph-canvas.ts` (2D canvas, d3-zoom, forceAtlas2 layout outside the renderer, hull → edge → node → region label → node label → hover draw order, zoom-gated label fade, hover halo). Licence AGPL-3.0.
- The native menu strip shown in the screenshot is `app.set_menu(menu)` in `desktop/src/main.rs`, added with the signed updater; the tray menu uses the same items.
- One engine, `web/src/lib/graph3d.ts`, is used by GraphNavigator (atlas), SectionGraph, SchemaNetwork, SchemaMaps; graph preferences live in `graphPreferences.ts` and WorkspaceSettings.
- The desktop backend already has a private owner pipe (stdin `quit`, stdout readiness) between the native shell and the sidecar.
- Tern 0.9.8 (spike, isolated HOME/XDG/`TERN_CONFIG_DIR`/socket): `tern daemon --socket PATH` makes a 0600 socket and keeps `PATH.state` beside it, restoring panes after a restart; it exits after ~10 s with no sessions and no clients; windows (`tern [--dir] DIR`) and `tern web serve` start one on `TERN_DAEMON_SOCKET` when none runs, and a window on another daemon socket does not take over another daemon's launch. `tern --dir DIR` attaches to the existing session without adding a tab. Scripting (`ls`, `inspect`, `new`, `run`, `capture`, `events` JSON lines) works without signing in; native windows show the closed-beta sign-in gate until a Stencil account is signed in. `tern web serve` needs a token file and WebGPU, serves no frame-blocking headers and fetches its wasm from build.stencil.so.

## Risks

- herdr's theme config may be global (`~/.config/herdr/config.toml`) rather than per session; theming kitty!'s session in Harness colours without changing the user's own herdr config needs verification (e.g. a kitty!-private config path or env override).
- The WebSocket upgrade cannot carry the custom request header; a same-origin upgrade proof must be designed and tested as strictly as the existing checks.
- Any process running as the user can reach the herdr socket; kitty!'s session name and socket permissions must not widen that.
- Bundled herdr lags upstream fixes until a kitty! release.
- Bare-F shortcut must never fire while typing (text fields, contenteditable, terminal); Ctrl+Shift+F must not collide with herdr's own bindings.
- Two graph renderers double the interaction surface (focus, drag, context menus, deletion, hide/show by drag); both need the same behavioural tests.
- Persisted hidden-node choices must tolerate modules being turned off (hidden or disabled sections must not resurrect or crash the atlas).
- Tern is closed beta: its CLI and `inspect --json` fields may change between the user's updates (kitty! does not pin it); an unreadable report is shown as an error, never as an empty session. Opening Tern from the AppImage environment is unverified.

## Deferred

None.

## Open threads

- Q8: "starts at what it currently is" was interpreted as the space the dock fills in the current layout (empty area below the main panel). Confirm during implementation if a different starting size was meant.
- The agent-activity strip could later also become a node under Plan next to the herdr view.
