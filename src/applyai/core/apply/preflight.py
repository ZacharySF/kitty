"""Read actual gates and allow only recognized, non-final Workday step navigation.

No account creation, authentication, CAPTCHA solving, consent or final action is
performed. Unknown structures stop for manual review, rather than guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import time

from applyai.core.apply.bidi import Frame, Tab
from applyai.core.apply.page import UnsafeAction, form_frames

# The same snapshot is rechecked in the click script, so a changed final button
# cannot be clicked using a stale decision from Python.
_SNAPSHOT = r"""
() => {
  const visible = e => !!e && !!e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const all = s => [...document.querySelectorAll(s)].filter(visible);
  const text = e => (e?.innerText || e?.value || '').replace(/\s+/g, ' ').trim();
  const body = text(document.body);
  const controls = all('button, input[type=submit], [role=button]');
  const has = s => all(s).length > 0;
  const step = all('[data-automation-id=progressBarActiveStep], [data-automation-id=progressBar] [aria-current=step], [data-automation-id=progressBar] [aria-selected=true]').map(text).join(' ');
  const stepLabel = step.replace(/^current step\s+\d+\s+of\s+\d+\s*/i, '').replace(/^\d+\s*/, '').trim();
  const workday = !!step && has('[data-automation-id=bottom-navigation-next-button]');
  const review = workday && /^review(?: your application)?$/i.test(stepLabel);
  const forbidden = /submit|finish|complete|accept|agree|consent|certif|sign|legal|privacy/i;
  const next = all('[data-automation-id=bottom-navigation-next-button]');
  const safeNext = workday && !review && next.length === 1 &&
    /^(next|continue|save and continue)$/i.test(text(next[0])) &&
    !forbidden.test(text(next[0]) + ' ' + (next[0].getAttribute('aria-label') || '')) &&
    !next[0].disabled && next[0].getAttribute('aria-disabled') !== 'true' &&
    next[0].tagName === 'BUTTON' && next[0].type === 'button';
  let entry = '';
  if (!step && !has('input[type=password], input[autocomplete=one-time-code]')) {
    for (const [id, label] of [
      ['applyManually', /^(apply manually|apply without (?:an )?account|continue as guest)$/i],
      ['jobPostingApplyButton', /^(apply|apply now)$/i],
      ['adventureButton', /^(apply|apply now)$/i],
    ]) {
      const matches = all('[data-automation-id="' + id + '"]');
      if (matches.length !== 1) continue;
      const e = matches[0];
      if (!label.test(text(e)) || forbidden.test(e.getAttribute('aria-label') || '') ||
          e.disabled || e.getAttribute('aria-disabled') === 'true') continue;
      const button = e.tagName === 'BUTTON' && e.type === 'button';
      const target = e.tagName === 'A' && e.href ? new URL(e.href) : null;
      const posting = path => decodeURIComponent(path).replace(/\/apply(?:\/.*)?\/?$/, '').split('/').filter(Boolean).pop();
      const link = target && target.protocol === 'https:' && target.origin === location.origin &&
        /\/apply(?:\/applyManually)?\/?$/.test(target.pathname) &&
        posting(target.pathname) === posting(location.pathname);
      if (button || link) { entry = id; break; }
    }
  }
  const knownForm = workday || has('form#application, form#application_form, form#application-form, form.ashby-application-form');
  const boundary = knownForm && controls.some(e => /^(submit(?: application)?|send application|review(?: application)?)$/i.test(text(e)));
  let blocker = null;
  if (/\b(?:this (?:job|position|posting) (?:is (?:no longer available|closed)|has (?:closed|been filled))|no longer accepting applications|(?:job|job posting|posting|position) (?:has expired|not found|no longer exists|is no longer available|has been removed))\b/i.test(body))
    blocker = 'closed: the page explicitly says this posting is unavailable';
  else if (has('input[autocomplete=one-time-code], input[name=otp], input[name=verificationCode]') ||
      /\b(?:enter (?:the |your )?(?:verification|security|authentication) code|verify your email|two-factor authentication|multi-factor authentication)\b/i.test(body))
    blocker = 'verification: complete the verification or MFA step yourself';
  else if (has('iframe[src*="/bframe"], iframe[title*="challenge" i], [role=dialog] [class*=captcha]') ||
      /\b(?:verify (?:that )?you are (?:a )?human|complete the captcha|select all (?:images|squares))\b/i.test(body))
    blocker = 'captcha: a visible challenge needs your action';
  else if (/^(?:create account(?:\s*\/\s*sign in)?|sign in|log in)$/i.test(stepLabel) ||
      has('input[type=password]') || (!has('form input:not([type=hidden]), form textarea, form select') &&
      /\b(?:sign in to (?:apply|continue)|log in to (?:apply|continue)|create an account to (?:apply|continue))\b/i.test(body)))
    blocker = 'login: sign in or create the required account yourself';
  const errors = all('[aria-invalid=true], [data-automation-id=errorMessage], .field-error, [role=alert]')
    .filter(e => e.getAttribute('aria-invalid') === 'true' || /required|invalid|error|please (?:select|enter|attach)/i.test(text(e)));
  const fields = all('input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select');
  const representedRequired = e => e.matches('input, textarea, select') &&
    !['hidden', 'button', 'submit', 'reset', 'image', 'search', 'password'].includes(e.type) &&
    !e.disabled && !e.readOnly &&
    (visible(e) || ['radio', 'checkbox', 'file'].includes(e.type)) &&
    (e.required || e.getAttribute('aria-required') === 'true');
  const unsupported = all('[aria-required=true]').some(e =>
    !representedRequired(e) && ![...e.querySelectorAll('input, textarea, select')].some(representedRequired));
  if (!blocker && unsupported) blocker = 'unsupported: required custom controls need manual completion';
  const hasFields = fields.length > 0 || [...document.querySelectorAll('input[type=file]')]
    .some(e => visible(e.parentElement));
  const signature = location.href + '|' + step + '|' + entry + '|' + fields.map(e => e.name + ':' + e.id + ':' + e.type).join('|');
  return {blocker, step, review, boundary, safeNext, signature, entry, hasFields,
    errors: errors.length + fields.filter(e => e.willValidate && !e.validity.valid).length,
    workday, url: location.href};
}
"""


@dataclass(frozen=True)
class Preflight:
    frame: Frame
    url: str
    signature: str
    blocker: str | None = None
    step: str = ''
    review: bool = False
    boundary: bool = False
    safe_next: bool = False
    errors: int = 0
    entry: str = ''
    has_fields: bool = False


def inspect_page(page: Tab) -> Preflight:
    """Inspect top page and application frames; blockers always beat a ready boundary."""
    snapshots = []
    for frame in form_frames(page):
        data = frame.call(_SNAPSHOT)
        snapshot = Preflight(
            frame=frame, url=data['url'], signature=data['signature'], blocker=data['blocker'],
            step=data['step'], review=data['review'], boundary=data['boundary'],
            safe_next=data['safeNext'], errors=data['errors'], entry=data['entry'],
            has_fields=data['hasFields'],
        )
        if snapshot.blocker:
            return snapshot
        snapshots.append(snapshot)
    selected = next((s for s in snapshots if s.safe_next or s.entry or s.review or s.boundary), snapshots[0])
    return replace(selected, errors=sum(s.errors for s in snapshots))


def wait_for_page(page: Tab, *, previous: str | None = None, seconds: float = 12) -> Preflight:
    """Wait through SPA loading, without touching fields until a page/gate is recognizable."""
    deadline = time.monotonic() + seconds
    while True:
        snapshot = inspect_page(page)
        recognized = (snapshot.blocker or snapshot.entry or snapshot.review or
                      ((snapshot.step or snapshot.boundary) and snapshot.has_fields))
        if (recognized and snapshot.signature != previous) or time.monotonic() >= deadline:
            return snapshot
        time.sleep(0.3)


def advance(snapshot: Preflight, ats: str) -> None:
    """Click a recognized Workday entry/Next, repeating all guards against the current DOM."""
    if ats != 'workday' or not (snapshot.safe_next or snapshot.entry) or snapshot.blocker or snapshot.errors:
        raise UnsafeAction('unsupported or unsafe application step')
    clicked = snapshot.frame.call(
        r"""(signature) => {
          const state = (""" + _SNAPSHOT + r""")();
          if (state.signature !== signature || !(state.safeNext || state.entry) || state.blocker || state.errors)
            return false;
          const id = state.entry || 'bottom-navigation-next-button';
          const targets = [...document.querySelectorAll('[data-automation-id="' + id + '"]')]
            .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' &&
              getComputedStyle(e).display !== 'none');
          if (targets.length !== 1) return false;
          targets[0].click();
          return true;
        }""", snapshot.signature,
    )
    if not clicked:
        raise UnsafeAction('application step changed or has validation errors; stopped before navigation')
