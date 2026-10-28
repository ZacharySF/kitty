"""Scoped verification mail: synthetic headers and isolated browser-only mailboxes.

No tests connect to Gmail or use a runtime profile. Browser fixtures exercise
real DOM navigation/metadata extraction; only the permitted origin is replaced
with a loopback server, leaving all realm and route guards active.
"""

from __future__ import annotations

import html
import json
import threading
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from kitty.core.apply import verification_mail
from kitty.core.apply.bidi import open_tab
from kitty.core.apply.browser import headless_firefox
from kitty.core.apply.verification_mail import (
    MailRequest,
    VerificationMailError,
    VerificationMessage,
    _account_url,
    _original_headers,
    _qualify,
    _query,
    _validate_request,
    read_verification_mail,
)
from kitty.settings import Settings, find_firefox

SENDER = "verify@sender.example.invalid"
RECIPIENT = "candidate+application@example.invalid"


def request_at() -> MailRequest:
    now = datetime.now(UTC).replace(microsecond=0)
    return MailRequest(RECIPIENT, SENDER, now - timedelta(minutes=2), now + timedelta(minutes=8))


def headers_for(request: MailRequest, **changes) -> dict:
    result = {
        "from": f"Synthetic recruitment <{SENDER}>",
        "to": RECIPIENT,
        "received": format_datetime(request.requested_at + timedelta(seconds=30)),
    }
    result.update(changes)
    return result


def test_request_and_message_reprs_hide_sensitive_fields():
    request = request_at()
    message = VerificationMessage("secret-id", SENDER, (RECIPIENT,), request.requested_at,
                                  "secret-subject", "synthetic-code-123456",
                                  ("https://verify.example.invalid/?token=secret",))
    for sensitive in (SENDER, RECIPIENT, "secret-id", "secret-subject", "123456", "token="):
        assert sensitive not in repr(request)
        assert sensitive not in repr(message)


def test_search_is_exact_and_bounded():
    request = request_at()
    _validate_request(request)
    query = _query(request)
    assert f'from:"{SENDER}"' in query
    assert f'to:"{RECIPIENT}"' in query
    assert f"after:{int(request.requested_at.timestamp()) - 1}" in query
    assert f"before:{int(request.expires_at.timestamp()) + 1}" in query
    assert " OR " not in query


@pytest.mark.parametrize("sender", [
    'x@example.invalid OR newer_than:2d', 'x\"@example.invalid', "x@example.invalid\nBcc:y@other.invalid",
])
def test_query_syntax_cannot_be_injected(sender):
    request = request_at()
    malicious = MailRequest(RECIPIENT, sender, request.requested_at, request.expires_at)
    with pytest.raises(VerificationMailError) as error:
        _validate_request(malicious)
    assert error.value.kind == "request"
    assert sender not in str(error.value)


@pytest.mark.parametrize("naive", [False, True])
def test_invalid_request_stops_before_any_mailbox_operation(naive):
    now = datetime.now(UTC)
    if naive:
        request = MailRequest(RECIPIENT, SENDER, now.replace(tzinfo=None), now + timedelta(minutes=5))
    else:
        request = MailRequest(RECIPIENT, SENDER, now - timedelta(minutes=5), now - timedelta(seconds=1))

    class UntouchedBrowser:
        def command(self, *args, **kwargs):
            raise AssertionError("An invalid request accessed the browser")

    with pytest.raises(VerificationMailError) as error:
        read_verification_mail(UntouchedBrowser(), request)
    assert error.value.kind == "request"


@pytest.mark.parametrize("url", [
    "https://mail.google.com.evil.invalid/mail/u/0/",
    "http://mail.google.com/mail/u/0/",
    "https://mail.google.com@evil.invalid/mail/u/0/",
    "https://mail.google.com/mail/u/0/elsewhere",
    "https://mail.google.com/mail/u/../",
    "https://accounts.google.com/mail/u/0/",
])
def test_only_exact_gmail_account_origin_and_path_qualify(url):
    assert _account_url(url) is None


def test_individual_delivery_headers_qualify_exact_alias_and_explicit_zone():
    request = request_at()
    headers = headers_for(request, to="other@example.invalid", **{"delivered-to": RECIPIENT})
    qualified = _qualify(headers, request)
    assert qualified is not None
    assert qualified[0] == SENDER
    assert qualified[1] == ("other@example.invalid", RECIPIENT)
    assert qualified[2] == request.requested_at + timedelta(seconds=30)


@pytest.mark.parametrize("changes", [
    {"from": "impostor@sender.example.invalid"},
    {"from": f"{SENDER}, impostor@example.invalid"},
    {"to": "candidate@example.invalid"},
    {"to": "other@example.invalid", "cc": RECIPIENT},
])
def test_search_match_never_substitutes_for_exact_message_headers(changes):
    request = request_at()
    assert _qualify(headers_for(request, **changes), request) is None


def test_old_message_in_matching_thread_does_not_qualify():
    request = request_at()
    old = format_datetime(request.requested_at - timedelta(seconds=1))
    assert _qualify(headers_for(request, received=old), request) is None


def test_original_uses_first_receiving_timestamp_not_forged_date_or_older_hop():
    request = request_at()
    old = format_datetime(request.requested_at - timedelta(hours=2))
    fresh = format_datetime(request.requested_at + timedelta(seconds=30))
    headers = _original_headers(
        f"Delivered-To: {RECIPIENT}\r\n"
        f"Received: by synthetic.mail.invalid with SMTP id synthetic;\r\n {old}\r\n"
        f"Received: by older.hop.invalid; {fresh}\r\n"
        f"From: {SENDER}\r\nTo: {RECIPIENT}\r\nDate: {fresh}\r\n\r\n"
    )
    assert headers["received"] == old
    assert _qualify(headers, request) is None


def test_original_rejects_duplicate_sender_header_without_leaking_source():
    source = f"From: {SENDER}\nFrom: wrong@example.invalid\nReceived: invalid\n\n"
    with pytest.raises(VerificationMailError) as error:
        _original_headers(source)
    assert error.value.kind == "unsupported"
    assert SENDER not in str(error.value)


@pytest.mark.parametrize("stamp", ["Thu, 22 Oct 2026 12:30:00", "yesterday", ""])
def test_no_timezone_or_unreliable_timestamp_is_safe_unsupported(stamp):
    with pytest.raises(VerificationMailError) as error:
        _qualify(headers_for(request_at(), received=stamp), request_at())
    assert error.value.kind == "unsupported"
    assert stamp not in str(error.value) or not stamp


def _mailbox_html(messages: list[dict]) -> str:
    entries = []
    for index, message in enumerate(messages):
        entries.append(
            f'<section class="adn" data-message-id="#msg-f:{1000 + index}" '
            f'data-legacy-message-id="{1000 + index:x}">'
            f'<div class="gE"><span class="gD" email="{html.escape(message.get("sender", SENDER))}">'
            f'Sender</span><span class="g2" email="{RECIPIENT}">to me</span>'
            '<button aria-label="More message options">More</button></div>'
            f'<div class="a3s">{message["body"]}</div></section>'
        )
    # The fixture deliberately returns this thread irrespective of the search
    # filter, just as a Gmail conversation can include nonmatching messages.
    thread = json.dumps("".join(entries)).replace("</", "<\\/")
    return """<!doctype html><title>Synthetic mailbox</title>
<form role="search"><input name="q" aria-label="Search mail"></form><nav role="navigation"></nav>
<main role="main"></main>
<button id="send">Send</button><button id="delete">Delete</button>
<button id="resend">Resend</button>
<ul role="menu" aria-label="More message options menu" hidden>
  <li role="menuitem" id="reply">Reply</li>
  <li role="menuitem" id="original">Show original</li>
  <li role="menuitem" id="menu-delete">Delete</li>
</ul>
<script>
window.mutations = 0;
for (const button of document.querySelectorAll('#send, #delete, #resend, #reply, #menu-delete'))
  button.onclick = () => window.mutations++;
const thread = """ + thread + """;
const menu = document.querySelector('ul[role=menu]');
document.querySelector('#original').addEventListener('click', () => {
  const id = menu.dataset.message;
  menu.hidden = true;
  window.open('/mail/u/0/?ik=synthetic&view=om&permmsgid=' + encodeURIComponent(id), '_blank');
});
function render() {
  // Gmail canonicalizes encoded spaces in search routes to '+'.
  if (location.hash.includes('%20'))
    history.replaceState(null, '', location.hash.replaceAll('%20', '+'));
  const hash = location.hash;
  const main = document.querySelector('main');
  if (hash.startsWith('#search/')) {
    const parts = hash.slice('#search/'.length).split('/');
    document.querySelector('input[name=q]').value = decodeURIComponent(parts[0].replaceAll('+', ' ')) + ' ';
    main.innerHTML = parts.length > 1 ? thread :
      '<table><tr style="display:none" class="zA"><td>' +
      '<span data-legacy-thread-id="deadbeef">Unrelated retained inbox row</span></td></tr>' +
      '<tr class="zA"><td><div class="xS" role="link">' +
      '<span data-legacy-thread-id="abc123">Candidate match</span></div></td></tr></table>';
  } else main.innerHTML = '<p>Unrelated inbox contents must never be extracted.</p>';
  const link = main.querySelector('div.xS[role="link"]');
  if (link) link.addEventListener('click', () => { location.hash = hash + '/abc123'; });
  for (const button of main.querySelectorAll('button[aria-label="More message options"]'))
    button.addEventListener('click', () => {
      menu.dataset.message = button.closest('.adn').getAttribute('data-message-id').slice(1);
      menu.hidden = false;
    });
}
addEventListener('hashchange', render);
render();
</script>"""


@pytest.fixture(scope="module")
def firefox(tmp_path_factory):
    if find_firefox() is None:
        pytest.skip("firefox is not on PATH")
    settings = Settings.load(root=tmp_path_factory.mktemp("verification-mail-firefox"))
    with headless_firefox(settings) as bidi:
        yield bidi


@pytest.fixture
def mailbox(firefox, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        document = ""
        sources: dict[str, str] = {}
        requests: list[str] = []

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.requests.append(self.path)
            params = parse_qs(urlsplit(self.path).query)
            if params.get("view") == ["om"]:
                document = self.sources.get(params.get("permmsgid", [""])[0], "<p>Not found</p>")
            else:
                document = self.document
            body = document.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.requests.append("POST " + self.path)
            self.send_response(405)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(verification_mail, "_MAIL_ORIGIN", origin)
    tab = open_tab(firefox, background=True)
    app = open_tab(firefox)
    app.goto("data:text/html,<title>Synthetic application</title><input value='Unsaved draft'>")

    class Mailbox:
        page = tab
        application = app

        def show(self, messages):
            Handler.document = _mailbox_html(messages)
            Handler.sources = {}
            for index, message in enumerate(messages):
                fields = message["headers"]
                lines = [
                    f"{key}: {('by synthetic.mail.invalid with SMTP id synthetic; ' + value) if key == 'received' else value}"
                    for key, value in fields.items()
                ]
                source = "\r\n".join(lines) + "\r\n\r\nRaw MIME body must not cross BiDi before qualification."
                Handler.sources[f"msg-f:{1000 + index}"] = (
                    '<!doctype html><title>Synthetic original</title>'
                    '<pre id="raw_message_text" class="raw_message_text">'
                    + html.escape(source) + "</pre>"
                )
            tab.goto(origin + "/mail/u/0/#inbox")

        @property
        def requests(self):
            return Handler.requests

        @property
        def foreign_url(self):
            return origin.replace("127.0.0.1", "localhost") + "/mail/u/0/"

    try:
        yield Mailbox()
    finally:
        tab.close()
        app.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_browser_old_thread_entry_is_not_returned_with_fresh_match(mailbox):
    request = request_at()
    mailbox.show([
        {"headers": headers_for(request, received=format_datetime(
            request.requested_at - timedelta(hours=1))), "body": "Old verification 111111"},
        {"headers": headers_for(request, subject="Synthetic verification"),
         "body": 'Fresh verification 654321 <a href="https://verify.example.invalid/?token=synthetic">Verify</a>'
                 '<blockquote>Older verification 111111</blockquote>'},
    ])
    mailbox.application.activate()
    foreground = mailbox.application.main.call("() => document.visibilityState")
    contexts = {node["context"] for node in mailbox.page.bidi.command(
        "browsingContext.getTree", {},
    )["contexts"]}
    messages = read_verification_mail(mailbox.page.bidi, request)

    assert len(messages) == 1
    assert messages[0].id == "#msg-f:1001"
    assert messages[0].subject == "Synthetic verification"
    assert "654321" in messages[0].text
    assert "111111" not in messages[0].text
    assert "Raw MIME body" not in messages[0].text
    assert messages[0].links == ("https://verify.example.invalid/?token=synthetic",)
    assert mailbox.page.main.call("() => location.hash") == "#inbox"
    assert mailbox.application.main.call("() => document.visibilityState") == foreground == "visible"
    assert mailbox.application.main.call("() => document.querySelector('input').value") == "Unsaved draft"
    assert mailbox.page.main.call("() => window.mutations", page_realm=True) == 0
    assert not any(path.startswith("POST ") for path in mailbox.requests)
    assert {node["context"] for node in mailbox.page.bidi.command(
        "browsingContext.getTree", {},
    )["contexts"]} == contexts
    originals = [parse_qs(urlsplit(path).query) for path in mailbox.requests
                 if parse_qs(urlsplit(path).query).get("view") == ["om"]]
    assert [params["permmsgid"] for params in originals] == [["msg-f:1000"], ["msg-f:1001"]]


def test_browser_wrong_recipient_in_matching_thread_is_not_returned(mailbox):
    request = request_at()
    mailbox.show([{"headers": headers_for(request, to="other@example.invalid"),
                   "body": "Not the approved recipient's verification 222222"}])
    assert read_verification_mail(mailbox.page.bidi, request) == []
    assert mailbox.page.main.call("() => location.hash") == "#inbox"
    assert mailbox.page.main.call("() => window.mutations", page_realm=True) == 0


def test_browser_narrow_layout_collapsed_query_still_reads_exact_message(mailbox):
    request = request_at()
    mailbox.show([{"headers": headers_for(request), "body": "Narrow verification 765432"}])
    mailbox.page.main.call("() => { document.querySelector('input[name=q]').style.cssText = 'width:0;padding:0;border:0'; }", page_realm=True)
    messages = read_verification_mail(mailbox.page.bidi, request)
    assert [message.id for message in messages] == ["#msg-f:1000"]
    assert "765432" in messages[0].text
    assert not any(path.startswith("POST ") for path in mailbox.requests)


def test_browser_origin_change_after_selection_stops_before_message_read(mailbox):
    request = request_at()
    mailbox.show([{"headers": headers_for(request), "body": "Cross-origin verification 333333"}])
    moved = False

    def checkpoint():
        nonlocal moved
        if not moved and mailbox.page.main.call("() => location.hash").startswith("#search/"):
            moved = True
            mailbox.page.goto(mailbox.foreign_url)

    with pytest.raises(VerificationMailError) as error:
        read_verification_mail(mailbox.page.bidi, request, checkpoint=checkpoint)
    assert moved
    assert error.value.kind == "origin"
    assert "333333" not in str(error.value)
    assert mailbox.page.main.call("() => location.hostname") == "localhost"
    assert mailbox.page.main.call("() => location.hash") == ""


def test_browser_closed_mail_tab_reopens_profile_session_and_reads_matching_message(mailbox):
    request = request_at()
    mailbox.show([{"headers": headers_for(request), "body": "Verification code: 765432"}])
    bidi = mailbox.page.bidi
    mailbox.page.goto("about:blank")
    before = {node["context"] for node in bidi.command("browsingContext.getTree", {})["contexts"]}
    try:
        messages = read_verification_mail(bidi, request)
        assert [message.text for message in messages] == ["Verification code: 765432"]
        assert mailbox.application.main.call("() => document.querySelector('input').value") == "Unsaved draft"
    finally:
        for node in bidi.command("browsingContext.getTree", {})["contexts"]:
            if node["context"] not in before:
                verification_mail.Tab(bidi, node["context"]).close()


def test_browser_two_existing_account_surfaces_are_ambiguous(mailbox):
    request = request_at()
    mailbox.show([{"headers": headers_for(request), "body": "Synthetic verification"}])
    duplicate = open_tab(mailbox.page.bidi, background=True)
    try:
        duplicate.goto(mailbox.page.main.call("() => location.href"))
        with pytest.raises(VerificationMailError) as error:
            read_verification_mail(mailbox.page.bidi, request)
        assert error.value.kind == "ambiguous"
        assert mailbox.page.main.call("() => location.hash") == "#inbox"
        assert duplicate.main.call("() => location.hash") == "#inbox"
    finally:
        duplicate.close()


def test_browser_unsupported_delivery_metadata_is_not_a_fresh_message(mailbox):
    request = request_at()
    mailbox.show([{"headers": headers_for(request, received="Thu, 22 Oct 2026 12:30:00"),
                   "body": "Unproven delivery 444444"}])
    with pytest.raises(VerificationMailError) as error:
        read_verification_mail(mailbox.page.bidi, request)
    assert error.value.kind == "unsupported"
    assert "444444" not in str(error.value)
    assert mailbox.page.main.call("() => location.hash") == "#inbox"


def test_browser_checkpoint_stop_restores_owned_route_without_consuming_mail(mailbox):
    request = request_at()
    mailbox.show([{"headers": headers_for(request), "body": "Synthetic verification"}])

    class Stopped(Exception):
        pass

    def checkpoint():
        if mailbox.page.main.call("() => location.hash").startswith("#search/"):
            raise Stopped

    with pytest.raises(Stopped):
        read_verification_mail(mailbox.page.bidi, request, checkpoint=checkpoint)
    assert mailbox.page.main.call("() => location.hash") == "#inbox"
    assert mailbox.page.main.call("() => window.mutations", page_realm=True) == 0


def test_browser_header_operations_have_a_hard_message_budget(mailbox):
    request = request_at()
    mailbox.show([
        {"headers": headers_for(request, to="other@example.invalid"), "body": "Unrelated candidate"}
        for _ in range(6)
    ])
    with pytest.raises(VerificationMailError) as error:
        read_verification_mail(mailbox.page.bidi, request)
    assert error.value.kind == "unsupported"
    originals = [path for path in mailbox.requests
                 if parse_qs(urlsplit(path).query).get("view") == ["om"]]
    assert len(originals) == 5
    assert mailbox.page.main.call("() => location.hash") == "#inbox"
    assert mailbox.application.main.call("() => document.visibilityState") == "visible"
