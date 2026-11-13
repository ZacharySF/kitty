# External modules (module api 1)

rika! has three built-in modules (ApplyAI, Workspace, Terminal; see
[GUIDE.md → Modules](GUIDE.md#modules)). **External modules** are add-ons installed
from a GitHub repository's latest release: a new view in the navigation, optionally
backed by Python code that runs inside rika!, or by a small web server rika! starts.

This page is the contract a module must follow and how to publish one. Code:
`src/kitty/core/external_modules/` (manifest, GitHub fetch, checksums, extraction,
registry, installer) and `src/kitty/web_external_modules.py` (loading, routes, views).

> A module runs with your rika!'s permissions: Python modules run inside rika!, process
> modules are programs on your computer, and element views run in rika!'s own page.
> Install only modules you trust. rika! checks that what it downloads is exactly what
> the release published (its SHA-256), not that the code is safe.

## Installing, updating, removing

**Settings → Modules → Add-on modules**: enter the repository as `owner/repo` (or its
`https://github.com/owner/repo` address) and press **Add**. rika!:

1. asks GitHub for the repository's **latest release** (`GET /repos/<owner>/<repo>/releases/latest`);
2. picks its one module file, `<id>-<version>.tar.gz`, where `<version>` is the release
   tag without a leading `v`;
3. downloads it and checks its SHA-256 against the checksum the release publishes:
   GitHub's own asset `digest` field and/or a `<id>-<version>.tar.gz.sha256` asset. A
   mismatch, a disagreement between the two, or **no checksum at all** refuses the install;
4. unpacks it into a fresh folder, refusing absolute paths, `..`, devices, FIFOs and links
   that point outside the module (setuid bits are dropped);
5. checks `rika-module.json` (below): `api` is 1, `id` and `version` match the file name
   and tag, and every file it names exists;
6. moves it to `data/modules/<id>/<version>/` and **pins** `{id, repo, version, sha256,
   installed_at}` in the `external_modules` table.

Nothing is imported or started until rika! restarts: the page says **Restart rika! to
finish installing**. Updates are explicit: **Check for updates** compares each module's
pinned version with its repository's latest release (`Update available 0.1.0 → 0.1.1`),
and **Update** installs that release the same way. The running version keeps working
until the restart. A release whose files change under the same version is refused.
**Remove** forgets the pin at once; its files go at the next start. The module's own
data (`data/modules-data/<id>/`) is kept.

**Private repositories** need a fine-grained GitHub token with read access to the
repository's *Contents* (releases): Settings → Keys → **GitHub token** (`GITHUB`; also read
from `GITHUB_TOKEN`). The token is sent only to the GitHub API, never to the download
host GitHub redirects to, and never to modules.

A module that fails to load (an import error, a broken migration) is shown in Settings
with its error; rika! itself still starts.

## The release

```
<id>-<version>.tar.gz          the module (required)
<id>-<version>.tar.gz.sha256   optional: "<hex sha256>  <file name>"
```

The tag is `v<version>` (or `<version>`), semantic versioning (`1.2.3`, `1.2.3-rc.1`). The
tarball's **top level** (not a sub-folder) contains `rika-module.json`.

## `rika-module.json`

```json
{
  "id": "rika-learn", "name": "Learn", "version": "0.1.0", "api": 1,
  "description": "What it does, in one sentence.", "license": "AGPL-3.0-only",
  "homepage": "https://github.com/owner/rika-learn",
  "kind": "process",
  "process": { "command": ["node", "server.js"], "cwd": ".",
               "env": { "LH_DATA_DIR": "{data}", "PORT": "{port}", "HOSTNAME": "127.0.0.1" },
               "health": "/", "ready_timeout_s": 30 },
  "view": { "id": "learn", "label": "Learn", "icon": "graduation-cap", "group": "study",
            "first": true, "ui": "iframe" }
}
```

Unknown fields are errors (so a typo cannot silently change behaviour).

| Field | Rule |
| --- | --- |
| `id` | lower-case letters/digits with single hyphens (`rika-learn`), at most 48 characters; not `applyai`, `workspace` or `terminal` |
| `name` | shown in Settings, at most 60 characters |
| `version` | semantic version; must equal the release tag |
| `api` | `1` |
| `description`, `license`, `homepage` | optional text (at most 500, 64 and 300 characters) |
| `kind` | `"python"` or `"process"`; exactly the matching `python` / `process` object is present |
| `python.package` | the importable package folder in the tarball (`rika_math/__init__.py`); its name must not clash with rika! or an installed library |
| `process.command` | 1-64 strings. A first entry with `/` is relative to the module folder (`./bin/server`); `node` is rika!'s bundled Node.js when the desktop app ships one, otherwise the one on `PATH`; any other bare name is looked up on `PATH` |
| `process.cwd` | working folder inside the module (default `.`) |
| `process.env` | extra variables; values may use `{data}` (the module's data folder), `{port}` (its port) and `{module}` (its install folder) |
| `process.health` | path rika! polls with `GET` until it answers 2xx/3xx (default `/`) |
| `process.ready_timeout_s` | 1-300 seconds (default 30) |
| `view` | optional; at most one view per module |
| `view.id` | same rule as `id` |
| `view.label` | navigation label, at most 40 characters |
| `view.icon` | icon name (`graduation-cap`); unknown names show a generic icon |
| `view.group` | `plan`, `study`, `jobs` or `settings` (the header groups Plan, Study, Job search, Settings) |
| `view.first` | `true` puts the view first in its group (default `false`: last) |
| `view.ui` | `"element"` for `python` modules, `"iframe"` for `process` modules |
| `view.element` | `element` only: `{"script": "ui/view.js", "tag": "rika-math-view"}`; `tag` is a custom element name (it contains a hyphen) |

The view opens at `#m/<module-id>/<view-id>` and appears with the built-in views in its
group, in the command palette and on the atlas home.

## `python` modules

rika! adds `data/modules/<id>/<version>/` to Python's import path and imports
`python.package`. The package is a web extension with the same hooks as rika!'s built-in
`web_*` modules; all are optional:

```python
def start(app): ...                       # once, when rika! starts
def stop(app): ...                        # when rika! closes
def get(app, conn, path, query): ...      # GET  -> dict, or None for "not mine"
def post(app, conn, path, body): ...      # POST -> dict, or None
def download(app, conn, path, query): ... # GET  -> (bytes, content type, file name) or None
def upgrade(app, handler, path, query): ...  # WebSocket upgrade -> True when handled
def mutated(app): ...                     # after any committed change
```

The module owns `/api/modules/<id>` and everything below it; it is only ever called for
those paths. `conn` is that request's SQLite connection (autocommit; use
`kitty.db.connection.transaction` for multi-statement writes); `query` is a `{name: value}`
dict and `body` the parsed JSON object. Raise `kitty.web_server.APIError(status, message)`
for a plain error. Requests carry rika!'s usual protections (loopback host, same origin,
UI secret, `X-Kitty-Request` on POST). Keep files in `app.settings.data_dir /
"modules-data" / "<id>"` (created for you).

**Migrations**: a `migrations/` folder of `NNN_lower_snake.sql` files is applied at start
by rika!'s own runner, in the module's own version namespace (`module_schema_version`), so
module numbers never collide with rika!'s. Files are never edited once released. Removing a
module leaves its tables.

Only the standard library and what rika! itself bundles can be imported (the desktop app
is a frozen Python 3.12); vendor pure-Python dependencies inside the package.

### Element views

An `element` view's script is served same-origin at
`/modules/<id>/<version>/<script>` (scripts, styles, images and fonts in the module are
served this way; HTML and Python files are not). rika! imports it as an ES module, waits
for `customElements.define(tag, …)`, creates the element, sets its `rika` property and
mounts it in the view:

```js
element.rika = {
  module: "rika-math", version: "0.1.0",
  get(path),         // GET  /api/modules/rika-math/<path>  -> parsed JSON
  post(path, body),  // POST /api/modules/rika-math/<path>  -> parsed JSON
};
```

`path` is relative (`"topics"`, `""` for `/api/modules/<id>` itself). The page's content
security policy applies: no inline scripts and no requests to other origins.

## `process` modules

A process module is a web server rika! starts when its view first opens (`POST
/api/modules/<id>/start`) and stops when rika! quits. rika! picks a free loopback port at
start-up and passes it as `{port}`; the server must listen on `127.0.0.1:{port}`. The
view is a sandboxed frame of `http://127.0.0.1:{port}` (`allow-scripts allow-same-origin
allow-forms allow-downloads`, no referrer); that origin is added to the page's CSP
`frame-src` and to the desktop app's navigation guard for this run only.

The environment is clean: `HOME=<data>/home`, `LANG=C.UTF-8`, `PATH`, Nix's loader
variables, and `process.env` with its placeholders filled. No API keys, workspace paths or
`NODE_OPTIONS` are passed. Output goes to `data/modules-data/<id>/process.log`. If the
health path does not answer in time, the view shows *"<name> did not become ready within
N seconds"* with a **Try again** button. `GET /api/modules/<id>` reports `{url, available}`.

## Publishing a module

1. Lay the module out with `rika-module.json` at the top:

   ```
   rika-module.json
   rika_hello/__init__.py      # python modules: the package
   migrations/001_hello.sql    # optional
   ui/view.js                  # element views
   ```

2. Pack it and record its checksum (the folder's contents, not the folder):

   ```bash
   tar -czf rika-hello-0.1.0.tar.gz -C module .
   sha256sum rika-hello-0.1.0.tar.gz > rika-hello-0.1.0.tar.gz.sha256
   ```

3. Publish both on a release tagged `v0.1.0` (GitHub adds the asset `digest` itself):

   ```bash
   gh release create v0.1.0 rika-hello-0.1.0.tar.gz rika-hello-0.1.0.tar.gz.sha256 \
     --title "rika-hello 0.1.0" --notes "First release"
   ```

4. In rika!: Settings → Modules → Add-on modules → `owner/rika-hello` → **Add**, then
   restart rika!.

To try a module before publishing, point rika! at a local stand-in for the GitHub API:
`KITTY_GITHUB_API=http://127.0.0.1:<port> kitty-web` makes rika! ask that server for
`/repos/<owner>/<repo>/releases/latest` and accept plain `http` downloads from it
(`tests/external_module_kit.py` has a small one).
