"""Stage 6 Gmail: rules classify mail, matching is conservative, and a check
only ever proposes. No network: a fake session stands in for the Gmail API."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from textual.widgets import DataTable

from applyai.core import applications as applications_core
from applyai.core import jobs as jobs_core
from applyai.core.gmail.check import check_gmail
from applyai.core.gmail.classify import classify
from applyai.core.gmail.fetch import GmailMessage, parse_message
from applyai.core.gmail.match import match_application, open_applications
from applyai.core.gmail.proposals import pending_proposals

FIXTURES = Path(__file__).parent / "fixtures" / "gmail"


# ---------------------------------------------------------------- helpers


def _email(name: str) -> tuple[str, str, str]:
    """(From header, subject, body) from a fixture file."""
    head, _, body = (FIXTURES / name).read_text(encoding="utf-8").partition("\n\n")
    headers = dict(line.split(": ", 1) for line in head.splitlines())
    return headers["From"], headers["Subject"], body


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _api_message(message_id: str, name: str, *, millis: int = 1_790_000_000_000) -> dict:
    """A users.messages.get (format=full) payload built from a fixture."""
    sender, subject, body = _email(name)
    return {
        "id": message_id,
        "internalDate": str(millis),
        "snippet": body[:80],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(body)}},
                {"mimeType": "text/html", "body": {"data": _b64(f"<p>{body}</p>")}},
            ],
        },
    }


def _message(name: str, message_id: str = "m1") -> GmailMessage:
    return parse_message(_api_message(message_id, name))


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(payload or "")

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """Answers messages.list and messages.get from a dict; counts the gets."""

    def __init__(self, messages: dict[str, dict], *, fail_with: int | None = None) -> None:
        self.messages = messages
        self.fail_with = fail_with
        self.gets: list[str] = []

    def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> FakeResponse:
        if self.fail_with:
            return FakeResponse(self.fail_with)
        if url.endswith("/messages"):
            # Gmail lists newest first.
            ids = sorted(self.messages, key=lambda i: int(self.messages[i]["internalDate"]), reverse=True)
            return FakeResponse(200, {"messages": [{"id": i} for i in ids]})
        message_id = url.rsplit("/", 1)[1]
        self.gets.append(message_id)
        return FakeResponse(200, self.messages[message_id])


def _apply(workspace, company: str, title: str, status: str = "applied") -> int:
    """Record an application at `company` and return its id."""
    track = workspace.config.track_names[0]
    job, _ = jobs_core.upsert_job(
        workspace.conn, company=company, title=title,
        url=f"https://example.invalid/{company}/{title}".replace(" ", "-"), tracks=[track],
    )
    application = applications_core.record_application(workspace.conn, job.id, track=track)
    if status != "applied":
        applications_core.set_status(workspace.conn, application.id, status)
    return application.id


def _status(workspace, application_id: int) -> str:
    return applications_core.get_application(workspace.conn, application_id).status


# --------------------------------------------------------------- classify


@pytest.mark.parametrize(("name", "expected"), [
    ("rejection.txt", "rejected"),       # mentions the interview, still a rejection
    ("oa.txt", "oa"),                    # "thank you for applying" plus a real OA invite
    ("interview.txt", "interview"),      # mentions the assessment, still an interview
    ("offer.txt", "offer"),
    ("confirmation.txt", None),          # "reach out to schedule an interview" is hedged
])
def test_classifier_reads_realistic_mail(name, expected):
    _, subject, body = _email(name)
    verdict = classify(subject, body)
    assert (verdict[0] if verdict else None) == expected
    if verdict:
        assert verdict[1] in f"{subject}\n{body}".lower()      # the evidence is a real quote


def test_hedge_survives_line_wrapping():
    body = "If you are selected, our recruiting team will reach out to\nschedule an interview."
    assert classify("Application received", body) is None
    assert classify("Next steps", "We would like to schedule an interview with you.") == (
        "interview", "schedule an interview")


def test_parse_message_falls_back_to_html_body():
    payload = {
        "id": "h1", "internalDate": "1790000000000",
        "payload": {
            "mimeType": "text/html",
            "headers": [{"name": "From", "value": "Acme <jobs@acme.example.invalid>"},
                        {"name": "Subject", "value": "Update"}],
            "body": {"data": _b64("<div>We regret to inform you</div><br>Acme")},
        },
    }
    message = parse_message(payload)
    assert "We regret to inform you" in message.body
    assert message.sender == "Acme" and message.sender_domain == "acme.example.invalid"
    assert message.received_at.startswith("2026-")


# ------------------------------------------------------------------ match


def test_match_picks_the_company_in_sender_or_subject(workspace):
    jane = _apply(workspace, "Example Trading", "Software Engineer Intern")
    _apply(workspace, "Example Capital", "Quant Research Intern")
    citadel_securities = _apply(workspace, "Example Capital Securities", "Software Engineering Intern")
    apps = open_applications(workspace.conn)

    assert match_application(_message("rejection.txt"), apps).application.id == jane
    # "Example Capital Securities" contains "Example Capital": the longer, more specific name wins.
    assert match_application(_message("oa.txt"), apps).application.id == citadel_securities


def test_match_skips_unknown_and_ambiguous_companies(workspace):
    _apply(workspace, "Example Trading", "Software Engineer Intern")
    _apply(workspace, "Example Logic", "FPGA Engineer Intern")
    apps = open_applications(workspace.conn)

    unknown = match_application(_message("offer.txt"), apps)
    assert unknown.application is None and unknown.reason == "no_company"

    both = GmailMessage("x", "Careers", "jobs@example.invalid", "Update on your application",
                        "2026-09-30T00:00:00Z", "Example Trading and Example Logic are both at the fair.")
    ambiguous = match_application(both, apps)
    assert ambiguous.application is None and ambiguous.reason == "ambiguous"


def test_match_uses_title_words_when_one_company_has_several(workspace):
    _apply(workspace, "Example Logic", "Software Engineer Intern")
    fpga = _apply(workspace, "Example Logic", "FPGA Engineer Intern")
    apps = open_applications(workspace.conn)
    assert match_application(_message("interview.txt"), apps).application.id == fpga

    vague = GmailMessage("y", "Example Logic", "recruiting@logic.example.invalid", "Interview invitation",
                         "2026-09-30T00:00:00Z", "We would like to invite you to interview.")
    result = match_application(vague, apps)
    assert result.application is None and result.reason == "several_applications"


def test_closed_applications_are_not_matched(workspace):
    _apply(workspace, "Example Trading", "Software Engineer Intern", status="rejected")
    result = match_application(_message("rejection.txt"), open_applications(workspace.conn))
    assert result.application is None


# ------------------------------------------------------------------ check


def test_check_proposes_without_moving_until_confirmed(workspace):
    jane = _apply(workspace, "Example Trading", "Software Engineer Intern")
    optiver = _apply(workspace, "Example Logic", "FPGA Engineer Intern", status="oa")
    session = FakeSession({
        "r1": _api_message("r1", "rejection.txt", millis=1_790_000_300_000),
        "i1": _api_message("i1", "interview.txt", millis=1_790_000_200_000),
        "c1": _api_message("c1", "confirmation.txt", millis=1_790_000_100_000),
    })

    result = check_gmail(workspace.conn, workspace.settings, session=session, delay=0)

    assert result.status == "ok" and result.error is None
    assert (result.fetched, result.classified, result.matched, result.proposed) == (3, 2, 2, 2)
    assert result.skipped_by_reason == {"not_status_mail": 1}
    # Proposals change nothing yet.
    assert _status(workspace, jane) == "applied" and _status(workspace, optiver) == "oa"
    pending = {p.application_id: p for p in pending_proposals(workspace.conn)}
    assert pending[jane].to_status == "rejected" and pending[optiver].to_status == "interview"
    assert pending[jane].occurred_at == "2026-09-21T14:18:20Z"       # the message's own date

    applications_core.confirm_proposal(workspace.conn, pending[jane].event_id)
    applications_core.reject_proposal(workspace.conn, pending[optiver].event_id)
    assert _status(workspace, jane) == "rejected" and _status(workspace, optiver) == "oa"

    # A second check never re-fetches or re-proposes the same messages,
    # even the declined one.
    session.gets.clear()
    again = check_gmail(workspace.conn, workspace.settings, session=session, delay=0)
    assert again.already_seen == 3 and again.fetched == 0 and again.proposed == 0
    assert session.gets == []
    assert pending_proposals(workspace.conn) == []

    run = workspace.conn.execute("SELECT kind, error FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    assert run["kind"] == "mail" and run["error"] is None


def test_check_skips_a_proposal_that_matches_the_current_status(workspace):
    optiver = _apply(workspace, "Example Logic", "FPGA Engineer Intern", status="interview")
    session = FakeSession({"i1": _api_message("i1", "interview.txt")})

    result = check_gmail(workspace.conn, workspace.settings, session=session, delay=0)

    assert result.proposed == 0 and result.skipped_by_reason == {"same_status": 1}
    assert pending_proposals(workspace.conn) == []
    assert _status(workspace, optiver) == "interview"


@pytest.mark.parametrize(("http_status", "kind"), [(401, "auth"), (429, "rate_limited")])
def test_check_reports_auth_and_rate_limit_errors(workspace, http_status, kind):
    _apply(workspace, "Example Trading", "Software Engineer Intern")
    session = FakeSession({"r1": _api_message("r1", "rejection.txt")}, fail_with=http_status)

    result = check_gmail(workspace.conn, workspace.settings, session=session, delay=0)

    assert result.status == kind and str(http_status) in result.error
    assert result.proposed == 0
    run = workspace.conn.execute("SELECT error FROM runs WHERE kind = 'mail'").fetchone()
    assert str(http_status) in run["error"]
    # Nothing was marked as seen, so the next check reads the message.
    assert workspace.conn.execute("SELECT COUNT(*) FROM gmail_messages").fetchone()[0] == 0


# -------------------------------------------------------------------- TUI


async def test_pipeline_proposals_modal_confirms_one(workspace):
    from applyai.tui.app import ApplyAIApp
    from applyai.tui.navigation import PLACES
    from applyai.tui.screens.pipeline import PipelineScreen
    from applyai.tui.screens.proposals import ProposalsModal

    jane = _apply(workspace, "Example Trading", "Software Engineer Intern")
    applications_core.set_status(
        workspace.conn, jane, "rejected", source="gmail", state="proposed",
        evidence='gmail r1: Your application | "not to proceed with your"',
    )
    key = {place.screen: place.key for place in PLACES}["pipeline"]

    app = ApplyAIApp(workspace=workspace)
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.press(key)
        await pilot.pause()
        assert isinstance(app.screen, PipelineScreen)
        await pilot.press("v")
        await pilot.pause()
        assert isinstance(app.screen, ProposalsModal)
        assert app.screen.query_one("#proposals-table", DataTable).row_count == 1

        await pilot.press("y")
        await pilot.pause()
        # The last proposal decided closes the list and repaints the board.
        assert isinstance(app.screen, PipelineScreen)
        assert app.screen.query_one("#cards-rejected", DataTable).row_count == 1
        assert _status(workspace, jane) == "rejected"
        assert pending_proposals(workspace.conn) == []
