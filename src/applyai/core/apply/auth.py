"""Employer credentials or separately reviewed Google session reuse; never application Submit.

Only an enabled policy with exact HTTPS origins can send credentials. It may come
from local configuration or a fingerprint-bound website review for one run.
Account creation additionally needs `allow_account_creation`; an explicit
`account_mode` chooses existing-account login or new-account registration.

What is recognized is deliberately narrow: one visible form with an email or
username box, a password box (plus confirmation boxes when creating an account)
and exactly one button reading Sign in / Log in, or Create account / Sign up /
Register. Anything else stops for manual handling: CAPTCHA widgets (even a passive
badge), verification or MFA codes, consent checkboxes or "by continuing you agree"
text, extra fields, application actions in the same form, single sign-on only
pages, a GET form, a form posting elsewhere, cross-origin frames, and boxes that
already hold different values. Explicit login/create mode may first follow one
clearly labeled link or non-submitting button to that form. Every write and the
credential click re-run the whole check inside the page. No retries or account-
existence inference: `acted` means submission was attempted, not a confirmed account.

Google mode instead uses the reviewed Profile email, never employer credentials.
It can enter one explicit Google sign-in control and select one matching existing
account on the exact official HTTPS chooser. Only newly opened, opener-associated
popups are eligible. Extra permissions, input or security steps are handed back
to the user in the same dedicated Firefox; autofill resumes only on the original
approved employer application. No Google password or consent action is automated.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from hmac import compare_digest

from applyai.config import AuthenticationSettings, _authentication_origin
from applyai.core.apply.bidi import BidiError, Tab
from applyai.core.apply.credentials import ApplicationCredentials, credential_binding
from applyai.core.apply.page import UnsafeAction, form_frames, settle

#: Frames that are themselves a challenge widget (form_frames skips them, so look here).
_CHALLENGE_FRAMES = re.compile(
    r"recaptcha|hcaptcha|turnstile|challenges\.cloudflare\.com|arkoselabs|funcaptcha", re.I)

#: Shared page-side analysis. Returns {state, els}: `state` is plain data for
#: Python, `els` the elements that later writes and the click act on.
_ANALYZE = r"""
(approved, email, password) => {
  const visible = e => !!e && e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const text = e => clean(e?.innerText || e?.value || '');
  const originOk = u => {
    try { const x = new URL(u, location.href); return x.protocol === 'https:' && approved.includes(x.origin); }
    catch { return false; }
  };
  const state = {blocker: null, hasPassword: false, mode: null, roles: [], signature: '',
                 url: location.href, action: null, top: window.top === window};
  const els = {};
  const stop = blocker => { state.blocker = blocker; return {state, els}; };
  const body = clean(document.body ? document.body.innerText : '');

  const visibleAny = s => [...document.querySelectorAll(s)].some(visible);
  if (visibleAny('input[autocomplete=one-time-code], input[name=otp], input[name=verificationCode]') ||
      /\b(?:enter (?:the |your )?(?:verification|security|authentication) code|verify your email|two-factor authentication|multi-factor authentication)\b/i.test(body))
    return stop('verification: complete the verification or MFA step yourself');
  if ([...document.querySelectorAll('iframe')].some(f => visible(f) &&
        /recaptcha|hcaptcha|turnstile|challenges\.cloudflare\.com|arkoselabs|funcaptcha/i.test(f.src || '')) ||
      visibleAny('.g-recaptcha, .grecaptcha-badge, .h-captcha, .cf-turnstile, [class*=captcha i], [id*=captcha i]') ||
      /\b(?:verify (?:that )?you are (?:a )?human|complete the captcha|i'?m not a robot)\b/i.test(body))
    return stop('captcha: a CAPTCHA is on the sign-in page; sign in yourself');
  if (/\bby (?:clicking|continuing|creating|signing (?:up|in)|registering|proceeding|selecting|logging in)\b[^.]{0,160}\b(?:agree|accept|consent|terms|privacy)/i.test(body))
    return stop('consent: signing in here also accepts terms; sign in yourself');

  const passwords = [...document.querySelectorAll('input[type=password]')].filter(visible);
  if (!passwords.length) return stop('login: no visible password box to fill; sign in yourself');
  state.hasPassword = true;
  if (!originOk(location.href)) return stop('login: this page is not an approved origin for credentials');
  if (!state.top) {
    let topOk = false;
    try { topOk = originOk(window.top.location.href); } catch { topOk = false; }
    if (!topOk) return stop('login: the sign-in form is in a cross-origin frame; sign in yourself');
  }
  if (passwords.length > 2) return stop('login: unrecognized sign-in form; sign in yourself');

  const forms = new Set(passwords.map(p => p.form));
  if (forms.size !== 1) return stop('login: unrecognized sign-in form; sign in yourself');
  const LOGIN = /^(?:sign in|sign-in|signin|log in|log-in|login|log on)$/i;
  const CREATE = /^(?:create account|create an account|create my account|sign up|sign-up|register)$/i;
  const controlsIn = box => [...box.querySelectorAll('button, input[type=submit], input[type=button], [role=button]')]
    .filter(visible);
  let box = passwords[0].form;
  if (!box) {
    box = passwords[0].parentElement;
    while (box && !(passwords.every(p => box.contains(p)) &&
                    controlsIn(box).some(e => LOGIN.test(text(e)) || CREATE.test(text(e)))))
      box = box.parentElement;
    if (!box) return stop('login: no sign-in button next to the password box; sign in yourself');
  }

  const labelOf = e => {
    const ids = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const byIds = ids.map(id => document.getElementById(id)?.innerText || '').join(' ');
    return clean([byIds, e.labels && e.labels[0] ? e.labels[0].innerText : ''].join(' '));
  };
  const describe = e => clean([e.name, e.id, e.getAttribute('autocomplete'), e.getAttribute('aria-label'),
                               e.getAttribute('placeholder'), e.getAttribute('data-automation-id'), labelOf(e)].join(' '));
  const confirmLike = e => /confirm|verify|re-?enter|repeat|retype|again/i.test(describe(e));
  const inputs = [...box.querySelectorAll('input, textarea, select')].filter(e => {
    const t = (e.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(t)) return false;
    if (t === 'checkbox' || t === 'radio') return visible(e) || visible(e.parentElement);
    return visible(e) && !e.disabled;
  });
  const identifiers = inputs.filter(e => e.tagName === 'INPUT' && e.type !== 'password' && (
    e.type === 'email' || /^(?:username|email)$/i.test(e.getAttribute('autocomplete') || '') ||
    (e.type === 'text' && /e-?mail|user ?name|user ?id|\blogin\b/i.test(describe(e)))));
  const remember = inputs.filter(e => e.type === 'checkbox' && !e.required && !e.checked &&
    /remember|keep me (?:signed|logged) in|stay (?:signed|logged) in/i.test(describe(e) + ' ' + text(e.parentElement)));
  const others = inputs.filter(e => e.type !== 'password' && !identifiers.includes(e) && !remember.includes(e));
  if (others.some(e => e.type === 'checkbox' || e.type === 'radio'))
    return stop('consent: the sign-in form has a checkbox (terms or consent) for you to decide');
  if (others.length) return stop('login: the sign-in form also asks for other details; continue yourself');

  const controls = controlsIn(box).filter(e => !e.disabled && e.getAttribute('aria-disabled') !== 'true');
  if (controls.some(e => /submit|apply|send application|finish/i.test(text(e))))
    return stop('login: the sign-in form also holds application actions; continue yourself');
  const logins = controls.filter(e => LOGIN.test(text(e)));
  const creates = controls.filter(e => CREATE.test(text(e)));
  if (logins.length + creates.length !== 1) return stop('login: no single Sign in or Create account button; sign in yourself');
  const button = logins[0] || creates[0];
  state.mode = logins.length ? 'login' : 'create';
  if (/agree|accept|consent|terms|privacy|submit|apply/i.test(button.getAttribute('aria-label') || ''))
    return stop('consent: the sign-in button also accepts terms; sign in yourself');

  let password2 = null;
  if (passwords.length === 2) {
    if (state.mode !== 'create' || confirmLike(passwords[0]) || !confirmLike(passwords[1]))
      return stop('login: unrecognized sign-in form; sign in yourself');
    password2 = passwords[1];
  }
  let id1 = null, id2 = null;
  if (identifiers.length === 1) id1 = identifiers[0];
  else if (identifiers.length === 2 && state.mode === 'create' && !confirmLike(identifiers[0]) && confirmLike(identifiers[1]))
    [id1, id2] = identifiers;
  else return stop('login: no single email or username box; sign in yourself');

  const form = button.form || (box.tagName === 'FORM' ? box : null);
  if (form) {
    const method = (button.getAttribute('formmethod') || form.getAttribute('method') || '').toLowerCase();
    const hasTarget = button.hasAttribute('formaction') || form.hasAttribute('action');
    if (method === 'get' || (!method && hasTarget))
      return stop('login: this form would send the password in the address; sign in yourself');
    if (method && method !== 'post') return stop('login: unrecognized sign-in form; sign in yourself');
    state.action = button.hasAttribute('formaction') ? button.formAction : form.action;
    if (!originOk(state.action)) return stop('login: the form sends credentials to an unapproved origin');
  }

  els.email = id1; els.password = passwords[0];
  state.roles = ['email', 'password'];
  if (id2) { els.emailConfirm = id2; state.roles.splice(1, 0, 'emailConfirm'); }
  if (password2) { els.passwordConfirm = password2; state.roles.push('passwordConfirm'); }
  els.button = button;
  // Values are compared only once Python has approved this frame and passes them in.
  for (const role of email === null ? [] : state.roles) {
    const value = role.startsWith('email') ? email : password;
    if (els[role].value && els[role].value !== value)
      return stop('login: the sign-in form already holds other values; continue yourself');
  }
  state.signature = [location.href, state.mode, text(button),
    ...state.roles.map(r => [r, els[r].name, els[r].id, els[r].type].join(':'))].join('|');
  return {state, els};
}
"""

_SNAPSHOT_JS = "(approved, email, password) => (" + _ANALYZE + ")(approved, email, password).state"

#: Fill one credential box, after re-running the whole analysis on the current DOM.
_WRITE_JS = r"""
(approved, email, password, signature, role) => {
  const {state, els} = (""" + _ANALYZE + r""")(approved, email, password);
  if (state.blocker) return state.blocker;
  if (state.signature !== signature || !els[role]) return false;
  const el = els[role];
  const value = role.startsWith('email') ? email : password;
  if (el.value === value) return true;
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

#: Click the one recognized button, only if the form and its values are still exactly as filled.
_CLICK_JS = r"""
(approved, email, password, signature) => {
  const {state, els} = (""" + _ANALYZE + r""")(approved, email, password);
  if (state.blocker) return state.blocker;
  if (state.signature !== signature) return false;
  if (state.roles.some(r => els[r].value !== (r.startsWith('email') ? email : password))) return false;
  els.button.click();
  return true;
}
"""

#: Enter only the explicitly chosen flow, without submitting any form or credentials.
#: Re-analyze in the same DOM turn as the click; redirects are checked before filling.
_ENTRY_JS = r"""
(approved, mode) => {
  const {state} = (""" + _ANALYZE + r""")(approved, null, null);
  if (!state.top || !approved.includes(location.origin) || location.protocol !== 'https:') return false;
  if (state.blocker && /^(?:captcha|verification|consent):/.test(state.blocker)) return state.blocker;
  const visible = e => e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const text = e => (e.innerText || e.value || '').replace(/\s+/g, ' ').trim();
  if ([...document.querySelectorAll('input[type=checkbox], input[type=radio]')]
      .some(e => (visible(e) || (e.parentElement && visible(e.parentElement))) &&
        !/remember|keep me (?:signed|logged) in/i.test(e.parentElement?.innerText || '')))
    return 'consent: decide the account-page consent yourself';
  const controls = [...document.querySelectorAll('a, button, input[type=submit], input[type=button], [role=button]')]
    .filter(visible);
  if (controls.some(e => /submit|apply|send application|finish/i.test(text(e)))) return false;
  const pattern = mode === 'create'
    ? /^(?:create account|create an account|create my account|sign up|sign-up|register)$/i
    : /^(?:sign in|sign-in|signin|log in|log-in|login|log on)$/i;
  const candidates = controls.filter(e => pattern.test(text(e)) && !e.disabled &&
    e.getAttribute('aria-disabled') !== 'true');
  if (candidates.length !== 1) return false;
  const el = candidates[0];
  if (/agree|accept|consent|terms|privacy|submit|apply/i.test(el.getAttribute('aria-label') || '')) return false;
  if (el.tagName === 'A') {
    if (!el.hasAttribute('href') || el.hasAttribute('download') ||
        (el.target && el.target !== '_self')) return false;
    const url = new URL(el.href, location.href);
    if (url.protocol !== 'https:' || !approved.includes(url.origin) || url.username || url.password) return false;
  } else if (el.form ? el.type !== 'button' : !(
      el.tagName === 'BUTTON' || el.type === 'button' || el.getAttribute('role') === 'button')) return false;
  el.click();
  return true;
}
"""

_CHANGED = "login: the sign-in form changed while it was being filled; sign in yourself"
_FAILED = "login: the browser refused a sign-in step; sign in yourself"


@dataclass(frozen=True)
class AuthResult:
    """Credential request attempted, or Google returned to the employer application; else why not."""

    acted: bool = False
    blocker: str | None = None


# Not configurable: tests may monkeypatch this constant to their isolated HTTPS fixture.
_GOOGLE_ORIGIN = "https://accounts.google.com"
_GOOGLE_HANDOFF = (
    " Complete Google sign-in with your reviewed Profile email manually in this SAME "
    "dedicated ApplyAI Firefox (not everyday Chrome), then return to this employer "
    "application. Keep its tab open; review a new plan and Continue to resume retained "
    "fields. Passwords, consent, account addition and security checks are always manual."
)

_GOOGLE_CLICK = r"""
(approved, googleOrigin, email, chooser) => {
  if (window.top !== window || location.protocol !== 'https:') return 'unapproved page';
  if (chooser ? location.origin !== googleOrigin : !approved.includes(location.origin))
    return 'unapproved origin';
  const visible = e => !!e && e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const text = e => clean(e.innerText || e.value || '');
  const all = s => [...document.querySelectorAll(s)].filter(visible);
  const body = text(document.body);
  if (all('input[autocomplete=one-time-code], input[name=otp], input[name=verificationCode], input[type=checkbox], input[type=radio]').length ||
      all('iframe').some(e => /captcha|turnstile|arkose|challenge/i.test(e.src)) ||
      all('[class*=captcha i], [id*=captcha i]').length ||
      /\b(?:verify|verification|two.step|two.factor|multi.factor|security check|not secure|couldn.t sign you in|access blocked|unusual activity|robot|captcha)\b/i.test(body))
    return 'security, verification or CAPTCHA needs your attention';
  if (/\b(?:by (?:continuing|clicking|signing|selecting)[^.]{0,160}(?:agree|accept|consent|terms)|wants (?:access|to access)|allow .{0,100}access|review .{0,40}permissions|share .{0,60}(?:with|data))\b/i.test(body))
    return 'consent or permissions need your decision';
  const controls = all('a, button, input[type=submit], input[type=button], [role=button], [role=link]');
  if (controls.some(e => /^(?:submit(?: application)?|send application|finish|allow|accept|agree|approve|grant access)$/i.test(text(e))))
    return 'application or consent controls need manual review';
  let candidates;
  if (chooser) {
    if (all('input[type=password], input[type=email], input[type=text], input:not([type])').length)
      return 'the reviewed account is not signed in or Google needs input';
    if (!all('h1, h2, [role=heading]').some(e => /^choose an account$/i.test(text(e))))
      return 'no recognized signed-in account chooser';
    // Google identifies existing accounts with data-identifier/email. Never use
    // substring text matches, account indices, Add account or Use another account.
    const rows = all('[data-identifier], [data-email]').filter(e =>
      (e.getAttribute('data-identifier') || e.getAttribute('data-email') || '') === email);
    candidates = [...new Set(rows.map(e => e.matches('button, a, [role=button], [role=link]')
      ? e : e.closest('button, a, [role=button], [role=link]')).filter(visible))];
    if (rows.length !== 1 || candidates.length !== 1)
      return 'the exact reviewed signed-in account is missing or ambiguous';
    if (/signed out|sign in again|use another|add account/i.test(text(candidates[0])) ||
        rows[0].closest('[data-authuser="-1"], [data-is-signed-in="false"]'))
      return 'the reviewed account is signed out';
  } else {
    if (!all('h1, h2, [role=heading]').some(e =>
        /^(?:candidate (?:sign in|login|account|home)|sign in|log in|create (?:an? )?account|sign up|register)$/i.test(text(e))))
      return 'no recognized employer sign-in page';
    candidates = controls.filter(e => /^(?:sign in|continue|log in) with google$/i.test(text(e)));
  }
  if (candidates.length !== 1) return 'Google controls are missing or ambiguous';
  const el = candidates[0];
  if (el.disabled || el.getAttribute('aria-disabled') === 'true' ||
      /consent|agree|accept|permission|submit|apply/i.test(el.getAttribute('aria-label') || ''))
    return 'Google control is disabled or unsafe';
  if (el.tagName === 'A') {
    if (!el.hasAttribute('href') || el.hasAttribute('download')) return 'unsafe Google link';
    const target = new URL(el.href, location.href);
    if (target.protocol !== 'https:' || target.username || target.password ||
        !(target.origin === googleOrigin || (!chooser && approved.includes(target.origin))))
      return 'Google link points to an unapproved origin';
    if (el.target && !['_self', '_blank'].includes(el.target)) return 'unassociated named target';
  } else if (el.form && el.type !== 'button') return 'form submission is not a Google account selection';
  el.click();
  return true;
}
"""


def google_session(page: Tab, policy: AuthenticationSettings, email: str) -> AuthResult:
    """Reuse one reviewed signed-in Google identity; never type or submit credentials.

    Only this employer tab and a newly opened context explicitly naming it as
    originalOpener are eligible. Unknown popup association fails closed.
    """
    def handoff(reason: str) -> AuthResult:
        return AuthResult(blocker="login: Google session: " + reason + "." + _GOOGLE_HANDOFF)

    if (not policy.enabled or policy.account_mode != "google" or
            not policy._google_email or not compare_digest(
                policy._google_email.encode(), email.encode())):
        return handoff("review the Google account action and Profile email first")
    employer = _authentication_origin(page.main.url)
    if employer == _GOOGLE_ORIGIN or not policy.approves(page.main.url):
        return handoff("this is not the approved employer sign-in page")
    try:
        contexts = page.bidi.command("browsingContext.getTree", {})["contexts"]
        existing = {c["context"] for c in contexts}
        clicked = page.main.call(_GOOGLE_CLICK, [employer], _GOOGLE_ORIGIN, email, False)
        if clicked is not True:
            return handoff(str(clicked))
        selected = False
        active = page
        popup_id = None
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            contexts = page.bidi.command("browsingContext.getTree", {})["contexts"]
            associated = [c for c in contexts if c["context"] not in existing and
                          c.get("originalOpener") == page.context]
            if len(associated) > 1:
                return handoff("more than one associated sign-in popup opened")
            if associated:
                if popup_id and popup_id != associated[0]["context"]:
                    return handoff("the sign-in popup changed")
                popup_id = associated[0]["context"]
                active = Tab(page.bidi, popup_id)
            elif popup_id:
                active = page
            # Resume only the original, approved employer application. Never
            # autofill the Google page, popup or an unrelated retained tab.
            if _authentication_origin(page.main.url) == employer:
                from applyai.core.apply.preflight import inspect_page
                snapshot = inspect_page(page)
                if not snapshot.blocker and snapshot.has_fields and (snapshot.boundary or snapshot.step):
                    return AuthResult(acted=True)
            origin = _authentication_origin(active.main.url)
            if origin == _GOOGLE_ORIGIN:
                if not selected:
                    settle(active, timeout_ms=3000)
                    clicked = active.main.call(_GOOGLE_CLICK, [employer], _GOOGLE_ORIGIN, email, True)
                    if clicked is not True:
                        return handoff(str(clicked))
                    selected = True
            elif origin not in (employer, None) or (
                origin is None and active.main.url != "about:blank"
            ):
                return handoff("sign-in redirected to an unapproved origin")
            time.sleep(0.2)
        return handoff("Google has not returned to a recognized employer application; finish any extra consent or security step yourself")
    except (BidiError, UnsafeAction):
        return handoff("the browser could not safely identify or complete this session flow")



def authenticate(
    page: Tab, policy: AuthenticationSettings, credentials: ApplicationCredentials,
) -> AuthResult:
    """Fill and submit one recognized sign-in or account-creation form, or say why not."""
    if policy.account_mode == "google":
        return AuthResult(blocker="login: Google session reuse never accepts employer credentials")
    if not policy.enabled:
        return AuthResult(blocker="login: login automation is off (apply.yaml authentication.enabled)")
    if policy._credential_binding is not None and not compare_digest(
        policy._credential_binding, credential_binding(credentials),
    ):
        return AuthResult(blocker="login: saved credentials changed after review; review the plan again")
    page.bidi.protect_secrets(credentials.email, credentials.password)
    approved = [origin for origin in policy.approved_origins if origin != _GOOGLE_ORIGIN]
    secrets = (credentials.email, credentials.password)
    try:
        for entry in (False, True):
            frames = page.frames()
            if not frames or _authentication_origin(frames[0].url) not in approved:
                return AuthResult(blocker="login: this page is not an approved origin for credentials")
            if any(_CHALLENGE_FRAMES.search(frame.url) for frame in frames[1:]):
                return AuthResult(blocker="captcha: a CAPTCHA is on the sign-in page; sign in yourself")
            found = []
            for frame in form_frames(page):
                state = frame.call(_SNAPSHOT_JS, approved, None, None)   # no values before approval
                blocker = state["blocker"]
                if blocker and blocker.split(":")[0] in ("captcha", "verification", "consent"):
                    return AuthResult(blocker=blocker)
                if state["hasPassword"]:
                    found.append((frame, state))
            if len(found) > 1:
                return AuthResult(blocker="login: multiple account forms; choose one yourself")
            if found:
                frame, state = found[0]
                if policy.account_mode == "recognized" or state["mode"] == policy.account_mode:
                    if state["blocker"]:
                        return AuthResult(blocker=state["blocker"])
                    break
            if entry or policy.account_mode == "recognized":
                return AuthResult(blocker="login: no single recognizable form for the chosen account action; continue yourself")
            if policy.account_mode == "create" and not policy.allow_account_creation:
                return AuthResult(blocker="login: account creation is off")
            done = page.main.call(_ENTRY_JS, approved, policy.account_mode)
            if done is not True:
                return AuthResult(blocker=done if isinstance(done, str) else
                                  "login: no safe entry to the chosen account form; open it yourself")
            settle(page)
        if not (policy.approves(frame.url) and policy.approves(state["url"])
                and (state["action"] is None or policy.approves(state["action"]))):
            return AuthResult(blocker="login: this page is not an approved origin for credentials")
        if state["mode"] == "create" and not policy.allow_account_creation:
            return AuthResult(blocker="login: this site wants a new account; account creation is off "
                                      "(apply.yaml authentication.allow_account_creation)")
        for role in state["roles"]:
            done = frame.call(_WRITE_JS, approved, *secrets, state["signature"], role)
            if done is not True:
                return AuthResult(blocker=done if isinstance(done, str) else _CHANGED)
        done = frame.call(_CLICK_JS, approved, *secrets, state["signature"])
        if done is not True:
            return AuthResult(blocker=done if isinstance(done, str) else _CHANGED)
    except (BidiError, UnsafeAction):
        return AuthResult(blocker=_FAILED)
    return AuthResult(acted=True)
