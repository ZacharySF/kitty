"""Read a narrowly scoped verification message in an existing Firefox Gmail tab.

This is independent of the OAuth status-mail importer. No browser/session is
created, and neither mailbox credentials nor cookies are read. Search matches
are candidates only: each message's own delivery headers must qualify before
its body is read. Unsupported Gmail layouts fail closed.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.errors import MessageError
from email.parser import HeaderParser
from email.utils import getaddresses, parsedate_to_datetime
from urllib.parse import parse_qs, quote, urlsplit

from kitty.core.apply.bidi import Bidi, BidiError, Frame, Tab

_MAIL_ORIGIN = "https://mail.google.com"
_ACCOUNT_PATH = re.compile(r"/mail/u/[0-9]+/")
_MAX_THREADS = 3
_MAX_MESSAGES = 8
_MAX_MESSAGE_READS = 5
_MAX_TEXT = 20_000
_WAIT_SECONDS = 5.0


class VerificationMailError(RuntimeError):
    """Safe mailbox failure; ``kind`` contains no addresses, bodies or URLs."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__({
            "request": "The verification mail request is invalid or expired.",
            "unavailable": "No supported existing Gmail tab is available.",
            "ambiguous": "The existing Gmail account surface is ambiguous.",
            "logged_out": "The existing Gmail tab is not signed in.",
            "origin": "The Gmail tab changed its origin or account route.",
            "unsupported": "Gmail does not expose supported verification delivery metadata.",
            "transport": "The scoped Gmail browser operation failed.",
        }.get(kind, "The scoped verification mail operation stopped."))


@dataclass(frozen=True)
class MailRequest:
    recipient: str = field(repr=False)
    sender: str = field(repr=False)
    requested_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class VerificationMessage:
    id: str = field(repr=False)
    sender: str = field(repr=False)
    recipients: tuple[str, ...] = field(repr=False)
    received_at: datetime
    subject: str = field(repr=False)
    text: str = field(repr=False)
    links: tuple[str, ...] = field(default=(), repr=False)


def _address(value: str) -> str:
    # A mailbox, not Gmail query syntax or an RFC display-name expression. Keep
    # dots/plus aliases exact; never silently broaden to an account/domain match.
    if not isinstance(value, str) or len(value) > 254 or not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value,
    ):
        raise VerificationMailError("request")
    local, domain = value.rsplit("@", 1)
    if local.startswith(".") or local.endswith(".") or ".." in value:
        raise VerificationMailError("request")
    return f"{local.casefold()}@{domain.casefold()}"


def _aware(value: datetime) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _validate_request(request: MailRequest) -> None:
    _address(request.recipient)
    _address(request.sender)
    if (not _aware(request.requested_at) or not _aware(request.expires_at)
            or request.expires_at <= request.requested_at
            or datetime.now(UTC) >= request.expires_at):
        raise VerificationMailError("request")


def _query(request: MailRequest) -> str:
    # Both values passed a strict mailbox grammar; quoted literals additionally
    # isolate punctuation from Gmail's operators. Epoch bounds avoid Gmail's
    # date-only search timezone. Validation below enforces the exact instants.
    def literal(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    return (f"from:{literal(_address(request.sender))} "
            f"to:{literal(_address(request.recipient))} "
            f"after:{math.floor(request.requested_at.timestamp()) - 1} "
            f"before:{math.ceil(request.expires_at.timestamp()) + 1} -in:chats -in:sent")


def _account_url(url: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin != _MAIL_ORIGIN or parsed.username or parsed.password:
            return None
        match = _ACCOUNT_PATH.fullmatch(parsed.path)
        return (origin, match.group()) if match else None
    except ValueError:
        return None


def _delivery_time(value: str) -> datetime | None:
    """Only explicit-zone delivery timestamps qualify (never browser-local time)."""
    try:
        stamp = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return stamp.astimezone(UTC) if _aware(stamp) else None


def _original_headers(source: str) -> dict[str, str]:
    """Reduce one Show original header block, never trusting its sender Date."""
    if len(source) > 64_000 or not re.search(r"\r?\n\r?\n", source):
        raise VerificationMailError("unsupported")
    block = re.split(r"\r?\n\r?\n", source, maxsplit=1)[0]
    try:
        parsed = HeaderParser(policy=policy.default).parsestr(block)
        if parsed.defects:
            raise VerificationMailError("unsupported")
        result: dict[str, str] = {}
        for name in ("from", "to", "delivered-to", "subject"):
            values = parsed.get_all(name, [])
            if len(values) > 1:
                raise VerificationMailError("unsupported")
            result[name] = str(values[0]) if values else ""
        received = parsed.get_all("received", [])
        if not received:
            raise VerificationMailError("unsupported")
        # The uppermost Received field was prepended by the receiving service.
        # Older Received and sender-controlled Date cannot freshen a message.
        result["received"] = str(received[0]).rsplit(";", 1)[-1].strip()
        return result
    except (MessageError, ValueError, IndexError, TypeError):
        raise VerificationMailError("unsupported") from None


def _qualify(headers: dict, request: MailRequest) -> tuple[str, tuple[str, ...], datetime] | None:
    """Validate one message's headers without touching its body or thread text."""
    senders = getaddresses([headers.get("from", "")])
    if len(senders) != 1 or senders[0][1].casefold() != _address(request.sender):
        return None
    recipients = tuple(dict.fromkeys(
        address.casefold() for _, address in getaddresses([
            headers.get("to", ""), headers.get("delivered-to", ""),
        ]) if address
    ))
    if _address(request.recipient) not in recipients:
        return None
    received_at = _delivery_time(headers.get("received", ""))
    if received_at is None:
        raise VerificationMailError("unsupported")
    if not request.requested_at <= received_at <= min(request.expires_at, datetime.now(UTC)):
        return None
    return senders[0][1].casefold(), recipients, received_at


# Every DOM operation repeats the origin/account check *inside* the sandbox
# before looking at mail. The URL in getTree is not trusted as realm evidence.
_GUARD_JS = """
const visible = el => {
  const rect = el.getBoundingClientRect();
  const style = getComputedStyle(el);
  return !!(el.getClientRects().length && rect.width && rect.height &&
            style.visibility !== 'hidden' && style.display !== 'none');
};
const getMain = () => [...document.querySelectorAll('[role="main"]')].filter(visible)[0];
// Narrow Gmail layouts collapse the query input to zero width inside the
// visible search form; the form, not the input's width, proves the surface.
const searchInputs = () => [...document.querySelectorAll('form[role="search"]')].filter(visible)
  .flatMap(form => [...form.querySelectorAll('input[name="q"]')]);
const getSearch = () => searchInputs()[0];
const guard = () => {
  if (location.origin !== origin || location.pathname !== path)
    return 'origin';
  if ([...document.querySelectorAll('input[type="password"], input[name="identifier"]')].some(visible))
    return 'logged_out';
  const search = searchInputs();
  const mains = [...document.querySelectorAll('[role="main"]')].filter(visible);
  if (search.length !== 1 || mains.length !== 1) return 'unsupported';
  return null;
};
const failure = guard();
if (failure) return {error: failure};
"""


def _call(frame: Frame, account: tuple[str, str], script: str, *args):
    return frame.call(
        "(origin, path, ...args) => {" + _GUARD_JS + script + "}",
        *account, *args, timeout=8,
    )


def _checked(result: dict) -> dict:
    if not isinstance(result, dict):
        raise VerificationMailError("unsupported")
    if result.get("error"):
        raise VerificationMailError(result["error"])
    return result


def _checkpoint(request: MailRequest, checkpoint: Callable[[], None] | None) -> None:
    if checkpoint is not None:
        checkpoint()
    if datetime.now(UTC) >= request.expires_at:
        raise VerificationMailError("request")


def _realm(frame: Frame, account: tuple[str, str]) -> None:
    realms = frame.bidi.command("script.getRealms", {"context": frame.context, "type": "window"})
    origins = {item.get("origin") for item in realms.get("realms", [])}
    if not origins or origins != {account[0]}:
        raise VerificationMailError("origin")


def _surface(frame: Frame, account: tuple[str, str]) -> dict:
    _realm(frame, account)
    return _checked(_call(frame, account, r"""
      if ([...document.querySelectorAll('[role="dialog"], .AD')].some(visible))
        return {error: 'ambiguous'};
      if (location.hash && !/^#(?:inbox|all|sent|spam|trash|starred|snoozed|imp|category|label|search)(?:\/[^?]*)?$/
          .test(location.hash)) return {error: 'ambiguous'};
      return {hash: location.hash};
    """))


def _route(query: str) -> str:
    # Gmail's canonical search route uses '+' for spaces; literal '+' stays %2B.
    return "#search/" + quote(query, safe="").replace("%20", "+")


def _search(frame: Frame, account: tuple[str, str], query: str,
            request: MailRequest, checkpoint: Callable[[], None] | None) -> tuple[str, list[str]]:
    route = _route(query)
    _checked(_call(frame, account, """
      location.hash = args[0];
      return {};
    """, route))
    deadline = time.monotonic() + _WAIT_SECONDS
    while True:
        _checkpoint(request, checkpoint)
        raw = _call(frame, account, """
          if (location.hash !== args[0]) return {error: 'origin'};
          const q = getSearch();
          // Gmail echoes the executed query with one trailing separator.
          if (q.value.replace(/ $/, '') !== args[1] ||
              [...document.querySelectorAll('[role="progressbar"]')].some(visible))
            return {pending: true};
          const main = getMain();
          if ([...main.querySelectorAll('.adn[data-message-id]')].some(visible))
            return {pending: true};
          const rows = [...main.querySelectorAll('tr.zA')].filter(visible);
          const ids = rows.map(row => {
            const values = [...new Set([...row.querySelectorAll('[data-legacy-thread-id]')]
              .map(el => el.getAttribute('data-legacy-thread-id')))];
            return values.length === 1 ? values[0] : '';
          });
          // Gmail renders (or replaces) rows before their single thread id settles;
          // wait within the bound, which still fails closed.
          if (ids.includes('')) return {pending: true};
          if (ids.some(id => !id || !/^[a-zA-Z0-9_-]+$/.test(id)))
            return {error: 'unsupported'};
          if (ids.length) return {threads: [...new Set(ids)].slice(0, args[2])};
          if ([...main.querySelectorAll('.TC')].some(visible)) return {threads: []};
          return {pending: true};
        """, route, query, _MAX_THREADS)
        # Gmail briefly re-renders its main/search surface after a route change;
        # an "unsupported" layout is final only once the bounded wait expires.
        if (isinstance(raw, dict) and raw.get("error") == "unsupported"
                and time.monotonic() < deadline):
            time.sleep(0.1)
            continue
        result = _checked(raw)
        if not result.get("pending"):
            return route, result["threads"]
        if time.monotonic() >= deadline:
            raise VerificationMailError("unsupported")
        time.sleep(0.1)


def _open_thread(frame: Frame, account: tuple[str, str], route: str, thread: str,
                 request: MailRequest, checkpoint: Callable[[], None] | None) -> list[str]:
    _checked(_call(frame, account, """
      if (location.hash !== args[0]) return {error: 'origin'};
      const rows = [...getMain().querySelectorAll('tr.zA')].filter(visible).filter(row =>
        [...row.querySelectorAll('[data-legacy-thread-id]')].some(el =>
          el.getAttribute('data-legacy-thread-id') === args[1]));
      if (rows.length !== 1) return {error: 'unsupported'};
      const links = [...rows[0].querySelectorAll('div.xS[role="link"]')].filter(visible);
      if (links.length !== 1) return {error: 'unsupported'};
      // This is Gmail's observed read-only message link, not a row checkbox,
      // toolbar action, form submission or guessed URL.
      links[0].click();
      return {};
    """, route, thread))
    deadline = time.monotonic() + _WAIT_SECONDS
    while True:
        _checkpoint(request, checkpoint)
        result = _checked(_call(frame, account, """
          if (location.hash === args[0]) return {pending: true};
          if (!location.hash.startsWith(args[0] + '/') ||
              !/^[a-zA-Z0-9_-]+$/.test(location.hash.slice(args[0].length + 1)))
            return {error: 'origin'};
          const messages = [...getMain().querySelectorAll('.adn[data-message-id]')].filter(visible);
          if (!messages.length) return {pending: true};
          if (messages.length > args[1]) return {error: 'unsupported'};
          const ids = messages.map(el => el.getAttribute('data-message-id'));
          if (ids.some(id => !id || !/^#msg-f:[0-9]+$/.test(id)) ||
              new Set(ids).size !== ids.length) return {error: 'unsupported'};
          return {messages: ids};
        """, route, _MAX_MESSAGES))
        if not result.get("pending"):
            return result["messages"]
        if time.monotonic() >= deadline:
            raise VerificationMailError("unsupported")
        time.sleep(0.1)


def _foreground(bidi: Bidi) -> Tab:
    visible_tabs: list[Tab] = []
    focused_tabs: list[Tab] = []
    for node in bidi.command("browsingContext.getTree", {}).get("contexts", []):
        frame = Frame(bidi, node["context"], node.get("url", ""))
        state = frame.call(
            "() => ({visible: document.visibilityState === 'visible', focused: document.hasFocus()})",
            timeout=5,
        )
        if state["visible"]:
            tab = Tab(bidi, frame.context)
            visible_tabs.append(tab)
            if state["focused"]:
                focused_tabs.append(tab)
    candidates = focused_tabs or visible_tabs
    if len(candidates) != 1:
        raise VerificationMailError("ambiguous")
    return candidates[0]


def _is_original(url: str, account: tuple[str, str], message_id: str) -> bool:
    if _account_url(url) != account:
        return False
    params = parse_qs(urlsplit(url).query)
    return (params.get("view") == ["om"]
            and params.get("permmsgid") == [message_id.removeprefix("#")])


def _original_source(
    frame: Frame, account: tuple[str, str], message_id: str, request: MailRequest,
    checkpoint: Callable[[], None] | None,
) -> tuple[dict[str, str], str] | None:
    metadata = _checked(_call(frame, account, """
      const roots = [...getMain().querySelectorAll('.adn[data-message-id]')].filter(visible)
        .filter(el => el.getAttribute('data-message-id') === args[0]);
      if (roots.length !== 1) return {error: 'unsupported'};
      const root = roots[0];
      const senders = [...root.querySelectorAll('.gD[email]')].filter(visible);
      if (senders.length !== 1) return {error: 'unsupported'};
      if (senders[0].getAttribute('email').toLowerCase() !== args[1]) return {skip: true};
      const legacy = root.getAttribute('data-legacy-message-id');
      if (!legacy || !/^[a-f0-9]+$/.test(legacy) ||
          BigInt('0x' + legacy) !== BigInt(args[0].slice(7))) return {error: 'unsupported'};
      if ([...document.querySelectorAll('ul[role="menu"][aria-label="More message options menu"]')]
          .some(visible)) return {error: 'ambiguous'};
      return {legacy};
    """, message_id, _address(request.sender)))
    if metadata.get("skip"):
        return None
    foreground = _foreground(frame.bidi)
    before = {node["context"] for node in frame.bidi.command(
        "browsingContext.getTree", {},
    ).get("contexts", [])}
    popup: Tab | None = None
    original_opened = False
    try:
        _checkpoint(request, checkpoint)
        Tab(frame.bidi, frame.context).activate()
        button = frame.node(
            "(origin, path, ...args) => {" + _GUARD_JS + """
              const roots = [...getMain().querySelectorAll('.adn[data-message-id]')].filter(visible)
                .filter(el => el.getAttribute('data-message-id') === args[0] &&
                  el.getAttribute('data-legacy-message-id') === args[1]);
              if (roots.length !== 1) return null;
              const buttons = [...roots[0].querySelectorAll(
                '.gE button[aria-label="More message options"]')].filter(visible);
              return buttons.length === 1 ? buttons[0] : null;
            }""", *account, message_id, metadata["legacy"],
        )
        if button is None:
            raise VerificationMailError("unsupported")
        # A real user activation on the harmless header menu allows Gmail's
        # normal Show original popup; no popup policy is modified.
        frame.click(button)
        deadline = time.monotonic() + _WAIT_SECONDS
        while True:
            _checkpoint(request, checkpoint)
            menu = _checked(_call(frame, account, """
              const menus = [...document.querySelectorAll(
                'ul[role="menu"][aria-label="More message options menu"]')].filter(visible);
              if (!menus.length) return {pending: true};
              if (menus.length !== 1) return {error: 'ambiguous'};
              const items = [...menus[0].querySelectorAll('li[role="menuitem"]')]
                .filter(el => visible(el) && el.textContent.trim() === 'Show original');
              if (items.length !== 1) return {error: 'unsupported'};
              items[0].click(); return {};
            """))
            if not menu.get("pending"):
                original_opened = True
                break
            if time.monotonic() >= deadline:
                raise VerificationMailError("unsupported")
            time.sleep(0.1)
        deadline = time.monotonic() + _WAIT_SECONDS
        while popup is None:
            _checkpoint(request, checkpoint)
            created = [
                node for node in frame.bidi.command("browsingContext.getTree", {}).get("contexts", [])
                if node["context"] not in before
                and _is_original(node.get("url", ""), account, message_id)
            ]
            if len(created) > 1:
                raise VerificationMailError("ambiguous")
            if created:
                popup = Tab(frame.bidi, created[0]["context"])
                break
            if time.monotonic() >= deadline:
                raise VerificationMailError("unsupported")
            time.sleep(0.1)
        source_frame = popup.main
        _realm(source_frame, account)
        deadline = time.monotonic() + _WAIT_SECONDS
        while True:
            _checkpoint(request, checkpoint)
            ready = _checked(source_frame.call("""(origin, path, id) => {
              const p = new URL(location.href).searchParams;
              if (location.origin !== origin || location.pathname !== path ||
                  p.getAll('view').join() !== 'om' || p.getAll('permmsgid').join() !== id)
                return {error: 'origin'};
              return {ready: document.querySelectorAll(
                'pre#raw_message_text.raw_message_text').length === 1};
            }""", *account, message_id.removeprefix("#"), timeout=5))
            if ready["ready"]:
                break
            if time.monotonic() >= deadline:
                raise VerificationMailError("unsupported")
            time.sleep(0.1)
        header = _checked(source_frame.call(r"""(origin, path, id) => {
          const params = new URL(location.href).searchParams;
          if (location.origin !== origin || location.pathname !== path ||
              params.getAll('view').join() !== 'om' ||
              params.getAll('permmsgid').join() !== id) return {error: 'origin'};
          const sources = document.querySelectorAll('pre#raw_message_text.raw_message_text');
          if (sources.length !== 1) return {error: 'unsupported'};
          // Only the RFC header block crosses BiDi. Never return MIME bodies,
          // attachments or a source URL while freshness is still unproven.
          const walker = document.createTreeWalker(sources[0], NodeFilter.SHOW_TEXT);
          let text = '', node;
          while ((node = walker.nextNode())) {
            for (let offset = 0; offset < node.length; offset += 256) {
              text += node.substringData(offset, Math.min(256, node.length - offset));
              const boundary = /\r?\n\r?\n/.exec(text);
              if (boundary) return {source: text.slice(0, boundary.index) + '\n\n'};
              if (text.length > 64000) return {error: 'unsupported'};
            }
          }
          return {error: 'unsupported'};
        }""", *account, message_id.removeprefix("#"), timeout=8))
        return _original_headers(header["source"]), metadata["legacy"]
    finally:
        try:
            # A just-created about:blank is never an approved mail realm. Even
            # after Stop, allow only a bounded metadata wait to identify and
            # close the header context this operation actually created.
            deadline = time.monotonic() + _WAIT_SECONDS
            while True:
                created = [
                    node for node in frame.bidi.command(
                        "browsingContext.getTree", {},
                    ).get("contexts", []) if node["context"] not in before
                ]
                owned = [node for node in created
                         if _is_original(node.get("url", ""), account, message_id)]
                if (owned or not original_opened or time.monotonic() >= deadline
                        or not any(node.get("url") == "about:blank" for node in created)):
                    break
                time.sleep(0.1)
            if len(owned) == 1:
                tab = Tab(frame.bidi, owned[0]["context"])
                if tab.main.call("""(origin, path, id) => {
                  const p = new URL(location.href).searchParams;
                  return location.origin === origin && location.pathname === path &&
                    p.getAll('view').join() === 'om' && p.getAll('permmsgid').join() === id;
                }""", *account, message_id.removeprefix("#"), timeout=5):
                    tab.close()
        finally:
            foreground.activate()


def _read_message(
    frame: Frame, account: tuple[str, str], message_id: str, request: MailRequest,
    checkpoint: Callable[[], None] | None,
) -> VerificationMessage | None:
    _realm(frame, account)
    original = _original_source(frame, account, message_id, request, checkpoint)
    if original is None:
        return None
    headers, legacy = original
    qualified = _qualify(headers, request)
    if qualified is None:
        return None
    _checkpoint(request, checkpoint)
    payload = _checked(_call(frame, account, """
      const roots = [...getMain().querySelectorAll('.adn[data-message-id]')].filter(visible)
        .filter(el => el.getAttribute('data-message-id') === args[0]);
      if (roots.length !== 1) return {error: 'unsupported'};
      const root = roots[0];
      if (root.getAttribute('data-legacy-message-id') !== args[1]) return {error: 'unsupported'};
      const senders = [...root.querySelectorAll('.gD[email]')].filter(visible);
      if (senders.length !== 1 || senders[0].getAttribute('email').toLowerCase() !== args[3])
        return {error: 'unsupported'};
      const bodies = [...root.querySelectorAll('.a3s')].filter(visible);
      if (bodies.length !== 1) return {error: 'unsupported'};
      const body = bodies[0].cloneNode(true);
      body.querySelectorAll('.gmail_quote, .gmail_extra, blockquote, script, style')
        .forEach(el => el.remove());
      const links = [...body.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))
        .filter(href => {
          try { const u = new URL(href); return ['https:', 'http:'].includes(u.protocol) &&
            !u.username && !u.password; } catch { return false; }
        }).slice(0, 20);
      return {text: body.textContent.slice(0, args[2]), links};
    """, message_id, legacy, _MAX_TEXT, _address(request.sender)))
    return VerificationMessage(
        id=message_id, sender=qualified[0], recipients=qualified[1], received_at=qualified[2],
        subject=headers.get("subject", ""), text=payload["text"], links=tuple(payload["links"]),
    )


def _restore(frame: Frame, account: tuple[str, str], original: str, owned: str) -> None:
    # Only the route this transaction owns may be restored. Do not undo a user's
    # intervening navigation, cross-origin redirect, open compose or dialog.
    _call(frame, account, """
      if ((location.hash === args[1] || location.hash.startsWith(args[1] + '/')) &&
          ![...document.querySelectorAll('[role="dialog"], .AD')].some(visible))
        location.hash = args[0];
      return {};
    """, original, owned)


def read_verification_mail(
    bidi: Bidi, request: MailRequest, *, checkpoint: Callable[[], None] | None = None,
) -> list[VerificationMessage]:
    """Return at most three exact, fresh messages; never activate another tab.

    The caller owns approval, verification-purpose matching and consumption.
    This reader never resends messages or enters a code in an application.
    """
    _validate_request(request)
    _checkpoint(request, checkpoint)
    try:
        candidates: list[tuple[Frame, tuple[str, str], str]] = []
        tree = bidi.command("browsingContext.getTree", {})
        for node in tree.get("contexts", []):
            _checkpoint(request, checkpoint)
            account = _account_url(node.get("url", ""))
            if account is None:
                continue
            frame = Frame(bidi, node["context"], node["url"])
            surface = _surface(frame, account)
            candidates.append((frame, account, surface["hash"]))
        if not candidates:
            raise VerificationMailError("unavailable")
        if len(candidates) != 1:
            raise VerificationMailError("ambiguous")
        frame, account, original = candidates[0]
        query = _query(request)
        owned = _route(query)
        try:
            route, threads = _search(frame, account, query, request, checkpoint)
            messages: list[VerificationMessage] = []
            seen: set[str] = set()
            read_count = 0
            for index, thread in enumerate(threads):
                _checkpoint(request, checkpoint)
                _realm(frame, account)
                if index:
                    _search(frame, account, query, request, checkpoint)
                ids = _open_thread(frame, account, route, thread, request, checkpoint)
                for message_id in ids:
                    _checkpoint(request, checkpoint)
                    if read_count >= _MAX_MESSAGE_READS:
                        raise VerificationMailError("unsupported")
                    read_count += 1
                    message = _read_message(frame, account, message_id, request, checkpoint)
                    if message is not None and message.id not in seen:
                        seen.add(message.id)
                        messages.append(message)
                    if len(messages) >= _MAX_THREADS:
                        return messages
            return messages
        finally:
            try:
                _restore(frame, account, original, owned)
            except BidiError:
                pass  # Never replace a safe primary failure with transport detail.
    except BidiError:
        raise VerificationMailError("transport") from None
