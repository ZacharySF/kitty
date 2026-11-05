"""The unattended account stage of a granted tenant (proposal-07 Step 4).

Called by the filler when an account form appears on a tenant whose ATS family
the owner granted (kitty.core.apply.accounts). It decides the account action
from the registry, runs auth.authenticate with the tenant's own credentials,
and records the outcome:

- no account yet: create one (cap: 3 new tenant accounts a day) with a fresh
  generated password; a password-policy refusal gets one stricter password (the
  refused one is never submitted again), a second refusal is ``account_problem``;
- an account kitty created or signed in to before: sign in (cap: 3 tries per
  tenant a day);
- ``email_in_use`` at creation, or a rejected sign-in: the tenant's own "Forgot
  password" flow, the reset email through the Step 5 reader
  (email_verification.account_link: listed sender, Gmail DKIM/DMARC pass, same
  recipient, unused, link on the tenant inside its family grant), a newly
  generated password stored as APPLY_PW_*, ``active``, then sign-in;
- a security question, SMS, CAPTCHA, expired link or anything unrecognized is an
  ``account_problem`` handoff, and a locked account is never attempted again.

Every click is a CREDENTIAL intent through the submit guard; nothing here can
reach an application Submit.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from datetime import datetime, timezone

from kitty.core.apply import accounts
from kitty.core.apply.accounts import ACCOUNT_PROBLEM, Tenant
from kitty.core.apply.auth import (
    _ACCOUNT_LOCKED, _EMAIL_IN_USE, _ENTRY_TARGET_JS, _PASSWORD_REFUSED, _SEPARATE_SIGN_IN,
    _SIGN_IN_REJECTED, AuthResult, _guarded_click, authenticate, authentication_pending,
)
from kitty.core.apply.bidi import BidiError, Tab
from kitty.core.apply.email_verification import AccountLinkError, account_link, finish_account_link
from kitty.core.apply.guard import CREDENTIAL, UnsafeAction
from kitty.settings import Settings

_RESET_WAIT_SECONDS = 20.0
#: Password resets per tenant per rolling day; an unfinished one blocks the next.
RESETS_PER_TENANT_PER_DAY = 2

#: One analysis of a password-reset page on the tenant origin: {state, els}.
_RESET_ANALYZE = r"""
(origin) => {
  const visible = e => !!e && e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const text = e => clean(e?.innerText || e?.value || e?.getAttribute('aria-label') || '');
  const state = {kind: 'none', refused: false};
  const els = {};
  const stop = kind => { state.kind = kind; return {state, els}; };
  if (window.top !== window || location.origin !== origin || location.protocol !== 'https:') return stop('origin');
  if (document.readyState === 'loading') return stop('loading');
  const body = clean(document.body ? document.body.innerText : '');
  if ([...document.querySelectorAll('iframe')].some(f => visible(f) &&
        /recaptcha|hcaptcha|turnstile|challenges\.cloudflare\.com|arkoselabs|funcaptcha/i.test(f.src || '')) ||
      [...document.querySelectorAll('.g-recaptcha, .h-captcha, .cf-turnstile, [class*=captcha i], [id*=captcha i]')].some(visible))
    return stop('captcha');
  if (/\b(?:security questions?|secret questions?|text message|sms|phone number ending|(?:sent|texted) to your (?:phone|mobile))\b/i.test(body))
    return stop('challenge');
  const alerts = [...document.querySelectorAll('[role=alert], [aria-live=assertive], .error, .error-message, [data-automation-id=errorMessage]')]
    .filter(visible).map(text).join(' ');
  if (/\b(?:link|token|request|code)\b[^.!?]{0,60}\b(?:expired|is invalid|no longer valid|already (?:been )?used)\b/i.test(alerts + ' ' + body))
    return stop('expired');
  state.refused =
    /\bpassword\b[^.!?]{0,80}\b(?:must|should|requirements?|too (?:short|long|weak|common|simple)|not (?:strong|valid|allowed)|does not meet|invalid|cannot (?:contain|be|include)|at least|at most|no more than|previously used|used before)\b/i.test(alerts) ||
    /\b(?:must|should) (?:contain|include|have|be)\b[^.!?]{0,80}\b(?:characters?|letters?|numbers?|digits?|symbols?)\b/i.test(alerts);
  const headings = [...document.querySelectorAll('h1, h2, [role=heading]')].filter(visible).map(text);
  const inputs = [...document.querySelectorAll('input, textarea, select')].filter(e =>
    !['hidden', 'submit', 'button', 'reset', 'image'].includes((e.type || '').toLowerCase()) && visible(e) && !e.disabled);
  const passwords = inputs.filter(e => e.type === 'password');
  const describe = e => [e.name, e.id, e.getAttribute('aria-label'), e.getAttribute('placeholder'),
    e.getAttribute('autocomplete'), ...(e.labels ? [...e.labels].map(l => l.innerText) : [])].join(' ');
  const identities = inputs.filter(e => e.type === 'email' ||
    /^(?:username|email)$/i.test(e.getAttribute('autocomplete') || '') ||
    (e.type === 'text' && /e-?mail|user ?name/i.test(describe(e))));
  const others = inputs.filter(e => !passwords.includes(e) && !identities.includes(e));
  const controls = [...document.querySelectorAll('a[href], button, input[type=submit], input[type=button], [role=button]')]
    .filter(e => visible(e) && !e.disabled && e.getAttribute('aria-disabled') !== 'true' && !e.closest('[aria-hidden=true]'));
  const single = list => list.length === 1 ? list[0] : null;
  if (controls.some(e => /\b(?:submit application|send application|apply now)\b/i.test(text(e))))
    return stop('unsupported');
  const RESET_HEADING = /\b(?:reset (?:your )?password|new password|change (?:your )?password|set (?:a |your )?(?:new )?password|choose (?:a )?(?:new )?password|create (?:a )?new password)\b/i;
  const REQUEST_HEADING = /\b(?:forgot|reset|recover|trouble signing in|can['’]?t sign in)\b/i;
  if (passwords.length && headings.some(t => RESET_HEADING.test(t))) {
    if (passwords.length > 2 || others.length || identities.some(e => !e.readOnly)) return stop('unsupported');
    const actions = controls.filter(e => e.tagName !== 'A' &&
      /^(?:reset(?: my| your)? password|change(?: my| your)? password|set(?: (?:a |my )?new)? password|save(?: (?:new )?password)?|update(?: my)? password|submit|continue|confirm)$/i.test(text(e)));
    els.password = passwords[0];
    if (passwords.length === 2) els.confirm = passwords[1];
    els.action = single(actions);
    return stop(els.action ? 'new' : 'unsupported');
  }
  if (!passwords.length && identities.length === 1 && !others.length && headings.some(t => REQUEST_HEADING.test(t))) {
    const actions = controls.filter(e => e.tagName !== 'A' &&
      /^(?:reset(?: my| your)? password|send(?: (?:reset|recovery))?(?: link| email| instructions)?|continue|next|submit|request(?: a)? (?:reset|new password|link)|email me(?: a (?:reset )?link)?)$/i.test(text(e)));
    els.email = identities[0];
    els.action = single(actions);
    return stop(els.action ? 'request' : 'unsupported');
  }
  if (!passwords.length && /\b(?:check your (?:e-?mail|inbox)|we(?:['’]ve| have)? (?:just )?sent|e-?mail (?:has been|was) sent|(?:link|instructions) (?:has|have) been sent)\b/i.test(body))
    return stop('sent');
  if (/\b(?:password (?:has been|was) (?:reset|changed|updated)|password (?:reset|change) (?:is )?(?:complete|successful))\b/i.test(body))
    return stop('done');
  const sameOrigin = e => {
    try {
      const u = new URL(e.href, location.href);
      return u.origin === origin && !u.username && !u.password && (!e.target || e.target === '_self') && !e.hasAttribute('download');
    } catch { return false; }
  };
  const forgot = controls.filter(e => /^(?:forgot(?: your)? password\??|reset(?: your)? password|trouble signing in\??|can['’]?t sign in\??)$/i.test(text(e)) &&
    (e.tagName === 'A' ? sameOrigin(e) : !((e.form || e.closest('form')) && e.type === 'submit')));
  if (forgot.length) {
    els.forgot = single(forgot);
    return stop(els.forgot ? 'signin' : 'unsupported');
  }
  return {state, els};
}
"""

_STATE_JS = "(origin) => (" + _RESET_ANALYZE + ")(origin).state"
_TARGET_JS = "(origin, role) => (" + _RESET_ANALYZE + ")(origin).els[role] || null"
_WRITE_JS = r"""
(origin, role, value) => {
  const {els} = (""" + _RESET_ANALYZE + r""")(origin);
  const el = els[role];
  if (!el) return false;
  if (el.value === value) return true;
  if (el.value && role !== 'email') return false;
  el.focus();
  if (typeof el.select === 'function') el.select();
  const typed = document.execCommand('insertText', false, value);
  if (!typed || el.value !== value) {
    el.value = value;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  }
  el.blur();
  return el.value === value;
}
"""


def _state(page: Tab, origin: str) -> dict:
    try:
        return page.main.call(_STATE_JS, origin)
    except BidiError:
        return {"kind": "loading", "refused": False}


def _wait(page: Tab, origin: str, kinds: set[str], *, previous: dict | None = None,
          seconds: float | None = None) -> dict:
    """The first reset-page state in `kinds` (or a stop kind), observing only. A
    foreign origin counts only once it persists (a navigation passes through blanks)."""
    stops = {"origin", "captcha", "challenge", "expired", "unsupported"}
    deadline = time.monotonic() + (_RESET_WAIT_SECONDS if seconds is None else seconds)
    foreign_since = None
    state = _state(page, origin)
    while time.monotonic() < deadline:
        if state["kind"] == "origin":
            foreign_since = foreign_since or time.monotonic()
            if time.monotonic() - foreign_since >= 1.0:
                return state
        else:
            foreign_since = None
            if (state["kind"] in kinds or state["kind"] in stops) and (previous is None or state != previous):
                return state
        time.sleep(0.15)
        state = _state(page, origin)
    return state


def _click(page: Tab, origin: str, role: str) -> bool:
    target = page.main.node(_TARGET_JS, origin, role)
    if target is None:
        return False
    try:
        page.main.click(target, intent=CREDENTIAL)
    except BidiError:
        pass  # the click's navigation may discard its reply; observe, never repeat
    return True


_STOPS = {
    "captcha": "a CAPTCHA is on the password-reset page; reset the password yourself",
    "challenge": "the employer asks a security question or SMS code to reset the password; do it yourself",
    "expired": "the employer says the reset link expired or was used; reset the password yourself",
    "origin": "the password reset left this employer's site; continue yourself",
}


class _Problem(Exception):
    pass


def _result(blocker: str | None = None, *, acted: bool = False, filled: tuple[str, ...] = (),
            modes: tuple[str, ...] = ()) -> AuthResult:
    return AuthResult(acted=acted, blocker=blocker, filled=filled, modes=modes)


def _merge(first: AuthResult, second: AuthResult) -> AuthResult:
    filled = tuple(dict.fromkeys((*first.filled, *second.filled)))
    return AuthResult(acted=first.acted or second.acted, blocker=second.blocker, filled=filled,
                      modes=(*first.modes, *second.modes))


class AccountStage:
    """One account stage of one fill on a granted tenant; tracks the current credentials."""

    def __init__(self, page: Tab, conn: sqlite3.Connection, settings: Settings, tenant: Tenant, *,
                 job_id: int, application_url: str | None,
                 checkpoint: Callable[[], None] | None = None) -> None:
        self.page, self.conn, self.settings, self.tenant = page, conn, settings, tenant
        self.job_id, self.application_url = job_id, application_url
        self.checkpoint = checkpoint or (lambda: None)
        self.memory = accounts.Memory(conn, settings)
        self.reset_done = False

    # ------------------------------------------------------------ helpers

    def _protect(self) -> None:
        self.page.bidi.protect_secrets(self.tenant.credentials.email, self.tenant.credentials.password)

    def _authenticate(self, mode: str, *, refused_ok: bool = False) -> AuthResult:
        self.tenant = accounts.with_mode(self.tenant, self.conn, mode)
        self._protect()
        self.checkpoint()
        result = authenticate(self.page, self.tenant.policy, self.tenant.credentials,
                              application_url=self.application_url, memory=self.memory,
                              refused_ok=refused_ok)
        self._record(result)
        try:
            self.checkpoint()       # the account pages it reached become the owned page
        except BidiError:
            pass
        return result

    def _record(self, result: AuthResult) -> None:
        """Registry receipt of one authenticate call: a new account, a sign-in try, active."""
        origin = self.tenant.origin
        account = accounts.get_account(self.conn, origin)
        refused = result.blocker in (_EMAIL_IN_USE, _PASSWORD_REFUSED, _SIGN_IN_REJECTED, _ACCOUNT_LOCKED)
        if "create" in result.modes and not (refused and result.modes[-1] == "create"):
            # Counted against the daily cap only when the employer accepted it.
            accounts.record_event(self.conn, origin, "create")
            if account.state == "unknown":
                account = accounts.set_state(self.conn, origin, "created")
        if "login" in result.modes:
            accounts.record_event(self.conn, origin, "sign_in")
        if result.acted and result.blocker is None and account.state not in ("rejected", "locked"):
            accounts.set_state(self.conn, origin, "active")
        self.tenant = accounts.with_mode(self.tenant, self.conn, self.tenant.mode)

    def _problem(self, reason: str, acted: bool = False) -> AuthResult:
        return _result(ACCOUNT_PROBLEM + reason, acted=acted)

    # ------------------------------------------------------------ stages

    def run(self) -> AuthResult:
        account = self.tenant.account
        if self.tenant.shared:
            return self.sign_in()
        if account.state == "rejected":
            if not (account.account_created_at or account.last_sign_in_at):
                return self._problem("the employer refused kitty's passwords for a new account; "
                                     "create or sign in to this account yourself")
            return self.reset_then_sign_in()
        if account.state in accounts.SIGN_IN_STATES:
            return self.sign_in()
        return self.create()

    def create(self) -> AuthResult:
        if cap := accounts.create_cap_blocker(self.conn):
            return _result(cap)
        if not self.tenant.credentials.password:
            # Stored in Keys before any page sees it, so a crash never loses it.
            profile = self.tenant.account.password_profile
            self.tenant = accounts.new_password(self.conn, self.settings, self.tenant,
                                                profile if profile in ("standard", "strict") else "standard")
        result = self._authenticate("create")
        if result.blocker == _PASSWORD_REFUSED:
            accounts.record_event(self.conn, self.tenant.origin, "password_refused")
            if self.tenant.account.password_profile == "strict":
                accounts.set_state(self.conn, self.tenant.origin, "rejected")
                return _merge(result, self._problem("the employer refused kitty's strict password too; "
                                                    "create this account yourself", acted=result.acted))
            # The refused password is replaced (in Keys too) before anything is typed again.
            self.tenant = accounts.new_password(self.conn, self.settings, self.tenant, "strict")
            retry = self._authenticate("create", refused_ok=True)
            result = _merge(result, retry)
            if retry.blocker == _PASSWORD_REFUSED:
                accounts.record_event(self.conn, self.tenant.origin, "password_refused")
                accounts.set_state(self.conn, self.tenant.origin, "rejected")
                return _merge(result, self._problem("the employer refused kitty's strict password too; "
                                                    "create this account yourself", acted=True))
        if result.blocker == _EMAIL_IN_USE:
            return _merge(result, self.reset_then_sign_in())
        if result.blocker == _SEPARATE_SIGN_IN:
            return _merge(result, self.sign_in())
        return result

    def sign_in(self) -> AuthResult:
        if cap := accounts.sign_in_cap_blocker(self.conn, self.tenant.origin):
            return _result(cap)
        if not self.tenant.credentials.password:
            return self._problem("this employer account's password is missing from Keys; sign in yourself")
        result = self._authenticate("login")
        if result.blocker == _ACCOUNT_LOCKED:
            return _merge(result, self._problem("the employer reports this account is locked; "
                                                "kitty will not try it again", acted=result.acted))
        if result.blocker == _SIGN_IN_REJECTED and not self.tenant.shared and not self.reset_done:
            return _merge(result, self.reset_then_sign_in())
        return result

    def reset_then_sign_in(self) -> AuthResult:
        reset = self.reset_password()
        if reset.blocker is not None:
            return reset
        if not self._account_form():
            # "Your password was changed" pages link back to sign-in; follow that once.
            try:
                _guarded_click(self.page.main, _ENTRY_TARGET_JS, [self.tenant.origin], "login",
                               intent=CREDENTIAL)
            except BidiError:
                pass
        if self._account_form():
            return _merge(reset, self.sign_in())
        return reset

    def _account_form(self) -> bool:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if authentication_pending(self.page):
                return True
            time.sleep(0.2)
        return False

    # ------------------------------------------------------------ reset

    def reset_password(self) -> AuthResult:
        """The tenant's Forgot-password flow, ending with a new stored password and ``active``."""
        tenant, page, origin = self.tenant, self.page, self.tenant.origin
        if tenant.shared:
            return self._problem("the shared credential's password is never reset automatically")
        if not tenant.policy.email_verification.enabled:
            return self._problem("this employer has no listed sender for reset emails; reset the password yourself")
        requested = accounts.count_events(self.conn, "reset_requested", origin=origin)
        if requested >= RESETS_PER_TENANT_PER_DAY or requested > accounts.count_events(self.conn, "reset", origin=origin):
            return self._problem("a password reset here already ran or did not finish in the last 24 hours; "
                                 "reset the password yourself")
        acted = False
        link = None
        try:
            self.checkpoint()
            state = _state(page, origin)
            if state["kind"] not in ("signin", "request"):
                # email_in_use on the create form: go to its sign-in page first.
                try:
                    blocker = _guarded_click(page.main, _ENTRY_TARGET_JS, [origin], "login", intent=CREDENTIAL,
                                             fallback="no way to the employer's sign-in page")
                except BidiError:
                    blocker = None
                if blocker:
                    raise _Problem("the password-reset entry is missing (" + blocker.removeprefix("login: ") + ")")
                acted = True
                state = _wait(page, origin, {"signin", "request"}, previous=state)
            if state["kind"] == "signin":
                if not _click(page, origin, "forgot"):
                    raise _Problem("the Forgot password control changed")
                acted = True
                state = _wait(page, origin, {"request"}, previous=state)
            if state["kind"] in _STOPS:
                raise _Problem(_STOPS[state["kind"]])
            if state["kind"] != "request":
                raise _Problem("no recognizable Forgot password form; reset the password yourself")
            self.checkpoint()
            if page.main.call(_WRITE_JS, origin, "email", tenant.credentials.email) is not True:
                raise _Problem("the reset form's email box changed")
            requested_at = datetime.now(timezone.utc).replace(microsecond=0)
            if not _click(page, origin, "action"):
                raise _Problem("the reset form's send control changed")
            accounts.record_event(self.conn, origin, "reset_requested")
            state = _wait(page, origin, {"sent", "signin", "none"}, previous=state, seconds=10)
            if state["kind"] in _STOPS:
                raise _Problem(_STOPS[state["kind"]])
            try:
                link = account_link(page, self.conn, self.job_id, tenant.policy, tenant.credentials.email,
                                    requested_at=requested_at, purpose="reset", checkpoint=self.checkpoint)
            except AccountLinkError as exc:
                return _result(str(exc), acted=True)
            self.checkpoint()
            page.goto(link.url)
            state = _wait(page, origin, {"new"})
            if state["kind"] in _STOPS:
                raise _Problem(_STOPS[state["kind"]])
            if state["kind"] != "new":
                raise _Problem("the reset link did not open a new-password form")
            for profile in ("standard", "strict"):
                self.checkpoint()
                # Stored before the page sees it: a crash never loses the new password.
                self.tenant = accounts.new_password(self.conn, self.settings, self.tenant, profile)
                self._protect()
                password = self.tenant.credentials.password
                if page.main.call(_WRITE_JS, origin, "password", password) is not True:
                    raise _Problem("the new-password box changed or already holds a value")
                if page.main.node(_TARGET_JS, origin, "confirm") is not None and \
                        page.main.call(_WRITE_JS, origin, "confirm", password) is not True:
                    raise _Problem("the confirm-password box changed")
                if not _click(page, origin, "action"):
                    raise _Problem("the reset form's save control changed")
                after = _wait(page, origin, {"done", "signin", "none", "new"}, previous=state)
                if after["kind"] in _STOPS:
                    raise _Problem(_STOPS[after["kind"]])
                if after["kind"] == "new" and after["refused"]:
                    accounts.record_event(self.conn, origin, "password_refused")
                    if not page.main.call("""(origin) => {
                      const {els} = (""" + _RESET_ANALYZE + r""")(origin);
                      for (const el of [els.password, els.confirm].filter(Boolean)) {
                        el.focus(); el.select?.();
                        if (!document.execCommand('delete') || el.value) {
                          el.value = ''; el.dispatchEvent(new Event('input', {bubbles: true}));
                        }
                        el.blur();
                      }
                      return !!els.password && !els.password.value;
                    }""", origin):
                        raise _Problem("the refused new password could not be cleared")
                    state = after
                    continue
                if after["kind"] == "new":
                    raise _Problem("the employer did not confirm the new password")
                finish_account_link(self.conn, link, used=True)
                link = None
                accounts.set_state(self.conn, origin, "active", event="reset", stamp=False)
                self.tenant = accounts.with_mode(self.tenant, self.conn, "login")
                self.reset_done = True
                return _result(acted=True)
            raise _Problem("the employer refused kitty's new passwords; reset the password yourself")
        except _Problem as exc:
            return _result(ACCOUNT_PROBLEM + str(exc), acted=acted or link is not None)
        except (BidiError, UnsafeAction):
            return _result(ACCOUNT_PROBLEM + "the browser refused a password-reset step; reset it yourself",
                           acted=True)
        except accounts.AccountError as exc:
            return _result(str(exc), acted=True)
        finally:
            if link is not None:
                finish_account_link(self.conn, link, used=False)
