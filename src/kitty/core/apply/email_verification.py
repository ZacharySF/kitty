"""Scoped employer email verification inside the owning preparation session.

Requests are bound to one job, tab-storage token, origin and saved alias. Codes,
message bodies and verification-link URLs never enter the database or reports.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlsplit

from kitty.config import AuthenticationSettings, _authentication_origin
from kitty.core.apply.auth import authentication_pending
from kitty.core.apply.bidi import BidiError, Tab
from kitty.core.apply.credentials import ApplicationCredentials, credential_binding
from kitty.core.apply.guard import CREDENTIAL, UnsafeAction
from kitty.core.apply.preflight import inspect_page
from kitty.core.apply.verification_mail import MailRequest, VerificationMailError, read_verification_mail
from kitty.db.connection import transaction

_MARKER = 'kitty.application.email-verification'

# Segmented code boxes: Oracle pin-code-N ids, or Eightfold "Please enter OTP character N".
_SEGMENTS = r"""(count) => {
  const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&!e.closest('[hidden],[inert],[aria-hidden="true"]');
  const pins=[...document.querySelectorAll('input[id^="pin-code-"]')].filter(visible).sort((a,b)=>a.id.localeCompare(b.id));
  if(pins.length)return pins;
  const n=e=>+((e.getAttribute('aria-label')||'').match(/^please enter otp character (\d+)$/i)||[])[1];
  return [...document.querySelectorAll('input[type=text][aria-label]')].filter(e=>visible(e)&&n(e)).sort((a,b)=>n(a)-n(b));
}"""


_GATE_JS = r'''(origin, recipient) => {
  if(location.origin!==origin || document.readyState==='loading')return {kind:'loading'};
  const visible=e=>!!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden' && !e.closest('[hidden],[inert],[aria-hidden="true"]');
  const text=document.body?.innerText||'';
  const resendText=/^(?:resend(?: (?:email|code|account verification))?|send (?:a )?new code|send code again)$/i;
  // Workday blocks sign-in on an unverified candidate account and offers its
  // own "Resend Account Verification" action beside the filled login form.
  // After Resend, the same login says "An email has been sent to you."
  if(/\.myworkdayjobs\.com$/.test(location.hostname)&&/\b(?:verify your account before you sign in|an email has been sent to you)\b/i.test(text)&&/\baccount may need verification\b/i.test(text)){
    const resend=[...document.querySelectorAll('button,input[type=button]')].filter(e=>visible(e)&&!e.disabled&&resendText.test((e.innerText||e.value||'').trim()));
    // Workday's login also renders a visible "beecatcher" robots-only input.
    const emails=[...document.querySelectorAll('input[type=email],input[data-automation-id=email]')].filter(visible);
    return {kind:'link',workday:true,resend:resend.length===1,recipientShown:!!recipient&&emails.length===1&&emails[0].value.toLowerCase()===recipient.toLowerCase()};
  }
  if([...document.querySelectorAll('input[type=password]')].some(visible))return {kind:'none'};
  if(/authenticator|text message|security key|passkey|biometric|\bsms\b/i.test(text))return {kind:'human'};
  if([...document.querySelectorAll('iframe[src], [data-sitekey]')].some(e=>visible(e)&&/captcha|turnstile/i.test(e.getAttribute('src')||e.outerHTML.slice(0,300))))return {kind:'human'};
  const pins=[...document.querySelectorAll('input[id^="pin-code-"]')].filter(visible).sort((a,b)=>a.id.localeCompare(b.id));
  const singles=[...document.querySelectorAll('input[autocomplete="one-time-code"]')].filter(e=>visible(e)&&!pins.includes(e)&&/^(text|tel|number)$/.test(e.type));
  const email=/\be-?mail\b/i.test(text);
  const knownOracle=/^\/hcmUI\/CandidateExperience\/en\/sites\/[^/]+\/job\/\d+\/apply\/email\/?$/.test(location.pathname)&&/\.oraclecloud\.com$/.test(location.hostname);
  const otps=pins.length?[]:(''' + _SEGMENTS + r''')(6);
  const visibleInputs=[...document.querySelectorAll('input:not([type=hidden]),textarea,select')].filter(visible);
  // Eightfold's account email step: exactly six "OTP character N" boxes, nothing else.
  const knownEightfold=location.pathname==='/careers/apply'&&/\bcheck your email\b/i.test(text)&&/\benter the code\b/i.test(text)&&
    otps.length===6&&visibleInputs.length===6&&visibleInputs.every(e=>otps.includes(e));
  let fields=[];
  if(knownOracle&&email&&pins.length===6&&pins.every((e,i)=>e.id==='pin-code-'+(i+1)))fields=pins;
  else if(knownEightfold)fields=otps;
  else if(email&&pins.length===0&&singles.length===1&&/verification|verify|confirmation|confirm|check your email/i.test(text))fields=singles;
  const buttons=[...document.querySelectorAll('button,input[type=button]')].filter(e=>visible(e)&&!e.disabled);
  const verify=buttons.filter(e=>(knownEightfold?/^(verify|submit)$/i:/^(verify|verify (?:email|code)|confirm (?:email|code))$/i).test((e.innerText||e.value||'').trim()));
  const resend=buttons.filter(e=>resendText.test((e.innerText||e.value||'').trim()));
  if(fields.length&&verify.length===1)return {kind:'code',eightfold:knownEightfold,count:fields.length,empty:fields.every(e=>!e.value),resend:resend.length===1,recipientShown:!!recipient&&text.toLowerCase().includes(recipient.toLowerCase())};
  if(email&&/check your email|verification (?:email|link)|verify your email|confirm your email/i.test(text)&&!document.querySelector('input:not([type=hidden])'))return {kind:'link',resend:resend.length===1};
  return {kind:'none'};
}'''

_TARGET_JS = r'''(origin, role, index, count) => {
  if(location.origin!==origin)return null;
  const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&!e.closest('[hidden],[inert],[aria-hidden="true"]');
  if(role==='field'){
    let fields=(''' + _SEGMENTS + r''')(count);
    if(count===1)fields=[...document.querySelectorAll('input[autocomplete="one-time-code"]')].filter(e=>visible(e)&&/^(text|tel|number)$/.test(e.type));
    if(fields.length!==count||!fields[index]||fields[index].disabled||fields[index].readOnly)return null;
    return fields[index];
  }
  const buttons=[...document.querySelectorAll('button,input[type=button]')].filter(e=>visible(e)&&!e.disabled);
  const eightfold=location.pathname==='/careers/apply'&&(''' + _SEGMENTS + r''')(6).length===6;
  const matches=buttons.filter(e=>role==='verify'?(eightfold?/^(verify|submit)$/i:/^(verify|verify (?:email|code)|confirm (?:email|code))$/i).test((e.innerText||e.value||'').trim()):/^(?:resend(?: (?:email|code|account verification))?|send (?:a )?new code|send code again)$/i.test((e.innerText||e.value||'').trim()));
  return matches.length===1?matches[0]:null;
}'''


@dataclass(frozen=True)
class VerificationResult:
    verified: bool = False
    acted: bool = False
    blocker: str | None = None


def _binding(value: str) -> str:
    return hashlib.sha256(value.casefold().encode()).hexdigest()


def _origin(page: Tab) -> str | None:
    value = page.main.call('() => location.origin', page_realm=True)
    return _authentication_origin(value) if isinstance(value, str) else None


def _gate(page: Tab, origin: str, recipient: str) -> dict:
    return page.main.call(_GATE_JS, origin, recipient, page_realm=True)


def _code(text: str, length: int) -> str | None:
    if length not in (6,):
        return None
    # Gmail's textContent can join the code to the next sentence ("123456Note").
    labeled = re.findall(r'\b(?:verification|confirmation|security|one[ -]time(?:\s+pass)?|access|pass)\s*code(?:\s+is)?\s*[:–-]?\s*(\d{6})(?!\d)', text, re.I)
    if not labeled:
        # "Enter the following code below to complete the verification process:123456"
        labeled = re.findall(r'\bcode\b[^.:\d]{0,80}:\s*(\d{6})(?!\d)', text, re.I)
    if not labeled and re.search(r'verify|verification|confirm your identity|confirm your email', text, re.I):
        labeled = re.findall(r'(?m)^\s*(\d{6})\s*$', text)
    codes = set(labeled)
    return next(iter(codes)) if len(codes) == 1 else None


def _link(links: tuple[str, ...], origin: str) -> str | None:
    eligible = set()
    for value in links:
        try:
            parts = urlsplit(value)
            approved_origin = _authentication_origin(value)
        except ValueError:
            continue
        if approved_origin != origin:
            continue
        if re.search(r'(?:^|[/_-])(?:verify(?:email)?|verification|activate|activation|confirm(?:email)?)(?:[/_.-]|$)', parts.path, re.I):
            eligible.add(value)
    return next(iter(eligible)) if len(eligible) == 1 else None


def _pending(conn, job_id, origin, recipient, sender, token, now):
    if not token:
        return None
    row = conn.execute('SELECT * FROM application_email_verification_requests WHERE job_id=? AND owner_token=? AND origin=? AND sender_email=? AND recipient_binding=? AND state=\'pending\'',
                       (job_id, token, origin, sender, _binding(recipient))).fetchone()
    if row is not None and datetime.fromisoformat(row['expires_at']) > now:
        return row
    return None


def _record_request(conn, page, job_id, origin, sender, recipient, started, max_age):
    now = datetime.now(timezone.utc)
    if started.tzinfo is None or started > now or now-started > timedelta(seconds=max_age):
        raise UnsafeAction('verification request time is not current')
    token = uuid.uuid4().hex
    stored = page.main.call('''(origin,key,token)=>{if(location.origin!==origin)return false;try{sessionStorage.setItem(key,token);return sessionStorage.getItem(key)===token}catch{return false}}''', origin, _MARKER, token, page_realm=True)
    if stored is not True:
        raise UnsafeAction('verification ownership cannot be stored')
    with transaction(conn):
        conn.execute('UPDATE application_email_verification_requests SET state=\'blocked\',updated_at=? WHERE job_id=? AND state=\'pending\'', (now.isoformat(),job_id))
        cursor = conn.execute('INSERT INTO application_email_verification_requests(job_id,owner_token,origin,sender_email,recipient_binding,requested_at,expires_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',
                              (job_id,token,origin,sender,_binding(recipient),started.isoformat(),(started+timedelta(seconds=max_age)).isoformat(),now.isoformat()))
    return conn.execute('SELECT * FROM application_email_verification_requests WHERE id=?', (cursor.lastrowid,)).fetchone()


def complete_email_verification(page: Tab, conn: sqlite3.Connection, job_id: int,
                                policy: AuthenticationSettings, credentials: ApplicationCredentials, *,
                                requested_at: datetime | None = None, request_sent: bool = False,
                                checkpoint: Callable[[], None] | None = None,
                                accepted_navigation: Callable[[], None] | None = None) -> VerificationResult:
    """Consume one exact fresh email, then verify an actual account/application transition."""
    settings = policy.email_verification
    if not policy.enabled or not settings.enabled:
        return VerificationResult(blocker='verification: automatic employer email verification is not enabled')
    if checkpoint:
        checkpoint()
    origin = _origin(page)
    scopes = settings.scopes_for([origin]) if origin else []
    if not origin or not policy.approves(origin) or origin in policy.manual_account_origins or len(scopes) != 1:
        return VerificationResult(blocker='verification: this employer has no approved mailbox scope')
    if policy._credential_binding is not None and credential_binding(credentials) != policy._credential_binding:
        return VerificationResult(blocker='verification: saved account no longer matches the reviewed plan')
    sender = scopes[0].sender_email
    recipient = credentials.email
    gate = _gate(page, origin, recipient)
    for _ in range(20):
        # A page snapshot can still hold the previous challenge's text during a
        # transition; let the current document settle before judging it.
        if gate['kind'] not in ('none', 'loading'):
            break
        time.sleep(0.25)
        gate = _gate(page, origin, recipient)
    if gate['kind'] == 'none':
        return VerificationResult()              # no email challenge present: re-inspect the page
    if gate['kind'] not in ('code','link'):
        return VerificationResult(blocker='verification: unsupported or non-email security check needs your review')
    row = None
    acted = False
    try:
        # Workday's blocked Sign In sends nothing; only its Resend action does.
        if request_sent and requested_at is not None and not gate.get('workday'):
            row = _record_request(conn,page,job_id,origin,sender,recipient,requested_at,settings.max_age_seconds)
        else:
            token = page.main.call('''(origin,key)=>{if(location.origin!==origin)return null;try{return sessionStorage.getItem(key)}catch{return null}}''',origin,_MARKER,page_realm=True)
            row = _pending(conn,job_id,origin,recipient,sender,token,datetime.now(timezone.utc))
        if row is None:
            # A retained unknown challenge has no trustworthy request time. Use its
            # explicit normal resend once, never consume an old inbox message.
            if not gate.get('resend') or not gate.get('recipientShown'):
                return VerificationResult(blocker='verification: request a new email code; the retained challenge has no current request receipt')
            if checkpoint:
                checkpoint()
            started = datetime.now(timezone.utc).replace(microsecond=0)
            target = page.main.node(_TARGET_JS,origin,'resend',0,0)
            if target is None:
                return VerificationResult(blocker='verification: the resend control changed; review this challenge')
            row = _record_request(conn,page,job_id,origin,sender,recipient,started,settings.max_age_seconds)
            page.main.click(target, intent=CREDENTIAL)
            acted = True
        request = MailRequest(recipient=recipient,sender=sender,requested_at=datetime.fromisoformat(row['requested_at']),expires_at=datetime.fromisoformat(row['expires_at']))
        deadline = min(time.monotonic()+settings.max_wait_seconds,time.monotonic()+max(0,(request.expires_at-datetime.now(timezone.utc)).total_seconds()))
        settle_deadline = min(deadline, time.monotonic()+5)
        messages = []
        while time.monotonic() < deadline:
            if checkpoint:
                checkpoint()
            current_kind = _gate(page,origin,recipient)['kind']
            if _origin(page) == origin and current_kind in {'none','loading'} and time.monotonic() < settle_deadline:
                # Native resend may temporarily replace/disable the challenge.
                # Observe its receipt without clicking again or reading mail early.
                time.sleep(.1)
                continue
            if _origin(page) != origin or current_kind != gate['kind']:
                return VerificationResult(acted=acted,blocker='verification: application challenge changed before the email was used')
            messages = read_verification_mail(page.bidi,request,checkpoint=checkpoint)
            if messages:
                break
            time.sleep(min(2,max(0,deadline-time.monotonic())))
        if len(messages) != 1:
            return VerificationResult(acted=acted,blocker='verification: no unique fresh matching employer email arrived within the bounded wait')
        message = messages[0]
        # The reader qualifies independently; retain this trust boundary if an
        # additional read-only provider is introduced later.
        received_at = message.received_at
        if (not isinstance(received_at, datetime) or received_at.tzinfo is None
                or received_at.utcoffset() is None or received_at > datetime.now(timezone.utc)):
            return VerificationResult(acted=acted,blocker='verification: employer email has no trustworthy delivery time')
        if (message.sender.casefold()!=sender.casefold() or recipient.casefold() not in {value.casefold() for value in message.recipients} or
                message.received_at < request.requested_at or message.received_at > request.expires_at):
            return VerificationResult(acted=acted,blocker='verification: employer email does not match this request')
        code = _code(message.text,6) if gate['kind']=='code' else None
        link = _link(message.links,origin) if gate['kind']=='link' else None
        if code is None and link is None:
            return VerificationResult(acted=acted,blocker='verification: matching email has no unique supported code or same-origin verification link')
        if checkpoint:
            checkpoint()
        fingerprint = _binding(sender+'\0'+message.id+'\0'+message.received_at.isoformat())
        with transaction(conn):
            cursor = conn.execute('UPDATE application_email_verification_requests SET state=\'consuming\',message_binding=?,updated_at=? WHERE id=? AND state=\'pending\'',(fingerprint,datetime.now(timezone.utc).isoformat(),row['id']))
        if cursor.rowcount != 1:
            return VerificationResult(acted=acted,blocker='verification: this request was already consumed; request a new code')
        page.activate()
        if code is not None:
            page.bidi.protect_secrets(code)
            current = _gate(page,origin,recipient)
            if current['kind']!='code' or not current.get('empty'):
                raise UnsafeAction('verification control contains an existing answer or changed')
            count = current['count']
            for index in range(count):
                if checkpoint:
                    checkpoint()
                target = page.main.node(_TARGET_JS,origin,'field',index,count)
                if target is None:
                    raise UnsafeAction('verification field changed')
                focused = page.main.call('''(origin,index,count)=>{if(location.origin!==origin)return false;let fields=count===1?[...document.querySelectorAll('input[autocomplete="one-time-code"]')].filter(e=>e.getClientRects().length):('''+_SEGMENTS+''')(count);if(fields.length!==count||!fields[index]||fields[index].value)return false;fields[index].focus();return document.activeElement===fields[index]}''',origin,index,count,page_realm=True)
                if focused is not True:
                    raise UnsafeAction('verification field focus changed')
                page.main.type(code if count==1 else code[index], into=target)
            committed = page.main.call('''(origin,code,count)=>{if(location.origin!==origin)return false;let fields=count===1?[...document.querySelectorAll('input[autocomplete="one-time-code"]')].filter(e=>e.getClientRects().length):('''+_SEGMENTS+''')(count);return fields.length===count&&fields.map(e=>e.value).join('')===code}''',origin,code,count,page_realm=True)
            if committed is not True:
                raise UnsafeAction('verification code did not commit')
            if checkpoint:
                checkpoint()
            target = page.main.node(_TARGET_JS,origin,'verify',0,count)
            if target is None:
                raise UnsafeAction('verification action changed')
            acted = True
            page.main.click(target, intent=CREDENTIAL)
        else:
            page.bidi.protect_secrets(link)
            acted = True
            page.goto(link)
        # Oracle Candidate Experience can take >12s to render the next section
        # after accepting a code; observe only, never repeat the action.
        transition_deadline = time.monotonic()+30
        while time.monotonic()<transition_deadline:
            actual = _origin(page)
            if actual is None:
                time.sleep(.2)
                continue
            if actual!=origin:
                raise UnsafeAction('verification left the approved employer')
            if gate.get('workday') and link is not None and urlsplit(page.main.url).path == urlsplit(link).path:
                # The consumed activation URL itself is not an application page;
                # wait for the employer's own redirect before judging ownership.
                time.sleep(.2)
                continue
            if accepted_navigation:
                accepted_navigation()
            if checkpoint:
                checkpoint()
            fresh = inspect_page(page)
            if _gate(page,origin,recipient)['kind']=='none' and ((fresh.has_fields and (fresh.step or fresh.boundary or fresh.review)) or authentication_pending(page)):
                with transaction(conn):
                    conn.execute('UPDATE application_email_verification_requests SET state=\'verified\',updated_at=? WHERE id=? AND state=\'consuming\'',(datetime.now(timezone.utc).isoformat(),row['id']))
                return VerificationResult(verified=True,acted=True)
            time.sleep(.2)
        return VerificationResult(acted=acted,blocker='verification: the employer did not confirm this code or link; continue in the retained tab')
    except VerificationMailError as exc:
        return VerificationResult(acted=acted,blocker=f'verification: {exc}')
    except sqlite3.IntegrityError:
        if row is not None:
            with transaction(conn):
                conn.execute('UPDATE application_email_verification_requests SET state=\'blocked\',updated_at=? WHERE id=?', (datetime.now(timezone.utc).isoformat(),row['id']))
        return VerificationResult(acted=acted,blocker='verification: this employer email was already used; request a new code')
    except (BidiError,UnsafeAction):
        return VerificationResult(acted=acted,blocker='verification: the owned challenge changed or could not be verified; no action is replayed')
    finally:
        if row is not None:
            with transaction(conn):
                conn.execute('UPDATE application_email_verification_requests SET state=\'blocked\',updated_at=? WHERE id=? AND state=\'consuming\'',(datetime.now(timezone.utc).isoformat(),row['id']))
