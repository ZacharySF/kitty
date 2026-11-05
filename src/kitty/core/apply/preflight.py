"""Read form gates and recognize non-final entry navigation (Workday, Phenom, providers, Apply links).

Account actions are handled separately. CAPTCHA, consent and final application
actions stay manual. Unknown structures stop rather than guessing.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import time
from urllib.parse import urlsplit

from kitty.core.apply.bidi import Frame, Tab
from kitty.core.apply.guard import ENTRY, NEXT, UnsafeAction
from kitty.core.apply.page import (
    _COMPOSED_CLOSEST_JS, _CUSTOM_CONTROL_JS, _DOM_HELPERS_JS, _QUERY_ALL_JS,
    _UPLOAD_GROUP_JS, _UPLOADED_FILE_GROUP_JS, form_frames,
)
from kitty.core.apply.urls import route_apply_link


# Phenom posting pages expose an application link, not an application form.
# Keep navigation bound to the displayed posting, exact sequence and same origin.
_PHENOM_ENTRY = r"""
() => {
  const shown = e => e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const queryAll = (""" + _QUERY_ALL_JS + r""");
  const candidates = queryAll('a[data-ph-at-id="apply-link"]').filter(shown);
  const closest = (""" + _COMPOSED_CLOSEST_JS + r""");
  if (!candidates.length || location.protocol !== 'https:' ||
      candidates.some(e => e.href !== candidates[0].href ||
        closest(e, ':disabled, [disabled], [aria-disabled=true], [inert]'))) return null;
  const e = candidates[0], target = new URL(e.href, location.href);
  const posting = location.pathname.match(/\/job\/([A-Za-z]+\d+)(?:\/|$)/i)?.[1];
  const sequence = target.searchParams.get('jobSeqNo');
  if (!posting || !sequence || !/^[A-Za-z0-9]+$/.test(sequence) ||
      !new RegExp(posting + '(?![0-9])', 'i').test(sequence) ||
      e.getAttribute('data-ph-at-job-seqno-text') !== sequence ||
      !/^(apply|apply now)$/i.test(e.innerText.replace(/\s+/g, ' ').trim()) ||
      target.protocol !== 'https:' || target.origin !== location.origin ||
      !/\/(?:hvhapply|apply)\/?$/.test(target.pathname) ||
      target.search !== '?jobSeqNo=' + sequence || target.hash ||
      target.username || target.password || e.hasAttribute('download') ||
      (e.target && e.target !== '_self') || e.getAttribute('aria-disabled') === 'true') return null;
  return e;
}
"""

# Entry is navigation from an unfilled posting, never a second interpretation
# of a candidate form's final "Apply" button. Search/alert fields observed on
# posting pages are not candidate answers.
_ENTRY_CLEAR_JS = r"""
() => {
  const queryAll = (""" + _QUERY_ALL_JS + r""");
  const shown = e => !!e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const all = s => queryAll(s).filter(shown);
  if (all('form#application, form#application_form, form#application-form, form.ashby-application-form, input[type=password], input[autocomplete=one-time-code], input[type=email], input[type=tel], input[type=file], textarea, input[autocomplete=given-name], input[autocomplete=family-name]').length)
    return false;
  if (all('h1, h2, [role=heading], [aria-current=step]').some(e =>
      /^(?:review(?: your)? application|application review|submit application|application complete|application submitted)$/i.test(e.innerText.trim())))
    return false;
  if (all('button, input[type=submit], [role=button]').some(e =>
      /^(?:submit(?: application)?|send application|finish application|complete application|review(?: your)? application)$/i.test(
        (e.innerText || e.value || e.getAttribute('aria-label') || '').trim())))
    return false;
  return !all('input:not([type=hidden]):not([type=button]):not([type=submit]), textarea, select, [role=checkbox], [role=radio], [role=combobox]').some(e => {
    if (['q', 'locationsearch', 'frequency'].includes(e.name) || e.type === 'search') return false;
    if (e.type === 'checkbox' || e.type === 'radio') return e.checked;
    if (e.matches('[role=checkbox], [role=radio]')) return e.getAttribute('aria-checked') === 'true';
    return !!(e.value || e.getAttribute('aria-valuetext') || '').trim();
  });
}
"""

# These observed posting CTAs are outside candidate forms.
_PROVIDER_ENTRY_JS = r"""
() => {
  if (location.protocol !== 'https:' || !(""" + _ENTRY_CLEAR_JS + r""")()) return null;
  const queryAll = (""" + _QUERY_ALL_JS + r""");
  const shown = e => !!e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  let selector = '';
  if (((/^[a-z0-9-]+\.fa\.[a-z0-9]+\.oraclecloud\.com$/i.test(location.hostname) &&
        /^\/hcmUI\/CandidateExperience\/[^/]+\/sites\/[^/]+\/job\/\d+\/?$/.test(location.pathname)) ||
       // Oracle Recruiting Cloud on a company domain (/en/sites/CX_2001/job/<id>).
       (!/(^|\.)oraclecloud\.com$/i.test(location.hostname) &&
        /^\/[a-z]{2}(?:-[A-Z]{2})?\/sites\/CX(?:_\d+)?\/job\/\d+\/?$/.test(location.pathname))))
    selector = '.job-details__section-apply-button button.apply-now-button';
  else if (location.hostname === 'jobs.dayforcehcm.com' &&
      /^\/[^/]+\/[^/]+\/CANDIDATEPORTAL\/jobs\/\d+\/?$/i.test(location.pathname))
    selector = '[test-id="job-detail-not-applied"] button[test-id="apply-button"][type=button]';
  else if (location.hostname === 'jobs.dayforcehcm.com' &&
      /^\/[^/]+\/[^/]+\/CANDIDATEPORTAL\/jobs\/\d+\/apply\/?$/i.test(location.pathname) &&
      new URL(location.href).searchParams.get('flowSelection') === 'true')
    selector = 'button[test-id="apply-without-account"][type=button]';
  else if (location.hostname === 'www.databricks.com' &&
      /^\/company\/careers\//.test(location.pathname)) {
    const id = new URL(location.href).searchParams.get('gh_jid');
    if (id && /^\d+$/.test(id) && location.pathname.endsWith('-' + id))
      selector = '[data-cy="Hero"] button[data-cy="Button"][type=button]';
  } else if (/^\/careers\/job\/\d+\/?$/.test(location.pathname) &&
      document.querySelector('a[href*="/careers/apply?"]')) {
    // Eightfold posting: the CTA is a same-origin link to this exact pid's form.
    // Eightfold copies the posting's tracking parameters (utm_*, hl, _ccid) into it.
    const pid = location.pathname.match(/\d+/)[0];
    const links = queryAll('a[href*="/careers/apply?"]').filter(shown).filter(a => {
      try {
        const u = new URL(a.href);
        // Sandbox Xrays cannot iterate URLSearchParams; split the query exactly.
        const query = u.search.replace(/^\?/, '').split('&');
        return u.origin === location.origin && u.pathname === '/careers/apply' &&
          query.filter(p => p.startsWith('pid=')).length === 1 && query.includes('pid=' + pid) &&
          query.every(p => p === 'pid=' + pid ||
            /^(?:domain=[A-Za-z0-9.-]+|hl=[A-Za-z_-]*|utm_[A-Za-z_]+=[A-Za-z0-9._~%+-]*|_ccid=[A-Za-z0-9._~%+-]*)$/.test(p)) &&
          !u.hash && !u.username && !u.password && (!a.target || a.target === '_self');
      } catch { return false; }
    });
    if (links.length !== 1) return null;
    const e = links[0];
    if (e.closest('form, [role=form], [aria-disabled=true], [aria-hidden=true], [inert]') ||
        !/^apply now$/i.test(e.innerText.trim())) return null;
    return e;
  }
  if (!selector) return null;
  const candidates = queryAll(selector).filter(shown);
  if (candidates.length !== 1) return null;
  const e = candidates[0];
  if (e.form || e.closest('form, [role=form]') || e.disabled ||
      e.closest('[aria-disabled=true], [aria-hidden=true], [inert]') ||
      !/^(?:apply|apply now|apply without an account)$/i.test(e.innerText.trim())) return null;
  return e;
}
"""

# Custom career sites (N2: 14 of 15 visible Apply controls were links): one
# visible, enabled, exact-label Apply link whose every copy leads to the same
# other document. Returns that destination (no fragment); kitty navigates to it
# only after routing it to a recognized family (filler), never by clicking.
_APPLY_LINK_JS = r"""
() => {
  if (location.protocol !== 'https:' || !(""" + _ENTRY_CLEAR_JS + r""")()) return null;
  const queryAll = (""" + _QUERY_ALL_JS + r""");
  const closest = (""" + _COMPOSED_CLOSEST_JS + r""");
  const shown = e => !!e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const label = /^(?:apply|apply now|apply online|apply here|apply for (?:this )?(?:job|position|role)|apply to (?:this )?job|apply on (?:the )?(?:company|employer) (?:site|website))$/i;
  const links = queryAll('a[href]').filter(shown).filter(a =>
    label.test((a.innerText || a.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim()));
  if (!links.length) return null;
  const here = location.origin + location.pathname + location.search;
  const targets = new Set();
  for (const a of links) {
    let u;
    try { u = new URL(a.getAttribute('href'), location.href); } catch { return null; }
    if (u.protocol !== 'https:' || u.username || u.password || a.hasAttribute('download') ||
        closest(a, 'form, [role=form], [aria-disabled=true], [aria-hidden=true], [inert]')) return null;
    const target = u.origin + u.pathname + u.search;
    if (target === here) return null;
    targets.add(target);
  }
  return targets.size === 1 ? [...targets][0] : null;
}
"""

_COOKIE_REJECT_JS = r"""
() => {
  // Only exact provider "reject all" controls; declining grants nothing.
  const dayforce = location.hostname === 'jobs.dayforcehcm.com' &&
    document.querySelector('button[test-id="cookie-preferences-banner-reject-button"][type=button]');
  const oracle = (/\.oraclecloud\.com$/.test(location.hostname) ||
      /^\/[a-z]{2}(?:-[A-Z]{2})?\/sites\/CX(?:_\d+)?(?:\/|$)/.test(location.pathname)) &&
    document.querySelectorAll('.cookie-consent-modal button.cookie-consent__button.decline[type=button]');
  const ketch = document.querySelectorAll('#ketch-consent-banner button#ketch-banner-button-secondary');
  const single = list => list && list.length === 1 ? list[0] : null;
  const e = dayforce || single(oracle) || single(ketch) || document.querySelector('#onetrust-reject-all-handler');
  if (!e || !e.getClientRects().length || getComputedStyle(e).visibility === 'hidden' ||
      getComputedStyle(e).display === 'none' || e.disabled || e.form ||
      (!dayforce && !e.closest('#onetrust-banner-sdk, #onetrust-pc-sdk, .cookie-consent-modal, #ketch-consent-banner')) ||
      !(dayforce ? /^reject$/i : /^reject all$/i).test(e.innerText.trim())) return null;
  return e;
}
"""
_EMBEDDED_APPLICATION_JS = r"""
() => {
  if (location.protocol !== 'https:' || location.hostname !== 'www.databricks.com' ||
      !/^\/company\/careers\//.test(location.pathname)) return false;
  const id = new URL(location.href).searchParams.get('gh_jid');
  if (!id || !/^\d+$/.test(id) || !location.pathname.endsWith('-' + id)) return false;
  const frames = document.querySelectorAll('iframe#grnhse_iframe');
  if (frames.length !== 1) return false;
  const frame = frames[0], source = new URL(frame.src, location.href);
  if (source.protocol !== 'https:' || source.hostname !== 'job-boards.greenhouse.io' ||
      source.pathname !== '/embed/job_app' || source.username || source.password || source.hash ||
      source.searchParams.getAll('for').length !== 1 || source.searchParams.get('for') !== 'databricks' ||
      source.searchParams.getAll('token').length !== 1 || source.searchParams.get('token') !== id ||
      source.searchParams.getAll('validityToken').length !== 1 || !source.searchParams.get('validityToken') ||
      frame.title !== 'Greenhouse Job Board' || !frame.getClientRects().length ||
      getComputedStyle(frame).visibility === 'hidden') return false;
  frame.scrollIntoView({behavior: 'instant', block: 'center', inline: 'nearest'});
  return true;
}
"""

_SMART_NEXT_JS = r"""
() => {""" + _DOM_HELPERS_JS + r"""
  if (location.protocol !== 'https:' || location.hostname !== 'jobs.smartrecruiters.com' ||
      !/^\/oneclick-ui\/company\/[^/]+\/publication\/[a-f0-9-]+\/?$/i.test(location.pathname) ||
      !queryAll('oc-oneclick-form').length) return null;
  const candidates = queryAll('button').filter(e =>
    closest(e, 'oc-button[data-test=footer-next]') &&
    e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  if (candidates.length !== 1) return null;
  const e = candidates[0];
  if (e.type !== 'button' ||
      !/^(weiter|next|continue)$/i.test(composedText(e).replace(/\s+/g, ' ').trim()) ||
      /submit|finish|complete|accept|agree|consent|certif|sign|legal|privacy|absenden|abschicken/i.test(
        e.getAttribute('aria-label') || '') ||
      closest(e, ':disabled, [disabled], [aria-disabled=true], [aria-hidden=true], [inert]')) return null;
  return e;
}
"""

# Workday's progress bar lists every step of the wizard and marks the current
# one; its Submit is the last step's pageFooterNextButton (atsfill workday.md:67)
# and step names are localized. So the step's position decides, not its label:
# {index, count} of the marked step (progressBarActiveStep, or aria-current=step /
# aria-selected=true inside the bar) among the bar's step items (automation-id
# step items, else its list items), else from the marked step's own rendered
# "step N of M" numbers (digits in any language), or null.
# Runs inside _SNAPSHOT (queryAll, closest).
_WORKDAY_STEPS_JS = r"""
() => {
  const bars = queryAll('[data-automation-id=progressBar]');
  const bar = bars.length === 1 ? bars[0] : null;
  let marked = queryAll('[data-automation-id=progressBarActiveStep]');
  if (!marked.length && bar) marked = queryAll('[aria-current=step], [aria-selected=true]', bar);
  if (marked.length !== 1) return null;
  const stepItem = e => /^progressBar[A-Za-z]*Step$/.test(e?.getAttribute('data-automation-id') || '');
  const owner = e => e.parentElement ? closest(e.parentElement, '[data-automation-id]') : null;
  let items = bar ? queryAll('[data-automation-id]', bar)
    .filter(e => e !== bar && stepItem(e) && !stepItem(owner(e))) : [];
  if (items.length < 2 && bar)
    items = queryAll('li', bar).filter(e => !(e.parentElement && closest(e.parentElement, 'li')));
  const current = items.find(e => e === marked[0] || e.contains(marked[0]));
  if (items.length >= 2 && current) return {index: items.indexOf(current), count: items.length};
  if (items.length >= 2) return null;
  const numbers = ((marked[0].innerText || marked[0].textContent || '').match(/\d+/g) || []).slice(0, 2).map(Number);
  if (numbers.length < 2 || numbers[0] < 1 || numbers[0] > numbers[1]) return null;
  return {index: numbers[0] - 1, count: numbers[1]};
}
"""

#: Workday page containers (data-automation-id): which wizard page is shown,
#: in any language. Review pages are final; the others are known earlier steps.
_WORKDAY_REVIEW_PAGES = ("applyFlowReviewPage", "reviewJobApplicationPage")
_WORKDAY_STEP_PAGES = (
    "applyFlowMyInfoPage", "applyFlowMyExpPage", "applyFlowPrimaryQuestionsPage",
    "applyFlowSecondaryQuestionsPage", "applyFlowSupplementaryQuestionsPage",
    "applyFlowVoluntaryDisclosuresPage", "applyFlowAutoFillPage",
    "contactInformationPage", "myExperiencePage", "voluntaryDisclosuresPage", "selfIdentificationPage",
)
_WORKDAY_PAGES_JS = (
    "{review: " + repr(", ".join(f"[data-automation-id={page}]" for page in _WORKDAY_REVIEW_PAGES))
    + ", step: " + repr(", ".join(f"[data-automation-id={page}]" for page in _WORKDAY_STEP_PAGES)) + "}"
)

# The same snapshot is rechecked in the click script, so a changed final button
# cannot be clicked using a stale decision from Python.
_SNAPSHOT = r"""
() => {""" + _DOM_HELPERS_JS + r"""
  const visible = e => !!e && !!e.getClientRects().length &&
    getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const all = s => queryAll(s).filter(visible);
  const text = e => (e?.innerText || (e ? composedText(e) : '') || e?.value || '').replace(/\s+/g, ' ').trim();
  const body = [text(document.body), ...all('h1, h2, p, label, [role=alert], [role=dialog]')
    .filter(e => e.getRootNode() !== document).map(text)].join(' ');
  const actionSafe = e => !closest(e, ':disabled, [disabled], [aria-disabled=true], [aria-hidden=true], [inert]');
  const controls = all('button, input[type=submit], [role=button], spl-button');
  const has = s => all(s).length > 0;
  // DataDome's /captcha document is the interactive puzzle/audio surface, not
  // its passive collection widget. Recognize it from the embedding document so
  // cross-origin challenge contents never need to be touched.
  const dataDomeChallenge = all('iframe').some(e => {
    try {
      if (![...e.getClientRects()].some(r => r.width > 0 && r.height > 0) ||
          closest(e, '[hidden], [aria-hidden=true], [inert]')) return false;
      const source = new URL(e.src, location.href);
      return source.protocol === 'https:' &&
        /(^|\.)captcha-delivery\.com$/i.test(source.hostname) &&
        /^\/captcha\/?$/.test(source.pathname);
    } catch { return false; }
  });
  const oracleLoading = location.protocol === 'https:' &&
    /^[a-z0-9-]+\.fa\.[a-z0-9]+\.oraclecloud\.com$/i.test(location.hostname) &&
    /^\/hcmUI\/CandidateExperience\/[^/]+\/sites\/[^/]+\/job\/\d+(?:\/|$)/.test(location.pathname) &&
    document.body?.classList.contains('app-loading') && has('body > .app-loading-spinner');
  const uploadGroupFor = (""" + _UPLOAD_GROUP_JS + r""");
  const uploadedGroup = (""" + _UPLOADED_FILE_GROUP_JS + r""");
  let step = all('[data-automation-id=progressBarActiveStep], [data-automation-id=progressBar] [aria-current=step], [data-automation-id=progressBar] [aria-selected=true]').map(text).join(' ');
  const stepLabel = step.replace(/^current step\s+\d+\s+of\s+\d+\s*/i, '').replace(/^\d+\s*/, '').trim();
  const footerNext = location.protocol === 'https:' &&
    /^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$/i.test(location.hostname) &&
    /\/job\/.+\/apply(?:\/|$)/.test(location.pathname);
  const nextSelector = '[data-automation-id=bottom-navigation-next-button]' +
    (footerNext ? ', [data-automation-id=pageFooterNextButton]' : '');
  const workday = !!step && has(nextSelector);
  // Locale-independent: the last progress step is Review whatever its label
  // says, and Next is safe only while a later step exists and no review
  // container is shown. The English label is a secondary check.
  const steps = workday ? (""" + _WORKDAY_STEPS_JS + r""")() : null;
  const pages = """ + _WORKDAY_PAGES_JS + r""";
  const reviewPage = workday && has(pages.review);
  // A readable position decides; a shown earlier-step page id stands in when the
  // bar cannot be read. A review page id is final either way.
  const stepsLeft = !reviewPage && (steps ? steps.index < steps.count - 1 : workday && has(pages.step));
  const review = workday && (/^review(?: your application)?$/i.test(stepLabel) || reviewPage ||
    (!!steps && steps.index === steps.count - 1));
  const reviewShown = all('[data-automation-id*=review i]').some(e => !closest(e, '[data-automation-id=progressBar]'));
  const forbidden = /submit|finish|complete|accept|agree|consent|certif|sign|legal|privacy/i;
  const next = all(nextSelector);
  let safeNext = workday && !review && stepsLeft && !reviewShown && next.length === 1 &&
    /^(next|continue|save and continue)$/i.test(text(next[0])) &&
    !forbidden.test(text(next[0]) + ' ' + (next[0].getAttribute('aria-label') || '')) &&
    actionSafe(next[0]) &&
    next[0].tagName === 'BUTTON' && (next[0].type === 'button' ||
      (footerNext && next[0].getAttribute('data-automation-id') === 'pageFooterNextButton' &&
       !next[0].hasAttribute('type') && !next[0].form));
  let entry = '', entryProof = '';
  const entryClear = (""" + _ENTRY_CLEAR_JS + r""")();
  if (!step && entryClear) {
    for (const [id, label] of [
      ['applyManually', /^(apply manually|apply without (?:an )?account|continue as guest)$/i],
      ['jobPostingApplyButton', /^(apply|apply now)$/i],
      ['adventureButton', /^(apply|apply now)$/i],
    ]) {
      const matches = all('[data-automation-id="' + id + '"]');
      if (matches.length !== 1) continue;
      const e = matches[0];
      if (!label.test(text(e)) || forbidden.test(e.getAttribute('aria-label') || '') ||
          !actionSafe(e)) continue;
      const button = e.tagName === 'BUTTON' && e.type === 'button';
      const target = e.tagName === 'A' && e.href ? new URL(e.href) : null;
      const posting = path => decodeURIComponent(path).replace(/\/apply(?:\/.*)?\/?$/, '').split('/').filter(Boolean).pop();
      const link = target && target.protocol === 'https:' && target.origin === location.origin &&
        /\/apply(?:\/applyManually)?\/?$/.test(target.pathname) &&
        posting(target.pathname) === posting(location.pathname);
      if (button || link) { entry = id; entryProof = e.outerHTML; break; }
    }
  }
  const phenom = /\/(?:hvhapply|apply)\/?$/.test(location.pathname) &&
    /^[A-Za-z0-9]+$/.test(new URL(location.href).searchParams.get('jobSeqNo') || '') &&
    has('input.phw-form-control[form-field]');
  if (!step && phenom) step = 'Application details';
  if (!entry && !step && entryClear &&
      (""" + _PHENOM_ENTRY + r""")()) {
    entry = 'phenom-apply';
    entryProof = (""" + _PHENOM_ENTRY + r""")().href;
  }
  if (!entry && !step && entryClear) {
    const provider = (""" + _PROVIDER_ENTRY_JS + r""")();
    if (provider) { entry = 'provider-apply'; entryProof = provider.outerHTML; }
  }
  let entryUrl = '';
  if (!entry && !step && entryClear) {
    const link = (""" + _APPLY_LINK_JS + r""")();
    if (link) { entry = 'apply-link'; entryProof = entryUrl = link; }
  }
  const ashbyForm = /(^|\.)ashbyhq\.com$/i.test(location.hostname) &&
    /^\/[^/]+\/[a-f0-9-]+\/application\/?$/i.test(location.pathname) &&
    has('input[id="_systemfield_name"], input[id="_systemfield_email"]');
  const smartScreening = /\/screening\/?$/.test(location.pathname) &&
    has('oc-screening-questions-form');
  const smartForm = location.protocol === 'https:' && location.hostname === 'jobs.smartrecruiters.com' &&
    /^\/oneclick-ui\/company\/[^/]+\/publication\/[a-f0-9-]+(?:\/screening)?\/?$/i.test(location.pathname) &&
    (smartScreening || has('oc-oneclick-form'));
  if (smartForm && (""" + _SMART_NEXT_JS + r""")()) safeNext = true;
  const oracleEmail = location.protocol === 'https:' &&
    /^[a-z0-9-]+\.fa\.[a-z0-9]+\.oraclecloud\.com$/i.test(location.hostname) &&
    /^\/hcmUI\/CandidateExperience\/[^/]+\/sites\/[^/]+\/job\/\d+\/apply\/email\/?$/.test(location.pathname) &&
    has('input[type=email][name=primary-email]');
  const oracleSections = queryAll('form.apply-flow__content-form apply-flow-section[data-scroll-spy="apply-flow-sections"]');
  const oracleForm = oracleSections.length > 0;
  const eightfoldForm = location.protocol === 'https:' && location.pathname === '/careers/apply' &&
    /^\d+$/.test(new URL(location.href).searchParams.get('pid') || '') && queryAll('form').length === 1 &&
    has('form input[id="Contact_Information_firstname"], form input[id="Contact_Information_email"], ' +
        'form input[data-test-id="Contact_Information_firstname"], form input[data-test-id="Contact_Information_email"]');
  // Oracle's standard flow renders every section together; the numeric route
  // follows scrolling, not a safe Next action. Hidden sections cannot be verified.
  const oracleCompleteSurface = oracleForm && oracleSections.every(e =>
    visible(e) && e.getAttribute('aria-hidden') !== 'true');
  const eightfoldOnboarding = location.protocol === 'https:' && location.pathname === '/careers/apply' &&
    queryAll('[data-testid=onboarding-manual-information-button]').length === 1 &&
    queryAll('input[type=file]').length === 1;
  const knownForm = workday || ashbyForm || smartForm || oracleForm || eightfoldForm ||
    has('form#application, form#application_form, form#application-form, form.ashby-application-form');
  if (!step && eightfoldOnboarding) step = 'Profile import';
  // Eightfold's post-signup profile form ("Create your profile") precedes the
  // job's own application; its Submit creates the candidate profile only.
  const eightfoldProfile = location.protocol === 'https:' && location.pathname === '/careers/apply' &&
    /\bProfile Bootstrapping\b/.test(body) && all('h1, h2, h3, [role=heading]').some(e => /^create your profile$/i.test(text(e)));
  if (!step && eightfoldProfile) step = 'Create profile';
  if (!step && (ashbyForm || smartForm || oracleForm || eightfoldForm))
    step = smartScreening && smartForm ? 'Application screening' : 'Application details';
  const boundary = knownForm && (!oracleForm || oracleCompleteSurface) && controls.some(e =>
    /^(submit(?: application)?|send application|review(?: application)?|bewerbung (?:absenden|abschicken|senden))$/i.test(text(e)) ||
    (smartScreening && smartForm && /^absenden$/i.test(text(e))));
  const accountLanding = !has('input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select') &&
    all('h1, h2, [role=heading]').some(e =>
      /^(?:candidate (?:account|home|sign in|login)|sign in|log in|create (?:an? )?account|sign up|register)$/i.test(text(e))) &&
    all('a, button, [role=button]').some(e =>
      /^(?:sign in|log in|create (?:an? )?account|sign up|register|(?:sign in|continue|log in) with google)$/i.test(text(e)));
  const primaryNotices = all('h1, h2, [role=alert], main > p, [role=main] > p, body > p, .jobDisplay .job > p, [data-automation-id=errorContainer] [data-automation-id=errorMessage]')
    .filter(e => !closest(e, 'footer, nav, aside')).map(text);
  if (!document.body?.childElementCount) primaryNotices.push(body);
  const unavailable = primaryNotices.some(value =>
    /\b(?:this (?:job|position|posting) (?:is (?:no longer (?:available|posted)|closed)|has (?:closed|been (?:closed|filled)))|no longer accepting applications|(?:job|job posting|posting|position) (?:has expired|not found|no longer exists|is no longer available|has been removed)|page (?:not found|(?:cannot|could not) be found)|page you are looking for (?:doesn[’']t|does not) exist)\b/i.test(value));
  let blocker = null;
  // Some ATSs (Eightfold) put autocomplete=one-time-code on ordinary answers to
  // suppress autofill. Only code-named fields, or a page of nothing but such
  // fields, are verification evidence.
  const otpInputs = all('input[autocomplete=one-time-code]');
  const codeNamed = e => /\b(?:code|otp|pin|passcode|verification|verify|token)\b|one[- ]time/i.test([
    ...[...(e.labels || [])].map(text), e.getAttribute('aria-label'), e.placeholder, e.name, e.id,
    e.getAttribute('data-test-id')].filter(Boolean).join(' ').replace(/[_-]/g, ' '));
  const otpOnly = otpInputs.length > 0 &&
    all('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]):not([type=file]), textarea, select')
      .every(e => otpInputs.includes(e));
  if (unavailable)
    blocker = 'closed: the primary page notice says this posting is unavailable';
  else if (otpInputs.some(codeNamed) || otpOnly ||
      has('input[name=otp], input[name=verificationCode], input[id^="pin-code-"][inputmode=numeric]') ||
      /\b(?:enter (?:the |your )?(?:verification|security|authentication) code|verify your (?:email|account)|two-factor authentication|multi-factor authentication)\b/i.test(body) ||
      (/\bcheck your email\b/i.test(body) && /\benter the (?:code|otp)\b/i.test(body)))
    blocker = 'verification: complete the verification or MFA step yourself';
  else if (dataDomeChallenge ||
      has('iframe[src*="/bframe"], iframe[title*="challenge" i], [role=dialog] [class*=captcha]') ||
      /\b(?:verify (?:that )?you are (?:a )?human|complete the captcha|select all (?:images|squares))\b/i.test(body))
    blocker = 'captcha: a visible challenge needs your action';
  else if (oracleLoading)
    blocker = 'loading: wait for the application loading overlay to clear, then resume';
  else if (!eightfoldProfile && all('[aria-modal=true], [role=dialog], dialog[open], #onetrust-banner-sdk, #onetrust-pc-sdk')
      .some(e => /\b(?:cookies?|consent|privacy preferences|terms and conditions)\b/i.test(text(e))))
    blocker = 'consent: review the visible consent dialog yourself';
  // iCIMS-style /login: a lone account email (plus acknowledgement) before any application.
  else if (accountLanding || oracleEmail || (/\/login\/?$/.test(location.pathname) &&
      all('form input[type=email]').length === 1 && !knownForm &&
      all('form input:not([type=hidden]):not([type=email]):not([type=checkbox]):not([type=submit]):not([type=button]), form textarea, form select').length === 0) ||
      has('input[type=password]') || (!has('form input:not([type=hidden]), form textarea, form select') &&
      /\b(?:sign in to (?:apply|continue)|log in to (?:apply|continue)|create an account to (?:apply|continue))\b/i.test(body)))
    blocker = 'login: sign in or create the required account yourself';
  if (!blocker || blocker.startsWith('consent:')) {
    const reject = (""" + _COOKIE_REJECT_JS + r""")();
    if (reject) { blocker = null; entry = 'cookie-reject'; entryProof = reject.outerHTML; }
  }
  const errors = all('[aria-invalid=true], [data-automation-id=errorMessage], .field-error, [role=alert]')
    .filter(e => e.getAttribute('aria-invalid') === 'true' || /required|invalid|error|please (?:select|enter|attach)/i.test(text(e)));
  const customKind = (""" + _CUSTOM_CONTROL_JS + r""");
  const fieldSelector = 'input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select, [role=checkbox], [role=radio], [role=combobox]';
  const fields = all(fieldSelector).filter(e => e.matches('input, textarea, select') || customKind(e));
  const supportedControl = e => {
    const kind = customKind(e);
    return (e.matches('input, textarea, select') || kind) &&
      (kind || !['hidden', 'button', 'submit', 'reset', 'image', 'search', 'password'].includes(e.type)) &&
      !e.disabled && !e.readOnly && actionSafe(e) &&
      (visible(e) || ['radio', 'checkbox', 'file'].includes(e.type));
  };
  const representedRequired = e => supportedControl(e) &&
    (e.required || e.getAttribute('aria-required') === 'true' ||
     (['checkbox', 'radio'].includes(customKind(e) || e.type) &&
      closest(e, '[role=group], [role=radiogroup]')?.getAttribute('aria-required') === 'true'));
  const invalidProfileItems = oracleForm ? all('[data-profile-item-invalid="true"]') : [];
  // These observed Add buttons mean required structured records are absent,
  // not that a personal-answer field is unknown. Unrecognized invalid items
  // still block below without guessing which record editor they belong to.
  const missingProfileSections = [
    ['education', 'timeline-add-education-button', /^add education$/i],
    ['experience', 'timeline-add-experience-button', /^add experience$/i],
  ].filter(([, id, label]) => invalidProfileItems.some(e =>
    e.tagName === 'BUTTON' && e.id === id && label.test(text(e)) &&
    oracleSections.includes(closest(e, 'apply-flow-section[data-scroll-spy="apply-flow-sections"]'))))
    .map(([section]) => section);
  // A radiogroup is represented by radio choices, not unrelated writable fields.
  const unsupported = all('[aria-required=true]').some(e =>
    e.getAttribute('role') === 'radiogroup'
      ? !queryAll(fieldSelector, e).some(input =>
          (customKind(input) || input.type) === 'radio' && supportedControl(input))
      : !representedRequired(e) && !uploadedGroup(e) &&
        !queryAll('input[type=file]', e).some(input => uploadGroupFor(input) === e) &&
        !queryAll(fieldSelector, e).some(supportedControl)) ||
    invalidProfileItems.length > 0;
  const hasFields = fields.length > 0 || queryAll('input[type=file]')
    .some(e => visible(e.parentElement || e.getRootNode().host));
  const signature = location.href + '|' + step + '|' + entry + '|' + entryProof + '|' +
    fields.map(e => e.name + ':' + e.id + ':' + (e.type || customKind(e))).join('|');
  return {blocker, step, review, boundary, safeNext, signature, entry, entryUrl, hasFields,
    unsupportedRequired: unsupported, missingProfileSections,
    errors: errors.length + fields.filter(e => e.willValidate && !e.validity.valid).length,
    workday, url: location.href};
}
"""


@dataclass(frozen=True)
class Preflight:
    """Form gates, including required structured records whose editors stay manual.

    Missing profile sections diagnose unsupported controls, not missing applicant
    facts. The empty default keeps existing snapshot constructors compatible.
    """

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
    unsupported_required: bool = False
    missing_profile_sections: tuple[str, ...] = ()
    #: The destination of an `apply-link` entry (a custom site's visible Apply link).
    entry_url: str = ''


def _listing_blocker(url: str) -> str | None:
    """A known aggregator index cannot identify the selected employer posting."""
    parts = urlsplit(url)
    if (parts.scheme == "https" and parts.hostname == "zapply.jobs" and
            parts.path.rstrip("/") == "/jobs"):
        return ("listing: this is a generic job listing, not the selected employer application. "
                "Find the exact posting on the employer's careers site and update the job's apply URL.")
    return None


def inspect_page(page: Tab) -> Preflight:
    """Separate page-level gates from controls needing manual completion.

    Unsupported required controls prevent advancement/readiness, not filling
    other known fields on an otherwise recognized application form. Required
    profile-section diagnostics are combined across frames just like those gates.
    """
    snapshots = []
    for index, frame in enumerate(form_frames(page)):
        # The embedding snapshot owns DataDome visibility. Its cross-origin
        # challenge is never an application form, including when its owner is
        # hidden; inspecting the child alone would lose that visibility guard.
        source = urlsplit(frame.url)
        if (index and source.scheme == 'https' and
                ((source.hostname or '') == 'captcha-delivery.com' or
                 (source.hostname or '').endswith('.captcha-delivery.com')) and
                source.path.rstrip('/') == '/captcha'):
            continue
        if blocker := _listing_blocker(frame.url):
            return Preflight(frame=frame, url=frame.url, signature=frame.url + "|listing", blocker=blocker)
        data = frame.call(_SNAPSHOT)
        snapshot = Preflight(
            frame=frame, url=data['url'], signature=data['signature'], blocker=data['blocker'],
            step=data['step'], review=data['review'], boundary=data['boundary'],
            safe_next=data['safeNext'], errors=data['errors'], entry=data['entry'],
            has_fields=data['hasFields'], unsupported_required=data['unsupportedRequired'],
            missing_profile_sections=tuple(data.get('missingProfileSections', ())),
            entry_url=data.get('entryUrl') or '',
        )
        if snapshot.entry == 'apply-link' and route_apply_link(snapshot.url, snapshot.entry_url) == (None, None):
            # An Apply link to an unrecognized site, or from a recognized family's
            # own page, is not an entry; keep waiting for the page's real one.
            snapshot = replace(snapshot, entry='', entry_url='')
        if snapshot.blocker:
            return snapshot
        snapshots.append(snapshot)
    # Employer landing pages often retain an Apply button after their ATS
    # iframe loads. Prefer the actual form, not another click on that entry.
    selected = next((s for s in snapshots if s.entry == 'cookie-reject'), None)
    if selected is None:
        selected = next((s for s in snapshots if s.safe_next or s.review or s.boundary or
                         (s.step and s.has_fields)), None)
    if selected is None:
        selected = next((s for s in snapshots if s.entry), snapshots[0])
    snapshot = replace(selected, errors=sum(s.errors for s in snapshots),
                       unsupported_required=any(s.unsupported_required for s in snapshots),
                       missing_profile_sections=tuple(dict.fromkeys(
                           section for s in snapshots for section in s.missing_profile_sections)))
    # The network tripwire (L2) fails every non-GET while Workday shows its Review page.
    page.observe_review(snapshot.review)
    return snapshot


def wait_for_page(page: Tab, *, previous: str | None = None, seconds: float = 12) -> Preflight:
    """Wait through SPA loading, without touching fields until a page/gate is recognizable."""
    deadline = time.monotonic() + seconds
    embedded = page.main.call(_EMBEDDED_APPLICATION_JS)
    while True:
        snapshot = inspect_page(page)
        if embedded and snapshot.entry == 'provider-apply':
            snapshot = replace(snapshot, entry='')
        loading = snapshot.blocker and snapshot.blocker.startswith('loading:')
        recognized = not loading and (snapshot.blocker or snapshot.entry or snapshot.review or
                      ((snapshot.step or snapshot.boundary) and snapshot.has_fields))
        if (recognized and snapshot.signature != previous) or time.monotonic() >= deadline:
            return snapshot
        time.sleep(0.3)


def advance(snapshot: Preflight, ats: str) -> None:
    """Recheck and enter a posting or advance a recognized non-final application step."""
    navigation = snapshot.entry in ('phenom-apply', 'provider-apply', 'cookie-reject')
    cookie_reject = snapshot.entry == 'cookie-reject'
    if ((ats not in ('workday', 'smartrecruiters') and not navigation) or not (snapshot.safe_next or snapshot.entry) or
            snapshot.blocker or (not cookie_reject and (snapshot.errors or snapshot.unsupported_required))):
        raise UnsafeAction('unsupported or unsafe application step')
    target = snapshot.frame.node(
        r"""(signature) => {
          const state = (""" + _SNAPSHOT + r""")();
          if (state.signature !== signature || !(state.safeNext || state.entry) ||
              state.blocker || (state.entry !== 'cookie-reject' && (state.errors || state.unsupportedRequired)))
            return null;
          if (state.entry === 'phenom-apply')
            return (""" + _PHENOM_ENTRY + r""")();
          if (state.entry === 'provider-apply')
            return (""" + _PROVIDER_ENTRY_JS + r""")();
          if (state.entry === 'cookie-reject')
            return (""" + _COOKIE_REJECT_JS + r""")();
          // Posting entry has its own proof; it has no application step/Next yet.
          if (!state.workday && !state.entry) return (""" + _SMART_NEXT_JS + r""")();
          const selector = state.entry ? '[data-automation-id="' + state.entry + '"]' :
            '[data-automation-id=bottom-navigation-next-button], [data-automation-id=pageFooterNextButton]';
          const targets = (""" + _QUERY_ALL_JS + r""")(selector)
            .filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' &&
              getComputedStyle(e).display !== 'none');
          return targets.length === 1 ? targets[0] : null;
        }""", snapshot.signature,
    )
    if target is None:
        raise UnsafeAction('application step changed or has validation errors; stopped before navigation')
    snapshot.frame.click(target, intent=ENTRY if snapshot.entry else NEXT)
