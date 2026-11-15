"""Employer credentials or separately reviewed Google session reuse; never application Submit.

Only an enabled policy with exact HTTPS origins can send credentials. It may come
from local configuration or a fingerprint-bound website review for one run.
Account creation additionally needs `allow_account_creation`. Auto mode follows
the site's recognized sign-in or registration form; explicit modes restrict it.
Declared existing-account origins force login-only even when creation is enabled;
manual-account origins stop before credential writes, including after navigation.

Recognition requires one bound account form and a single credential action,
including explicit email-first stages and visibly identity-bound password steps.
Accessibility overlays derive account semantics only from their directly
associated hidden native submit; clicks use a real, unobstructed pointer target.
Only explicit account-status evidence or a distinct post-creation sign-in stage
permits an automatic alternate flow; a password is never retried.
Up to eight stages (three distinct entry controls) are observed synchronously,
with six-second semantic receipt waits. A click, rerender or URL change alone
never proves departure. CAPTCHA, verification,
unsafe destinations, extra fields and different saved values stop all writes.
Authorized account creation includes routine required terms/privacy choices, not factual
certifications, marketing, waivers or final application actions. Other consent
remains manual. Every write, agreement click and credential click repeats guards.

Google mode instead uses the reviewed Profile email, never employer credentials.
It can enter one explicit Google sign-in control and select one matching existing
account on the exact official HTTPS chooser. Only newly opened, opener-associated
popups are eligible. Extra permissions, input or security steps are handed back
to the user in the same dedicated Firefox; autofill resumes only on the original
approved employer application's loaded document, not a pending navigation URL.
Navigation can discard a click's reply: wait for that approved return without
repeating the click. No Google password or consent action is automated.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from hmac import compare_digest
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlsplit

from kitty.config import AuthenticationSettings, _authentication_origin
from kitty.core.apply.bidi import BidiError, Frame, Tab
from kitty.core.apply.credentials import ApplicationCredentials, credential_binding
from kitty.core.apply.guard import ACTIVATE_JS, CREDENTIAL, FIELD, UnsafeAction
from kitty.core.apply.oracle_email import oracle_email_gate
from kitty.core.apply.page import _CHOICE_TARGET_JS, _DOM_HELPERS_JS, form_frames, settle

#: Frames that are themselves a challenge widget (form_frames skips them, so look here).
_CHALLENGE_FRAMES = re.compile(
    r"recaptcha|hcaptcha|turnstile|challenges\.cloudflare\.com|arkoselabs|funcaptcha", re.I)
_INVISIBLE_BADGE = re.compile(r"/recaptcha/(?:api2|enterprise)/anchor\?(?:[^#]*&)?size=invisible(?:&|$)", re.I)
_SIGN_IN_REJECTED = (
    "login: employer rejected sign-in or the account may be locked; "
    "resolve it in the retained tab before retrying"
)
_ACCOUNT_LOCKED = "login: employer rejected sign-in: this account is locked; sign in yourself after it unlocks"
_EMAIL_IN_USE = "login: this email already has an account; use the existing-account sign-in"
_SEPARATE_SIGN_IN = "login: account creation reached a separate sign-in step; continue yourself"
_PASSWORD_REFUSED = "login: the employer refused the new account password's format"


class AccountMemory(Protocol):
    """Durable account facts authenticate consults and records.

    The registry implementation is kitty.core.apply.accounts.Memory (apply_accounts);
    a rejected identity is never submitted again with the same password, and an
    observed existing account forces sign-in instead of another registration.
    """

    def rejected(self, origin: str, credentials: ApplicationCredentials) -> bool: ...
    def reject(self, origin: str, credentials: ApplicationCredentials, *, locked: bool) -> None: ...
    def exists(self, origin: str, email: str) -> bool: ...
    def mark_exists(self, origin: str, email: str) -> None: ...


class _CallMemory:
    """Account facts for one authenticate call when no registry is supplied."""

    def __init__(self) -> None:
        self._rejected: set[tuple[str, str]] = set()
        self._existing: set[tuple[str, str]] = set()

    def rejected(self, origin: str, credentials: ApplicationCredentials) -> bool:
        return (origin, credential_binding(credentials)) in self._rejected

    def reject(self, origin: str, credentials: ApplicationCredentials, *, locked: bool) -> None:
        self._rejected.add((origin, credential_binding(credentials)))

    def exists(self, origin: str, email: str) -> bool:
        return (origin, email.casefold()) in self._existing

    def mark_exists(self, origin: str, email: str) -> None:
        self._existing.add((origin, email.casefold()))


# Workday's login URL carries the retained application in a once-encoded
# redirect. A requisition-looking substring is not ownership evidence: bind the
# exact HTTPS tenant, career site and entire requisition slug, not a loose ID.
_WORKDAY_HOST = re.compile(r"[a-z0-9-]+\.wd[0-9]+\.myworkdayjobs\.com")
_WORKDAY_POSTING = re.compile(
    r"/(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[^/]+)/job/[^/]+/(?P<slug>[^/]+)"
    r"(?:/apply(?:/(?:applyManually|autofillWithResume|useMyLastApplication))?)?/?"
)


def workday_application_binding(url: str) -> tuple[str, str] | None:
    """Read a narrowly recognized Workday posting or same-site login redirect.

    Query redirects must be unique, same-origin and point directly to the same
    career site's posting. Locale insertion and location-slug punctuation are
    presentation changes; the full requisition slug must remain exact. Decode
    only the query layer and path segments; encoded separators, dot segments
    and nested redirects are never followed. Identity is not authorization.
    """
    origin = _authentication_origin(url)
    if origin is None:
        return None
    parsed = urlsplit(url)
    if not _WORKDAY_HOST.fullmatch(parsed.hostname or "") or parsed.fragment:
        return None

    def path_parts(path: str) -> list[str] | None:
        try:
            parts = [unquote(part, errors="strict") for part in path.split("/")]
        except UnicodeError:
            return None
        if any(part in (".", "..") or any(
            char in "/\\%?#" or ord(char) < 32 or ord(char) == 127 for char in part
        ) for part in parts):
            return None
        return parts

    parts = path_parts(parsed.path)
    if parts is None:
        return None
    path = "/".join(parts)
    # Account activation lands on .../login/ok with the same retained redirect.
    login = re.fullmatch(r"/(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[^/]+)/login(?:/ok)?/?", path)
    if login:
        try:
            query = parse_qs(parsed.query, keep_blank_values=True, errors="strict")
        except UnicodeError:
            return None
        if set(query) != {"redirect"} or len(query["redirect"]) != 1:
            return None
        target = query["redirect"][0]
        # A root-relative path has exactly one leading slash; all absolute URLs
        # must repeat the approved tenant origin without userinfo or ambiguity.
        if target.startswith("/") and not target.startswith("//"):
            target = origin + target
        if _authentication_origin(target) != origin:
            return None
        redirected = urlsplit(target)
        target_parts = path_parts(redirected.path)
        if target_parts is None or redirected.query or redirected.fragment:
            return None
        path = "/".join(target_parts)
    elif parsed.query:
        # Direct posting URLs may carry source attribution, but never another
        # navigation target masquerading as a posting.
        if any(key.lower() in {"redirect", "redirect_uri", "returnurl", "returnto", "url"}
               for key in parse_qs(parsed.query, keep_blank_values=True)):
            return None
    posting = _WORKDAY_POSTING.fullmatch(path)
    if posting is None or (login and posting["site"] != login["site"]):
        return None
    return origin, "/" + posting["site"] + "/job/" + posting["slug"]


#: Shared page-side analysis. Returns {state, els}: `state` is plain data for
#: Python, `els` the elements that later writes and the click act on.
_ANALYZE = r"""
(approved, email, password) => {
  const visible = e => !!e && e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const text = e => clean(e?.innerText || e?.value || e?.getAttribute('aria-label') || '');
  const originOk = u => {
    try { const x = new URL(u, location.href); return x.protocol === 'https:' && approved.includes(x.origin); }
    catch { return false; }
  };
  const state = {blocker: null, submitBlocker: null, hasPassword: false, hasIdentity: false,
                 rejected: false, locked: false, passwordRefused: false,
                 stage: null, accountStatus: null, mode: null, roles: [], signature: '',
                 url: location.href, action: null, preparePost: false, top: window.top === window};
  const els = {};
  const stop = blocker => { state.blocker = blocker; return {state, els}; };
  const body = clean(document.body ? document.body.innerText : '');

  const visibleAny = s => [...document.querySelectorAll(s)].some(visible);
  if (visibleAny('input[autocomplete=one-time-code], input[name=otp], input[name=verificationCode]') ||
      /\b(?:enter (?:the |your )?(?:verification|security|authentication) code|verify your (?:email|account)|two-factor authentication|multi-factor authentication)\b/i.test(body) ||
      (/\bcheck your email\b/i.test(body) && /\benter the (?:code|otp)\b/i.test(body)))
    return stop('verification: complete the verification or MFA step yourself');
  if ([...document.querySelectorAll('iframe')].some(f => visible(f) &&
        /recaptcha|hcaptcha|turnstile|challenges\.cloudflare\.com|arkoselabs|funcaptcha/i.test(f.src || '')) ||
      visibleAny('.g-recaptcha, .grecaptcha-badge, .h-captcha, .cf-turnstile, [class*=captcha i], [id*=captcha i]') ||
      /\b(?:verify (?:that )?you are (?:a )?human|complete the captcha|i'?m not a robot)\b/i.test(body))
    return stop('captcha: a CAPTCHA is on the sign-in page; sign in yourself');

  const passwords = [...document.querySelectorAll('input[type=password]')].filter(visible);
  state.hasPassword = passwords.length > 0;
  const identityInputs = [...document.querySelectorAll(
    'input[type=email], input[autocomplete=username], input[autocomplete=email], input[type=text], input:not([type])'
  )].filter(e => visible(e) && (e.type === 'email' ||
    /^(?:username|email)$/i.test(e.getAttribute('autocomplete') || '') ||
    /e-?mail|user ?name|user ?id/i.test([e.name, e.id, e.getAttribute('aria-label'),
      e.getAttribute('placeholder'), ...(e.labels || [])].map(x => x?.innerText || x || '').join(' '))));
  state.hasIdentity = identityInputs.length > 0;
  if (!originOk(location.href)) return stop('login: this page is not an approved origin for credentials');
  if (!state.top) {
    let topOk = false;
    try { topOk = originOk(window.top.location.href); } catch { topOk = false; }
    if (!topOk) return stop('login: the sign-in form is in a cross-origin frame; sign in yourself');
  }
  // Field errors are often bound only through aria-errormessage (Eightfold).
  const fieldErrors = [...passwords, ...identityInputs]
    .map(e => e.getAttribute('aria-errormessage') && document.getElementById(e.getAttribute('aria-errormessage')))
    .filter(Boolean);
  const alertTexts = [...new Set([...document.querySelectorAll(
    '[role=alert], [aria-live=assertive], .error, .error-message, [data-automation-id=errorMessage]'
  ), ...fieldErrors])].filter(visible).map(text);
  const credentialAlerts = alertTexts.join(' ');
  state.rejected =
    /\b(?:wrong|incorrect|invalid) (?:e-?mail(?: address)?|user ?name|password|credentials|login)\b/i.test(credentialAlerts) ||
    /\b(?:password|credentials|sign[ -]?in|log[ -]?in)\b[^.!?]{0,80}\b(?:incorrect|invalid|not correct|do(?:es)? not match|failed|unsuccessful)\b/i.test(credentialAlerts) ||
    /\baccount (?:is|has been|might be) (?:temporarily )?locked\b/i.test(credentialAlerts);
  state.locked = /\baccount (?:is|has been) (?:temporarily )?locked\b/i.test(credentialAlerts);
  // A new-account password the employer's policy refused (create forms only).
  state.passwordRefused =
    /\bpassword\b[^.!?]{0,80}\b(?:must|should|requirements?|too (?:short|long|weak|common|simple)|not (?:strong|valid|allowed)|does not meet|invalid|cannot (?:contain|be|include)|at least|at most|no more than)\b/i.test(credentialAlerts) ||
    /\b(?:must|should) (?:contain|include|have|be)\b[^.!?]{0,80}\b(?:characters?|letters?|numbers?|digits?|symbols?)\b/i.test(credentialAlerts);
  const accountAlerts = alertTexts.filter(t => !/^(?:if|when)\b/i.test(t)).join(' ');
  if (/(?:account(?: with (?:this|your) email(?: address)?)? already exists|email (?:is )?already (?:registered|in use))/i.test(accountAlerts))
    state.accountStatus = 'exists';
  else if (/(?:no account (?:exists|found)|account (?:does not|doesn't) exist|could(?: not|n't) find (?:an? |your )?account|email (?:is not registered|not found)|user(?:name)? not found|(?:don['’]t|do not) recognize (?:this|your|that) e-?mail)/i.test(accountAlerts))
    state.accountStatus = 'missing';
  if (!passwords.length && !identityInputs.length)
    return stop('login: no visible account fields to fill; sign in yourself');
  if (passwords.length > 2 || passwords.some(e => e.disabled || e.readOnly))
    return stop('login: unrecognized or unavailable password boxes; sign in yourself');

  const anchors = passwords.length ? passwords : identityInputs;
  const forms = new Set(anchors.map(p => p.form));
  if (forms.size !== 1) return stop('login: unrecognized sign-in form; sign in yourself');
  const LOGIN = /^(?:sign in|sign-in|signin|log in|log-in|login|log on)$/i;
  const CREATE = /^(?:create account|create an account|create my account|create your account|sign up|sign-up|signup|register)$/i;
  const NEXT = /^(?:continue|next)$/i;
  const LOGIN_HEADING = /^(?:candidate (?:sign in|login|account|home)|(?:sign in|log in|login)(?: to (?:your account|continue|apply))?)$/i;
  const CREATE_HEADING = /^(?:create (?:an? |your |my )?account|sign up|register)(?: to (?:apply|continue))?$/i;
  const headings = [...document.querySelectorAll('h1, h2, [role=heading]')].filter(visible).map(text);
  const identityFirst = !passwords.length;
  const anchorForm = anchors[0].form;
  // A form-less component may wrap only its password/action row. Resolve the
  // shared identity scope too, without borrowing fields from another native
  // form or an unrelated semantic account region.
  const accountRegion = anchors[0].closest('section, [role=dialog], dialog, [role=form], main');
  const scopedIdentifiers = identityInputs.filter(e => anchorForm ? e.form === anchorForm :
    !e.form && (!accountRegion || accountRegion.contains(e)));
  const staged = identityFirst || !scopedIdentifiers.length;
  const controlsIn = box => [...new Set([
    ...box.querySelectorAll('button, input[type=submit], input[type=button], [role=button]'),
    ...(box.tagName === 'FORM' ? [...box.elements].filter(e =>
      e.matches('button, input[type=submit], input[type=button]')) : [])
  ])].filter(e => visible(e) && !e.closest('[aria-hidden=true]'));
  let box = anchors[0].form;
  if (!box) {
    box = anchors[0].parentElement;
    while (box && !(anchors.every(p => box.contains(p)) &&
                    scopedIdentifiers.every(e => box.contains(e)) &&
                    controlsIn(box).some(e => LOGIN.test(text(e)) || CREATE.test(text(e)) ||
                      (staged && NEXT.test(text(e))))))
      box = box.parentElement;
    if (!box) return stop('login: no account action next to the account fields; sign in yourself');
  }
  if (staged && !headings.some(t => LOGIN_HEADING.test(t) || CREATE_HEADING.test(t)))
    return stop('login: an email field alone does not identify an account form; continue yourself');

  els.box = box;
  const labelOf = e => {
    const ids = (e.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
    const byIds = ids.map(id => document.getElementById(id)?.innerText || '').join(' ');
    return clean([byIds, e.labels && e.labels[0] ? e.labels[0].innerText : ''].join(' '));
  };
  const describe = e => clean([e.name, e.id, e.getAttribute('autocomplete'), e.getAttribute('aria-label'),
                               e.getAttribute('placeholder'), e.getAttribute('data-automation-id'), labelOf(e)].join(' '));
  const confirmLike = e => /confirm|verify|re-?enter|repeat|retype|again/i.test(describe(e));
  const fields = box.tagName === 'FORM' ? [...box.elements] : [...box.querySelectorAll('input, textarea, select')];
  const inputs = fields.filter(e => e.matches('input, textarea, select')).filter(e => {
    const t = (e.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(t)) return false;
    if (t === 'checkbox' || t === 'radio') return visible(e) || visible(e.parentElement);
    return visible(e) && !e.disabled;
  });
  const identifiers = inputs.filter(e => e.tagName === 'INPUT' && e.type !== 'password' && (
    e.type === 'email' || /^(?:username|email)$/i.test(e.getAttribute('autocomplete') || '') ||
    (e.type === 'text' && /e-?mail|user ?name|user ?id|\blogin\b/i.test(describe(e)))));
  const remember = inputs.filter(e => e.type === 'checkbox' && !e.required &&
    e.getAttribute('aria-required') !== 'true' && !e.checked &&
    !/agree|accept|consent|terms|privacy/i.test(describe(e)) &&
    /remember|keep me (?:signed|logged) in|stay (?:signed|logged) in/i.test(describe(e)));
  const choiceSelector = 'input[type=checkbox], input[type=radio], [role=checkbox], [role=radio], [role=switch]';
  const choices = [...new Set([...inputs.filter(e => e.type === 'checkbox' || e.type === 'radio'),
    ...box.querySelectorAll(choiceSelector)])].filter(e => visible(e) || visible(e.parentElement));
  const others = inputs.filter(e => e.type !== 'password' && !identifiers.includes(e) &&
    !remember.includes(e) && !choices.includes(e));
  if (others.length) return stop('login: the sign-in form also asks for other details; continue yourself');
  if (identifiers.some(e => e.readOnly))
    return stop('login: the email or username box is read-only; continue yourself');

  const controls = controlsIn(box);
  // A component may put an accessible pointer target over its aria-hidden
  // native submit. Its generic "Submit" label is not application semantics:
  // only the directly associated, uniquely named native account action is.
  const nativeFor = e => {
    if (box.tagName !== 'FORM' || e.getAttribute('role') !== 'button' ||
        e.matches('button, input')) return null;
    const adjacent = [e.previousElementSibling, e.nextElementSibling,
      ...e.querySelectorAll('button, input[type=submit]')];
    const natives = [...new Set(adjacent)].filter(n => n && n.form === box &&
      n.type === 'submit' && n.getAttribute('aria-hidden') === 'true' &&
      (LOGIN.test(text(n)) || CREATE.test(text(n))));
    return natives.length === 1 ? natives[0] : null;
  };
  const semanticAction = e => {
    const native = nativeFor(e);
    const label = text(e);
    return native && (LOGIN.test(label) || CREATE.test(label) || /^submit$/i.test(label))
      ? native : e;
  };
  if (controls.some(e => {
    const native = nativeFor(e), label = text(e);
    return native && (LOGIN.test(label) || CREATE.test(label)) &&
      LOGIN.test(label) !== LOGIN.test(text(native));
  })) return stop('login: the account proxy and native action disagree; continue yourself');
  if (controls.some(e => /submit|apply|send application|finish/i.test(text(semanticAction(e)))))
    return stop('login: the sign-in form also holds application actions; continue yourself');
  const accountControls = controls.filter(e => LOGIN.test(text(semanticAction(e))) ||
    CREATE.test(text(semanticAction(e))) || (staged && NEXT.test(text(e))));
  // Native submits establish the credential action; type=button controls and
  // links alongside them can switch flows without becoming a second submit.
  const submits = accountControls.filter(e => e.form === box && e.type === 'submit');
  let candidates = submits.length ? submits : accountControls;
  // Identity-first components (Eightfold) offer "Continue" beside a non-submit
  // "Create an account" flow switch. Continue is the single email action; the
  // switch is never a second credential submit.
  if (candidates.length > 1 && staged) {
    const nexts = candidates.filter(e => NEXT.test(text(e)));
    if (nexts.length === 1 && candidates.every(e => e === nexts[0] || e.type !== 'submit' || !e.form))
      candidates = nexts;
  }
  if (candidates.length !== 1) return stop('login: no single Sign in or Create account action; sign in yourself');
  const button = candidates[0];
  const nativeAction = nativeFor(button);
  const action = semanticAction(button);
  state.mode = CREATE.test(text(action)) ? 'create' :
    staged && NEXT.test(text(action)) &&
      headings.some(t => CREATE_HEADING.test(t))
      ? 'create' : 'login';
  state.stage = identityFirst ? 'identity' : 'password';
  if (state.mode === 'create' && state.passwordRefused && !state.locked) state.rejected = false;
  else state.passwordRefused = false;
  if (state.rejected) return stop(state.locked ? '""" + _ACCOUNT_LOCKED + r"""' : '""" + _SIGN_IN_REJECTED + r"""');
  if (state.mode === 'create' && state.accountStatus === 'exists')
    return stop('""" + _EMAIL_IN_USE + r"""');
  if (/submit|apply/i.test(button.getAttribute('aria-label') || '') &&
      !(nativeAction && /^submit$/i.test(text(button))))
    return stop('login: the account button also holds application actions; continue yourself');
  if (choices.some(e => !remember.includes(e)))
    state.submitBlocker = 'consent: credential fields are prepared; review the account checkbox or choices and submit yourself';
  const agreement = /\bby (?:clicking|continuing|creating|signing (?:up|in)|registering|proceeding|selecting|logging in)\b[^.\n]{0,200}\b(?:agree|accept|consent|terms|privacy)/i;
  const described = [box, button, ...inputs].flatMap(e =>
    (e.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean)
      .map(id => document.getElementById(id)).filter(visible)).map(text).join(' ');
  const region = box.closest('[role=dialog], dialog, section') || box;
  const regionChoices = [...region.querySelectorAll(choiceSelector)]
    .filter(e => visible(e) || visible(e.parentElement));
  if (regionChoices.some(e => !remember.includes(e)))
    state.submitBlocker = 'consent: credential fields are prepared; review the account checkbox or choices and submit yourself';
  // Elsewhere only a clause naming this action, or an unresolved "continuing"
  // agreement, applies. "By clicking Accept All Cookies" is not account consent.
  const actionWords = (state.mode === 'create' ? CREATE : LOGIN).source.slice(1, -1);
  const actionVerb = state.mode === 'create'
    ? 'creating (?:an? |your )?(?:external )?account|signing up|registering'
    : 'signing in|logging in';
  const externalAgreement = new RegExp(
    String.raw`\bby (?:(?:clicking|selecting) [“"' ]*${actionWords}|${actionVerb})\b[^.]{0,200}\b(?:agree|accept|consent|terms|privacy)`, 'i');
  if (agreement.test(text(region) + ' ' + described) || externalAgreement.test(body) ||
      // "By continuing to use this site, closing this banner…" is cookie/site-use
      // notice text, not an agreement attached to the account action.
      [...body.matchAll(/\bby (?:continuing|proceeding)\b[^.]{0,200}\b(?:agree|accept|consent|terms|privacy)/gi)]
        .some(m => !/\b(?:cookies?|this (?:web)?site|banner|browsing)\b/i.test(m[0])) ||
      /agree|accept|consent|terms|privacy/i.test(button.getAttribute('aria-label') || ''))
    state.submitBlocker = 'consent: credential fields are prepared; signing in or creating this account accepts terms, so review them and submit yourself';

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
  else if (!identifiers.length && passwords.length) {
    // A password-only step must visibly name exactly the approved identity.
    // Hidden inputs, a previous form locator and a remembered email are not proof.
    const identities = [...new Set(body.match(/[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) || [])];
    if (identities.length !== 1 || (email !== null && identities[0] !== email))
      return stop('login: the password step does not uniquely show the approved account; continue yourself');
    const hiddenIds = [...box.querySelectorAll('input[type=hidden]')].filter(e =>
      /^(?:email|username|user_?id)$/i.test(e.name));
    if (email !== null && hiddenIds.some(e => e.value && e.value !== email))
      return stop('login: the password step holds a different account identity; continue yourself');
  } else return stop('login: no single email or username box; sign in yourself');

  const form = button.form || button.closest('form') || (box.tagName === 'FORM' ? box : null);
  if (form) {
    const methodControl = nativeAction || button;
    const method = methodControl.hasAttribute('formmethod')
      ? clean(methodControl.getAttribute('formmethod')).toLowerCase() : form.method.toLowerCase();
    if (method !== 'post') {
      // Component forms can omit method while a non-native accessibility proxy
      // delegates to a hidden submit. Make the native fallback POST before any
      // credentials are populated; never permit an explicit GET or override.
      const componentProxy = !!nativeAction;
      if (form.hasAttribute('method') || button.hasAttribute('formmethod') ||
          methodControl.hasAttribute('formmethod') || !componentProxy)
        return stop('login: the account form must explicitly use POST; sign in yourself');
      state.preparePost = true;
    }
    state.action = methodControl.hasAttribute('formaction') ? methodControl.formAction : form.action;
    if (button.hasAttribute('formaction') && !originOk(button.formAction))
      return stop('login: the form sends credentials to an unapproved origin');
    if (button.hasAttribute('formmethod') &&
        clean(button.getAttribute('formmethod')).toLowerCase() !== 'post')
      return stop('login: an account submit override is unsafe; sign in yourself');
    if (!originOk(state.action)) return stop('login: the form sends credentials to an unapproved origin');
    // Accessible component actions can represent aria-hidden native submits.
    // Validate every override before choosing the native click target.
    for (const submit of [...form.elements].filter(e => e.type === 'submit')) {
      if (/submit|apply|send application|finish/i.test(text(submit)) ||
          /submit|apply/i.test(submit.getAttribute('aria-label') || ''))
        return stop('login: the sign-in form also holds application actions; continue yourself');
      const override = clean(submit.getAttribute('formmethod')).toLowerCase();
      if ((submit.hasAttribute('formmethod') && override !== 'post') ||
          (submit.hasAttribute('formaction') && !originOk(submit.formAction)))
        return stop('login: an account submit override is unsafe; sign in yourself');
    }
    els.form = form;
  }

  if (id1) els.email = id1;
  if (passwords.length) els.password = passwords[0];
  state.roles = [...(id1 ? ['email'] : []), ...(passwords.length ? ['password'] : [])];
  if (id2) { els.emailConfirm = id2; state.roles.splice(1, 0, 'emailConfirm'); }
  if (password2) { els.passwordConfirm = password2; state.roles.push('passwordConfirm'); }
  els.button = button;
  if (nativeAction) {
    const nativeActions = [...form.elements].filter(e => e.type === 'submit');
    if (nativeActions.length !== 1)
      return stop('login: multiple native actions behind the account control; continue yourself');
    els.nativeAction = nativeAction;
  }
  // Values are compared only once Python has approved this frame and passes them in.
  for (const role of email === null ? [] : state.roles) {
    const value = role.startsWith('email') ? email : password;
    if (els[role].value && els[role].value !== value)
      return stop('login: the sign-in form already holds other values; continue yourself');
  }
  const keys = globalThis.kittyAuthKeys ??= new WeakMap();
  const key = e => {
    if (!keys.has(e)) keys.set(e, globalThis.kittyAuthCounter = (globalThis.kittyAuthCounter || 0) + 1);
    return keys.get(e);
  };
  state.signature = [performance.timeOrigin, location.href, state.action, state.mode, state.stage,
    key(box), key(button), text(button), text(action),
    ...state.roles.map(r => [r, key(els[r]), els[r].name, els[r].id, els[r].type].join(':'))].join('|');
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
  if (state.preparePost) els.form.method = 'post';
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

#: Empty the refused password boxes of a still-displayed create form (no value is
#: read back), so the replacement password is written into empty fields.
_CLEAR_PASSWORDS_JS = r"""
(approved) => {
  const {state, els} = (""" + _ANALYZE + r""")(approved, null, null);
  if (state.blocker || state.mode !== 'create' || !state.passwordRefused) return false;
  for (const role of ['password', 'passwordConfirm']) {
    const el = els[role];
    if (!el || !el.value) continue;
    el.focus();
    if (typeof el.select === 'function') el.select();
    if (!document.execCommand('delete') || el.value) {
      el.value = '';
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
    }
    el.blur();
    if (el.value) return false;
  }
  return true;
}
"""

# Only required, routine account terms are covered by creation or explicit terms
# approval. Factual attestations, marketing, waivers and certifications are not.
_ACCOUNT_TERMS_JS = r"""
(state, els) => {""" + _DOM_HELPERS_JS + r"""
  const denied = {allowed: false, terms: []};
  if (state.blocker || !['create', 'login'].includes(state.mode) || !els.box) return denied;
  const region = closest(els.box, '[role=dialog], dialog, section') || els.box;
  const legal = /\b(?:terms(?: and conditions| of use| of service)?|privacy (?:policy|notice|statement)|data protection notice)\b/i;
  const excluded = /\b(?:certif(?:y|ication)|attest|truthful|accurate|background check|credit check|criminal|drug test|marketing|newsletter|promotional|future opportunities|talent community|arbitration|waiv(?:e|er)|non.?compete|biometric|sell|third.party)\b/i;
  const optionalOptIn = /\b(?:marketing|newsletter|promotional|future opportunities|talent community)\b/i;
  const described = e => (e.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean)
    .map(id => reference(e, id)).filter(Boolean).map(composedText).join(' ');
  const shown = e => e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const choices = queryAll('input[type=checkbox], input[type=radio], [role=checkbox], [role=radio], [role=switch]', region)
    .filter(e => shown(e) || shown(parentOf(e)));
  // Some account forms label the one required checkbox only "I agree" and
  // explicitly explain "By selecting the checkbox ... Terms ..." nearby.
  // That clause binds only a single choice, never unrelated or extra consent.
  const explanations = queryAll('p, [role=note]', region).map(composedText)
    .filter(text => /\bby (?:selecting|checking) (?:the |this )?checkbox\b/i.test(text));
  const checkboxClause = explanations.length === 1 ? explanations[0].replace(/\s+/g, ' ') : '';
  const terms = [];
  for (const e of choices) {
    const label = (e.getAttribute('aria-label') ||
      (e.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => reference(e, id)?.textContent || '').join(' ') ||
      labelsOf(e).map(composedText).join(' ') || composedText(e)).replace(/\s+/g, ' ').trim();
    const checked = e.matches('input') ? e.checked : e.getAttribute('aria-checked') === 'true';
    const required = e.required || e.getAttribute('aria-required') === 'true' ||
      closest(e, '[role=group], [role=radiogroup]')?.getAttribute('aria-required') === 'true' ||
      (/^https:\/\/[^/]+\.myworkdayjobs\.com\//i.test(location.href) &&
       e.getAttribute('data-automation-id') === 'createAccountCheckbox') ||
      /[*✱]\s*$/.test(label);
    const choiceText = [label, described(e), ...labelsOf(e).map(described)].join(' ');
    // Expanded privacy notices describe many processing purposes; they are not
    // separate opt-ins. Classify this choice and its explicit descriptions only.
    if (!required && !checked && optionalOptIn.test(choiceText) &&
        e.matches('input[type=checkbox], [role=checkbox]') && fieldSafe(e)) continue;
    if (!required && !checked && /remember|keep me signed in/i.test(label)) continue;
    const explainedAgreement = choices.length === 1 && required && /^i agree$/i.test(label) &&
      legal.test(checkboxClause) && !excluded.test(checkboxClause);
    if (!e.matches('input[type=checkbox], [role=checkbox]') || !fieldSafe(e) ||
        (!legal.test(label) && !explainedAgreement) || excluded.test(choiceText) ||
        (!checked && !required)) return denied;
    terms.push(e);
  }
  // Cookie/site-use notices ("by continuing to use this site…") are not account terms.
  const clauses = ((document.body?.innerText || '').match(
    /\bby (?:creating (?:an? |your |this )?(?:external )?account|clicking [“"' ]*(?:create (?:an? )?account|sign[ -]?in|log[ -]?in)|signing (?:up|in)|logging in|registering|continuing|proceeding)[^.]{0,600}(?:\.|$)/gi
  ) || []).filter(text => !/^by (?:continuing|proceeding)\b/i.test(text) ||
    !/\b(?:cookies?|this (?:web)?site|banner|browsing)\b/i.test(text));
  if (clauses.some(text => !legal.test(text) || excluded.test(text))) return denied;
  if (state.submitBlocker && !terms.length && !clauses.length && !choices.length) return denied;
  return {allowed: true, terms};
}
"""

_TERMS_ANALYZE_JS = r"""
(approved, email, password, signature) => {
  const {state, els} = (""" + _ANALYZE + r""")(approved, email, password);
  if (state.blocker || state.signature !== signature ||
      state.roles.some(r => els[r].value !== (r.startsWith('email') ? email : password)))
    return {allowed: false, terms: []};
  return (""" + _ACCOUNT_TERMS_JS + r""")(state, els);
}
"""

_TERMS_STATUS_JS = r"""
(...args) => {
  const {allowed, terms} = (""" + _TERMS_ANALYZE_JS + r""")(...args);
  return {allowed, pending: terms.filter(e =>
    e.matches('input') ? !e.checked : e.getAttribute('aria-checked') !== 'true').length};
}
"""

_TERMS_TARGET_JS = r"""
(...args) => {
  const {allowed, terms} = (""" + _TERMS_ANALYZE_JS + r""")(...args);
  if (!allowed) return null;
  const e = terms.find(e => e.matches('input') ? !e.checked : e.getAttribute('aria-checked') !== 'true');
  if (!e) return null;
  const keys = globalThis.kittyKeys ??= new WeakMap();
  const byKey = globalThis.kittyByKey ??= new Map();
  if (!keys.has(e)) {
    globalThis.kittyCounter = (globalThis.kittyCounter ?? 0) + 1;
    keys.set(e, 'e' + globalThis.kittyCounter);
  }
  const key = keys.get(e);
  byKey.set(key, e);
  return (""" + _CHOICE_TARGET_JS + r""")(key);
}
"""

#: Resolve the account action only while its form and values still match.
_ACCOUNT_TARGET_JS = r"""
(approved, email, password, signature, authorizeTerms = false) => {
  const {state, els} = (""" + _ANALYZE + r""")(approved, email, password);
  if (state.blocker) return state.blocker;
  if (state.submitBlocker) {
    const proof = authorizeTerms && (""" + _TERMS_STATUS_JS + r""")(approved, email, password, signature);
    if (!proof || !proof.allowed || proof.pending) return state.submitBlocker;
  }
  if (state.signature !== signature) return false;
  if (state.roles.some(r => els[r].value !== (r.startsWith('email') ? email : password))) return false;
  if ([els.button, els.nativeAction].filter(Boolean).some(e =>
      e.disabled || e.getAttribute('aria-disabled') === 'true'))
    return 'login: credential fields are prepared; the account action is disabled, so continue yourself';
  if (state.preparePost) els.form.method = 'post';
  if (els.nativeAction) {
    // A native submit may be visible in layout but covered by its accessibility
    // proxy. Never point through that overlay: use the associated real target.
    els.nativeAction.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
    const r = els.nativeAction.getClientRects()[0];
    if (r?.width && r.height) {
      const x = (Math.max(0, r.left) + Math.min(innerWidth, r.right)) / 2;
      const y = (Math.max(0, r.top) + Math.min(innerHeight, r.bottom)) / 2;
      const hit = document.elementFromPoint(x, y);
      if (hit && (hit === els.nativeAction || els.nativeAction.contains(hit))) return els.nativeAction;
    }
  }
  return els.button;
}
"""

#: Enter only the explicitly chosen flow, without submitting any form or credentials.
#: Re-analyze before resolving any native navigation target, including email entry.
_ENTRY_TARGET_JS = r"""
(approved, mode) => {
  const {state, els} = (""" + _ANALYZE + r""")(approved, null, null);
  if (!state.top || !approved.includes(location.origin) || location.protocol !== 'https:') return false;
  if (state.rejected) return '""" + _SIGN_IN_REJECTED + r"""';
  if (state.blocker && (/^(?:captcha|verification|consent):/.test(state.blocker) ||
      state.blocker === '""" + _SIGN_IN_REJECTED + r"""')) return state.blocker;
  const body = document.body?.innerText || '';
  const entryTerms = body.match(/\bby (?:creating (?:an? |your )?(?:external )?account|signing (?:up|in)|logging in|registering|continuing)\b[^.]{0,200}\b(?:agree|accept|consent|terms|privacy)/gi) || [];
  if (!state.hasPassword && entryTerms.some(m => !/^by continuing\b/i.test(m) ||
      !/\b(?:cookies?|this (?:web)?site|banner|browsing)\b/i.test(m)))
    return 'consent: review the account-entry terms and open the account form yourself';
  const visible = e => e.getClientRects().length > 0 &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const text = e => (e.innerText || e.value || '').replace(/\s+/g, ' ').trim();
  if (!state.hasPassword && [...document.querySelectorAll('input[type=checkbox], input[type=radio]')]
      .some(e => (visible(e) || (e.parentElement && visible(e.parentElement))) &&
        !/remember|keep me (?:signed|logged) in/i.test(e.parentElement?.innerText || '')))
    return 'consent: decide the account-page consent yourself';
  const controls = [...document.querySelectorAll('a, button, input[type=submit], input[type=button], [role=button]')]
    .filter(visible);
  if (controls.some(e => /submit|apply|send application|finish/i.test(text(e)))) return false;
  const login = /^(?:(?:already (?:have an account|registered)\??|existing (?:user|account)\??)\s*[-:–]?\s*)?(?:sign in|sign-in|signin|log in|log-in|login|log on)(?: instead)?$/i;
  const create = /^(?:(?:don['’]?t have an account\??|new (?:user|here)\??)\s*[-:–]?\s*)?(?:create account|create an account|create my account|create your account|sign up|sign-up|signup|register)$/i;
  const eligible = controls.filter(e => !e.disabled && e.getAttribute('aria-disabled') !== 'true');
  const navigations = eligible.filter(e => e.tagName === 'A' ? e.hasAttribute('href')
    : (e.form || e.closest('form')) ? e.type === 'button' : (
      e.tagName === 'BUTTON' || e.type === 'button' || e.getAttribute('role') === 'button'));
  const local = els.box?.parentElement;
  const localEntries = local ? navigations.filter(e => local.contains(e) &&
    (mode === 'create' ? create.test(text(e)) : login.test(text(e)))) : [];
  const entries = localEntries.length ? localEntries : navigations;
  const emailEntries = entries.filter(e => /^(?:sign in|log in|continue) with (?:e-?mail)$/i.test(text(e)));
  const modalEmails = emailEntries.filter(e => e.closest('[role=dialog], [aria-modal=true]'));
  const reachableEmails = emailEntries.filter(e => {
    const r = e.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return hit && (hit === e || e.contains(hit));
  });
  const emailEntry = modalEmails.length === 1 ? modalEmails[0] :
    reachableEmails.length === 1 ? reachableEmails[0] :
    emailEntries.length === 1 ? emailEntries[0] : null;
  const useEmailEntry = !state.hasPassword && mode !== 'create' && emailEntry &&
    ![...document.querySelectorAll('input[type=email], input[type=text], input:not([type]), textarea')]
      .some(visible);
  const logins = entries.filter(e => login.test(text(e)));
  const creates = entries.filter(e => create.test(text(e)));
  const candidates = useEmailEntry ? [emailEntry] :
    mode === 'auto' ? (logins.length ? logins : creates) : mode === 'create' ? creates : logins;
  if (candidates.length !== 1) return false;
  const el = candidates[0];
  if (/agree|accept|consent|terms|privacy|submit|apply/i.test(el.getAttribute('aria-label') || '')) return false;
  if (el.tagName === 'A') {
    if (!el.hasAttribute('href') || el.hasAttribute('download') ||
        (el.target && el.target !== '_self')) return false;
    const url = new URL(el.href, location.href);
    if (url.protocol !== 'https:' || !approved.includes(url.origin) || url.username || url.password) return false;
  } else if ((el.form || el.closest('form')) ? el.type !== 'button' : !(
      el.tagName === 'BUTTON' || el.type === 'button' || el.getAttribute('role') === 'button')) return false;
  return el;
}
"""

_ENTRY_RECEIPT_JS = r"""
(approved, mode) => {
  const target = (""" + _ENTRY_TARGET_JS + r""")(approved, mode);
  if (!target || target.nodeType !== 1) return null;
  return [location.href, target.tagName, target.getAttribute('href') || '',
    (target.innerText || target.value || '').replace(/\s+/g, ' ').trim()].join('|');
}
"""

_CHANGED = "login: the sign-in form changed while it was being filled; sign in yourself"
_FAILED = "login: the browser refused a sign-in step; sign in yourself"


@dataclass(frozen=True)
class AuthResult:
    """Credential request attempted, or Google returned; filled contains credential roles only.

    `modes` lists each account action submitted, in order (``create`` / ``login``),
    so the registry can record a new account or a sign-in try.
    """

    acted: bool = False
    blocker: str | None = None
    filled: tuple[str, ...] = ()
    modes: tuple[str, ...] = ()


def _guarded_click(
    frame: Frame, target_js: str, *args: object, intent: str, fallback: str = _CHANGED,
) -> str | None:
    """Resolve a guarded element, then use the driver's real pointer interaction."""
    target = frame.node("""(...args) => {
      const target = (""" + target_js + r""")(...args);
      if (!target || target.nodeType !== 1) return null;
      target.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'});
      return target;
    }""", *args)
    if target is None:
        reason = frame.call("""(...args) => {
          const target = (""" + target_js + r""")(...args);
          return typeof target === 'string' ? target : false;
        }""", *args)
        return reason if isinstance(reason, str) else fallback
    frame.click(target, intent=intent)
    return None


# Not configurable: tests may monkeypatch this constant to their isolated HTTPS fixture.
_GOOGLE_ORIGIN = "https://accounts.google.com"
_GOOGLE_HANDOFF = (
    " Complete Google sign-in with your reviewed Profile email manually in this SAME "
    "dedicated rika! Firefox (not everyday Chrome), then return to this employer "
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
  const kittyActivate = (""" + ACTIVATE_JS + r""");
  kittyActivate(el, 'credential', () => el.click());
  return true;
}
"""


def google_session(page: Tab, policy: AuthenticationSettings, email: str) -> AuthResult:
    """Reuse one reviewed signed-in Google identity; never type or submit credentials.

    Only this employer tab and a newly opened context explicitly naming it as
    originalOpener are eligible. Unknown popup association fails closed. A lost
    navigation reply is not success or permission to repeat a click; only a
    recognized, loaded employer document can complete the bounded wait.
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
        try:
            employer_frame = page.main     # its in-page credential click gets L2's network token
            with employer_frame.granting(CREDENTIAL):
                clicked = employer_frame.call(_GOOGLE_CLICK, [employer], _GOOGLE_ORIGIN, email, False)
        except BidiError:
            # A same-tab navigation can discard the realm before its click
            # reply arrives. Do not repeat it; observe where this flow returns.
            pass
        else:
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
            # getTree can report a navigation's destination while scripts still
            # see the previous document. Check the live document before and
            # after inspection; neither a provisional URL nor a detached realm
            # proves that the employer application is available for autofill.
            try:
                original = page.main
                original_url = original.call("() => location.href")
                if _authentication_origin(original_url) == employer:
                    from kitty.core.apply.preflight import inspect_page
                    snapshot = inspect_page(page)
                    if (not snapshot.blocker and snapshot.has_fields and
                            (snapshot.boundary or snapshot.step) and
                            policy.approves(snapshot.url) and
                            _authentication_origin(snapshot.url) != _GOOGLE_ORIGIN and
                            original.call(
                                "(url) => location.href === url && document.readyState === 'complete'",
                                original_url,
                            )):
                        return AuthResult(acted=True)
                active_url = active.main.call("() => location.href")
            except BidiError:
                # Read-only observations may race either leg of the redirect.
                # Keep the deadline and do not turn an unreadable page into success.
                time.sleep(0.2)
                continue
            origin = _authentication_origin(active_url)
            if origin == _GOOGLE_ORIGIN:
                if not selected:
                    settle(active, timeout_ms=3000)
                    selected = True
                    try:
                        chooser = active.main
                        with chooser.granting(CREDENTIAL):
                            clicked = chooser.call(_GOOGLE_CLICK, [employer], _GOOGLE_ORIGIN, email, True)
                    except BidiError:
                        # The choice may already have navigated. Never select
                        # again, even if its acknowledgement was lost.
                        pass
                    else:
                        if clicked is not True:
                            return handoff(str(clicked))
            elif origin not in (employer, None) or (
                origin is None and active_url != "about:blank"
            ):
                return handoff("sign-in redirected to an unapproved origin")
            time.sleep(0.2)
        return handoff("Google has not returned to a recognized employer application; finish any extra consent or security step yourself")
    except (BidiError, UnsafeAction):
        return handoff("the browser could not safely identify or complete this session flow")


#: Sign-in and account receipts wait on page evidence (navigation, an error banner,
#: the next screen) this long; a navigation still pending at the end gets one more
#: wait (slow redirect). Credentials are never submitted again by the wait.
_AUTH_WAIT_SECONDS = 45.0
#: A disabled account action may enable itself after validation (Workday ~3 s).
_ENABLE_WAIT_SECONDS = 15.0
_NO_PROGRESS = (
    "login: the account action did not advance to a verified next step; "
    "continue in the retained tab without repeating credentials"
)


def _auth_context(
    page: Tab, approved: list[str], *,
    application_binding: tuple[str, str] | None = None,
) -> tuple[Frame | None, dict | None, str | None]:
    """Read current account forms without passing applicant credentials to a realm."""
    frames = page.frames()
    if not frames:
        raise BidiError("account document navigation is pending")
    top = frames[0]
    live = top.call("() => ({url: location.href, loading: document.readyState === 'loading'})")
    # getTree and the live realm can straddle a navigation. An empty/blank
    # provisional URL is neither an approved document nor an unsafe-origin
    # receipt. Read-only callers may wait; credential callers still fail closed.
    provisional = ("", "about:blank")
    if any(url not in provisional and _authentication_origin(url) not in approved
           for url in (top.url, live["url"])):
        return None, None, "login: this page is not an approved origin for credentials"
    if (top.url in provisional or live["url"] in provisional or
            top.url != live["url"] or live["loading"]):
        raise BidiError("account document navigation is pending")
    if application_binding is not None and any(
        workday_application_binding(url) != application_binding for url in (top.url, live["url"])
    ):
        return None, None, "login: application ownership changed; review a new preparation plan"
    # An invisible reCAPTCHA badge anchor (size=invisible) is not a challenge; a
    # served challenge appears as its own bframe/iframe and still stops here.
    if any(_CHALLENGE_FRAMES.search(frame.url) and not _INVISIBLE_BADGE.search(frame.url)
           for frame in frames[1:]):
        return None, None, "captcha: a CAPTCHA is on the sign-in page; sign in yourself"
    found = []
    for frame in form_frames(page):
        state = frame.call(_SNAPSHOT_JS, approved, None, None)
        # form_frames takes a fresh tree; it can race the navigation after the
        # first top-document observation too. Bind its account receipt to the
        # actual document instead of a provisional destination.
        if state["top"]:
            if any(url not in provisional and _authentication_origin(url) not in approved
                   for url in (frame.url, state["url"])):
                return frame, state, "login: this page is not an approved origin for credentials"
            if (frame.url in provisional or state["url"] in provisional or
                    frame.url != state["url"]):
                raise BidiError("account document navigation is pending")
            if application_binding is not None and any(
                workday_application_binding(url) != application_binding
                for url in (frame.url, state["url"])
            ):
                return frame, state, "login: application ownership changed; review a new preparation plan"
        blocker = state["blocker"]
        if state["rejected"]:
            return frame, state, _ACCOUNT_LOCKED if state.get("locked") else _SIGN_IN_REJECTED
        if blocker and (blocker in (_SIGN_IN_REJECTED, _ACCOUNT_LOCKED) or
                        blocker.startswith(("captcha:", "verification:", "consent:"))):
            return frame, state, blocker
        if state["hasPassword"] or state["stage"] or state["accountStatus"]:
            found.append((frame, state))
    if len(found) > 1:
        return None, None, "login: multiple account forms; choose one yourself"
    if found:
        frame, state = found[0]
        return frame, state, None
    return None, None, None


def authentication_pending(page: Tab) -> bool:
    """Recognize account stages using authenticate's analyzer, without loading secrets.

    Ordinary applicant/contact/newsletter email fields are not account stages:
    identity-only forms need an explicit account heading and a unique account or
    Next/Continue action. Unsafe recognized account forms still need a handoff.
    This is detection, not origin approval or permission to send credentials.
    """
    try:
        origin = _authentication_origin(page.main.url)
        if origin is None or origin == _GOOGLE_ORIGIN:
            return False
        _, state, _ = _auth_context(page, [origin])
        return bool(state and (state["stage"] or state["hasPassword"]))
    except (BidiError, UnsafeAction):
        return False


def _wait_auth_receipt(
    page: Tab, approved: list[str], before: dict | None, *,
    entry_mode: str | None = None, entry_receipt: str | None = None,
    application_binding: tuple[str, str] | None = None,
) -> tuple[Frame | None, dict | None, str | None]:
    """Wait for semantic account progress or a loaded application, never DOM churn.

    After credential submission, replacing an unchanged form is not progress.
    Observe a new stage/mode, explicit account-status receipt, a new password-policy
    refusal, or a recognized application boundary. Read-only navigation races never
    repeat the action; a navigation still pending when the wait ends gets one more.
    """
    from kitty.core.apply.preflight import inspect_page

    deadline = time.monotonic() + _AUTH_WAIT_SECONDS
    pending = extended = False
    while True:
        if time.monotonic() >= deadline:
            if not pending or extended:
                return None, None, _NO_PROGRESS
            extended = True
            deadline = time.monotonic() + _AUTH_WAIT_SECONDS
        try:
            frame, state, blocker = _auth_context(
                page, approved, application_binding=application_binding,
            )
            pending = False
            if blocker:
                return frame, state, blocker
            if state and state.get("passwordRefused") and (
                not before or not before.get("passwordRefused") or state["signature"] != before["signature"]
            ):
                return frame, state, _PASSWORD_REFUSED
            if state:
                changed = not before or state["signature"] != before["signature"]
                semantic = not before or any(
                    state[key] != before[key] for key in ("stage", "mode", "accountStatus")
                )
                if changed and (entry_mode is not None or semantic):
                    return frame, state, None
                if state["accountStatus"] and (
                    not before or state["accountStatus"] != before["accountStatus"]
                ):
                    return frame, state, None
            if not state:
                original = page.main
                url = original.call("() => location.href")
                snapshot = inspect_page(page)
                if snapshot.blocker and snapshot.blocker.startswith(
                    ("captcha:", "verification:", "consent:")
                ):
                    return None, None, snapshot.blocker
                if (not snapshot.blocker and snapshot.has_fields and
                        (snapshot.boundary or snapshot.step) and
                        _authentication_origin(url) in approved and snapshot.url == url and
                        original.call(
                            "(url) => location.href === url && document.readyState === 'complete'",
                            url,
                        )):
                    return None, None, None
                if entry_mode is not None:
                    receipt = original.call(_ENTRY_RECEIPT_JS, approved, entry_mode)
                    if receipt and receipt != entry_receipt:
                        return None, None, None
        except BidiError:
            # Only observations are retried; a discarded click reply is not an
            # authorization to submit the same password a second time.
            pending = True
        time.sleep(0.15)


def authenticate(
    page: Tab, policy: AuthenticationSettings, credentials: ApplicationCredentials, *,
    application_url: str | None = None, memory: AccountMemory | None = None,
    refused_ok: bool = False,
) -> AuthResult:
    """Run bounded account stages, retaining a supplied Workday posting binding.

    The caller supplies its reviewed job URL, never the current login redirect.
    Binding checks supplement approved origins and existing one-shot credential
    guards; accepting a login redirect never permits another account submission.
    Tenant credentials (``credentials.origin``) are typed only on that origin.
    `memory` holds durable account facts (kitty.core.apply.accounts.Memory).
    `refused_ok` lets a caller that replaced a refused new-account password fill
    the still-displayed create form once more with the new one.
    """
    if policy.account_mode == "google":
        return AuthResult(blocker="login: Google session reuse never accepts employer credentials")
    if not policy.enabled:
        return AuthResult(blocker="login: login automation is off (apply.yaml authentication.enabled)")
    binding = credential_binding(credentials)
    if policy._credential_binding is not None and not compare_digest(
        policy._credential_binding, binding,
    ):
        return AuthResult(blocker="login: saved credentials changed after review; review the plan again")
    page.bidi.protect_secrets(credentials.email, credentials.password)
    approved = [origin for origin in policy.approved_origins if origin != _GOOGLE_ORIGIN]
    application_binding = workday_application_binding(application_url) if application_url else None
    secrets = (credentials.email, credentials.password)
    filled: list[str] = []
    modes: list[str] = []
    acted = False
    desired = policy.account_mode
    submitted: set[tuple[str, str]] = set()
    entries: set[tuple[str, str | None]] = set()
    existing_account = False
    memory = memory if memory is not None else _CallMemory()
    origin: str | None = None
    wrong_tenant = "login: these account credentials belong to another employer origin; continue yourself"

    def result(blocker: str | None = None) -> AuthResult:
        if blocker in (_SIGN_IN_REJECTED, _ACCOUNT_LOCKED):
            rejected_origin = origin
            try:
                rejected_origin = _authentication_origin(page.main.url)
            except (BidiError, UnsafeAction):
                pass
            if rejected_origin in approved:
                memory.reject(rejected_origin, credentials, locked=blocker == _ACCOUNT_LOCKED)
        return AuthResult(acted=acted, blocker=blocker, filled=tuple(filled), modes=tuple(modes))

    try:
        origin = _authentication_origin(page.main.url)
        if origin in policy.manual_account_origins:
            return result("login: this account origin is configured for manual authentication; continue yourself")
        if credentials.origin is not None and origin != credentials.origin:
            return result(wrong_tenant)
        if memory.rejected(origin, credentials):
            return result(_SIGN_IN_REJECTED)
        if origin in policy.existing_account_origins or memory.exists(origin, credentials.email):
            existing_account = True
            desired = "login"
            policy = policy.model_copy(update={"account_mode": "login", "allow_account_creation": False})
        oracle = oracle_email_gate(page, policy, credentials)
        if oracle is not None:
            return AuthResult(acted=oracle.acted, blocker=oracle.blocker, filled=oracle.filled)
        for _ in range(8):
            origin = _authentication_origin(page.main.url)
            if origin in policy.manual_account_origins:
                return result("login: this account origin is configured for manual authentication; continue yourself")
            if memory.rejected(origin, credentials):
                return result(_SIGN_IN_REJECTED)
            # Never retain a frame/form locator across navigation or identity
            # transitions. The writer also binds each field to this DOM instance.
            frame, state, blocker = _auth_context(
                page, approved, application_binding=application_binding,
            )
            document_origin = _authentication_origin(state["url"]) if state else origin
            if document_origin in policy.manual_account_origins:
                return result("login: this account origin is configured for manual authentication; continue yourself")
            if credentials.origin is not None and credentials.origin != origin or (
                    credentials.origin is not None and credentials.origin != document_origin):
                return result(wrong_tenant)
            if memory.rejected(document_origin, credentials):
                return result(_SIGN_IN_REJECTED)
            if (origin in policy.existing_account_origins or
                    document_origin in policy.existing_account_origins or
                    memory.exists(origin, credentials.email) or
                    memory.exists(document_origin, credentials.email)):
                existing_account = True
                desired = "login"
            if blocker:
                return result(blocker)
            if state and state.get("passwordRefused") and not refused_ok:
                return result(_PASSWORD_REFUSED)
            if state and state["accountStatus"] == "exists":
                existing_account = True
                if document_origin in approved:
                    memory.mark_exists(document_origin, credentials.email)
                if policy.account_mode == "auto":
                    desired = "login"
                elif desired == "create":
                    return result(_EMAIL_IN_USE)
            elif state and state["accountStatus"] == "missing" and policy.account_mode == "auto":
                if existing_account or ("create", "password") in submitted:
                    return result("login: account-state evidence conflicts; do not create another account")
                desired = "create"
            if desired == "create" and not policy.allow_account_creation:
                return result("login: this site requires an account; account creation is off "
                              "(apply.yaml authentication.allow_account_creation)")
            if (state and state["blocker"] and not state["accountStatus"] and
                    (desired in ("auto", "recognized") or not state["mode"] or
                     state["mode"] == desired)):
                return result(state["blocker"])
            chosen = state and state["stage"] and (
                desired in ("auto", "recognized") or state["mode"] == desired
            )
            if not chosen:
                receipt = page.main.call(_ENTRY_RECEIPT_JS, approved, desired)
                if not state and receipt is None:
                    _, delayed, blocker = _wait_auth_receipt(
                        page, approved, None, entry_mode=desired,
                        application_binding=application_binding,
                    )
                    if blocker:
                        return result(blocker)
                    if delayed is not None:
                        continue
                    receipt = page.main.call(_ENTRY_RECEIPT_JS, approved, desired)
                if desired == "recognized":
                    return result("login: no single recognizable form for the chosen account action; continue yourself")
                entry_key = (desired, receipt)
                if entry_key in entries or len(entries) >= 3:
                    return result(_NO_PROGRESS)
                entries.add(entry_key)
                try:
                    blocker = _guarded_click(
                        page.main, _ENTRY_TARGET_JS, approved, desired, intent=CREDENTIAL,
                        fallback="login: no safe entry to the chosen account form; open it yourself",
                    )
                except BidiError:
                    # Navigation may destroy the click's reply. Observe once;
                    # never resolve and click that entry again on this attempt.
                    blocker = None
                if blocker:
                    return result(blocker)
                _, _, blocker = _wait_auth_receipt(
                    page, approved, state, entry_mode=desired, entry_receipt=receipt,
                    application_binding=application_binding,
                )
                if blocker:
                    return result(blocker)
                continue
            if state["blocker"]:
                return result(state["blocker"])
            if not (policy.approves(frame.url) and policy.approves(state["url"]) and
                    (state["action"] is None or policy.approves(state["action"]))):
                return result("login: this page is not an approved origin for credentials")
            if state["mode"] == "create":
                if existing_account:
                    return result("login: an existing account was observed; another account will not be created")
                if not policy.allow_account_creation:
                    return result("login: this site wants a new account; account creation is off "
                                  "(apply.yaml authentication.allow_account_creation)")
            attempt = (state["mode"], state["stage"])
            if attempt in submitted:
                return result(_NO_PROGRESS)
            if refused_ok and state.get("passwordRefused") and frame.call(_CLEAR_PASSWORDS_JS, approved) is not True:
                return result(_CHANGED)
            for role in state["roles"]:
                done = frame.call(_WRITE_JS, approved, *secrets, state["signature"], role)
                if done is not True:
                    return result(done if isinstance(done, str) else _CHANGED)
                if role not in filled:
                    filled.append(role)
            authorize_terms = policy.allow_account_terms or (
                policy.allow_account_creation and state["mode"] == "create")
            if authorize_terms:
                for _ in range(8):
                    proof = frame.call(_TERMS_STATUS_JS, approved, *secrets, state["signature"])
                    if not proof["allowed"] or not proof["pending"]:
                        break
                    blocker = _guarded_click(
                        frame, _TERMS_TARGET_JS, approved, *secrets, state["signature"], intent=FIELD,
                        fallback="consent: the required account terms changed or cannot be selected safely",
                    )
                    if blocker:
                        return result(blocker)
                    # Framework-controlled choices may acknowledge asynchronously.
                    # Observe the same choice; never click again to force a receipt.
                    receipt_deadline = time.monotonic() + 3
                    while True:
                        next_proof = frame.call(_TERMS_STATUS_JS, approved, *secrets, state["signature"])
                        if (not next_proof["allowed"] or next_proof["pending"] < proof["pending"] or
                                time.monotonic() >= receipt_deadline):
                            break
                        time.sleep(0.1)
                    if not next_proof["allowed"] or next_proof["pending"] >= proof["pending"]:
                        return result("consent: account agreement selection was not confirmed")
                else:
                    return result("consent: too many account agreements; review them yourself")
            # Component forms (Workday) validate on blur; a click that arrives
            # during that re-render is consumed without submitting. Let the last
            # field commit before the single account action.
            frame.call("() => { document.activeElement?.blur?.(); }")
            time.sleep(0.8)
            deadline = time.monotonic() + min(_AUTH_WAIT_SECONDS, _ENABLE_WAIT_SECONDS)
            while True:
                readiness = frame.call("""(...args) => {
                  const target = (""" + _ACCOUNT_TARGET_JS + r""")(...args);
                  return typeof target === 'string' ? target : !!target;
                }""", approved, *secrets, state["signature"], authorize_terms)
                if (not isinstance(readiness, str) or "account action is disabled" not in readiness or
                        time.monotonic() >= deadline):
                    break
                time.sleep(0.15)
            if readiness is not True:
                return result(readiness if isinstance(readiness, str) else _CHANGED)
            # A synchronous receipt is checked after every write. The click
            # resolver repeats the rejection, ownership, values and terms guards.
            submitted.add(attempt)
            try:
                blocker = _guarded_click(
                    frame, _ACCOUNT_TARGET_JS, approved, *secrets,
                    state["signature"], authorize_terms, intent=CREDENTIAL,
                )
            except BidiError:
                # The request may already have left; keep its one-shot guard.
                blocker = None
            if blocker:
                return result(blocker)
            acted = True
            modes.append(state["mode"])
            _, observed, blocker = _wait_auth_receipt(
                page, approved, state, application_binding=application_binding,
            )
            if blocker:
                return result(blocker)
            if observed is None:
                if document_origin is not None:
                    memory.mark_exists(document_origin, credentials.email)
                return result()
            if (state["stage"] == "password" and state["mode"] == "create" and
                    observed["mode"] == "login" and not observed["blocker"]):
                existing_account = True
                if document_origin is not None:
                    memory.mark_exists(document_origin, credentials.email)
                if policy.account_mode != "auto":
                    return result(_SEPARATE_SIGN_IN)
                desired = "login"
            elif state["stage"] == "password" and not observed["accountStatus"]:
                return result(_NO_PROGRESS)
        return result("login: the bounded account-stage limit was reached; continue in the retained tab")
    except (BidiError, UnsafeAction):
        return result(_FAILED)
