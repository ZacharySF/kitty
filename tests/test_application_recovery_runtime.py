"""Actual isolated Firefox exercises for recovery, ownership and cancellation."""

import pytest

from test_apply import (
    _assert_nothing_submitted, _attach_fixture_resume, _job, _js, _safety_form,
    _value, firefox, page, space,
)
from kitty.core import jobs
from kitty.core.apply.filler import fill_form, fill_job, fill_jobs
from kitty.core.apply.recovery_runtime import JobRecovery, PreparationStopped
from kitty.core.apply.watch import Watch
from kitty.settings import find_firefox


def test_transient_blur_rejection_is_retried_without_reupload_or_submission(space, page):
    job = _safety_form(space, page)
    _attach_fixture_resume(space, page)
    _js(page, """() => {
      window.resumeChanges = 0;
      document.querySelector('[name=resume]').addEventListener('change', () => window.resumeChanges++);
      document.querySelector('[name=first_name]').addEventListener('blur', event => {
        event.target.value = '';
      }, {once: true});
    }""")
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == 'ready'
    assert _value(page, '[name=first_name]') == space.config.profile.name.split()[0]
    assert any(event['action'] == 'retry' and event['result'] == 'completed' for event in result.recovery)
    assert _js(page, '() => window.resumeChanges') == 0
    _assert_nothing_submitted(page)


def test_missing_facts_are_handed_off_without_recovery_writes(space, page):
    job = _safety_form(space, page)
    _attach_fixture_resume(space, page)
    _js(page, """() => {
      const label = document.createElement('label');
      label.innerText = 'Explain an unlisted project requirement';
      const input = document.createElement('textarea');
      input.name = 'unlisted_requirement'; input.required = true;
      label.appendChild(input); document.querySelector('#application').prepend(label);
    }""")
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.cause == 'missing_applicant_fact'
    assert result.status != 'ready'
    assert _value(page, '[name=unlisted_requirement]') == ''
    assert not any(event['action'] in ('retry', 'reload') for event in result.recovery)
    _assert_nothing_submitted(page)


def test_recovery_refuses_same_origin_posting_replacement(space, page):
    job = _safety_form(space, page)
    guard = JobRecovery(space.conn, space.settings, space.config, job, page)
    page.goto(page.main.url + '?different-posting=1')
    with pytest.raises(PreparationStopped):
        guard.guard()
    assert _value(page, '[name=first_name]') == ''
    _assert_nothing_submitted(page)


def test_job_deadline_stops_before_a_new_field_write(space, page):
    job = _safety_form(space, page)
    guard = JobRecovery(space.conn, space.settings, space.config, job, page)
    guard.started -= space.config.apply.recovery.max_job_seconds + 1
    with pytest.raises(PreparationStopped):
        fill_form(page, space.config, None, checkpoint=guard.checkpoint)
    assert _value(page, '[name=first_name]') == ''
    _assert_nothing_submitted(page)


def test_cancel_without_submission_watching_preserves_completed_report(space):
    if find_firefox() is None:
        pytest.skip("firefox is not on PATH")
    first, second = _job(space, 'application_safety'), _job(space, 'greenhouse')
    monitor = Watch(hours=0)
    observed = []
    def published(reports):
        observed.extend(reports)
        monitor.stop.set()
    result = fill_jobs(space.settings, space.config, [first.id, second.id], headless=True,
                       pause=0, watch=monitor, on_report=published)
    assert [r.job_id for r in result] == [first.id]
    assert observed[0].job_id == first.id
    assert jobs.get_job(space.conn, second.id).fill_state == 'unattempted'


def test_feedback_is_generated_from_safe_categories_not_applicant_text(space, page):
    job = _safety_form(space, page)
    _attach_fixture_resume(space, page)
    marker = 'PRIVATE-SYNTHETIC-APPLICANT-ANSWER'
    _js(page, 'value => document.querySelector("[name=first_name]").value = value', marker)
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == 'ready'
    feedback = space.settings.data_dir / 'application-recovery' / 'PROTOCOL_FEEDBACK.md'
    assert marker not in feedback.read_text()
    stored = space.conn.execute('SELECT action,result,reason FROM application_recovery_events WHERE job_id=?', (job.id,)).fetchall()
    assert marker not in str([tuple(row) for row in stored])
    assert any(row['action'] == 'outcome' and row['result'] == 'completed' for row in stored)
    assert feedback.stat().st_mode & 0o077 == 0
    _assert_nothing_submitted(page)

def test_agent_recommits_exact_saved_invalid_text_and_preserves_manual_values(space, page):
    job = _safety_form(space, page)
    _attach_fixture_resume(space, page)
    _js(page, """saved => {
      const first = document.querySelector('[name=first_name]');
      first.value = saved;
      first.setAttribute('aria-invalid', 'true');
      first.setCustomValidity('State has not received this saved value');
      let cleared = false;
      first.addEventListener('input', () => {
        if (!first.value) cleared = true;
        if (cleared && first.value === saved) {
          first.setCustomValidity('');
          first.setAttribute('aria-invalid', 'false');
        }
      });
      const last = document.createElement('input');
      last.name = 'last_name'; last.setAttribute('aria-label', 'Last Name');
      last.value = 'Manual Family Name';
      document.querySelector('#application').prepend(last);
    }""", space.config.profile.given_name)
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status == 'ready'
    assert _value(page, '[name=first_name]') == space.config.profile.given_name
    assert _value(page, '[name=last_name]') == 'Manual Family Name'
    assert _js(page, "() => document.querySelector('[name=first_name]').checkValidity()")
    _assert_nothing_submitted(page)


def test_agent_does_not_recommit_a_different_invalid_manual_value(space, page):
    job = _safety_form(space, page)
    _attach_fixture_resume(space, page)
    _js(page, """() => {
      const first = document.querySelector('[name=first_name]');
      first.value = 'Manually corrected name';
      first.setAttribute('aria-invalid', 'true');
      first.setCustomValidity('Needs manual review');
      window.repairWrites = 0;
      first.addEventListener('input', () => window.repairWrites++);
    }""")
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status != 'ready'
    assert _value(page, '[name=first_name]') == 'Manually corrected name'
    assert _js(page, '() => window.repairWrites') == 0
    _assert_nothing_submitted(page)


def test_agent_does_not_count_dom_churn_or_write_logs_as_accepted_answers(space, page):
    job = _safety_form(space, page)
    _attach_fixture_resume(space, page)
    _js(page, """() => {
      window.rejectedWrites = 0;
      document.querySelector('[name=first_name]').addEventListener('blur', event => {
        if (!event.target.value) return;
        window.rejectedWrites++;
        event.target.value = '';
        const optional = document.createElement('input');
        optional.setAttribute('aria-label', 'Optional unknown field ' + window.rejectedWrites);
        document.querySelector('#application').append(optional);
      });
    }""")
    result = fill_job(page, space.conn, space.settings, space.config, job, resume_tab=True)
    assert result.status != 'ready'
    assert _value(page, '[name=first_name]') == ''
    assert _js(page, '() => window.rejectedWrites') == 2
    assert any(event['action'] == 'retry' and event['result'] == 'no_effect' for event in result.recovery)
    assert not any(event['action'] == 'retry' and event['result'] == 'completed' for event in result.recovery)
    _assert_nothing_submitted(page)


def test_agent_restores_owned_tab_before_repairing_blank_fields(space, page):
    from kitty.core.apply.bidi import open_tab
    from kitty.core.apply.filler import _recover_page
    from kitty.core.apply.preflight import inspect_page

    job = _safety_form(space, page)
    _attach_fixture_resume(space, page)
    fill_form(page, space.config, None)
    _js(page, "() => { document.querySelector('[name=first_name]').value = ''; }")
    recovery = JobRecovery(space.conn, space.settings, space.config, job, page)
    other = open_tab(page.bidi)
    try:
        other.activate()
        assert _js(page, "() => document.visibilityState") == 'hidden'
        repaired = _recover_page(
            recovery, inspect_page(page), None,
            retry=lambda: fill_form(page, space.config, None, checkpoint=recovery.checkpoint),
            absorb=lambda result: None, verify=recovery.checkpoint,
        )
        assert repaired
        assert _value(page, '[name=first_name]') == space.config.profile.given_name
        assert _js(page, "() => document.visibilityState") == 'visible'
        _assert_nothing_submitted(page)
    finally:
        other.close()

def test_recovery_never_recommits_invalid_consent_even_with_a_matching_saved_answer(space, page):
    from dataclasses import replace
    from kitty.config import AnswerEntry, AnswersFile

    job = _safety_form(space, page)
    _attach_fixture_resume(space, page)
    label = 'Einwilligung zur Datenschutzerklärung'
    config = replace(space.config, answers=AnswersFile(answers=[
        AnswerEntry(question=label, answer='Yes'),
    ]))
    _js(page, """label => {
      const e = document.createElement('input');
      e.name = 'consent'; e.required = true; e.setAttribute('aria-label', label);
      e.value = 'Yes'; e.setAttribute('aria-invalid', 'true');
      e.setCustomValidity('Explicit consent still required');
      window.consentWrites = 0;
      e.addEventListener('input', () => window.consentWrites++);
      document.querySelector('#application').prepend(e);
    }""", label)
    result = fill_job(page, space.conn, space.settings, config, job, resume_tab=True)
    assert result.status != 'ready'
    assert _js(page, '() => window.consentWrites') == 0
    assert _value(page, '[name=consent]') == 'Yes'
    _assert_nothing_submitted(page)
