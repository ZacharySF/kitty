"""Observed application navigation stays non-final and identity-bound."""
from __future__ import annotations

import json

import pytest

from kitty.core.apply import preflight
from kitty.core.apply.page import UnsafeAction, read_fields, form_frames
from test_application_auth import firefox, https_site, page, site  # noqa: F401
from test_apply import space  # noqa: F401


DATABRICKS = (
    'https://www.databricks.com/company/careers/research/'
    'phd-genai-research-scientist-intern-7011263002?gh_jid=7011263002'
)
WORKDAY = 'https://pwc.wd3.myworkdayjobs.com/en-US/Campus/job/Singapore/Intern_741688WD/apply/applyManually'
SMART = 'https://jobs.smartrecruiters.com/oneclick-ui/company/BoschGroup/publication/73d22a91-52e6-4e51-b734-e818e86fd2e1'


def _location(script, url):
    return script.replace('() => {', '() => { const location = new URL(' + json.dumps(url) + ');', 1)


def _document(site, page, markup):
    site.pages['/preflight'] = '<!doctype html><body>' + markup
    page.goto(site.origin + '/preflight')


@pytest.mark.parametrize('change', ['', 'employer', 'job', 'token', 'host', 'title', 'duplicate'])
def test_embedded_greenhouse_scroll_requires_exact_posting_identity(site, page, change):
    _document(site, page, '<iframe id="grnhse_iframe" title="Greenhouse Job Board" loading="lazy"></iframe>')
    source = 'https://job-boards.greenhouse.io/embed/job_app?for=databricks&validityToken=original&token=7011263002'
    if change == 'employer':
        source = source.replace('for=databricks', 'for=another')
    elif change == 'job':
        source = source.replace('token=7011263002', 'token=7011263003')
    elif change == 'token':
        source = source.replace('validityToken=original', 'validityToken=')
    elif change == 'host':
        source = source.replace('job-boards.greenhouse.io', 'unrelated.example')
    page.main.call("""(source, change) => {
      const frame = document.querySelector('iframe');
      Object.defineProperty(frame, 'src', {get: () => source});
      window.scrolls = [];
      frame.scrollIntoView = options => window.scrolls.push(options);
      if (change === 'title') frame.title = 'Unrelated iframe';
      if (change === 'duplicate') document.body.append(frame.cloneNode());
    }""", source, change, page_realm=True)
    result = page.main.call(_location(preflight._EMBEDDED_APPLICATION_JS, DATABRICKS), page_realm=True)
    assert result is (not change)
    scrolls = page.main.call('() => window.scrolls', page_realm=True)
    assert scrolls == ([] if change else [{'behavior': 'instant', 'block': 'center', 'inline': 'nearest'}])
    assert page.main.call('() => document.querySelector("iframe").src', page_realm=True) == source
    assert page.main.call('() => document.querySelector("iframe").loading') == 'lazy'


@pytest.mark.parametrize('label, step, in_form, safe', [
    ('Save and Continue', 'My Information', False, True),
    ('Submit application', 'My Information', False, False),
    ('Save and Continue', 'Review', False, False),
    ('Save and Continue', 'My Information', True, False),
    ('Accept and Continue', 'My Information', False, False),
])
def test_workday_footer_next_is_exact_nonfinal_control(site, page, monkeypatch, label, step, in_form, safe):
    button = f'<button data-automation-id="pageFooterNextButton" onclick="window.clicks++">{label}</button>'
    if in_form:
        button = '<form>' + button + '</form>'
    _document(site, page, f'<div data-automation-id="progressBarActiveStep">current step 1 of 6 {step}</div>' + button)
    page.main.call('() => { window.clicks = 0; }', page_realm=True)
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, WORKDAY))
    snapshot = preflight.inspect_page(page)
    assert snapshot.safe_next is safe
    if safe:
        preflight.advance(snapshot, 'workday')
        assert page.main.call('() => window.clicks', page_realm=True) == 1
    else:
        with pytest.raises(UnsafeAction):
            preflight.advance(snapshot, 'workday')
        assert page.main.call('() => window.clicks', page_realm=True) == 0


def test_workday_footer_next_rechecks_changed_final_label(site, page, monkeypatch):
    _document(site, page, '<div data-automation-id="progressBarActiveStep">current step 1 of 6 My Information</div>'
              '<button data-automation-id="pageFooterNextButton">Save and Continue</button>')
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, WORKDAY))
    snapshot = preflight.inspect_page(page)
    assert snapshot.safe_next
    page.main.call('() => { document.querySelector("button").textContent = "Submit application"; }')
    with pytest.raises(UnsafeAction):
        preflight.advance(snapshot, 'workday')


def test_smart_shadow_next_and_required_consent_handoff(site, page, monkeypatch):
    snapshot_js = preflight._SNAPSHOT
    _document(site, page, '<oc-oneclick-form><label>Name<input value="Known"></label></oc-oneclick-form>'
              '<oc-button data-test="footer-next"><spl-button><span>Weiter</span></spl-button></oc-button>')
    page.main.call("""() => {
      const host = document.querySelector('spl-button');
      host.attachShadow({mode: 'open'}).innerHTML = '<button type="button"><slot></slot></button>';
      window.clicks = 0;
      host.addEventListener('click', () => window.clicks++);
    }""", page_realm=True)
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(snapshot_js, SMART))
    monkeypatch.setattr(preflight, '_SMART_NEXT_JS', _location(preflight._SMART_NEXT_JS, SMART))
    snapshot = preflight.inspect_page(page)
    assert snapshot.safe_next and not snapshot.boundary
    preflight.advance(snapshot, 'smartrecruiters')
    assert page.main.call('() => window.clicks', page_realm=True) == 1

    page.main.call("""() => {
      document.body.innerHTML = '<oc-screening-questions-form><label>Required question'
        + '<input required></label><label>Bosch Datenschutzerklärung'
        + '<input type="checkbox" required></label></oc-screening-questions-form>'
        + '<spl-button><span>Absenden</span></spl-button>';
      document.querySelector('spl-button').attachShadow({mode:'open'}).innerHTML =
        '<button type="button"><slot></slot></button>';
    }""", page_realm=True)
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(snapshot_js, SMART + '/screening'))
    snapshot = preflight.inspect_page(page)
    assert snapshot.boundary and snapshot.step == 'Application screening'
    assert not snapshot.safe_next
    assert snapshot.errors >= 2
    assert any(f.kind == 'checkbox' and f.required and f.blank for f in read_fields(form_frames(page)))
    with pytest.raises(UnsafeAction):
        preflight.advance(snapshot, 'smartrecruiters')


def test_workday_unknown_required_history_prevents_next(site, page, monkeypatch):
    _document(site, page, '<div data-automation-id="progressBarActiveStep">current step 1 of 6 My Information</div>'
              '<fieldset><legend>Have you ever worked for PwC?</legend>'
              '<label>Yes<input type="radio" name="history" required></label>'
              '<label>No<input type="radio" name="history" required></label></fieldset>'
              '<button data-automation-id="pageFooterNextButton">Save and Continue</button>')
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, WORKDAY))
    snapshot = preflight.inspect_page(page)
    assert snapshot.safe_next and snapshot.errors
    with pytest.raises(UnsafeAction):
        preflight.advance(snapshot, 'workday')
    assert all(f.blank for f in read_fields(form_frames(page)) if f.kind == 'radio')


@pytest.mark.parametrize('inert', [False, True])
def test_workday_posting_entry_reaches_gate_or_stops_after_one_inert_click(site, page, space, monkeypatch, inert):
    from kitty.core import jobs as jobs_core
    from kitty.core.apply import filler

    _document(site, page, '''
      <h1>Fixture role</h1>
      <button type="button" data-automation-id="jobPostingApplyButton">Apply</button>
      <script>
        window.entries = 0; window.submissions = 0;
        document.addEventListener('submit', e => { window.submissions++; e.preventDefault(); });
        document.querySelector('button').onclick = () => {
          window.entries++;
          if (window.inertEntry) return;
          document.body.innerHTML = '<button type="button" data-automation-id="applyManually">Apply Manually</button>';
          document.querySelector('button').onclick = () => {
            window.entries++;
            document.body.innerHTML = '<p>Loading application...</p>';
            setTimeout(() => {
              document.body.innerHTML = '<h1>Create Account</h1><form>'
                + '<label>Email<input type="email" name="email"></label>'
                + '<label>Password<input type="password" name="password"></label>'
                + '<button type="submit">Create Account</button></form>';
            }, 30);
          };
        };
      </script>''')
    page.main.call('(inert) => { window.inertEntry = inert; }', inert, page_realm=True)
    job, _ = jobs_core.upsert_job(
        space.conn, company='Fixture Co', title='Posting entry intern',
        url=page.main.url, tracks=[space.track.name],
    )
    monkeypatch.setattr(filler, 'detect_ats', lambda url: 'workday')
    monkeypatch.setattr(filler, 'wait_for_page',
                        lambda tab, **kwargs: preflight.wait_for_page(tab, seconds=0.4, **kwargs))
    initial = preflight.inspect_page(page)
    assert initial.entry == 'jobPostingApplyButton'
    assert not initial.step and not initial.safe_next
    report = filler.fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert report.status == 'blocked'
    assert report.filled == []
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None
    assert page.main.call('() => [window.entries, window.submissions]', page_realm=True) == [
        1 if inert else 2, 0,
    ]
    if not inert:
        assert preflight.inspect_page(page).blocker.startswith('login:')
        assert page.main.call('() => [...document.querySelectorAll("input")].map(e => e.value)') == ['', '']


@pytest.mark.parametrize('change', ['label', 'target', 'provider', 'identity', 'final', 'ats'])
def test_workday_posting_entry_rechecks_target_identity_and_action(site, page, change):
    url = site.origin + '/job/Fixture_123'
    site.pages['/job/Fixture_123'] = '''<!doctype html><body>
      <a data-automation-id="jobPostingApplyButton" href="/job/Fixture_123/apply">Apply</a>
      <script>window.clicks = 0;
        document.querySelector('a').onclick = e => { window.clicks++; e.preventDefault(); };
      </script>'''
    page.goto(url)
    snapshot = preflight.inspect_page(page)
    assert snapshot.entry == 'jobPostingApplyButton'
    page.main.call('''(change) => {
      const link = document.querySelector('a');
      if (change === 'label') link.textContent = 'Submit application';
      if (change === 'target') link.href = '/job/Other_456/apply';
      if (change === 'provider') link.href = 'https://unapproved.example/job/Fixture_123/apply';
      if (change === 'identity') document.body.insertAdjacentHTML('beforeend', '<input type=email name=email>');
      if (change === 'final') document.body.insertAdjacentHTML('beforeend', '<button>Submit application</button>');
    }''', change)
    with pytest.raises(UnsafeAction):
        preflight.advance(snapshot, 'unknown' if change == 'ats' else 'workday')
    assert page.main.call('() => window.clicks', page_realm=True) == 0
    assert page.main.url == url


@pytest.mark.parametrize('role', ['checkbox', 'radio', 'combobox'])
def test_required_committed_aria_button_is_represented_before_next(site, page, role):
    state = 'aria-checked="true"' if role != 'combobox' else 'aria-controls="choices" aria-expanded="false"'
    _document(site, page, f'''
      <div data-automation-id="progressBarActiveStep">current step 1 of 6 My Information</div>
      <button type="button" role="{role}" aria-required="true" {state}
        aria-label="Known answer">Known answer</button>
      <div role="listbox" id="choices"><div role="option" aria-selected="true">Known answer</div></div>
      <button type="button" data-automation-id="bottom-navigation-next-button"
        onclick="window.nextClicks++">Next</button>
      <script>window.nextClicks = 0; window.submissions = 0;
        document.addEventListener('submit', e => {{ window.submissions++; e.preventDefault(); }});
      </script>''')
    snapshot = preflight.inspect_page(page)
    fields = read_fields(form_frames(page))
    assert any(f.kind == role and f.required and not f.blank for f in fields)
    assert not snapshot.unsupported_required and not snapshot.errors
    preflight.advance(snapshot, 'workday')
    assert page.main.call('() => [window.nextClicks, window.submissions]', page_realm=True) == [1, 0]


def test_submit_button_disguised_as_required_aria_control_stays_unsupported(site, page):
    _document(site, page, '''
      <div data-automation-id="progressBarActiveStep">current step 1 of 6 My Information</div>
      <form><button type="submit" role="checkbox" aria-required="true" aria-checked="true">Known answer</button></form>
      <button type="button" data-automation-id="bottom-navigation-next-button">Next</button>
      <script>window.submissions = 0;
        document.addEventListener('submit', e => { window.submissions++; e.preventDefault(); });
      </script>''')
    snapshot = preflight.inspect_page(page)
    assert snapshot.unsupported_required
    with pytest.raises(UnsafeAction):
        preflight.advance(snapshot, 'workday')
    assert page.main.call('() => window.submissions', page_realm=True) == 0


@pytest.mark.parametrize('stuck', [False, True])
def test_oracle_loading_overlay_waits_before_guarded_posting_entry(site, page, space, monkeypatch, stuck):
    from kitty.core import jobs as jobs_core
    from kitty.core.apply import filler

    oracle = 'https://hdnn.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/114233'
    _document(site, page, '''
      <style>.app-loading-spinner {position:fixed;inset:0;z-index:1000;background:white}</style>
      <div class="app-loading-spinner"></div>
      <div class="job-details__section-apply-button">
        <button type="button" class="apply-now-button" style="margin-top:1500px">Apply Now</button>
      </div>
      <script>
        document.body.classList.add('app-loading');
        window.entries = []; window.submissions = 0;
        document.addEventListener('submit', e => { window.submissions++; e.preventDefault(); });
        document.querySelector('button').onclick = e => {
          window.entries.push(e.isTrusted);
          document.body.innerHTML = '<h1>Sign in</h1><form><input type="email" name="email">'
            + '<input type="password" name="password"><button type="submit">Sign in</button></form>';
        };
      </script>''')
    snapshot_js = _location(preflight._SNAPSHOT, oracle)
    monkeypatch.setattr(preflight, '_SNAPSHOT', snapshot_js)
    monkeypatch.setattr(preflight, '_PROVIDER_ENTRY_JS', _location(preflight._PROVIDER_ENTRY_JS, oracle))
    initial = preflight.inspect_page(page)
    assert initial.entry == 'provider-apply' and initial.blocker.startswith('loading:')
    with pytest.raises(UnsafeAction):
        preflight.advance(initial, 'oracle')
    if not stuck:
        page.main.call("""() => setTimeout(() => {
          document.body.classList.remove('app-loading');
          document.querySelector('.app-loading-spinner').remove();
        }, 100)""")
    job, _ = jobs_core.upsert_job(
        space.conn, company='Fixture Co', title='Oracle entry intern',
        url=page.main.url, tracks=[space.track.name],
    )
    monkeypatch.setattr(filler, 'detect_ats', lambda url: 'oracle')
    monkeypatch.setattr(filler, 'wait_for_page',
                        lambda tab, **kwargs: preflight.wait_for_page(tab, seconds=0.6, **kwargs))
    report = filler.fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert report.status == 'blocked'
    assert report.note.startswith('loading:' if stuck else 'login:')
    assert report.filled == []
    assert page.main.call('() => [window.entries, window.submissions]', page_realm=True) == [
        [] if stuck else [True], 0,
    ]
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None


@pytest.mark.parametrize('mode', ['visible', 'hidden', 'zero-size', 'aria-hidden', 'passive'])
def test_datadome_challenge_blocks_before_fields_upload_and_next(site, page, space, monkeypatch, mode):
    from kitty.core import jobs as jobs_core
    from kitty.core.apply import filler

    _document(site, page, '''
      <oc-oneclick-form><form id="application-form">
        <label>First Name<input name="first_name" required></label>
        <label>Resume<input name="resume" type="file" required></label>
        <button type="submit">Submit application</button>
      </form></oc-oneclick-form>
      <oc-button data-test="footer-next"><button type="button">Weiter</button></oc-button>
      <iframe title="Verification system"></iframe>
      <script>
        window.controlActions = 0;
        document.addEventListener('click', () => window.controlActions++);
        document.addEventListener('input', () => window.controlActions++);
        document.addEventListener('change', () => window.controlActions++);
        document.addEventListener('submit', e => { window.controlActions++; e.preventDefault(); });
      </script>''')
    source = 'https://geo.captcha-delivery.com/' + ('js/' if mode == 'passive' else 'captcha/')
    page.main.call("""(source, mode) => {
      const frame = document.querySelector('iframe');
      Object.defineProperty(frame, 'src', {get: () => source});
      if (mode === 'hidden') frame.style.display = 'none';
      if (mode === 'zero-size') frame.style.cssText = 'width:0;height:0;border:0';
      if (mode === 'aria-hidden') frame.setAttribute('aria-hidden', 'true');
    }""", source, mode, page_realm=True)
    # Read the mocked iframe source in its owning realm without a network request.
    original_call = page.main.__class__.call

    def in_page_realm(frame, function, *args, **kwargs):
        return original_call(frame, function, *args, **{**kwargs, 'page_realm': True})

    monkeypatch.setattr(page.main.__class__, 'call', in_page_realm)
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, SMART))
    state = preflight.inspect_page(page)
    if mode != 'visible':
        assert state.blocker is None
        return
    assert state.blocker.startswith('captcha:')
    job, _ = jobs_core.upsert_job(
        space.conn, company='Fixture Co', title='DataDome boundary intern',
        url=page.main.url, tracks=[space.track.name],
    )
    report = filler.fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert report.status == 'blocked' and report.note.startswith('captcha:')
    assert report.filled == []
    assert page.main.call("""() => [
      document.querySelector('[name=first_name]').value,
      document.querySelector('[name=resume]').files.length, window.controlActions,
    ]""") == ['', 0, 0]
    assert jobs_core.get_job(space.conn, job.id).fill_ready_at is None


def test_datadome_appearing_after_safe_next_snapshot_stops_before_pointer_action(site, page, monkeypatch):
    _document(site, page, '''
      <oc-oneclick-form></oc-oneclick-form>
      <oc-button data-test="footer-next"><button type="button" onclick="window.clicks++">Weiter</button></oc-button>
      <script>window.clicks = 0;</script>''')
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, SMART))
    state = preflight.inspect_page(page)
    assert state.safe_next and state.blocker is None
    # Install the owner in the sandbox used by advance's final recheck.
    page.main.call("""() => {
      const frame = document.createElement('iframe');
      Object.defineProperty(frame, 'src', {get: () => 'https://geo.captcha-delivery.com/captcha/'});
      document.body.append(frame);
    }""")
    with pytest.raises(UnsafeAction):
        preflight.advance(state, 'smartrecruiters')
    assert page.main.call('() => window.clicks', page_realm=True) == 0


def test_required_container_with_supported_unanswered_radios_is_not_unsupported(site, page, space, monkeypatch):
    from kitty.core import jobs as jobs_core
    from kitty.core.apply import filler

    _document(site, page, '''
      <div data-automation-id="progressBarActiveStep">current step 1 of 6 My Information</div>
      <div class="question">
        <label id="radio-label1">Have you ever worked for PwC? *</label>
        <div id="previousWorker--candidateIsPreviousWorker" aria-required="true" aria-labelledby="radio-label1">
          <label>Yes<input name="candidateIsPreviousWorker" type="radio" value="true"></label>
          <label>No<input name="candidateIsPreviousWorker" type="radio" value="false"></label>
        </div>
      </div>
      <button data-automation-id="pageFooterNextButton" onclick="window.nextClicks++">Save and Continue</button>
      <script>window.nextClicks = 0;</script>''')
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, WORKDAY))
    snapshot = preflight.inspect_page(page)
    assert not snapshot.unsupported_required
    radios = [f for f in read_fields(form_frames(page)) if f.kind == 'radio']
    assert radios and all(f.blank and f.required for f in radios)
    job, _ = jobs_core.upsert_job(
        space.conn, company='Fixture Co', title='Unanswered history intern',
        url=page.main.url, tracks=[space.track.name],
    )
    monkeypatch.setattr(filler, 'detect_ats', lambda url: 'workday')
    report = filler.fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert report.status != 'ready'
    assert page.main.call('() => window.nextClicks', page_realm=True) == 0
    assert page.main.call('() => [...document.querySelectorAll("input")].some(e => e.checked)') is False


@pytest.mark.parametrize("question", [
    "Have you ever worked for Fixture? If so, please answer the questions below. "
    "If not, continue to the next page.",
    "Have you previously worked as Employee or Intern for Fixture Co? "
    "If Yes, provide additional information; if No, please continue.",
])
def test_saved_employer_history_advances_to_review_without_submit_or_manual_overwrite(
    site, page, space, monkeypatch, question,
):
    from dataclasses import replace

    from kitty.config import AnswerEntry, AnswersFile
    from kitty.core import jobs as jobs_core
    from kitty.core.apply import filler
    from kitty.core.apply.page import upload

    _document(site, page, f'''
      <div data-automation-id="progressBarActiveStep">current step 1 of 6 My Information</div>
      <form onsubmit="window.submissions++;return false">
        <label>Last Name<input id="surname" value="Manually retained surname" required></label>
        <label>Resume<input type="file" required></label>
        <div class="question">
          <label id="history-label">{question} *</label>
          <div aria-required="true" aria-labelledby="history-label">
            <label>Yes<input name="history" type="radio" value="true"></label>
            <label>No<input name="history" type="radio" value="false"></label>
          </div>
        </div>
        <button type="submit" id="final-submit" hidden>Submit application</button>
      </form>
      <button data-automation-id="pageFooterNextButton">Save and Continue</button>
      <script>
        window.nextClicks=0;window.submissions=0;window.selectedHistory=null;window.choiceTrusted=[];
        for (const radio of document.querySelectorAll('input[type=radio]'))
          radio.addEventListener('click', e => window.choiceTrusted.push(e.isTrusted));
        document.querySelector('[data-automation-id=pageFooterNextButton]').onclick = () => {{
          window.nextClicks++;
          window.selectedHistory=document.querySelector('input[name=history]:checked')?.value;
          document.querySelector('[data-automation-id=progressBarActiveStep]').textContent='Review';
          document.querySelector('[data-automation-id=pageFooterNextButton]').textContent='Submit application';
          document.querySelector('#final-submit').hidden=false;
        }};
      </script>''')
    config = replace(space.config, answers=AnswersFile(answers=[
        AnswerEntry(
            question="Have you previously been employed by this company?", answer="No",
            match=["worked for this company", "previously worked as employee or intern"],
            exclude_companies=["Excluded Co"],
        ),
    ]))
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, WORKDAY))
    monkeypatch.setattr(filler, 'detect_ats', lambda url: 'workday')
    resume = next(f for f in read_fields(form_frames(page)) if f.kind == 'file')
    upload(page.main, resume.key, space.settings.root / 'resume.pdf')
    job, _ = jobs_core.upsert_job(
        space.conn, company='Fixture Co', title='Saved history intern',
        url=page.main.url, tracks=[space.track.name],
    )

    report = filler.fill_job(page, space.conn, space.settings, config, job, resume_tab=True)

    assert report.status == 'ready' and report.pages == 2 and not report.unanswered
    assert page.main.call('() => window.nextClicks', page_realm=True) == 1
    assert page.main.call('() => window.selectedHistory', page_realm=True) == 'false'
    assert page.main.call('() => window.choiceTrusted', page_realm=True) == [True]
    assert page.main.call('() => document.querySelector("#surname").value') == 'Manually retained surname'
    assert page.main.call('() => window.submissions', page_realm=True) == 0
    final = preflight.inspect_page(page)
    assert final.review and not final.safe_next and not final.errors
    with pytest.raises(UnsafeAction):
        preflight.advance(final, 'workday')


@pytest.mark.parametrize('role, control, unsupported', [
    ('', '<input name="answer" aria-label="Required custom answer" value="Known">', False),
    ('group', '<input name="answer" aria-label="Required custom answer" value="Known">', False),
    ('radiogroup', '<input name="answer" aria-label="Required custom answer" value="Known">', True),
    ('radiogroup', '<input name="answer" role="radio" value="Known">', True),
    ('radiogroup', '<input type="checkbox" name="answer" checked>', True),
    ('radiogroup', '<input type="radio" name="answer" checked>', False),
    ('radiogroup', '<input type="radio" name="answer" checked disabled>', True),
    ('radiogroup', '<button type="button" role="radio" aria-checked="true">Known</button>', False),
])
def test_required_owner_descendants_match_choice_semantics(site, page, role, control, unsupported):
    _document(site, page, f'''
      <div data-automation-id="progressBarActiveStep">current step 1 of 6 My Information</div>
      <div role="{role}" aria-required="true" aria-label="Required answer">{control}</div>
      <button type="button" data-automation-id="bottom-navigation-next-button"
        onclick="window.nextClicks++">Next</button>
      <script>window.nextClicks = 0;</script>''')
    snapshot = preflight.inspect_page(page)
    assert snapshot.safe_next
    assert snapshot.unsupported_required is unsupported
    if unsupported:
        with pytest.raises(UnsafeAction):
            preflight.advance(snapshot, 'workday')
    else:
        preflight.advance(snapshot, 'workday')
    assert page.main.call('() => window.nextClicks', page_realm=True) == (0 if unsupported else 1)


def test_segmented_pin_without_autocomplete_is_a_verification_gate(site, page):
    _document(site, page, '<p>A code was sent by email.</p>' +
              ''.join(f'<input id="pin-code-{i}" type="text" inputmode="numeric" autocomplete="off">' for i in range(1, 7)) +
              '<button type="button">Verify</button>')
    snapshot = preflight.inspect_page(page)
    assert snapshot.blocker and snapshot.blocker.startswith('verification:')
    assert not snapshot.entry and not snapshot.boundary


ORACLE = 'https://ibqbjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/Honeywell/job/158957'


@pytest.mark.parametrize('markup, url, rejected', [
    ('<div class="cookie-consent-modal"><div role="dialog">We use cookies.<button type="button" class="cookie-consent__button button accept">ACCEPT ALL</button>'
     '<button type="button" class="cookie-consent__button button decline">REJECT ALL</button></div></div>', ORACLE, True),
    ('<div class="cookie-consent-modal"><div role="dialog">We use cookies.'
     '<button type="button" class="cookie-consent__button button decline">REJECT ALL</button></div></div>', 'https://example.com/jobs/1', False),
    ('<div id="ketch-consent-banner" role="dialog">We use cookies.<button id="ketch-banner-button-primary">Accept All</button>'
     '<button id="ketch-banner-button-secondary">Reject All</button></div>', 'https://us-careers-rivian.icims.com/jobs/1/login', True),
    ('<div id="ketch-consent-banner" role="dialog">We use cookies.<button id="ketch-banner-button-secondary">Accept All</button></div>',
     'https://us-careers-rivian.icims.com/jobs/1/login', False),
])
def test_only_exact_provider_cookie_reject_clears_consent_gate(site, page, monkeypatch, markup, url, rejected):
    _document(site, page, markup)
    page.main.call('() => { window.clicks = []; document.addEventListener("click", e => window.clicks.push(e.target.innerText), true); }', page_realm=True)
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, url))
    snapshot = preflight.inspect_page(page)
    if rejected:
        assert snapshot.blocker is None and snapshot.entry == 'cookie-reject'
        assert 'reject' in snapshot.signature.lower()
    else:
        assert snapshot.blocker and snapshot.blocker.startswith('consent:')
    assert page.main.call('() => window.clicks', page_realm=True) == []


EIGHTFOLD_FORM = '''<form><input id="Contact_Information_firstname" data-test-id="Contact_Information_firstname" required>
<input id="Contact_Information_email" data-test-id="Contact_Information_email" required>
<input name="Application_Questions_q_preferredName" autocomplete="one-time-code">
<input name="Application_Questions_q_custEmployeeID" autocomplete="one-time-code">
<button type="button">Submit application</button></form>'''


@pytest.mark.parametrize('markup, verification', [
    (EIGHTFOLD_FORM, False),
    (EIGHTFOLD_FORM.replace('q_custEmployeeID', 'verification_code'), True),
    ('<form><input name="answer" autocomplete="one-time-code"><button type="button">Continue</button></form>', True),
])
def test_autofill_suppression_is_not_verification_but_code_fields_are(site, page, monkeypatch, markup, verification):
    _document(site, page, markup)
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(
        preflight._SNAPSHOT, 'https://lockheedmartin.eightfold.ai/careers/apply?pid=996477261987'))
    snapshot = preflight.inspect_page(page)
    if verification:
        assert snapshot.blocker and snapshot.blocker.startswith('verification:')
    else:
        assert snapshot.blocker is None and snapshot.step == 'Application details' and snapshot.boundary


@pytest.mark.parametrize('href, entry', [
    ('https://lockheedmartin.eightfold.ai/careers/apply?pid=996477261987', True),
    ('https://lockheedmartin.eightfold.ai/careers/apply?pid=111', False),
    ('https://other.example/careers/apply?pid=996477261987', False),
    # Eightfold copies the posting's tracking parameters into the CTA (N2: 3 of 8 missed).
    ('https://lockheedmartin.eightfold.ai/careers/apply?pid=996477261987&utm_source=indeed&domain=micron.com', True),
    ('https://lockheedmartin.eightfold.ai/careers/apply?pid=996477261987&utm_source=appcast_indeedo&hl=en'
     '&domain=starbucks.com&_ccid=1700000000abc', True),
    ('https://lockheedmartin.eightfold.ai/careers/apply?utm_source=indeed&pid=996477261987', True),
    ('https://lockheedmartin.eightfold.ai/careers/apply?pid=111&utm_source=indeed', False),
    ('https://lockheedmartin.eightfold.ai/careers/apply?pid=996477261987&pid=111', False),
    ('https://lockheedmartin.eightfold.ai/careers/apply?pid=996477261987&next=/submit', False),
])
def test_eightfold_entry_requires_exact_same_pid_link(site, page, monkeypatch, href, entry):
    _document(site, page, '<h1>Software Engineer Intern</h1><a class="action-2lm8g linkbutton-module_link-button__-XtWC" '
              f'href="{href.replace("&", "&amp;")}">Apply Now</a>')
    url = 'https://lockheedmartin.eightfold.ai/careers/job/996477261987'
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, url))
    snapshot = preflight.inspect_page(page)
    assert (snapshot.entry == 'provider-apply') is entry
    assert snapshot.entry in ('provider-apply', '')


CUSTOM_POSTING = 'https://careers.synth-grocers.com/jobs/12345'
ICIMS_LOGIN = 'https://careers-synth.icims.com/jobs/12345/login'


@pytest.mark.parametrize('markup, entry_url', [
    # Jibe-style front: the visible Apply link names the iCIMS posting.
    (f'<h1>Firmware Intern</h1><a class="btn" href="{ICIMS_LOGIN}?mode=job&amp;iis=Synth" target="_blank">'
     '<span>Apply Now</span></a>', ICIMS_LOGIN + '?mode=job&iis=Synth'),
    # Every copy of the link agrees on one destination.
    (f'<a href="{ICIMS_LOGIN}">Apply</a><footer><a href="{ICIMS_LOGIN}#apply">Apply now</a></footer>', ICIMS_LOGIN),
    # Two destinations, a hidden link, a link inside a form, or an unrecognized destination: no entry.
    (f'<a href="{ICIMS_LOGIN}">Apply</a><a href="https://careers-synth.icims.com/jobs/99999/login">Apply</a>', ''),
    (f'<a href="{ICIMS_LOGIN}" style="display:none">Apply</a>', ''),
    (f'<form><a href="{ICIMS_LOGIN}">Apply</a></form>', ''),
    ('<a href="https://careers.synth-grocers.com/apply/12345">Apply</a>', ''),
    # A header "Apply" link to a board's home names no posting.
    ('<a href="https://synth.wd5.myworkdayjobs.com/External">Apply</a>', ''),
    # Not an Apply label.
    (f'<a href="{ICIMS_LOGIN}">Submit application</a>', ''),
])
def test_custom_site_apply_link_is_an_entry_only_to_a_recognized_posting(site, page, monkeypatch, markup, entry_url):
    _document(site, page, markup)
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, CUSTOM_POSTING))
    snapshot = preflight.inspect_page(page)
    assert (snapshot.entry, snapshot.entry_url) == (('apply-link', entry_url) if entry_url else ('', ''))


def test_apply_link_to_an_excluded_flow_stays_an_entry_for_its_handoff(site, page, monkeypatch):
    _document(site, page, '<a href="https://smartapply.indeed.com/beta/indeedapply/form/12345">Apply now</a>')
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, CUSTOM_POSTING))
    snapshot = preflight.inspect_page(page)
    assert snapshot.entry == 'apply-link' and 'smartapply.indeed.com' in snapshot.entry_url


@pytest.mark.parametrize('url, entry', [
    ('https://jobs.synth-grocers.com/en/sites/CX_2001/job/123456', 'provider-apply'),
    ('https://jobs.synth-grocers.com/en/sites/Other_1/job/123456', ''),
])
def test_oracle_recruiting_on_a_company_domain_has_its_provider_entry(site, page, monkeypatch, url, entry):
    _document(site, page, '<h1>Firmware Intern</h1><div class="job-details__section-apply-button">'
              '<button type="button" class="apply-now-button">Apply Now</button></div>')
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, url))
    assert preflight.inspect_page(page).entry == entry


def test_oracle_company_domain_cookie_decline_is_the_exact_reject(site, page, monkeypatch):
    _document(site, page, '<div class="cookie-consent-modal" role="dialog">We use cookies.'
              '<button type="button" class="cookie-consent__button decline">Reject all</button></div>')
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(
        preflight._SNAPSHOT, 'https://jobs.synth-grocers.com/en/sites/CX_2001/job/123456'))
    snapshot = preflight.inspect_page(page)
    assert snapshot.blocker is None and snapshot.entry == 'cookie-reject'


def test_filler_follows_a_routed_apply_link_by_navigation_and_stores_it(site, page, space, monkeypatch):
    """End to end on the local HTTPS site: a GET to the routed posting, never a click or POST."""
    from kitty.core import jobs as jobs_core
    from kitty.core.apply import filler

    destination = site.origin + '/icims/jobs/12345/login'
    site.pages['/careers/jobs/12345'] = (
        '<!doctype html><body><h1>Firmware Intern</h1>'
        f'<a href="{destination}?utm_source=jibe">Apply Now</a>')
    site.pages['/icims/jobs/12345/login'] = (
        '<!doctype html><body><h2>Enter Your Information</h2><form><label>Email'
        '<input type="email" name="css_loginName"></label><input type="submit" value="Next"></form>')
    # The local site stands in for an iCIMS host: route its login page as that family.
    def route(page_url, link):
        return (link.split('?')[0], None) if '/icims/' in link and '/careers/' in page_url else (None, None)
    monkeypatch.setattr(preflight, 'route_apply_link', route)
    monkeypatch.setattr(filler, 'route_apply_link', route)
    monkeypatch.setattr(filler, 'wait_for_page',
                        lambda tab, **kwargs: preflight.wait_for_page(tab, seconds=1, **kwargs))
    job, _ = jobs_core.upsert_job(space.conn, company='Synthetic Grocers', title='Firmware Intern',
                                  url=site.origin + '/careers/jobs/12345', tracks=[space.track.name])
    report = filler.fill_job(page, space.conn, space.settings, space.config, job)
    assert report.url == destination and report.status == 'blocked'
    assert report.note.startswith('login:') and report.filled == []
    assert ('GET', '/icims/jobs/12345/login', {}) in site.requests
    assert not [request for request in site.requests if request[0] == 'POST']
    assert jobs_core.get_job(space.conn, job.id).apply_url == destination


def test_filler_stores_a_javascript_redirect_destination_and_closes_from_it(site, page, space, monkeypatch):
    """appcast-style: the browser follows the JS redirect; the landed posting is stored, then checked."""
    from kitty.core import jobs as jobs_core
    from kitty.core.apply import filler

    site.pages['/appcast/t/abc'] = ('<!doctype html><title>Redirecting...</title><script>'
                                    'location.replace("/careers/job/56789?utm_source=appcast");</script>')
    site.pages['/careers/job/56789'] = '<!doctype html><body><h1>This job is no longer available</h1>'
    monkeypatch.setattr(filler, 'redirect_source', lambda url: 'appcast' if '/appcast/' in url else None)
    monkeypatch.setattr(filler, 'wait_for_page',
                        lambda tab, **kwargs: preflight.wait_for_page(tab, seconds=1, **kwargs))
    job, _ = jobs_core.upsert_job(space.conn, company='Synthetic Coffee', title='Barista Intern',
                                  url=site.origin + '/appcast/t/abc', tracks=[space.track.name])
    report = filler.fill_job(page, space.conn, space.settings, space.config, job)
    assert report.url == site.origin + '/careers/job/56789'
    assert report.note.startswith('closed:') and report.cause == 'unavailable'
    stored = jobs_core.get_job(space.conn, job.id)
    assert stored.apply_url == site.origin + '/careers/job/56789' and not stored.is_active
    assert not [request for request in site.requests if request[0] == 'POST']


def test_icims_email_login_is_an_account_gate_not_an_unsupported_page(site, page, monkeypatch):
    _document(site, page, '<h2>Enter Your Information</h2><form><label>Email<input type="email" name="css_loginName"></label>'
              '<label><input type="checkbox" name="accept_privacy">I confirm I have read the Candidate Data Privacy Notice</label>'
              '<input type="submit" value="Next"></form>')
    monkeypatch.setattr(preflight, '_SNAPSHOT', _location(preflight._SNAPSHOT, 'https://us-careers-rivian.icims.com/jobs/33833/login'))
    snapshot = preflight.inspect_page(page)
    assert snapshot.blocker and snapshot.blocker.startswith('login:')


def test_recommit_retypes_only_an_exact_restored_value(site, page):
    from kitty.core.apply.page import recommit_text
    # Framework-like tracker: a value counts only after an input event changes it.
    _document(site, page, '''<form><input id="first" value="Pat" aria-invalid="true">
      <input id="other" value="Manual" aria-invalid="true"></form><script>
      for (const e of document.querySelectorAll('input')) { let last = e.value;
        e.addEventListener('input', () => { if (e.value !== last && e.value) e.setAttribute('aria-invalid', 'false'); last = e.value; }); }
      </script>''')
    fields = {f.element_id: f for f in read_fields(form_frames(page))}
    frame = form_frames(page)[0]
    assert recommit_text(frame, fields['first'].key, 'Pat')
    assert not recommit_text(frame, fields['other'].key, 'Pat')
    state = page.main.call('() => [...document.querySelectorAll("input")].map(e => [e.value, e.getAttribute("aria-invalid")])', page_realm=True)
    assert state == [['Pat', 'false'], ['Manual', 'true']]
