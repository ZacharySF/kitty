"""Local candidate inspection and explicit owner decisions; no automatic promotion."""

from __future__ import annotations

import json
import webbrowser
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import RichLog, Static

from kitty.core import resume_versions
from kitty.db import connect
from kitty.tui.screens.modals import PickModal


def review_text(version: dict) -> str:
    """Unabridged plain-text audit, shared by CLI and the scrolling review surface."""
    lines = [
        f"Version: {version['id']}   track: {version['track']}   status: {version['status']}",
        f"Created: {version.get('created_at', '')}",
        f"Source: {version.get('source_path', '')}",
        f"PDF: {version.get('pdf_path', '')}",
        f"Manifest: {version.get('manifest_path', '')}",
        "Local processing only: no upload, no paid AI calls ($0).",
        "Frequency/coverage are measurements, not an ATS score or a promise of interviews.",
        "Missing skills are review items, not permission to invent facts.",
        "Promotion changes only the default for future unchosen jobs; existing choices stay fixed.",
    ]
    lines.extend(("", "EXACT UNIFIED DIFF", version.get("diff") or "(No changes.)"))
    for title, key in (
        ("VALIDATION", "validation"),
        ("EXACT PATCHES / FACT AND JOB-DESCRIPTION EVIDENCE", "patches"),
        ("MEASUREMENTS", "metrics"),
        ("WARNINGS", "warnings"),
        ("FACTUAL REVIEW ITEMS (not inserted)", "review_items"),
        ("ORIGINAL FACT ANCHORS", "facts"),
        ("COHORT / SOURCE COVERAGE", "cohort"),
    ):
        lines.extend(("", title, json.dumps(version.get(key, []), ensure_ascii=False, indent=2)))
    for key in ("history", "is_default", "current_default"):
        if key in version:
            lines.extend(("", key.upper(), json.dumps(version[key], ensure_ascii=False, indent=2)))
    return "\n".join(lines)


def open_pdf(version: dict) -> None:
    """Open only the local PDF, never the candidate HTML or a remote address."""
    path = Path(version.get("pdf_path") or "").expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError(f"Candidate PDF is unavailable: {path}")
    if not webbrowser.open(path.as_uri()):
        raise RuntimeError(f"No PDF viewer opened. Open this local file manually: {path}")


class ResumeReviewModal(ModalScreen[bool]):
    """Full exact diff, provenance and validation, before an explicit decision."""

    DEFAULT_CSS = """
    #resume-review-modal { width: 95%; height: 95%; }
    #resume-review-log { height: 1fr; }
    """
    BINDINGS = [
        ("o", "open_pdf", "Open PDF"),
        ("p", "promote", "Approve / promote"),
        ("x", "reject", "Reject"),
        ("escape", "close", "Close"),
    ]

    def __init__(self, version: dict) -> None:
        super().__init__()
        self.version = version
        self.changed = False
        self.busy = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal", id="resume-review-modal"):
            yield Static("Resume candidate: exact edits, evidence and validation", classes="modal-title")
            yield RichLog(id="resume-review-log", wrap=True, markup=False, highlight=False)
            yield Static("o open local PDF   p approve/promote   x reject   esc close", classes="modal-hint")

    def on_mount(self) -> None:
        self._render_review()
        self.query_one(RichLog).focus()

    def _render_review(self) -> None:
        log = self.query_one(RichLog)
        log.clear()
        log.write(review_text(self.version), scroll_end=False)
        log.scroll_home(animate=False)

    def action_open_pdf(self) -> None:
        app = self.app

        def work() -> None:
            try:
                open_pdf(self.version)
            except Exception as exc:
                app.call_from_thread(app.notify, str(exc), severity="error", timeout=15)

        self.run_worker(work, thread=True, group="resume-preview", exit_on_error=False)

    def action_promote(self) -> None:
        if self.busy:
            return
        if self.version["status"] != "candidate":
            self.notify("Only an undecided candidate can be promoted.", severity="warning")
            return
        if not self.version.get("validation", {}).get("valid"):
            self.notify("Validation failed. Read the blocking errors; promotion is unavailable.", severity="error")
            return
        self.app.push_screen(
            PickModal(
                "Approve this exact candidate as the future default?",
                [("Cancel; keep the current default", "cancel"),
                 ("I reviewed the diff, evidence and PDF: promote", "promote")],
                hint="Existing jobs/applications and original files will not change. Esc cancels.",
            ),
            lambda choice: self._change("promote") if choice == "promote" else None,
        )

    def action_reject(self) -> None:
        if self.busy:
            return
        if self.version["status"] != "candidate":
            self.notify("Only an undecided candidate can be rejected.", severity="warning")
            return
        self.app.push_screen(
            PickModal("Reject this candidate? Audit artifacts are retained.",
                      [("Cancel", "cancel"), ("Reject candidate", "reject")]),
            lambda choice: self._change("reject") if choice == "reject" else None,
        )

    def _change(self, action: str) -> None:
        if self.busy:
            return
        self.busy = True
        workspace = self.app.workspace  # type: ignore[attr-defined]
        app = self.app
        version_id = self.version["id"]

        def work() -> None:
            try:
                conn = connect(workspace.settings.db_path)
                try:
                    if action == "promote":
                        resume_versions.promote(conn, workspace.config, workspace.settings, version_id)
                    else:
                        resume_versions.reject(conn, workspace.settings, version_id)
                    version = resume_versions.get_version(conn, workspace.settings, version_id)
                finally:
                    conn.close()
            except Exception as exc:
                app.call_from_thread(self._failed, str(exc))
            else:
                app.call_from_thread(self._decided, version)

        self.run_worker(work, thread=True, group="resume-decision", exit_on_error=False)

    def _failed(self, message: str) -> None:
        self.busy = False
        self.notify(message, severity="error", timeout=15)

    def _decided(self, version: dict) -> None:
        self.busy = False
        self.changed = True
        self.version = version
        self._render_review()
        self.notify(f"Candidate {version['status']}. Audit artifacts retained.")

    def action_close(self) -> None:
        if self.busy:
            self.notify("Saving your decision; wait before closing.", severity="warning")
            return
        self.dismiss(self.changed)
