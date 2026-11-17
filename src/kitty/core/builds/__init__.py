"""Builds (docs/platform-design.md, Q8/Q9): one shareable, schema-versioned file describing
a rika!: modules, settings, theme tokens, custom CSS and interface preferences.

* ``document`` reads a build file (JSON with line numbers, the published schema
  ``kitty-build.schema.json``, rika!'s own checks) and normalizes it;
* ``css`` is the custom CSS and token-value policy;
* ``store`` is the active file (config/build.json), its lock, atomic writes and history;
* ``project`` keeps the SQLite settings rows a projection of the active build;
* ``share`` exports, diffs, installs and rolls back builds.

No web imports: the CLI, TUI, MCP server and the worker share this package. rika-server
answers the build routes, watches the file and fetches build links (server/src/builds/);
``share`` is no Python caller's any more, only the reference tests/test_build_parity.py,
tests/test_web_build.py and tests/test_builds.py compare the front with.
"""
