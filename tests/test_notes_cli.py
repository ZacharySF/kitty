"""`kitty notes`: ls, show, search, new, append, inbox and quick over kitty.core.block_api.

Synthetic temporary workspaces only: KITTY_HOME points at tmp_path.
"""
from __future__ import annotations

import json
import re

import pytest
from typer.testing import CliRunner

from kitty import cli
from kitty.modules import save_modules


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KITTY_HOME", str(tmp_path))
    monkeypatch.delenv("KITTY_DB", raising=False)
    (tmp_path / "config").mkdir()
    save_modules(tmp_path / "config", ["workspace"])
    return tmp_path


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(cli.app, list(args), input=input)


def data(*args: str, input: str | None = None) -> dict:
    result = run(*args, "--json", input=input)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def plain(result) -> str:
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", result.output).split())


def test_new_append_show_search_and_ls(home):
    made = data("notes", "new", "Field notes", "first observation", "--notebook", "Lab")
    assert made["revision"] == 1 and made["blocks"][0]["cite"].endswith(f"@{made['seq']}))")
    added = data("notes", "append", str(made["number"]), "check the samples", "--type", "to_do", "--revision", "1")
    assert added["revision"] == 2 and added["seq"] > made["seq"]
    from_stdin = data("notes", "append", made["page_id"], "-", input="piped text\n")
    page = data("notes", "show", made["page_id"])
    assert [(block["type"], block["text"]) for block in page["blocks"]] == [
        ("text", "first observation"), ("to_do", "check the samples"), ("text", "piped text")]
    assert page["blocks"][2]["id"] == from_stdin["blocks"][0]["id"]

    shown = plain(run("notes", "show", str(made["number"])))
    assert "Field notes" in shown and "[ ] check the samples" in shown and "revision 3" in shown
    hits = data("notes", "search", "samples")["hits"]
    assert [hit["block_id"] for hit in hits] == [added["blocks"][0]["id"]]
    assert "Field notes" in plain(run("notes", "search", "samples"))
    assert [page["title"] for page in data("notes", "ls", "field")["pages"]] == ["Field notes"]
    assert "Field notes" in plain(run("notes", "ls"))


def test_inbox_quick_notes_and_refusals(home):
    inbox = data("notes", "inbox", "an idea", "another")
    assert len(inbox["blocks"]) == 2
    quick = data("notes", "quick", "Buy reagents")
    assert quick["number"] == 1
    assert {page["type"] for page in data("notes", "ls")["pages"]} == {"page", "quick_note"}

    assert data("notes", "inbox", "later")["revision"] == 2
    stale = run("notes", "append", str(inbox["number"]), "late", "--revision", "1")
    assert stale.exit_code == 1 and "changed" in plain(stale)
    missing = run("notes", "show", "99")
    assert missing.exit_code == 1 and "Page not found" in plain(missing)
    wrong = run("notes", "append", str(inbox["number"]), "x", "--type", "canvas")
    assert wrong.exit_code == 1 and "Block type" in plain(wrong)


def test_notes_belong_to_the_workspace_module(home):
    save_modules(home / "config", ["applyai"])
    result = run("notes", "ls")
    assert result.exit_code == 1 and "workspace module" in plain(result)
