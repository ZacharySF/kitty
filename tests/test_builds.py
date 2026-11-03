"""Builds (docs/platform-design.md, Q8/Q9): the build file, its policy, projection and sharing."""
from __future__ import annotations

import json
import os
import stat
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from kitty.config import ConfigError
from kitty.core import notes, opportunities, quick_notes, study
from kitty.core.blocks import history, tags
from kitty.core.builds import css, document, project, share, store
from kitty.core.workspace import open_workspace
from kitty.db import connect, migrate
from kitty.settings import Settings


def write_build(root: Path, data: dict | str) -> str:
    text = data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "build.json").write_text(text, encoding="utf-8")
    return text


def read_build(root: Path) -> dict:
    return json.loads((root / "config" / "build.json").read_text(encoding="utf-8"))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    write_build(tmp_path, {"schema": 1, "modules": ["workspace"]})
    return tmp_path


@pytest.fixture
def conn(root: Path):
    connection = connect(root / "data" / "test.db")
    migrate(connection)
    project.sync(connection, root)
    yield connection
    connection.close()


# The file format -------------------------------------------------------------------------

def test_the_published_schema_is_valid_and_an_empty_build_means_every_default():
    Draft202012Validator.check_schema(document.schema())
    checked = document.check('{"schema": 1}')
    assert checked.ok and checked.problems == []
    assert checked.build == document.defaults()
    assert checked.build["modules"] == ["applyai", "workspace", "terminal"]
    assert checked.build["settings"]["history"]["retention_days"] == 30
    assert checked.build["settings"]["tags"]["mode"] == "suggest"


def test_problems_point_at_the_offending_line_and_column():
    text = '{\n  "schema": 1,\n  "settings": {\n    "tags": {"mode": "loud"}\n  }\n}\n'
    checked = document.check(text)
    assert not checked.ok
    (problem,) = checked.errors
    assert (problem.line, problem.column, problem.path) == (4, 22, "settings.tags.mode")
    syntax = document.check('{\n  "schema": 1,\n  "name": "x",,\n}')
    assert syntax.errors[0].line == 3 and "not valid JSON" in syntax.errors[0].message
    duplicate = document.check('{"schema": 1,\n"name": "a",\n"name": "b"}')
    assert duplicate.errors[0].line == 3 and "duplicate key 'name'" in duplicate.errors[0].message
    assert "NaN" in document.check('{"schema": 1, "ui": {"graph": {"physics": {"spring": NaN}}}}').errors[0].message


def test_a_build_from_a_newer_kitty_or_without_a_version_is_refused_with_a_reason():
    assert "newer rika!" in document.check('{"schema": 2}').errors[0].message
    assert '"schema": 1' in document.check('{"name": "x"}').errors[0].message
    assert "JSON object" in document.check("[]").errors[0].message


def test_unknown_keys_and_themes_are_warnings_so_newer_builds_still_load():
    text = json.dumps({"schema": 1, "x-gallery": {"id": 4}, "brain": True,
                       "settings": {"tags": {"mode": "always", "fuzzy": True}},
                       "theme": {"base": "aurora", "tokens": {"glass-blur": "18px"}},
                       "ui": {"planner": {"zoom": 2}, "brain": {"zoom": 2}}}, indent=1)
    checked = document.check(text)
    assert checked.ok
    warned = {problem.path for problem in checked.warnings}
    assert warned == {"brain", "settings.tags.fuzzy", "theme.base", "ui.brain.zoom"}, warned
    assert all(problem.line > 1 for problem in checked.warnings)
    build = checked.build
    assert build["theme"]["base"] == "kitty" and build["theme"]["tokens"] == {"glass-blur": "18px"}
    assert "brain" not in build and "x-gallery" not in build and "planner" not in build["ui"]
    assert "zoom" not in build["ui"]["brain"]
    assert build["settings"]["tags"] == {"mode": "always"}


def test_theme_tokens_are_hex_colours_for_roles_and_plain_css_values_otherwise():
    good = document.check(json.dumps({"schema": 1, "theme": {"tokens": {
        "ink": "#fafafa", "void": "#000", "graph3": "#123456", "radius": "6px",
        "glass": "color-mix(in srgb, #fff 40%, transparent)"}}}))
    assert good.ok
    bad = document.check(json.dumps({"schema": 1, "theme": {"tokens": {
        "ink": "red", "backdrop": "url(https://evil.example/x.png)", "pad": "1px; } body { display: none",
        "loud": "red !important"}}}, indent=1))
    messages = {problem.path: problem.message for problem in bad.errors}
    assert set(messages) == {"theme.tokens.ink", "theme.tokens.backdrop", "theme.tokens.pad", "theme.tokens.loud"}
    assert "hex colour" in messages["theme.tokens.ink"] and "url()" in messages["theme.tokens.backdrop"]


def test_css_as_a_list_of_lines_reports_the_json_line_of_the_bad_css_line():
    text = '{\n "schema": 1,\n "theme": {"css": [\n  "#app { color: red }",\n  "@import \\"https://evil.example/a.css\\";"\n ]}\n}'
    checked = document.check(text)
    (problem,) = checked.errors
    assert problem.line == 5 and "@import" in problem.message
    one = document.check(json.dumps({"schema": 1, "theme": {"css": "a {}\nb {}\nc { background: url(https://x.y) }"}}))
    assert "CSS line 3" in one.errors[0].message
    assert document.check(json.dumps({"schema": 1, "theme": {"css": ["a {", "color: red }"]}})).build["theme"]["css"] == "a {\ncolor: red }"


# The CSS policy --------------------------------------------------------------------------

ALLOWED_CSS = [
    ".sidebar { background: var(--panel); border-radius: 12px !important }",
    "@media (max-width: 800px) { .tabs { display: none } }",
    "@supports (backdrop-filter: blur(4px)) { .glass { backdrop-filter: blur(4px) } }",
    "@container (min-width: 300px) { .card { padding: 2rem } }",
    ".x::before { content: attr(data-label) ' \\2014' }",
    ".x { --glow: 0 0 4px #fff; box-shadow: var(--glow) }",
    ".logo { background: url(data:image/png;base64,iVBORw0KGgo=) }",
    ".logo { background: url('data:image/webp;base64,UklGR=') }",
    "/* a comment with } and url(https://x) inside */ .a { color: red }",
    ".a[title=\"}\"] { color: red }",
    ":scope { --accent: #ff00ff }",
]

REFUSED_CSS = [
    ("@import url(https://evil.example/x.css);", "@import"),
    ("@import 'theme.css';", "@import"),
    ("@IMPORT 'theme.css';", "@import"),
    (".a { background: url(https://evil.example/beacon) }", "url()"),
    (".a { background: url(\"https://evil.example/beacon\") }", "url()"),
    (".a { background: URL(//evil.example/beacon) }", "url()"),
    (".a { background: url(/api/keys) }", "url()"),
    (".a { background: u\\72l(https://evil.example/x) }", "url()"),
    (".a { background: \\75 rl(https://evil.example/x) }", "url()"),
    (".a { background: url(data:image/svg+xml;base64,PHN2Zz4=) }", "url()"),
    (".a { background: image-set(\"https://evil.example/a.png\" 1x) }", "image-set()"),
    (".a { background: -webkit-image-set(\"a.png\" 1x) }", "-webkit-image-set()"),
    (".a { background: image(\"a.png\") }", "image()"),
    (".a { background: cross-fade(\"a.png\", \"b.png\", 50%) }", "cross-fade()"),
    (".a { background: element(#keys) }", "element()"),
    (".a { background: -moz-element(#keys) }", "-moz-element()"),
    (".a { width: expression(alert(1)) }", "expression()"),
    (".a { background: paint(worklet) }", "paint()"),
    (".a { behavior: something.htc }", "behavior"),
    (".a { -moz-binding: x }", "-moz-binding"),
    ("@font-face { font-family: X; src: local(Arial) }", "@font-face"),
    ("@namespace svg x;", "@namespace"),
    ("@scope (body) { * { display: none } }", "@scope"),
    ("} body { display: none } .a {", "unexpected '}'"),
    (".a { color: red", "never closed"),
    (".a { color: red } /* never ends", "unterminated comment"),
    (".a { content: \"open }", "unterminated string"),
    ("<!-- .a { color: red } -->", "HTML comment"),
    (".a { color: red; } .b { x: ( ] }", "unexpected ']'"),
    ("a {" * 40 + "}" * 40, "nested too deeply"),
]


@pytest.mark.parametrize("text", ALLOWED_CSS)
def test_css_that_only_styles_is_allowed_as_written(text):
    assert css.check_css(text) == []


@pytest.mark.parametrize(("text", "reason"), REFUSED_CSS)
def test_css_that_could_load_run_or_escape_its_scope_is_refused(text, reason):
    problems = css.check_css(text)
    assert problems, text
    assert any(reason in problem.message for problem in problems), [problem.message for problem in problems]


def test_css_problems_carry_their_line_and_column():
    (problem,) = css.check_css(".a { color: red }\n\n  .b { background: url(https://x.example) }")
    assert (problem.line, problem.column) == (3, 20)
    assert css.check_css("x" * (css.MAX_CSS + 1))[0].message.startswith("custom CSS is limited")


# What a build holds and never holds -------------------------------------------------------

def test_a_build_has_exactly_the_documented_sections():
    assert set(document.schema()["properties"]) == {
        "$schema", "schema", "kitty_version", "name", "author", "description", "modules", "settings", "theme", "ui"}
    assert set(document.schema()["properties"]["settings"]["properties"]) == {
        "ai", "notes", "history", "tags", "study", "opportunities", "tern"}
    assert set(document.schema()["properties"]["ui"]["properties"]) == {
        "dock", "graph", "atlas", "notes", "terminal", "appearance", "home", "brain", "notifications", "flashcards", "study"}


def test_export_never_carries_keys_credentials_devices_clients_or_applicant_data(tmp_path):
    root = tmp_path
    secret = "sk-or-SYNTHETIC-SECRET-0123456789"
    (root / ".private" / "keys").mkdir(parents=True)
    (root / ".private" / "openrouter-key").write_text(secret)
    (root / ".private" / "application-credentials.env").write_text("PASSWORD=SYNTHETIC-PASSWORD\n")
    (root / "config").mkdir()
    (root / "config" / "profile.yaml").write_text("name: Synthetic Applicant\nemail: applicant@example.invalid\n")
    (root / "config" / "answers.yaml").write_text("answers: {sponsorship: SYNTHETIC-ANSWER}\n")
    write_build(root, {"schema": 1, "modules": ["workspace"], "name": "Mine", "x-private": "SYNTHETIC-EXTRA"})
    with open_workspace(Settings(root=root, db_path=root / "data" / "apply.db", demo=False)) as workspace:
        connection = workspace.conn
        connection.execute("INSERT INTO clients(id,name,kind,scopes,origin,token_hash,created_at) "
                           "VALUES ('c1','SYNTHETIC-CLIENT','tern-plugin','[]',NULL,'hash-SYNTHETIC','2026-11-01')")
        connection.execute("UPDATE opportunity_settings SET profile=? WHERE id=1", (json.dumps(
            {"major": "SYNTHETIC-MAJOR", "track": "x", "interests": [], "locations": ["SYNTHETIC-CITY"],
             "prefer_remote": False}),))
        connection.execute("UPDATE study_settings SET timezone='Pacific/Chatham' WHERE id=1")
        notebook = notes.create_notebook(connection, "SYNTHETIC-NOTEBOOK")
        notes.create_page(connection, notebook["id"], "SYNTHETIC-PAGE")
        quick_notes.create(connection, "SYNTHETIC-QUICK-NOTE")
        synced = project.sync(connection, root)
    exported = share.export_text(synced.build)
    data = json.loads(exported)
    assert set(data) == {"schema", "kitty_version", "name", "author", "description", "modules", "settings", "theme", "ui"}
    assert data["name"] == "Mine" and data["kitty_version"]
    for leaked in (secret, "SYNTHETIC-PASSWORD", "Synthetic Applicant", "applicant@example.invalid", "SYNTHETIC-ANSWER",
                   "SYNTHETIC-CLIENT", "hash-SYNTHETIC", "SYNTHETIC-MAJOR", "SYNTHETIC-CITY", "Pacific/Chatham",
                   "SYNTHETIC-NOTEBOOK", "SYNTHETIC-PAGE", "SYNTHETIC-QUICK-NOTE", "SYNTHETIC-EXTRA", str(root)):
        assert leaked not in exported, leaked


# Projection, migration and the live file ---------------------------------------------------

def closing_connection(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return closing(connect(path))


def test_the_first_open_moves_modules_yaml_and_the_settings_rows_into_the_build(tmp_path):
    root = tmp_path
    (root / "config").mkdir()
    (root / "config" / "modules.yaml").write_text("enabled: [workspace, terminal]\n")
    db = root / "data" / "apply.db"
    with closing_connection(db) as connection:
        migrate(connection)
        connection.execute("UPDATE ai_settings SET model='synthetic/model', daily_budget_usd='2.50' WHERE id=1")
        connection.execute("UPDATE note_preferences SET allow_ai=1, retention_days=12 WHERE id=1")
        connection.execute("UPDATE study_settings SET focus_seconds=1800, sound=0 WHERE id=1")
        connection.execute("UPDATE opportunity_settings SET interval_hours=48 WHERE id=1")
        connection.execute("UPDATE tag_settings SET mode='always' WHERE id=1")
    with open_workspace(Settings(root=root, db_path=db, demo=False)) as workspace:
        assert workspace.config.modules == ("workspace", "terminal")
    assert not (root / "config" / "modules.yaml").exists()
    build = read_build(root)
    assert build["modules"] == ["workspace", "terminal"]
    assert build["settings"]["ai"] == {"model": "synthetic/model", "routing": "latency", "max_output_tokens": 2048,
                                       "daily_budget_usd": 2.5}
    assert build["settings"]["notes"] == {"allow_ai": True, "attachment_retention_days": 12}
    assert build["settings"]["study"]["focus_seconds"] == 1800 and build["settings"]["study"]["sound"] is False
    assert build["settings"]["opportunities"] == {"monitoring": False, "interval_hours": 48}
    assert build["settings"]["tags"] == {"mode": "always"}
    assert stat.S_IMODE((root / "config" / "build.json").stat().st_mode) == 0o600
    before = (root / "config" / "build.json").read_text()
    with open_workspace(Settings(root=root, db_path=db, demo=False)):
        pass
    assert (root / "config" / "build.json").read_text() == before


def test_settings_already_in_a_build_file_win_over_the_rows_on_the_first_move(tmp_path):
    write_build(tmp_path, {"schema": 1, "modules": [], "settings": {"tags": {"mode": "suggest"}}})
    db = tmp_path / "data" / "apply.db"
    with closing_connection(db) as connection:
        migrate(connection)
        connection.execute("UPDATE tag_settings SET mode='always' WHERE id=1")
        project.sync(connection, tmp_path)
        assert tags.settings(connection)["mode"] == "suggest"
    assert read_build(tmp_path)["settings"]["tags"] == {"mode": "suggest"}


def test_an_edited_file_is_projected_and_an_invalid_one_keeps_the_last_good_build(root, conn):
    data = read_build(root)
    data["settings"]["tags"] = {"mode": "always"}
    data["settings"]["history"] = {"retention_days": 7}
    data["settings"]["tern"] = {"plugin": False}
    write_build(root, data)
    synced = project.sync(conn, root)
    assert not synced.invalid and synced.active_revision == synced.revision
    assert tags.settings(conn)["mode"] == "always"
    assert history.retention_days(conn) == 7 and not project.tern_plugin_enabled(conn)
    good = synced.revision
    revision = tags.settings(conn)["revision"]
    broken = (root / "config" / "build.json").read_text().replace('"always"', '"sometimes"')
    write_build(root, broken)
    synced = project.sync(conn, root)
    assert synced.invalid and synced.active_revision == good and synced.revision != good
    assert synced.build["settings"]["tags"]["mode"] == "always"
    assert synced.problems[0].path == "settings.tags.mode"
    assert tags.settings(conn) == {"mode": "always", "revision": revision}
    settings = Settings(root=root, db_path=root / "data" / "test.db", demo=False)
    with pytest.raises(ConfigError):
        from kitty.modules import load_modules
        load_modules(settings.config_dir)
    assert project.startup_modules(settings) == ("workspace",)


def test_a_settings_screen_writes_the_build_file_and_the_rows_together(root, conn):
    revision = tags.settings(conn)["revision"]
    assert tags.set_mode(conn, root, "always", revision)["mode"] == "always"
    assert read_build(root)["settings"]["tags"]["mode"] == "always"
    current = conn.execute("SELECT revision FROM study_settings WHERE id=1").fetchone()[0]
    study.save_settings(conn, {"revision": current, "focus_seconds": 600, "timezone": "Europe/Oslo"}, root)
    assert read_build(root)["settings"]["study"]["focus_seconds"] == 600
    assert "Europe/Oslo" not in (root / "config" / "build.json").read_text(), "a device's timezone stays out"
    assert conn.execute("SELECT focus_seconds, timezone FROM study_settings").fetchone()[:] == (600, "Europe/Oslo")
    prefs = notes.preferences(conn, root, conn.execute("SELECT revision FROM note_preferences").fetchone()[0], True, 9)
    assert prefs["allow_ai"] is True and read_build(root)["settings"]["notes"] == {"allow_ai": True, "attachment_retention_days": 9}
    saved = opportunities.save_settings(conn, {"revision": opportunities.settings(conn)["revision"], "interval_hours": 12,
                                               "profile": {"major": "SYNTHETIC-MAJOR", "track": "t", "interests": [],
                                                           "locations": [], "prefer_remote": True}}, root)
    assert saved["interval_hours"] == 12 and saved["profile"]["major"] == "SYNTHETIC-MAJOR"
    text = (root / "config" / "build.json").read_text()
    assert json.loads(text)["settings"]["opportunities"]["interval_hours"] == 12 and "SYNTHETIC-MAJOR" not in text


def test_settings_changes_are_refused_while_the_file_has_an_error(root, conn):
    write_build(root, '{"schema": 1, "modules": [}')
    revision = tags.settings(conn)["revision"]
    with pytest.raises(store.BuildStoreError) as raised:
        tags.set_mode(conn, root, "always", revision)
    assert raised.value.status == 409 and raised.value.problems[0].line == 1
    assert tags.settings(conn) == {"mode": "suggest", "revision": revision}
    assert (root / "config" / "build.json").read_text() == '{"schema": 1, "modules": [}'


def test_a_symlinked_build_file_is_never_followed(tmp_path):
    (tmp_path / "config").mkdir()
    target = tmp_path / "elsewhere.json"
    target.write_text('{"schema": 1}')
    os.symlink(target, tmp_path / "config" / "build.json")
    with pytest.raises(store.BuildStoreError):
        store.read_text(tmp_path)
    with pytest.raises(store.BuildStoreError):
        store.edit(tmp_path, lambda build: build.update(name="x"))
    assert target.read_text() == '{"schema": 1}'


# Sharing: diff, install, rollback, fetch ----------------------------------------------------

def stranger(**changes) -> str:
    data = {"schema": 1, "name": "Stranger's kitty", "author": "someone", "modules": ["workspace", "terminal"],
            "theme": {"base": "nord", "tokens": {"ink": "#ffeeff"}, "css": ".tabs { gap: 2px }"},
            "settings": {"notes": {"allow_ai": True}, "ai": {"model": "expensive/model", "daily_budget_usd": 50},
                         "opportunities": {"monitoring": True}, "tags": {"mode": "always"}}}
    data.update(changes)
    return json.dumps(data)


def test_a_preview_lists_every_change_with_restart_and_privacy_or_cost_flags(root, conn):
    current = project.sync(conn, root).build
    plan = share.resolve(current, stranger(), accept_sensitive=True)
    changes = {change["path"]: change for change in plan["changes"]}
    assert changes["modules"]["restart"] and plan["restart"]
    assert changes["theme.base"]["after"] == "nord" and changes["theme.tokens.ink"]["before"] is None
    assert {path for path, change in changes.items() if change["sensitive"]} == {
        "settings.notes.allow_ai", "settings.ai.model", "settings.ai.daily_budget_usd", "settings.opportunities.monitoring"}
    assert not changes["settings.tags.mode"]["sensitive"]
    cheaper = share.diff(current, share.resolve(current, stranger(settings={"ai": {"daily_budget_usd": 0.5}}),
                                                accept_sensitive=True)["build"])
    assert [change["sensitive"] for change in cheaper if change["path"] == "settings.ai.daily_budget_usd"] == [False]


def test_installing_holds_back_privacy_and_cost_changes_unless_accepted(root, conn):
    current = project.sync(conn, root).build
    plan = share.resolve(current, stranger(), accept_sensitive=False)
    assert {change["path"] for change in plan["held"]} == {
        "settings.notes.allow_ai", "settings.ai.model", "settings.ai.daily_budget_usd", "settings.opportunities.monitoring"}
    build = plan["build"]
    assert build["settings"]["notes"]["allow_ai"] is False and build["settings"]["ai"]["daily_budget_usd"] == 1.0
    assert build["settings"]["tags"]["mode"] == "always" and build["theme"]["base"] == "nord"
    accepted = share.resolve(current, stranger(), accept_sensitive=True)["build"]
    assert accepted["settings"]["notes"]["allow_ai"] is True and accepted["settings"]["ai"]["daily_budget_usd"] == 50
    with pytest.raises(share.ShareError) as refused:
        share.resolve(current, stranger(theme={"css": "@import 'x';"}), accept_sensitive=True)
    assert refused.value.problems[0].path == "theme.css"


def test_install_is_one_step_and_rollback_restores_the_previous_build(root, conn):
    before_text = (root / "config" / "build.json").read_text()
    current = project.sync(conn, root).build
    plan = share.resolve(current, stranger(), accept_sensitive=False)
    project.replace(conn, root, document.dump(plan["build"]), history=("install", "file"))
    assert read_build(root)["theme"]["base"] == "nord" and tags.settings(conn)["mode"] == "always"
    (entry,) = store.list_history(root)
    assert entry["reason"] == "install" and entry["source"] == "file"
    assert store.history_text(root, entry["id"]) == before_text
    project.replace(conn, root, store.history_text(root, entry["id"]), history=("rollback", entry["id"]))
    assert (root / "config" / "build.json").read_text() == before_text
    assert tags.settings(conn)["mode"] == "suggest"
    assert len(store.list_history(root)) == 2, "the rolled-back build stays available too"


def test_a_failed_install_leaves_the_file_and_the_rows_as_they_were(root, conn, monkeypatch):
    before_text = (root / "config" / "build.json").read_text()
    current = project.sync(conn, root).build
    plan = share.resolve(current, stranger(), accept_sensitive=False)

    def broken(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(store, "write_text", broken)
    with pytest.raises(OSError):
        project.replace(conn, root, document.dump(plan["build"]), history=("install", "file"))
    assert (root / "config" / "build.json").read_text() == before_text
    assert tags.settings(conn)["mode"] == "suggest"


def test_history_keeps_only_the_newest_builds(root):
    for index in range(store.HISTORY_KEEP + 3):
        store.push_history(root, json.dumps({"schema": 1, "name": f"n{index}"}), "install")
    entries = store.list_history(root)
    assert len(entries) == store.HISTORY_KEEP and entries[0]["name"] == f"n{store.HISTORY_KEEP + 2}"
    with pytest.raises(store.BuildStoreError):
        store.history_text(root, "../../config/build")


def public(host, *_args, **_kwargs):
    return [(2, 1, 6, "", ("140.82.112.3" if host != "evil.internal" else "127.0.0.1", 443))]


def transport(routes: dict[str, httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: routes[str(request.url)])


def test_fetching_a_build_link_follows_only_https_and_caps_its_size():
    good = '{"schema": 1, "name": "gist"}'
    routes = {
        "https://gist.githubusercontent.com/u/1/raw/kitty-build.json": httpx.Response(200, text=good),
        "https://github.com/u/r/raw/main/kitty-build.json": httpx.Response(
            302, headers={"location": "https://raw.githubusercontent.com/u/r/main/kitty-build.json"}),
        "https://raw.githubusercontent.com/u/r/main/kitty-build.json": httpx.Response(200, text=good),
        "https://gist.githubusercontent.com/u/2/raw": httpx.Response(301, headers={"location": "http://plain.example/x"}),
        "https://gist.githubusercontent.com/u/3/raw": httpx.Response(200, content=b"x" * (document.MAX_BYTES + 1)),
        "https://gist.githubusercontent.com/u/4/raw": httpx.Response(404),
        **{f"https://loop.example/{n}": httpx.Response(302, headers={"location": f"https://loop.example/{n + 1}"})
           for n in range(6)},
    }
    fetch = lambda url: share.fetch(url, transport=transport(routes), resolver=public)  # noqa: E731
    assert fetch("https://gist.githubusercontent.com/u/1/raw/kitty-build.json") == good
    assert fetch("https://github.com/u/r/raw/main/kitty-build.json") == good
    for url, reason in (("http://gist.githubusercontent.com/u/1/raw", "https"),
                        ("https://gist.githubusercontent.com/u/2/raw", "non-https"),
                        ("https://gist.githubusercontent.com/u/3/raw", "limited"),
                        ("https://gist.githubusercontent.com/u/4/raw", "404"),
                        ("https://loop.example/0", "redirects more than"),
                        ("https://user:pw@gist.githubusercontent.com/x", "user name"),
                        ("https://evil.internal/build.json", "public https address"),
                        ("file:///etc/passwd", "https")):
        with pytest.raises(share.ShareError) as refused:
            fetch(url)
        assert reason in str(refused.value), (url, str(refused.value))


def test_a_new_workspace_gets_an_owner_only_build_with_every_module(tmp_path):
    text = store.ensure(tmp_path)
    assert set(json.loads(text)) == {"schema", "kitty_version"}
    assert document.require(text)["modules"] == ["applyai", "workspace", "terminal"]
    assert stat.S_IMODE((tmp_path / "config" / "build.json").stat().st_mode) == 0o600
