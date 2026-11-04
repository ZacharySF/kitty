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


def test_each_command_is_an_agent_session_unless_one_is_named_and_one_session_undoes(home, monkeypatch):
    made = data("notes", "new", "Agent page", "first")
    appended = data("notes", "append", made["page_id"], "second")
    listed = data("notes", "sessions")["sessions"]
    assert len(listed) == 2 and {item["actor"]["name"] for item in listed} == {"kitty notes"}
    monkeypatch.setenv("KITTY_AGENT_SESSION", "nightly-run-1")
    monkeypatch.setenv("KITTY_AGENT_NAME", "nightly")
    data("notes", "append", made["page_id"], "third")
    data("notes", "append", made["page_id"], "fourth")
    newest = data("notes", "sessions")["sessions"][0]
    assert newest["session"] == "nightly-run-1" and newest["actor"]["name"] == "nightly" and newest["transactions"] == 2

    plan = run("notes", "undo-session", "nightly-run-1", "--dry-run")
    assert plan.exit_code == 0 and "Would revert 2 block(s) from 2 transaction(s)" in plain(plan)
    done = data("notes", "undo-session", "nightly-run-1")
    assert done["applied"] and done["reverted_count"] == 2
    texts = [block["text"] for block in data("notes", "show", made["page_id"])["blocks"]]
    assert texts == ["first", "second"]
    cite = appended["blocks"][0]["cite"]
    assert data("notes", "citations", cite)["citations"][0]["status"] == "unchanged"
    monkeypatch.setenv("KITTY_AGENT_SESSION", "not ok!")
    refused = run("notes", "ls")
    assert refused.exit_code == 1 and "KITTY_AGENT_SESSION" in plain(refused)
    monkeypatch.delenv("KITTY_AGENT_SESSION")
    missing = run("notes", "undo-session", "no-such-session")
    assert missing.exit_code == 1 and "No kept history" in plain(missing)
