# kitty!

A local-first desktop workspace. Notes, documents, study tools and planning live
in one SQLite database on your machine and are connected in a navigable
knowledge atlas. A bundled terminal and an optional job-application assistant
plug in as modules.

![kitty! demo: the atlas home, writing a note and finding it again](docs/media/demo.gif)

**[Download for Linux](https://github.com/ZacharySF/kitty-releases/releases/latest)**
(signed AppImage and .deb with in-app updates) ·
**[Full guide](docs/GUIDE.md)** · **[Behaviour spec](docs/SPEC.md)**

## What it does

| Module | What you get |
| --- | --- |
| **Workspace** | Block-based notes with stable block IDs, `((block))` references and `[[wiki]]` links, backlinks, canvas, mind maps, documents and PDFs, flashcards, study, calendar, tasks, quick notes in floating windows |
| **Terminal** | A pinned [herdr](https://github.com/herdrdev/herdr) session in a resizable dock, streamed into the window over a same-origin WebSocket; open omp, Claude or Codex coding agents in labelled tabs with live activity; optionally hand off to a separately installed [Stencil Tern](https://stencil.so/tern) |
| **ApplyAI** (optional) | Job discovery from public boards, triage, application preparation, a tracker and reports. It fills forms but **never submits an application** and never invents facts about the applicant |
| **Core** (always on) | Atlas home in 2D, 3D or as a Brain of every record and edit, tabs, command palette, themes, settings, module selection |

Modules can be turned on or off; disabled modules disappear from navigation,
routes, workers and MCP tools while their data stays intact.

![The 2D knowledge atlas](docs/screenshots/kitty-graph-2d/atlas-2d.png)

## Architecture

```mermaid
flowchart LR
  subgraph Desktop["Tauri shell (Rust)"]
    Updater["Signed updates"]
  end
  UI["Svelte 5 + TypeScript UI"]
  Server["Loopback HTTP + WebSocket adapter"]
  CLI["CLI (Typer)"]
  TUI["TUI (Textual)"]
  MCP["MCP server"]
  Core["Python core<br/>(all behaviour, no UI imports)"]
  DB[("SQLite<br/>numbered migrations")]
  Herdr["herdr sidecar"]

  Desktop --> UI
  Desktop -->|"PyInstaller sidecar"| Server
  UI --> Server
  Server --> Core
  CLI --> Core
  TUI --> Core
  MCP --> Core
  Core --> DB
  Server -->|"terminal socket"| Herdr
```

One rule holds the layout together: nothing under `src/kitty/core/` imports a UI
framework, so the website, desktop app, CLI, TUI and MCP server all call the
same functions against the same database.

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
  exact Host and Origin, requires a custom header on every mutation, and gates
  the terminal WebSocket behind a single-use token that expires in 30 seconds.
- **It ships.** Tauri builds signed Linux installers in CI and the app updates
  itself, including through an `appimage-run` launcher on NixOS.
- **Browser automation without a framework.** ApplyAI drives Firefox through its
  own WebDriver BiDi client and stops at every human boundary: sign-in
  challenges, consent, unknown answers and the final submit.

## Run it

Requirements: Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22+.

```bash
uv sync
npm --prefix web ci && npm --prefix web run build
uv run kitty-web --demo        # disposable workspace with synthetic data
```

Open the printed `http://127.0.0.1:…` address (not `localhost`: the server
checks the exact Host). To use a real workspace, run `uv run kitty init` and
`uv run kitty-web`. The [guide](docs/GUIDE.md) covers modules, the desktop
build, configuration and every command.

## Develop

```bash
uv run pytest -n auto          # Python core, web adapter, TUI and browser tests (~6 min)
npm --prefix web run check     # svelte-check / TypeScript
cargo test --manifest-path desktop/Cargo.toml
```

CI runs the Python tests, the frontend check and the web unit tests on every pull request.

## Built on

kitty! integrates these projects rather than reimplementing them; credit for
their features belongs to their authors.

- [herdr](https://github.com/herdrdev/herdr) (Apache-2.0): the terminal
  multiplexer behind the Terminal module, bundled at a pinned version.
- [LearnHarness](https://github.com/amosblomqvist/learnharness) (AGPL-3.0): the
  upstream learning app opened from Study, run as a separate local server in a
  cross-origin frame. Its licence and corresponding source ship with the desktop
  app.
- [Tauri](https://tauri.app), [Svelte](https://svelte.dev),
  [Textual](https://textual.textualize.io) and
  [3d-force-graph](https://github.com/vasturiano/3d-force-graph).

## License

[MIT](LICENSE). Bundled third-party components keep their own licences.
