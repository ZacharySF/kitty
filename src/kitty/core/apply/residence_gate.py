"""Eightfold's pre-account "country/region of residence" gate.

Before showing its create-account form, Eightfold (e.g. careers.gf.com) asks only
for the applicant's country of residence so it can show the matching privacy
notice. That single fact is the profile's confirmed country. The gate is
recognized exactly: one country chooser, at most its one required privacy-policy
acknowledgment (only under reviewed account-terms authorization) and one Submit
on the apply route. Anything else is left for the normal flow or the user.
"""

from __future__ import annotations

import time

from kitty.config import Config
from kitty.core.apply.bidi import BidiError, Tab
from kitty.core.apply.guard import FIELD, PROFILE_GATE, UnsafeAction

_GATE_JS = r"""function (want, country) {
  const shown = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const text = e => (e.innerText || e.value || '').replace(/\s+/g, ' ').trim();
  if (location.protocol !== 'https:' || location.pathname !== '/careers/apply' ||
      !/^\d+$/.test(new URL(location.href).searchParams.get('pid') || '')) return null;
  const body = text(document.body);
  if (!/\bplease review before continuing\b/i.test(body) || !/\bcountry\/region of residence\b/i.test(body))
    return null;
  const all = [...document.querySelectorAll('input:not([type=hidden]), textarea, select')].filter(shown);
  const boxes = all.filter(e => e.type === 'checkbox');
  const inputs = all.filter(e => e.type !== 'checkbox');
  // At most the one required privacy-policy acknowledgment that appears after a
  // country is chosen; marketing/talent-community choices are never routine.
  const ackText = e => text(e.closest('label, div')?.parentElement || e.parentElement || e);
  if (boxes.length > 1 || boxes.some(e => e.id !== 'privacy_policy-consent-checkbox' ||
      e.getAttribute('aria-required') !== 'true' ||
      !/^I have read, understood and to the extent requested, consent with the Data Privacy Policy\.?$/i.test(ackText(e)) ||
      /marketing|newsletter|promotional|talent community|future opportunit|third.party|\bsell\b/i.test(ackText(e))))
    return null;
  const chooser = inputs.filter(e => e.getAttribute('role') === 'combobox' &&
    /^select a country$/i.test(e.getAttribute('placeholder') || ''));
  const submits = [...document.querySelectorAll('button')].filter(e => shown(e) && /^submit$/i.test(text(e)));
  if (inputs.length !== 1 || chooser.length !== 1 || submits.length !== 1 || submits[0].disabled) return null;
  if (want === 'submit') return chooser[0].value ? submits[0] : null;
  return {chosen: chooser[0].value, ack: boxes.length ? boxes[0].checked : null};
}"""

_COUNTRY_JS = r"""function (want, country) {
  const shown = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const text = e => (e.innerText || e.value || '').replace(/\s+/g, ' ').trim();
  const choosers = [...document.querySelectorAll('input[role=combobox]')].filter(e =>
    shown(e) && /^select a country$/i.test(e.getAttribute('placeholder') || ''));
  const boxes = [...document.querySelectorAll('input[type=checkbox]')].filter(shown);
  const ackText = e => text(e.closest('label, div')?.parentElement || e.parentElement || e);
  const ack = boxes.filter(e => e.id === 'privacy_policy-consent-checkbox' && e.getAttribute('aria-required') === 'true' &&
    /^I have read, understood and to the extent requested, consent with the Data Privacy Policy\.?$/i.test(ackText(e)) &&
    !/marketing|newsletter|promotional|talent community|future opportunit|third.party|\bsell\b/i.test(ackText(e)));
  if (choosers.length > 1 || ack.length > 1) return null;
  const chooser = choosers[0];
  if (want === 'input') return chooser || null;
  if (want === 'ack') return ack.length === 1 && !ack[0].checked ? ack[0] : null;
  if (want === 'option') {
    const list = chooser && document.getElementById(chooser.getAttribute('aria-controls') || '');
    const options = list ? [...list.querySelectorAll('[role=option]')].filter(e =>
      shown(e) && (e.getAttribute('label') || text(e)) === country) : [];
    return options.length === 1 ? options[0] : null;
  }
  return {chooser: !!chooser, chosen: chooser ? chooser.value : null,
          ack: ack.length ? ack[0].checked : null, otherBoxes: boxes.length - ack.length};
}"""


def _country_state(page: Tab) -> dict:
    return page.main.call(_COUNTRY_JS, "state") or {}


def _wait_state(page: Tab, key: str, expected: object, seconds: float = 4.0) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if _country_state(page).get(key) == expected:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)


def _choose_country_and_ack(page: Tab, country: str, allow_terms: bool) -> bool:
    """Choose the confirmed residence country and its one required privacy acknowledgment."""
    state = _country_state(page)
    if state.get("chooser") and state.get("chosen") != country:
        if state.get("chosen"):
            return False                         # a different existing choice is the user's
        chooser = page.main.node(_COUNTRY_JS, "input")
        if chooser is None:
            return False
        if page.main.node(_COUNTRY_JS, "option", country) is None:
            page.main.click(chooser, intent=FIELD)   # open its list only when not already open
        option = None
        for _ in range(20):
            option = page.main.node(_COUNTRY_JS, "option", country)
            if option is not None:
                break
            time.sleep(0.15)
        if option is None:
            return False
        page.main.click(option, intent=FIELD)
        if not _wait_state(page, "chosen", country):
            return False
    # A still-open country list overlays the acknowledgment below it.
    page.main.call("() => { document.activeElement?.blur?.(); }")
    time.sleep(0.4)
    for _ in range(25):                           # the acknowledgment renders after the choice
        if _country_state(page).get("ack") is not None:
            break
        time.sleep(0.2)
    if page.main.node(_COUNTRY_JS, "ack") is not None:
        if not allow_terms:
            return False
        page.main.click(page.main.node(_COUNTRY_JS, "ack"), intent=FIELD)
        if not _wait_state(page, "ack", True):
            return False
    return True


def residence_gate(page: Tab) -> bool:
    try:
        return page.main.call(_GATE_JS, "state") is not None
    except BidiError:
        return False


def complete_residence_gate(page: Tab, config: Config, *, allow_terms: bool) -> bool:
    """Choose the confirmed country, acknowledge the required privacy policy (only
    when routine account terms are authorized), then press Submit once."""
    country = (config.profile.country or "").strip()
    if not country or not residence_gate(page):
        return False
    try:
        if not _choose_country_and_ack(page, country, allow_terms):
            return False
        submit = page.main.node(_GATE_JS, "submit")
        if submit is None:
            return False
        page.main.click(submit, intent=PROFILE_GATE)
    except (BidiError, UnsafeAction):
        return False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not residence_gate(page):
            return True
        time.sleep(0.3)
    return False


_PROFILE_JS = r"""function (want) {
  const shown = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const text = e => (e.innerText || e.value || '').replace(/\s+/g, ' ').trim();
  if (location.protocol !== 'https:' || location.pathname !== '/careers/apply' ||
      !/\bProfile Bootstrapping\b/.test(text(document.body)) ||
      ![...document.querySelectorAll('h1, h2, h3, [role=heading]')].some(e => shown(e) && /^create your profile$/i.test(text(e))))
    return null;
  if (want === 'page') return true;
  const submits = [...document.querySelectorAll('button')].filter(e => shown(e) && /^submit$/i.test(text(e)));
  if (submits.length !== 1 || submits[0].disabled) return null;
  const fields = [...document.querySelectorAll('input:not([type=hidden]):not([type=file]), textarea, select')].filter(shown);
  const required = e => e.required || e.getAttribute('aria-required') === 'true';
  if (fields.some(e => e.getAttribute('aria-invalid') === 'true' || (required(e) && !e.value))) return null;
  // Profile only: refuse anything that reads like an application or legal attestation.
  if (/\b(?:certify|attest|signature|sign by typing|submit application)\b/i.test(text(document.body))) return null;
  return want === 'submit' ? submits[0] : true;
}"""


def submit_profile_bootstrap(page: Tab, config: Config, *, allow_terms: bool) -> bool:
    """Choose residence/privacy facts, then press Eightfold's profile Submit once."""
    country = (config.profile.country or "").strip()
    try:
        if page.main.call(_PROFILE_JS, "page") is None or not country:
            return False
        if not _choose_country_and_ack(page, country, allow_terms) or _country_state(page).get("otherBoxes"):
            return False
        target = page.main.node(_PROFILE_JS, "submit")
        if target is None:
            return False
        page.main.click(target, intent=PROFILE_GATE)
    except (BidiError, UnsafeAction):
        return False
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if page.main.call(_PROFILE_JS, "page") is None:
                return True
        except BidiError:
            pass
        time.sleep(0.3)
    return False


def prepare_profile_bootstrap(page: Tab, config: Config, *, allow_terms: bool) -> bool:
    """Set the profile's residence country and privacy acknowledgment; never submit."""
    country = (config.profile.country or "").strip()
    try:
        if not country or page.main.call(_PROFILE_JS, "page") is None:
            return False
        return _choose_country_and_ack(page, country, allow_terms)
    except (BidiError, UnsafeAction):
        return False
