"""One-use scoped email transactions against a real isolated HTTPS application."""
import json
from datetime import datetime,timedelta,timezone

import pytest

from kitty.config import AuthenticationSettings,EmailVerificationSettings,EmailVerificationScope
from kitty.core.apply import email_verification as module
from kitty.core.apply.credentials import ApplicationCredentials
from kitty.core.apply.recovery_runtime import PreparationStopped
from kitty.core.apply.verification_mail import VerificationMessage
from test_application_auth import EMAIL,PASSWORD,_job,_posts,firefox,https_site,page,site

CODE='734219'
SENDER='accounts@employer.example'
FORM='''<!doctype html><title>Verify email</title><p>We sent an email verification code.</p>
<form><label>Email verification code<input name="otp" autocomplete="one-time-code"></label>
<button type="button" onclick="if(document.querySelector('[name=otp]').value==='734219'){location.href='/application'}else{document.querySelector('output').textContent='Invalid code'}">Verify</button><output></output></form>'''


def _policy(site):
    return AuthenticationSettings(enabled=True,approved_origins=[site.origin],email_verification=EmailVerificationSettings(
        enabled=True,scopes=[EmailVerificationScope(origin=site.origin,sender_email=SENDER)],max_wait_seconds=5))


def _message(**overrides):
    fields=dict(id='synthetic-message-1',sender=SENDER,recipients=(EMAIL,),
                received_at=datetime.now(timezone.utc)-timedelta(seconds=1),
                subject='Confirm your identity',text=f'Your verification code is: {CODE}',links=())
    return VerificationMessage(**(fields|overrides))


def _run(workspace,site,page,monkeypatch,message,*,path='/email-check'):
    site.pages[path]=FORM
    page.goto(site.origin+path)
    job=_job(workspace,site,path)
    monkeypatch.setattr(module,'read_verification_mail',lambda *args,**kwargs:[message])
    result=module.complete_email_verification(page,workspace.conn,job.id,_policy(site),ApplicationCredentials(EMAIL,PASSWORD),
        requested_at=datetime.now(timezone.utc)-timedelta(seconds=3),request_sent=True)
    return job,result


def test_fresh_email_unlocks_application_once_without_submitting(workspace,site,page,monkeypatch):
    job,result=_run(workspace,site,page,monkeypatch,_message())
    assert result.verified and result.acted
    assert page.main.call("()=>!!document.querySelector('#application')",page_realm=True)
    assert _posts(site)==[]
    rows=workspace.conn.execute('SELECT * FROM application_email_verification_requests WHERE job_id=?',(job.id,)).fetchall()
    assert [row['state'] for row in rows]==['verified']
    stored=json.dumps([dict(row) for row in rows])
    assert CODE not in stored and EMAIL not in stored and PASSWORD not in stored
    page.goto(site.origin+'/email-check')
    replay=module.complete_email_verification(page,workspace.conn,job.id,_policy(site),ApplicationCredentials(EMAIL,PASSWORD))
    assert not replay.verified and not replay.acted
    assert page.main.call("()=>document.querySelector('[name=otp]').value",page_realm=True)==''
    assert _posts(site)==[]


@pytest.mark.parametrize('change',[{'sender':'other@employer.example'},{'recipients':('other@example.invalid',)},
    {'received_at':datetime.now(timezone.utc)-timedelta(minutes=20)},
    {'received_at':datetime.now(timezone.utc)+timedelta(days=1)},
    {'received_at':datetime(2026,1,1)}])
def test_mismatched_email_never_enters_the_code(workspace,site,page,monkeypatch,change):
    job,result=_run(workspace,site,page,monkeypatch,_message(**change))
    assert not result.verified and not result.acted
    assert page.main.call("()=>document.querySelector('[name=otp]').value",page_realm=True)==''
    assert _posts(site)==[]
    assert workspace.conn.execute('SELECT message_binding FROM application_email_verification_requests WHERE job_id=?',(job.id,)).fetchone()[0] is None


def test_an_ambiguous_code_preserves_the_challenge(workspace,site,page,monkeypatch):
    _,result=_run(workspace,site,page,monkeypatch,_message(text='Your verification code is: 734219\nYour verification code is: 843210'))
    assert not result.verified and not result.acted
    assert page.main.call("()=>document.querySelector('[name=otp]').value",page_realm=True)==''
    assert _posts(site)==[]


def test_only_supported_same_origin_verification_link_is_followed(workspace,site,page,monkeypatch):
    site.pages['/email-link']='<!doctype html><p>Check your email to verify your email.</p>'
    site.pages['/verify-email']='''<!doctype html><form id="application"><label>Preferred name<input name="preferred_name"></label><button type="submit">Submit application</button></form>'''
    page.goto(site.origin+'/email-link')
    job=_job(workspace,site,'/email-link')
    monkeypatch.setattr(module,'read_verification_mail',lambda *args,**kwargs:[_message(text='Confirm your email.',links=(site.origin+'/verify-email?token=SyntheticToken',))])
    result=module.complete_email_verification(page,workspace.conn,job.id,_policy(site),ApplicationCredentials(EMAIL,PASSWORD),
        requested_at=datetime.now(timezone.utc)-timedelta(seconds=3),request_sent=True)
    assert result.verified
    assert page.main.call("()=>!!document.querySelector('#application')",page_realm=True)
    assert _posts(site)==[]


def test_non_email_mfa_remains_a_human_gate(workspace,site,page,monkeypatch):
    site.pages['/mfa']=FORM.replace('We sent an email verification code.','Open your authenticator app. This is not an email code.')
    page.goto(site.origin+'/mfa')
    job=_job(workspace,site,'/mfa')
    def forbidden(*args,**kwargs):
        pytest.fail('Non-email MFA must not read the mailbox')
    monkeypatch.setattr(module,'read_verification_mail',forbidden)
    result=module.complete_email_verification(page,workspace.conn,job.id,_policy(site),ApplicationCredentials(EMAIL,PASSWORD))
    assert not result.verified and not result.acted
    assert page.main.call("()=>document.querySelector('[name=otp]').value",page_realm=True)==''
    assert workspace.conn.execute('SELECT COUNT(*) FROM application_email_verification_requests WHERE job_id=?',(job.id,)).fetchone()[0]==0


def test_stop_after_mail_read_prevents_code_entry(workspace,site,page,monkeypatch):
    site.pages['/email-check']=FORM
    page.goto(site.origin+'/email-check')
    job=_job(workspace,site,'/email-check')
    stopped=False
    def messages(*args,**kwargs):
        nonlocal stopped
        stopped=True
        return [_message()]
    def checkpoint():
        if stopped:
            raise PreparationStopped('stopped')
    monkeypatch.setattr(module,'read_verification_mail',messages)
    with pytest.raises(PreparationStopped):
        module.complete_email_verification(page,workspace.conn,job.id,_policy(site),ApplicationCredentials(EMAIL,PASSWORD),
            requested_at=datetime.now(timezone.utc)-timedelta(seconds=3),request_sent=True,checkpoint=checkpoint)
    assert page.main.call("()=>document.querySelector('[name=otp]').value",page_realm=True)==''
    assert _posts(site)==[]


def test_link_selection_does_not_promote_other_origins_or_malformed_urls():
    assert module._link(('https://other.example/verify-email?token=x','https://employer.example:broken/verify-email','https://employer.example/apply'), 'https://employer.example') is None


def test_resend_waits_for_reenabled_challenge_without_repeating_action(workspace,site,page,monkeypatch):
    site.pages['/resend']=FORM+f'<p>{EMAIL}</p>'+'''<button type="button" onclick="document.querySelector('form button').disabled=true;this.remove();setTimeout(()=>document.querySelector('form button').disabled=false,250)">Send New Code</button>'''
    page.goto(site.origin+'/resend')
    job=_job(workspace,site,'/resend')
    monkeypatch.setattr(module,'read_verification_mail',lambda *args,**kwargs:[_message(received_at=datetime.now(timezone.utc))])
    result=module.complete_email_verification(page,workspace.conn,job.id,_policy(site),ApplicationCredentials(EMAIL,PASSWORD))
    assert result.verified and result.acted
    assert page.main.call("()=>!!document.querySelector('#application')",page_realm=True)
    assert workspace.conn.execute('SELECT COUNT(*) FROM application_email_verification_requests WHERE job_id=?',(job.id,)).fetchone()[0]==1
    assert _posts(site)==[]


@pytest.mark.parametrize('text, code', [
    ('Hello Pat,Here is your one-time pass code : 734219Note : This code will expire in 10 minutes.', '734219'),
    ('Your passcode is 734219.', '734219'),
    ('Your one-time pass code : 7342190', None),
    ('Reference 734219. Your verification code is: 843210', '843210'),
    # Eightfold (careers.gf.com) wording, observed 2026-10-22.
    ('Verify your emailWe just need to verify your email address. Enter the following code below to complete the verification process:734219The code will expire after 10 minutes.', '734219'),
])
def test_code_requires_a_labeled_six_digit_value(text, code):
    assert module._code(text, 6) == code
