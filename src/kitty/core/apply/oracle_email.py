"""Oracle CandidateExperience email-first account entry, never application submission.

Only an approved HTTPS Oracle job /apply/email component is eligible. The saved
account alias is the only credential used; passwords never enter this module's
page scripts. Account creation approval covers its required routine terms, not
other consents. Verification and CAPTCHA remain human steps. An omitted native
method is made POST only on the observed Knockout submit: next component, before
writing the alias, so a failed JavaScript handler cannot leak it through GET.
After Next, missing/about:blank context URLs and pending documents are rechecked
within a bounded read-only window. A blank context is never inspected or approved.
Concrete unapproved destinations always stop; neither email nor Next is repeated.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from kitty.config import AuthenticationSettings, _authentication_origin
from kitty.core.apply.bidi import BidiError, Frame, Tab
from kitty.core.apply.credentials import ApplicationCredentials
from kitty.core.apply.guard import CREDENTIAL, FIELD
from kitty.core.apply.page import _CHOICE_TARGET_JS, _FIELD_SAFE_JS

_ORACLE_HOST = re.compile(r"[a-z0-9-]+\.fa\.[a-z0-9-]+\.oraclecloud\.com")
_EMAIL_PATH = re.compile(
    r"/hcmUI/CandidateExperience/[a-zA-Z-]+/sites/[a-zA-Z0-9_-]+/job/[0-9]+/apply/email/?"
)
_CHANGED = "login: the Oracle email entry changed; continue yourself"
_UNCONFIRMED = "login: the browser could not confirm the Oracle email step; continue in its retained tab without retrying"
_LEFT_ORIGIN = "login: Oracle left the approved origin; continue yourself"

# No account alias is passed until Python approves the actual top-level URL.
_ANALYZE_JS = r"""
(approved, expectedUrl, email) => {
  const state = {blocker: null, pendingTerms: false, filled: false, disabled: false,
                 url: location.href, ready: document.readyState !== 'loading'};
  const els = {};
  const stop = blocker => {state.blocker = blocker; return {state, els};};
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const shown = e => !!e && [...e.getClientRects()].some(r => r.width > 0 && r.height > 0) &&
    getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden';
  const fieldSafe = (""" + _FIELD_SAFE_JS + r""");
  if (window.top !== window || location.href !== expectedUrl ||
      location.protocol !== 'https:' || !approved.includes(location.origin))
    return stop('login: the Oracle email entry is not on the approved page');
  const body = clean(document.body?.innerText);
  if ([...document.querySelectorAll('input[autocomplete=one-time-code], input[name=otp], input[name=verificationCode]')].some(shown) ||
      /\b(?:verification code|verify your (?:email|identity|account)|(?:authenticate|confirm) your identity|confirm your email|check your email|enter (?:the |your )?(?:security |authentication |verification )?code|two-factor|multi-factor)\b/i.test(body))
    return stop('verification: complete Oracle email ownership verification yourself; no code or inbox access is automated');
  const interactiveCaptcha = e => shown(e) && !e.closest('.grecaptcha-badge') &&
    !(e.tagName === 'IFRAME' && /[?&]size=invisible(?:&|$)/i.test(e.src));
  if ([...document.querySelectorAll('iframe')].some(e => interactiveCaptcha(e) &&
        /recaptcha|hcaptcha|turnstile|challenges\.cloudflare\.com|arkoselabs|funcaptcha/i.test(e.src)) ||
      [...document.querySelectorAll('.g-recaptcha, .h-captcha, .cf-turnstile, [id*=captcha i], [class*=captcha i]')].some(interactiveCaptcha) ||
      /\b(?:verify (?:that )?you are (?:a )?human|complete the captcha|i'?m not a robot)\b/i.test(body))
    return stop('captcha: complete the Oracle CAPTCHA yourself');
  const emails = [...document.querySelectorAll('input[name="primary-email"]')];
  if (emails.length !== 1) return stop('login: no single Oracle account email field; continue yourself');
  const input = emails[0], form = input.form;
  if (!form || document.forms.length !== 1 || input.type !== 'email' ||
      input.id !== 'primary-email-0' || input.autocomplete !== 'email' ||
      input.getAttribute('aria-required') !== 'true' || !shown(input) || !fieldSafe(input) ||
      !form.noValidate || !/^submit:\s*next,\s*setBodyScrollTo:\s*\{\s*to:\s*0\s*\}\s*$/.test(form.getAttribute('data-bind') || ''))
    return stop('login: unrecognized Oracle email component; continue yourself');
  const controls = [...form.elements];
  const buttons = controls.filter(e => e.matches('button, input[type=submit], input[type=button], input[type=image], input[type=reset]'));
  const inactiveTermsButton = e => {
    const dialog = e.closest('oj-dialog.standard-apply-flow-agreement__dialog[role=dialog]');
    return !shown(e) && dialog && getComputedStyle(dialog).display === 'none' &&
      dialog.getAttribute('aria-label') === 'Terms and Conditions' &&
      ((e.getAttribute('aria-label') === 'Close' && /\bclick:\s*onClose\b/.test(e.getAttribute('data-bind') || '')) ||
       (clean(e.textContent) === 'Agree' && /\bclick:\s*accept\b/.test(e.getAttribute('data-bind') || '')));
  };
  const next = buttons.filter(e => shown(e) && e.tagName === 'BUTTON' && e.type === 'submit' &&
    /^next$/i.test(clean(e.getAttribute('aria-label'))) && /^next$/i.test(clean(e.innerText)) &&
    /enable\s*:\s*\$parent\.isNextButtonEnabled\b/.test(e.getAttribute('data-bind') || ''));
  if (next.length !== 1 || buttons.some(e => e !== next[0] && !inactiveTermsButton(e) && !(e.tagName === 'BUTTON' &&
      e.type === 'button' && /^cancel$/i.test(clean(e.getAttribute('aria-label'))) &&
      /^cancel$/i.test(clean(e.innerText)))))
    return stop('login: the Oracle email component has an unrecognized or application action');
  if (buttons.some(e => ['formaction', 'formmethod', 'formtarget', 'formenctype'].some(a => e.hasAttribute(a))))
    return stop('login: Oracle email action overrides are not allowed');
  const target = form.target || document.querySelector('base[target]')?.getAttribute('target') || '';
  if ((target && target !== '_self') || form.enctype !== 'application/x-www-form-urlencoded')
    return stop('login: the Oracle email form has an unsafe destination or encoding');
  if (form.action !== expectedUrl)
    return stop('login: the Oracle email form destination changed');
  if (form.hasAttribute('method') && form.method.toLowerCase() !== 'post')
    return stop('login: an explicit GET or other Oracle email form method is refused');
  const honeys = controls.filter(e => e.name === 'honey-pot');
  if (honeys.length > 1 || honeys.some(e => e.tagName !== 'INPUT' || e.type !== 'text' ||
      e.getAttribute('aria-hidden') !== 'true' || e.getAttribute('tabindex') !== '-1' || e.value))
    return stop('login: the Oracle honeypot changed; continue yourself');
  const choices = controls.filter(e => e.matches('input[type=checkbox]'));
  const terms = choices[0];
  if (choices.length > 1 || (terms && (terms.id !== 'legal-disclaimer-checkbox' ||
      !terms.required || !fieldSafe(terms) ||
      terms.getAttribute('aria-labelledby') !== 'legal-disclaimer-checkbox-label')))
    return stop('consent: review the Oracle account choices yourself');
  if (controls.some(e => e !== input && !honeys.includes(e) && !buttons.includes(e) && e !== terms) ||
      [...document.querySelectorAll('input, textarea, select, [role=checkbox], [role=radio], [role=switch], [contenteditable=true]')]
        .some(e => !controls.includes(e) && shown(e)))
    return stop('login: the Oracle email form asks for extra details; continue yourself');
  const excluded = /\b(?:certif(?:y|ication)|attest|truthful|accurate|background check|credit check|criminal|drug test|marketing|newsletter|promotional|future opportunities|talent community|arbitration|waiv(?:e|er)|biometric|sell|third.party)\b/i;
  if (excluded.test(clean(form.innerText)))
    return stop('consent: Oracle asks for more than routine account terms; review it yourself');
  if (terms) {
    const label = document.getElementById('legal-disclaimer-checkbox-label');
    if (!label || !shown(label) || !/^I agree with (?:the )?terms and conditions[ .]*$/i.test(clean(label.textContent)))
      return stop('consent: the Oracle account terms changed; review them yourself');
    els.terms = terms;
    state.pendingTerms = !terms.checked;
  }
  if (email !== null && input.value && input.value !== email)
    return stop('login: the Oracle account email already holds a different saved value; continue yourself');
  state.filled = email !== null && input.value === email;
  state.disabled = next[0].matches(':disabled') || next[0].getAttribute('aria-disabled') === 'true' ||
    !!next[0].closest('[inert], [aria-disabled=true]') ||
    !!next[0].parentElement?.closest('a[href], button, [role=button], [role=link]');
  els.input = input; els.form = form; els.next = next[0];
  return {state, els};
}
"""
_SNAPSHOT_JS = "(...args) => (" + _ANALYZE_JS + ")(...args).state"
_WRITE_JS = r"""
(...args) => {
  const {state, els} = (""" + _ANALYZE_JS + r""")(...args);
  if (state.blocker) return state.blocker;
  // Only the exact observed JS-submit component with an omitted method reaches here.
  if (!els.form.hasAttribute('method')) els.form.method = 'post';
  const value = args[2], input = els.input;
  if (input.value === value) return true;
  input.focus();
  input.select();
  const before = (""" + _ANALYZE_JS + r""")(...args);
  if (before.state.blocker || before.els.input !== input) return before.state.blocker || false;
  const previousValue = input.value;
  const typed = document.execCommand('insertText', false, value);
  if (input.value !== value) {
    if (typed || input.value !== previousValue) return false;
    const current = (""" + _ANALYZE_JS + r""")(...args);
    if (current.state.blocker || current.els.input !== input) return current.state.blocker || false;
    input.value = value;
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
  }
  input.blur();
  return input.value === value;
}
"""
_TARGET_JS = r"""
(approved, expectedUrl, email, role) => {
  const {state, els} = (""" + _ANALYZE_JS + r""")(approved, expectedUrl, email);
  if (state.blocker || !state.filled || els.form.method.toLowerCase() !== 'post') return null;
  if (role === 'terms') {
    if (!state.pendingTerms) return null;
    const e = els.terms;
    const keys = globalThis.kittyKeys ??= new WeakMap();
    const byKey = globalThis.kittyByKey ??= new Map();
    if (!keys.has(e)) {
      globalThis.kittyCounter = (globalThis.kittyCounter ?? 0) + 1;
      keys.set(e, 'e' + globalThis.kittyCounter);
    }
    const key = keys.get(e);
    byKey.set(key, e);
    const nativeTarget = (""" + _CHOICE_TARGET_JS + r""")(key);
    if (nativeTarget) return nativeTarget;
    // Oracle's zero-size native input has a visible, explicit toggle beside a
    // terms link. Never click that link merely because it is the label's centre.
    const markers = document.querySelectorAll('#legal-disclaimer-checkbox-label > span.apply-flow-input-checkbox__button');
    if (markers.length !== 1) return null;
    const marker = markers[0];
    const markerSafe = (""" + _FIELD_SAFE_JS + r""")(marker);
    if (marker.closest('label')?.control !== e ||
        !/^click:\s*toggleAccepted(?:,|$)/.test(marker.getAttribute('data-bind') || '') ||
        !markerSafe ||
        marker.querySelector('a[href], button, input, [role=button], [role=link]')) return null;
    return marker;
  }
  if (role !== 'next' || state.pendingTerms || state.disabled) return null;
  return els.next;
}
"""


@dataclass(frozen=True)
class OracleEmailResult:
    """Email entry outcome, compatible with auth's result without an import cycle."""

    acted: bool = False
    blocker: str | None = None
    filled: tuple[str, ...] = ()


def _recognized(url: str) -> bool:
    if _authentication_origin(url) is None:
        return False
    parts = urlsplit(url)
    return bool(_ORACLE_HOST.fullmatch(parts.hostname or "")
                and _EMAIL_PATH.fullmatch(parts.path) and not parts.query and not parts.fragment)


def _click(frame: Frame, args: tuple[object, ...], role: str) -> bool:
    target = frame.node(_TARGET_JS, *args, role)
    if target is None:
        return False
    # The terms box is a field; Next is the proven native account action (a POST).
    frame.click(target, intent=FIELD if role == "terms" else CREDENTIAL)
    return True


def oracle_email_gate(
    page: Tab, policy: AuthenticationSettings, credentials: ApplicationCredentials,
) -> OracleEmailResult | None:
    """Prepare and advance one approved Oracle account email entry, at most once.

    None means this is not an Oracle email-entry URL. ``acted`` means the single
    Next action was attempted, not that an account or application was completed.
    All return messages are fixed text, never page values or credential values.
    """
    filled: tuple[str, ...] = ()
    acted = False
    try:
        frame = page.main
        url = frame.url
        if not _recognized(url):
            return None
        if not policy.approves(url):
            return OracleEmailResult(blocker="login: the Oracle email origin is not approved")
        if not policy.allow_account_creation or policy.account_mode in ("login", "google"):
            return OracleEmailResult(blocker="login: Oracle email entry can create a profile; account creation approval is required")
        args = (policy.approved_origins, url, credentials.email)
        state = frame.call(_SNAPSHOT_JS, *args)
        if state["blocker"]:
            return OracleEmailResult(blocker=state["blocker"])
        written = frame.call(_WRITE_JS, *args)
        if written is not True:
            return OracleEmailResult(blocker=written if isinstance(written, str) else _CHANGED)
        filled = ("email",)
        state = frame.call(_SNAPSHOT_JS, *args)
        if state["blocker"]:
            return OracleEmailResult(blocker=state["blocker"], filled=filled)
        if state["pendingTerms"]:
            if not _click(frame, args, "terms"):
                return OracleEmailResult(blocker="consent: the Oracle terms cannot be selected safely", filled=filled)
            state = frame.call(_SNAPSHOT_JS, *args)
            if state["blocker"] or state["pendingTerms"]:
                return OracleEmailResult(blocker=state["blocker"] or "consent: Oracle account agreement selection was not confirmed", filled=filled)
        # Knockout enable bindings can update asynchronously after blur/consent.
        deadline = time.monotonic() + 3
        while state["disabled"] and time.monotonic() < deadline:
            time.sleep(0.1)
            state = frame.call(_SNAPSHOT_JS, *args)
            if state["blocker"]:
                return OracleEmailResult(blocker=state["blocker"], filled=filled)
        if state["disabled"]:
            return OracleEmailResult(blocker="login: Oracle Next remains disabled; review the email entry yourself", filled=filled)
        # Do not retry a navigation-discarded click: it may already have sent email.
        acted = True
        if not _click(frame, args, "next"):
            acted = False
            return OracleEmailResult(blocker=_CHANGED, filled=filled)
        deadline = time.monotonic() + 10
        pending = False
        while time.monotonic() < deadline:
            current = page.main
            # getTree is not a navigation/readiness barrier. Firefox can report
            # about:blank between the trusted Next and its HTTPS destination.
            # Neither a missing URL nor this blank context approves a document:
            # wait read-only, without inspecting content or repeating Next.
            if current.url in ("", "about:blank"):
                pending = True
                time.sleep(0.2)
                continue
            if not policy.approves(current.url):
                return OracleEmailResult(acted=True, blocker=_LEFT_ORIGIN, filled=filled)
            # The live document can change after getTree. The script checks its
            # exact URL and origin before reading content; no alias is needed.
            observed = current.call(_SNAPSHOT_JS, policy.approved_origins, current.url, None)
            if observed["url"] in ("", "about:blank"):
                # The live realm can enter the same provisional state after
                # getTree; _ANALYZE_JS rejects it before reading page content.
                pending = True
            elif not policy.approves(observed["url"]):
                return OracleEmailResult(acted=True, blocker=_LEFT_ORIGIN, filled=filled)
            else:
                pending = observed["url"] != current.url or not observed["ready"]
            if pending:
                time.sleep(0.2)
                continue
            blocker = observed["blocker"]
            if blocker and blocker.startswith(("verification:", "captcha:")):
                return OracleEmailResult(acted=True, blocker=blocker, filled=filled)
            if current.url != url:
                return OracleEmailResult(acted=True, filled=filled)
            time.sleep(0.2)
        if pending:
            return OracleEmailResult(acted=True, blocker=_UNCONFIRMED, filled=filled)
        return OracleEmailResult(acted=True, blocker="login: Oracle remained on email entry after Next; review its response yourself", filled=filled)
    except BidiError:
        return OracleEmailResult(acted=acted, blocker=_UNCONFIRMED, filled=filled)
