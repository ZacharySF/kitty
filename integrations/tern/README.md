# rika! plugin for Tern

A [Tern](https://stencil.so/tern) plugin that sends things from Tern to rika! and shows
rika!'s Inbox inside Tern. It is the Tern → rika! half of platform design Q1; the
rika! → Tern half is rika!'s Tern bridge. MIT licensed, like rika!.

rika! never bundles or downloads Tern (Stencil's closed beta). This plugin only runs in a
Tern you installed yourself.

## What it adds

Palette commands (group **rika!**):

| Command | Sends |
| --- | --- |
| Capture selection to rika! Inbox | The selected text, as a quote, with the pane's title and folder |
| Capture pane to rika! Inbox | The focused pane's text (newest 20 000 bytes) as a code block |
| New rika! quick note | A line you type, as a rika! quick note |
| Send browser page to rika! Inbox | A Tern browser block's URL, title, readable text and selection, as a page snapshot |
| Pick from browser page to rika! Inbox | One element you click in a browser block, as a snapshot with that text |
| Save failed command output to rika! | The last failed command line, its exit status and the pane's output (listed only after a failure) |
| Toggle rika! offers for failed commands | When on, a failed command shows a toast pointing at the command above (off by default) |
| Connect to rika! | Pairs now, or confirms the connection |

A **rika! Inbox** block ("New rika! Inbox block" in the palette) lists rika!'s recently
edited pages and open tasks. Enter or a click opens the page (`#notes/<number>`) or the
Calendar in rika!'s web address; `r` or Refresh reloads it, `j`/`k` move.

## Installing

In rika!: **Tern** (under Plan) → **rika! plugin for Tern** → **Install**. rika! copies
this folder to `<workspace>/.private/tern/plugin` and runs `tern plugin link` on the copy.
Tern's daemons and windows watch their plugins folder, so a running Tern loads it by itself.
Installing again after a rika! update replaces the copy; **Remove** unlinks and deletes it.
The button is offered while Tern is installed and the build's `settings.tern.plugin` is on.

Why a copy: the plugin finds the running rika! through `.private/endpoint.json`, two
folders above the copy, so it needs no setting; and a desktop rika! unpacks its bundled
files into a temporary folder that a link must not point into. A link to this folder in a
checkout loads, but says it was not installed by rika! when used.

## Pairing and the token

The first command that needs rika! asks rika! to pair (kind `tern-plugin`, scopes
`capture` and `read`) and shows a code in a toast (and in an Inbox block waiting for data).
Approve the same code in rika! (the bar at the top, or Settings → Connected apps). The
command then continues by itself. If rika! later refuses the token (revoked in Settings →
Connected apps), the next command pairs again and retries once.

The token is kept with `tern.secrets` (the system keychain: Secret Service on Linux, the
login keychain on macOS; Tern itself falls back to owner-only files when the keychain
fails). Only when `tern.secrets` is missing, or reports a failure, does the plugin keep it in
`tern.kv` instead: `kv.json` in the plugin's data folder, a plain file any program running as
you can read. A toast says so when that happens. A keychain that does not answer when the
plugin reads the token is reported, and the plugin does not pair again behind it; one that
never answers a write (an unlock prompt left open) leaves the token in that window's memory
until the window closes.

Turning `settings.tern.plugin` off in rika!'s build refuses pairing and every request
without revoking the token; turning it back on needs no new pairing.

## When rika! is not running

Every command answers with a toast: "rika! isn't running. Start rika!, then try again."
(no endpoint file, a stale one after a crash, or a refused connection). The Inbox block shows
the same message; nothing raises.

## How it is built

- `window.luau` (each Tern window) owns every request to rika!, because only window halves
  have `tern.secrets`. `lib/client.luau` finds the endpoint, pairs, keeps the token and
  sends requests with `tern.fetch`.
- `host.luau` (Tern's session daemon) defines the Inbox block. It holds no token: it asks a
  window for data by opening `kitty-tern://inbox?pane=N`, which the window half claims with
  `tern.route.link`, and the window sends the result back as an action event to the block
  (`cx.session:event`). A block shown only where no plugin window runs (Tern's web client)
  says so after a few tries.
- Failed commands are noticed with the window's `command_finished` event rather than a
  host hook: the host half cannot read pane text or reach the token.
- `lib/core.luau` and `lib/inbox.luau` are pure: request bodies, limits, links, outcome
  messages, and the block's state and view.

## Developing

```sh
luau tests/run.luau                         # unit tests of lib/ (Luau 0.7xx)
tern plugin types .                         # writes tern.d.luau (ignored by Git)
luau-lsp analyze --platform=standard --definitions=@tern=tern.d.luau host.luau window.luau lib/*.luau
```

Test against a Tern daemon of its own, never your everyday one, for example
`TERN_CONFIG_DIR=/tmp/t/cfg TERN_DAEMON_SOCKET=/tmp/t/d.sock tern daemon --socket /tmp/t/d.sock`.
