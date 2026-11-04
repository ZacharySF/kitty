"""Real Google Calendar/Tasks REST adapter using isolated, opt-in OAuth.

No credential or provider error body crosses the API boundary. Sync uses ETags
and local revisions; divergent edits become review records, never last-writer wins.

Every Google write is an effect (kitty.core.effects) approved in Calendar's own view:
``google_calendar_push`` freezes the pending local writes of a both-direction sync and
``google_resource_change`` the live review of one exact resource-editor request. A
preview proposes the effect; the person's approval carries its id and digest, and the
kernel refuses it, for good, when anything reviewed changed.
"""
from __future__ import annotations

import json
import os
import ipaddress
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import uuid4

from kitty.core import calendar as local, effects
from kitty.db.connection import transaction

CALENDAR_SCOPE = 'https://www.googleapis.com/auth/calendar'
TASKS_SCOPE = 'https://www.googleapis.com/auth/tasks'
BASE = 'https://www.googleapis.com/calendar/v3/'
TASKS_BASE = 'https://tasks.googleapis.com/tasks/v1/'
SETUP = ['Enable Google Calendar API in Google Cloud Console.',
         'Configure OAuth consent audience/test users and create a Desktop app OAuth client.',
         'Upload the downloaded client JSON here; rika! stores it only in .private/calendar_client.json.',
         'Select Authorize, review the separate Calendar permissions in your browser, and consent manually.',
         'Select calendars to sync. Each push requires explicit approval; Gmail authorization is never reused or widened.']

PUBLIC_OPERATIONS = {
    'calendarList': ['list', 'get', 'create', 'update', 'replace', 'delete', 'watch'],
    'calendars': ['get', 'create', 'update', 'replace', 'delete', 'clear'],
    'colors': ['list', 'get'], 'settings': ['list', 'get', 'watch'],
    'events': ['list', 'get', 'create', 'update', 'replace', 'delete', 'rsvp', 'instances', 'move', 'import', 'quickAdd', 'watch'],
    'acl': ['list', 'get', 'create', 'update', 'replace', 'delete', 'watch'],
    'freebusy': ['freebusy'], 'channels': ['stop'],
    'tasklists': ['list', 'get', 'create', 'update', 'replace', 'delete'],
    'tasks': ['list', 'get', 'create', 'update', 'replace', 'delete', 'move', 'clear'],
}


def paths(settings):
    private = settings.root / '.private'
    return private / 'calendar_client.json', private / 'calendar_token.json'


def _private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.calendar-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_client(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            local.fail('OAuth client must be valid JSON')
    if not isinstance(value, dict) or set(value) != {'installed'} or not isinstance(value['installed'], dict):
        local.fail('Only a Google Desktop (installed) OAuth client is accepted')
    client = value['installed']
    if not isinstance(client.get('client_id'), str) or not client['client_id'].endswith('.apps.googleusercontent.com') or not isinstance(client.get('client_secret'), str):
        local.fail('Client JSON is missing a Google Desktop client ID/secret')
    if client.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth' or client.get('token_uri') != 'https://oauth2.googleapis.com/token':
        local.fail('OAuth endpoints must be the official Google endpoints')
    redirects = client.get('redirect_uris', [])
    if not isinstance(redirects, list) or not redirects or any(not isinstance(v, str) or not (v == 'http://localhost' or v.startswith('http://localhost:') or v.startswith('http://127.0.0.1:')) for v in redirects):
        local.fail('Desktop client must allow loopback HTTP redirect URIs')
    return value


def status(settings, conn=None):
    client, token = paths(settings)
    scopes = []
    token_usable = False
    if token.is_file():
        try:
            saved = json.loads(token.read_text())
            scopes = saved.get('scopes', [])
            token_usable = CALENDAR_SCOPE in scopes
        except (OSError, ValueError):
            pass
    selected = []
    if conn is not None:
        selected = [dict(v) for v in conn.execute('SELECT id,name,google_id,timezone,revision FROM calendar_calendars WHERE google_id IS NOT NULL AND deleted=0')]
    return {'client_present': client.is_file(), 'authorized': token_usable, 'tasks_authorized': TASKS_SCOPE in scopes,
            'demo_blocked': settings.demo, 'steps': SETUP, 'selected': selected,
            'calendar_conflicts': [json.loads(v['value']) for v in conn.execute("SELECT value FROM calendar_google_state WHERE key LIKE 'calendar-conflict:%'")] if conn is not None else [],
            'public_operations': PUBLIC_OPERATIONS,
            'capabilities': {'local': ['events', 'recurrence', 'tasks', 'imports', 'availability'],
                             'google': ['calendarList', 'calendars', 'colors', 'settings.read', 'events', 'recurrence', 'attendees', 'rsvp', 'conference.meet', 'attachments.links', 'reminders', 'freebusy', 'acl', 'channels', 'tasks.optional', 'birthday', 'eventLabels'],
                             'account_dependent': ['focusTime', 'outOfOffice', 'workingLocation', 'Meet', 'calendar sharing'],
                             'unsupported': {'appointmentSchedules': 'Google Calendar API does not expose appointment schedules.',
                                             'settings.write': 'Google Settings API is read-only; edit primary timezone through Calendars resource.'}},
            'limits': [
                'Linked sync covers local events and calendar name/timezone/color. Local tasks, including local repeat rules, are not mirrored to Google Tasks: the public Tasks API cannot represent recurrence. Use the reviewed Tasks resource editor for one-off Google tasks.',
                'The exact resource editor exposes the public_operations inventory, including explicit move/import/quickAdd/watch/clear/stop and full replacement. Every change is prepared as a server-built preview (exact URL, query, body, If-Match, live target, recipients, clear listing) and is sent only with that frozen preview plus explicit approval; any drift returns 409.',
                'Appointment schedules and consumer-only settings have no public API; they remain an explicit handoff to Google Calendar in a browser.',
                'Watch requires your own ready public HTTPS webhook receiver. rika! localhost is not a receiver; rika! never provisions, automatically subscribes or renews channels.',
                'Move, clear and Tasks mutations use an ETag GET preflight; these endpoints do not promise atomic If-Match. A change can race between the final preflight/listing and the mutation.',
                'Calendar clear deletes every event on a primary calendar and requires a separate confirmation of the full listing. Tasks clear hides completed tasks; it does not delete all tasks.',
                'QuickAdd has no provider dry-run: review its exact text and use sendUpdates=none; email addresses are rejected. Google may still send some emails; use structured create/import when exact parsed fields are required.',
                'Birthday creation requires a private, transparent, one-day annual event; only summary/color/reminders/date can be changed, and contact/self birthday dates remain Google-owned. fromGmail creation and organizer changes remain Google-owned; only documented editable fields are accepted.',
                'Local recurrence supports one RRULE plus EXDATE and edited instances, not RDATE/EXRULE or multiple RRULEs.',
                'Local reminders are popup minute offsets; provider default/email reminder policies are preserved on linked event updates and can be edited through the exact resource editor.',
            ],
            'notice': 'Calendar OAuth is separate from Gmail. Google may notify attendees even when sendUpdates=none; review recipients before every external change.'}


def upload_client(settings, body):
    if settings.demo:
        local.fail('OAuth credentials cannot be stored in demo mode', 403)
    if body.get('confirmed') is not True:
        local.fail('Confirm storing this private OAuth client')
    value = validate_client(body.get('client'))
    client, token = paths(settings)
    if token.exists():
        local.fail('Disconnect the current account before replacing the OAuth client', 409)
    _private_write(client, json.dumps(value))
    return {'ok': True, 'client_present': True}


def authorize(settings, include_tasks=False):
    if settings.demo:
        local.fail('Google authorization is disabled in demo mode', 403)
    client, token = paths(settings)
    if not client.is_file():
        local.fail('Upload a Google Desktop OAuth client first', 409)
    from google_auth_oauthlib.flow import InstalledAppFlow
    config = validate_client(json.loads(client.read_text()))
    scopes = [CALENDAR_SCOPE] + ([TASKS_SCOPE] if include_tasks else [])
    flow = InstalledAppFlow.from_client_config(config, scopes=scopes)
    credentials = flow.run_local_server(host='127.0.0.1', port=0, open_browser=True,
                                        timeout_seconds=300, access_type='offline', prompt='consent')
    if not credentials.has_scopes(scopes):
        local.fail('Google did not grant the requested permissions; authorize again', 403)
    _private_write(token, credentials.to_json())
    return {'authorized': True, 'tasks_authorized': include_tasks}


def session(settings):
    if settings.demo:
        local.fail('External Google calls are disabled in demo mode', 403)
    _, token = paths(settings)
    if not token.is_file():
        local.fail('Calendar is not authorized; upload the client then authorize', 409)
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import AuthorizedSession, Request
    from google.auth.exceptions import GoogleAuthError
    try:
        saved = json.loads(token.read_text())
        if CALENDAR_SCOPE not in saved.get('scopes', []):
            local.fail('Saved token does not grant Calendar scope; authorize separately', 403)
        credentials = Credentials.from_authorized_user_info(saved)
        if not credentials.valid:
            credentials.refresh(Request())
            _private_write(token, credentials.to_json())
        return AuthorizedSession(credentials)
    except (GoogleAuthError, ValueError, OSError):
        local.fail('Calendar authorization expired or was revoked; authorize again', 401)


def disconnect(settings, revoke=False):
    if settings.demo:
        local.fail('Google account actions are disabled in demo mode', 403)
    _, token = paths(settings)
    if revoke and token.is_file():
        import requests
        saved = json.loads(token.read_text())
        response = requests.post('https://oauth2.googleapis.com/revoke', data={'token': saved.get('refresh_token') or saved.get('token')}, timeout=30)
        if response.status_code not in {200, 400}:
            local.fail('Google could not revoke this token; disconnect locally or retry', 502)
    token.unlink(missing_ok=True)
    return {'ok': True, 'notice': 'Local token removed. Revoke access in your Google account if not revoked here.'}


class RemoteError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__('Google request failed')


def request(client, method, path, *, body=None, params=None, etag=None, tasks=False):
    headers = {'If-Match': etag} if etag else {}
    from requests.exceptions import RequestException
    try:
        response = client.request(method, (TASKS_BASE if tasks else BASE) + path,
                                  json=body, params=params, headers=headers, timeout=45)
    except RequestException:
        raise RemoteError(502) from None
    if response.status_code >= 400:
        # Never surface arbitrary remote bodies, which may contain private details.
        raise RemoteError(response.status_code)
    if response.status_code == 204 or not response.content:
        return {'ok': True}
    return response.json()


def checked_request(*args, **kwargs):
    try:
        return request(*args, **kwargs)
    except RemoteError as exc:
        messages = {401: 'Calendar authorization expired; authorize again',
                    403: 'Google denied this operation: review granted scopes, account feature eligibility, sharing permissions and API enablement',
                    404: 'Google resource is unavailable or not accessible to this account',
                    409: 'Google resource conflicts with an existing item',
                    410: 'Google synchronization token expired; run a full refresh',
                    412: 'Google resource changed; reload and review before applying your change',
                    429: 'Google rate limit reached; retry this operation later'}
        local.fail(messages.get(exc.status, 'Google rejected this operation; review the resource and account capabilities'),
                   exc.status if exc.status in {400, 401, 403, 404, 409, 410, 412, 429} else 502)


def page_all(client, path, params=None, tasks=False):
    params = dict(params or {})
    items = []
    token = None
    while True:
        if token:
            params['pageToken'] = token
        result = checked_request(client, 'GET', path, params=params, tasks=tasks)
        items.extend(result.get('items', []))
        token = result.get('nextPageToken')
        if not token:
            return {'items': items, 'nextSyncToken': result.get('nextSyncToken')}


def enc(value):
    return quote(local.text(value, 'Google resource ID', 1024), safe='')


def remote_to_local(conn, remote, calendar_id):
    cal = local._get_calendar(conn, calendar_id)
    start = remote.get('start', {})
    end = remote.get('end', {})
    all_day = 'date' in start
    recurrence = remote.get('recurrence', [])
    unsupported = [line for line in recurrence if not line.startswith(('RRULE:', 'EXDATE'))]
    if unsupported or sum(line.startswith('RRULE:') for line in recurrence) > 1:
        local.fail('Google recurrence uses RDATE/EXRULE or multiple rules; keep this event in Google or use the exact resource editor')
    exdates = []
    for line in recurrence:
        if line.startswith('EXDATE'):
            from dateutil.parser import parse
            prop, values = line.split(':', 1)
            ex_tz = prop.split('TZID=', 1)[1].split(';')[0] if 'TZID=' in prop else cal['timezone']
            for value in values.split(','):
                dt = parse(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=local.zone(ex_tz))
                exdates.append(dt.date().isoformat() if all_day else dt.isoformat())
    return local.normalize_event(conn, {'calendar_id': calendar_id, 'title': remote.get('summary') or '(Untitled Google event)',
                                       'start': start.get('date') or start.get('dateTime', ''), 'end': end.get('date') or end.get('dateTime', ''),
                                       'all_day': all_day, 'timezone': start.get('timeZone') or cal['timezone'],
                                       'description': remote.get('description', ''), 'location': remote.get('location', ''),
                                       'url': remote.get('htmlLink', ''), 'source_key': None,
                                       'rrule': next((v[6:] for v in recurrence if v.startswith('RRULE:')), ''), 'exdates': exdates,
                                       'privacy': remote.get('visibility', 'default'), 'busy': remote.get('transparency', 'opaque') != 'transparent',
                                       'reminders': [v.get('minutes', 10) for v in remote.get('reminders', {}).get('overrides', [])],
                                       'attendees': [{'email': v['email'], 'name': v.get('displayName', ''), 'response': v.get('responseStatus', 'needsAction'), 'optional': v.get('optional', False)} for v in remote.get('attendees', []) if v.get('email')],
                                       'attachments': [{'url': v['fileUrl'], 'title': v.get('title', '')} for v in remote.get('attachments', []) if v.get('fileUrl')],
                                       'event_type': remote.get('eventType', 'default'),
                                       'google_options': {k: remote[k] for k in ('focusTimeProperties', 'outOfOfficeProperties', 'workingLocationProperties', 'conferenceData') if k in remote}})


def local_to_remote(data, *, meet=False):
    out = {'summary': data['title'], 'description': data['description'], 'location': data['location'],
           'visibility': 'private' if data['privacy'] == 'confidential' else data['privacy'], 'transparency': 'opaque' if data['busy'] else 'transparent',
           'start': {'date' if data['all_day'] else 'dateTime': data['start']},
           'end': {'date' if data['all_day'] else 'dateTime': data['end']},
           'attendees': [{'email': a['email'], 'displayName': a['name'], 'responseStatus': a['response'], 'optional': a['optional']} for a in data['attendees']],
           'attachments': [{'fileUrl': a['url'], 'title': a['title']} for a in data['attachments']],
           'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': m} for m in data['reminders']]},
           'eventType': data['event_type'], **data['google_options']}
    if not data['all_day']:
        out['start']['timeZone'] = data['timezone']
        out['end']['timeZone'] = data['timezone']
    recurrence = ['RRULE:' + data['rrule']] if data['rrule'] else []
    if data['exdates']:
        if data['all_day']:
            recurrence.append('EXDATE;VALUE=DATE:' + ','.join(v.replace('-', '') for v in data['exdates']))
        else:
            recurrence.append('EXDATE:' + ','.join(local.instant(v).strftime('%Y%m%dT%H%M%SZ') for v in data['exdates']))
    out['recurrence'] = recurrence
    if meet:
        out['conferenceData'] = {'createRequest': {'requestId': uuid4().hex, 'conferenceSolutionKey': {'type': 'hangoutsMeet'}}}
    return out


def _remote_signature(remote):
    ignored = {'etag', 'updated', 'created', 'sequence', 'htmlLink', 'iCalUID', 'kind'}
    return json.dumps({k: v for k, v in remote.items() if k not in ignored}, sort_keys=True)


def _exception_key(calendar_id, remote):
    return 'exception:' + calendar_id + ':' + remote['id']


def _remember_exception(conn, calendar_id, remote):
    conn.execute('INSERT INTO calendar_google_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (_exception_key(calendar_id, remote), _remote_signature(remote)))


def _state(conn, key, value):
    conn.execute('INSERT INTO calendar_google_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (key, json.dumps(value)))


def _calendar_remote(client, google_id):
    resource = checked_request(client, 'GET', 'calendars/' + enc(google_id))
    subscription = checked_request(client, 'GET', 'users/me/calendarList/' + enc(google_id))
    return {'resource': resource, 'subscription': subscription,
            'values': {'name': resource.get('summary', 'Google calendar'), 'timezone': resource.get('timeZone', 'UTC'),
                       'color': subscription.get('backgroundColor') or 'c1'}}


def _calendar_metadata(client, conn, cal, direction):
    remote = _calendar_remote(client, cal['google_id'])
    key = 'calendar-base:' + cal['id']
    saved = conn.execute('SELECT value FROM calendar_google_state WHERE key=?', (key,)).fetchone()
    base = json.loads(saved['value']) if saved else {'local': remote['values'], 'remote': remote}
    values = {k: cal[k] for k in ('name', 'timezone', 'color')}
    # Google stores hex only: a theme palette slot stays local, and is replaced only
    # when the calendar's colour changes in Google.
    palette = not local.HEX.fullmatch(cal['color'])
    if palette:
        values['color'] = base['local']['color']
    keep = palette and remote['values']['color'] == base['remote']['values']['color']
    pulled_color = cal['color'] if keep else remote['values']['color']
    local_changed = values != base['local']
    remote_changed = remote['values'] != base['remote']['values']
    if local_changed and remote_changed and values != remote['values']:
        _state(conn, 'calendar-conflict:' + cal['id'], {'calendar_id': cal['id'], 'local': {**values, 'revision': cal['revision']}, 'remote': remote})
        return False
    if local_changed and direction == 'both':
        if local._get_calendar(conn, cal['id'])['revision'] != cal['revision']:
            local.fail('Calendar settings changed while synchronizing; review and retry', 409)
        if values['name'] != remote['values']['name'] or values['timezone'] != remote['values']['timezone']:
            checked_request(client, 'PATCH', 'calendars/' + enc(cal['google_id']),
                            body={'summary': values['name'], 'timeZone': values['timezone']}, etag=remote['resource'].get('etag'))
        if values['color'] != remote['values']['color'] and local.HEX.fullmatch(values['color']):
            checked_request(client, 'PATCH', 'users/me/calendarList/' + enc(cal['google_id']),
                            body={'backgroundColor': values['color']}, params={'colorRgbFormat': 'true'}, etag=remote['subscription'].get('etag'))
        remote = _calendar_remote(client, cal['google_id'])
        _state(conn, key, {'local': values, 'remote': remote})
    elif not local_changed:
        with transaction(conn):
            current = local._get_calendar(conn, cal['id'])
            if current['revision'] != cal['revision']:
                local.fail('Calendar settings changed while synchronizing; review and retry', 409)
            if values != remote['values']:
                zone_name = remote['values']['timezone']
                local.zone(zone_name)
                conn.execute('UPDATE calendar_calendars SET name=?,timezone=?,color=?,revision=revision+1,updated_at=? WHERE id=?',
                             (remote['values']['name'], zone_name, pulled_color, local.now(), cal['id']))
            _state(conn, key, {'local': remote['values'], 'remote': remote})
    return True


def resolve_calendar(settings, conn, body):
    if body.get('confirmed') is not True or body.get('choice') not in {'local', 'remote'}:
        local.fail('Confirm keeping the local or Google calendar settings')
    cal = local._get_calendar(conn, body.get('calendar_id'))
    row = conn.execute('SELECT value FROM calendar_google_state WHERE key=?', ('calendar-conflict:' + cal['id'],)).fetchone()
    if not row:
        local.fail('Calendar settings conflict not found', 404)
    conflict = json.loads(row['value'])
    if body.get('revision') != cal['revision']:
        local.fail('Calendar settings changed; reload before resolving', 409)
    with session(settings) as client:
        latest = _calendar_remote(client, cal['google_id'])
    if latest['values'] != conflict['remote']['values']:
        _state(conn, 'calendar-conflict:' + cal['id'], {**conflict, 'remote': latest})
        local.fail('Google calendar settings changed again; review refreshed settings', 409)
    with transaction(conn):
        if local._get_calendar(conn, cal['id'])['revision'] != body['revision']:
            local.fail('Calendar settings changed during review', 409)
        if body['choice'] == 'remote':
            local.zone(latest['values']['timezone'])
            conn.execute('UPDATE calendar_calendars SET name=?,timezone=?,color=?,revision=revision+1,updated_at=? WHERE id=?',
                         (latest['values']['name'], latest['values']['timezone'], latest['values']['color'], local.now(), cal['id']))
        _state(conn, 'calendar-base:' + cal['id'], {'local': latest['values'], 'remote': latest})
        conn.execute('DELETE FROM calendar_google_state WHERE key=?', ('calendar-conflict:' + cal['id'],))
    return {'ok': True, 'notice': 'Kept local settings; review a both-direction sync to push.' if body['choice'] == 'local' else 'Adopted Google calendar settings.'}


def unselect(conn, body):
    if body.get('confirmed') is not True:
        local.fail('Confirm unlinking this calendar (local events remain)')
    cal = local._get_calendar(conn, body.get('calendar_id'))
    if body.get('revision') != cal['revision']:
        local.fail('Calendar changed; reload before unlinking', 409)
    with transaction(conn):
        conn.execute('DELETE FROM calendar_google_conflicts WHERE calendar_id=?', (cal['id'],))
        conn.execute('DELETE FROM calendar_google_events WHERE calendar_id=?', (cal['id'],))
        conn.execute('DELETE FROM calendar_google_state WHERE key IN (?,?)', ('calendar-base:' + cal['id'], 'calendar-conflict:' + cal['id']))
        conn.execute('UPDATE calendar_calendars SET google_id=NULL,sync_token=NULL,google_etag=NULL,revision=revision+1,updated_at=? WHERE id=?', (local.now(), cal['id']))
    return {'ok': True}


def select_calendar(settings, conn, body):
    if body.get('confirmed') is not True:
        local.fail('Confirm linking this Google calendar')
    remote_id = body.get('google_id', '')
    with session(settings) as client:
        remote = checked_request(client, 'GET', 'users/me/calendarList/' + enc(remote_id))
    existing = conn.execute('SELECT id FROM calendar_calendars WHERE google_id=?', (remote_id,)).fetchone()
    if existing:
        return local._get_calendar(conn, existing['id'])
    cal = local.save_calendar(conn, {'name': remote.get('summaryOverride') or remote.get('summary', 'Google calendar'),
                                     **({'color': remote['backgroundColor']} if remote.get('backgroundColor') else {}),
                                     'timezone': remote.get('timeZone', 'UTC')})
    conn.execute('UPDATE calendar_calendars SET google_id=?,google_etag=? WHERE id=?', (remote_id, remote.get('etag'), cal['id']))
    return local._get_calendar(conn, cal['id'])


def conflicts(conn):
    """Open conflicts. `remote` is Google's side as a kitty event (None when Google deleted it,
    or when its recurrence cannot be represented locally), so both can be drawn the same way."""
    from kitty.web_server import APIError
    out = []
    for row in conn.execute('SELECT * FROM calendar_google_conflicts WHERE resolved_at IS NULL ORDER BY id'):
        remote = json.loads(row['remote_json'])
        view = None
        if remote.get('status') != 'cancelled':
            try:
                view = remote_to_local(conn, remote, row['calendar_id'])
            except APIError:
                view = None
        out.append({**dict(row), 'remote_json': remote, 'remote': view,
                    'local': local._event(conn, row['event_id'], include_deleted=True)})
    return out


def _record_conflict(conn, mapping, remote):
    current = local._event(conn, mapping['event_id'], include_deleted=True)
    existing = conn.execute('SELECT id FROM calendar_google_conflicts WHERE event_id=? AND google_id=? AND resolved_at IS NULL',
                            (mapping['event_id'], remote['id'])).fetchone()
    if existing:
        conn.execute('UPDATE calendar_google_conflicts SET remote_json=?,local_revision=? WHERE id=?', (json.dumps(remote), current['revision'], existing['id']))
    else:
        conn.execute('INSERT INTO calendar_google_conflicts(event_id,google_id,calendar_id,local_revision,remote_json,created_at) VALUES(?,?,?,?,?,?)',
                     (mapping['event_id'], remote['id'], mapping['calendar_id'], current['revision'], json.dumps(remote), local.now()))


def _apply_remote(conn, calendar_id, remote, mapping=None):
    if remote.get('recurringEventId'):
        master = conn.execute('SELECT * FROM calendar_google_events WHERE calendar_id=? AND google_id=?', (calendar_id, remote['recurringEventId'])).fetchone()
        if not master:
            return
        data = local._event(conn, master['event_id'], include_deleted=True)
        original = remote.get('originalStartTime', {})
        key = local.stamp(local.instant(original.get('date') or original.get('dateTime', ''), data['timezone'], data['all_day']), data['all_day'])
        replacement = None if remote.get('status') == 'cancelled' else remote_to_local(conn, remote, calendar_id)
        if replacement:
            previous = conn.execute('SELECT data FROM calendar_exceptions WHERE event_id=? AND occurrence=?',
                                    (master['event_id'], key)).fetchone()
            shown = json.loads(previous['data']) if previous and previous['data'] else data
            replacement.update({field: shown.get(field) for field in local.LOCAL_DISPLAY})
        conn.execute('INSERT INTO calendar_exceptions VALUES(?,?,?) ON CONFLICT(event_id,occurrence) DO UPDATE SET data=excluded.data',
                     (master['event_id'], key, json.dumps(replacement) if replacement else None))
        local._update(conn, master['event_id'], {k: v for k, v in data.items() if k in local.EVENT_FIELDS}, manual=False)
        current = local._event(conn, master['event_id'], include_deleted=True)
        if data['revision'] == master['synced_revision']:
            conn.execute('UPDATE calendar_google_events SET synced_revision=? WHERE event_id=?', (current['revision'], master['event_id']))
        _remember_exception(conn, calendar_id, remote)
        return
    if remote.get('status') == 'cancelled':
        if mapping:
            event = local._event(conn, mapping['event_id'], include_deleted=True)
            if event['deleted'] and mapping['base_json'] and _remote_signature(json.loads(mapping['base_json'])) == _remote_signature(remote):
                return
            local._update(conn, mapping['event_id'], {k: v for k, v in event.items() if k in local.EVENT_FIELDS}, deleted=True, manual=False)
            current = local._event(conn, mapping['event_id'], include_deleted=True)
            conn.execute('UPDATE calendar_google_events SET etag=?,synced_revision=?,base_json=?,remote_json=? WHERE event_id=?',
                         (remote.get('etag'), current['revision'], json.dumps(remote), json.dumps(remote), mapping['event_id']))
        return
    data = remote_to_local(conn, remote, calendar_id)
    if mapping:
        ident = mapping['event_id']
        old = local._event(conn, ident, include_deleted=True)
        # Preserve source linkage, human provenance and local-only display (subject, colour).
        data['source_key'], data['source_url'] = old.get('source_key'), old.get('source_url', '')
        data.update({field: old.get(field) for field in local.LOCAL_DISPLAY})
        local._update(conn, ident, data, manual=False)
    else:
        ident = local._insert(conn, data)
    event = local._event(conn, ident, include_deleted=True)
    conn.execute('INSERT INTO calendar_google_events(calendar_id,google_id,event_id,etag,synced_revision,base_json,remote_json) VALUES(?,?,?,?,?,?,?) ON CONFLICT(calendar_id,google_id) DO UPDATE SET etag=excluded.etag,synced_revision=excluded.synced_revision,base_json=excluded.base_json,remote_json=excluded.remote_json',
                 (calendar_id, remote['id'], ident, remote.get('etag'), event['revision'], json.dumps(remote), json.dumps(remote)))


EFFECT_LIFETIME = timedelta(minutes=30)
SEND_UPDATES = {'none', 'all', 'externalOnly'}
SYNC_DRIFT = 'Local calendar changes differ from the reviewed preview; prepare and approve a fresh sync preview'
SYNC_FRESH = 'prepare and approve a fresh sync preview'


@dataclass(frozen=True)
class _Local:
    """What a push's preconditions read: the connection and whether ApplyAI events count."""

    conn: object
    applyai: bool


def _effect_ref(effect):
    """What the UI sends back to approve exactly this proposal."""
    return {'id': effect.id, 'digest': effect.digest, 'expires_at': effect.expires_at}


def _refused(exc, drift, fresh):
    """A kernel refusal in this module's error style: anything reviewed that changed means review again."""
    local.fail(drift if exc.changed else f'{str(exc).rstrip(".")}; {fresh}', exc.status)


def _proposed(conn, body, kind, fresh):
    """The stored proposal of `kind` this approval names (never another kind's effect)."""
    try:
        effect = effects.get(conn, body.get('effect_id'))
    except effects.EffectError as exc:
        _refused(exc, fresh, fresh)
    if effect.kind != kind.name:
        local.fail(f'This is not a reviewed {kind.title.lower()}; {fresh}', 409)
    return effect


def _sync_request(body):
    """The push being proposed: which linked calendars, and the attendee email policy."""
    selected = body.get('calendar_ids', [])
    if not isinstance(selected, list) or not selected or any(not isinstance(v, str) for v in selected):
        local.fail('Select at least one linked calendar')
    updates = body.get('send_updates', 'none')
    if updates not in SEND_UPDATES:
        local.fail('Invalid email update policy')
    return {'calendar_ids': sorted(set(selected)), 'send_updates': updates}


def _pending_writes(ctx, payload):
    """Every pending local write and its recipients, including events outside the UI range."""
    conn = ctx.conn
    review = {'calendars': [], 'events': []}
    for calendar_id in payload['calendar_ids']:
        cal = local._get_calendar(conn, calendar_id)
        if not cal['google_id']:
            local.fail('Only linked calendars can sync')
        review['calendars'].append({k: cal[k] for k in ('id', 'revision', 'name', 'timezone', 'color')})
        rows = conn.execute('SELECT e.*,m.google_id,m.synced_revision,m.remote_json FROM calendar_events e LEFT JOIN calendar_google_events m ON m.event_id=e.id WHERE e.calendar_id=? AND (m.event_id IS NULL OR e.revision!=m.synced_revision) ORDER BY e.id', (calendar_id,))
        for row in rows:
            if not ctx.applyai and local.applyai_sourced(row['source_key']):
                continue
            if row['deleted'] and not row['google_id']:
                continue
            if conn.execute('SELECT 1 FROM calendar_google_conflicts WHERE event_id=? AND resolved_at IS NULL', (row['id'],)).fetchone():
                continue
            data = json.loads(row['data'])
            remote = json.loads(row['remote_json']) if row['remote_json'] else {}
            overrides = [json.loads(v['data']) for v in conn.execute('SELECT data FROM calendar_exceptions WHERE event_id=? AND data IS NOT NULL', (row['id'],))]
            recipients = sorted({v['email'] for v in data['attendees']} |
                                {v['email'] for v in remote.get('attendees', []) if v.get('email')} |
                                {v['email'] for override in overrides for v in override.get('attendees', [])})
            review['events'].append({'id': row['id'], 'revision': row['revision'], 'calendar_id': calendar_id,
                                     'operation': 'delete' if row['deleted'] else 'update' if row['google_id'] else 'create',
                                     'title': data['title'], 'start': data['start'], 'end': data['end'],
                                     'attendees': recipients, 'rrule': data['rrule'],
                                     'overrides': conn.execute('SELECT COUNT(*) FROM calendar_exceptions WHERE event_id=?', (row['id'],)).fetchone()[0]})
    return review


def _push_approved(ctx, effect):
    """Approving records the push; the push itself is network work after the claim commits."""
    return None


PUSH = effects.REGISTRY.register(effects.Executable(
    name='google_calendar_push', title='Google Calendar push', proposers=frozenset({'user'}),
    preconditions=_pending_writes, review='inline', lifetime=EFFECT_LIFETIME, run=_push_approved,
))


def preview_sync(conn, body, *, actor, applyai=True):
    """Propose a push whose preconditions freeze every pending local write and its recipients."""
    payload = _sync_request(body)
    try:
        effect = effects.propose(conn, PUSH.name, payload, actor=actor, ctx=_Local(conn, applyai))
    except effects.EffectError as exc:
        local.fail(str(exc), exc.status)
    return {**effect.preconditions, 'effect': _effect_ref(effect)}


def sync(settings, conn, body, *, actor, applyai=True):
    if body.get('confirmed') is not True:
        local.fail('Confirm fetching Google data and the selected push direction')
    direction = body.get('direction', 'pull')
    if direction not in {'pull', 'both'}:
        local.fail('Sync direction must be pull or both')
    updates = body.get('send_updates', 'none')
    if updates not in SEND_UPDATES:
        local.fail('Invalid email update policy')
    if direction == 'both' and body.get('external_approved') is not True:
        local.fail('Explicitly approve Google event changes and possible attendee notifications')
    selected = body.get('calendar_ids', [])
    if not isinstance(selected, list) or not selected:
        local.fail('Select at least one linked calendar')
    proposal = None
    if direction == 'both':
        if 'effect_id' not in body:
            local.fail('Prepare and approve a fresh sync preview before pushing local changes', 409)
        proposal = _proposed(conn, body, PUSH, SYNC_FRESH)
        if _sync_request(body) != proposal.payload:
            local.fail(SYNC_DRIFT, 409)
        selected = proposal.payload['calendar_ids']
    result = {'pulled': 0, 'pushed': 0, 'conflicts': 0, 'errors': []}
    with session(settings) as client:
        reviewed, reviewed_calendars = {}, {}
        if proposal is not None:
            try:
                approved, _ = effects.approve_and_execute(conn, proposal.id, body.get('digest'), actor=actor,
                                                          ctx=_Local(conn, applyai))
            except effects.EffectError as exc:
                _refused(exc, SYNC_DRIFT, SYNC_FRESH)
            # Each calendar and event is pushed only at the revision approved here.
            reviewed = {v['id']: v['revision'] for v in approved.preconditions['events']}
            reviewed_calendars = {v['id']: v['revision'] for v in approved.preconditions['calendars']}
        for calendar_id in selected:
            cal = local._get_calendar(conn, calendar_id)
            if not cal['google_id']:
                local.fail('Only linked calendars can sync')
            if direction == 'both' and reviewed_calendars.get(calendar_id) != cal['revision']:
                result['errors'].append({'calendar_id': calendar_id, 'error': 'Calendar settings changed after approval; prepare a fresh sync preview.'})
                continue
            if not _calendar_metadata(client, conn, cal, direction):
                result['conflicts'] += 1
                result['errors'].append({'calendar_id': cal['id'], 'error': 'Calendar settings diverged. Review settings conflict before synchronizing this calendar.'})
                continue
            path = 'calendars/' + enc(cal['google_id']) + '/events'
            params = {'showDeleted': 'true', 'maxResults': 2500}
            if cal['sync_token'] and not body.get('full'):
                params['syncToken'] = cal['sync_token']
            pages, token, full = [], None, 'syncToken' not in params
            while True:
                if token:
                    params['pageToken'] = token
                try:
                    page = request(client, 'GET', path, params=params)
                except RemoteError as exc:
                    if exc.status == 410 and 'syncToken' in params:
                        params.pop('syncToken', None)
                        params.pop('pageToken', None)
                        pages, token, full = [], None, True
                        continue
                    local.fail('Google sync failed; permissions, API quota or authorization may need attention', 502)
                pages.extend(page.get('items', []))
                token = page.get('nextPageToken')
                if not token:
                    break
            # Masters first, then exceptions. Dirty master revision protects its exceptions.
            pages.sort(key=lambda v: bool(v.get('recurringEventId')))
            seen = {v['id'] for v in pages if not v.get('recurringEventId')}
            with transaction(conn):
                for remote in pages:
                    if remote.get('status') != 'cancelled' and remote.get('eventType', 'default') not in {'default', 'focusTime', 'outOfOffice', 'workingLocation'}:
                        result['errors'].append({'google_id': remote['id'], 'error': 'This provider-owned event type cannot be edited/imported by this adapter; it remains available in Google.'})
                        continue
                    if remote.get('status') != 'cancelled':
                        try:
                            remote_to_local(conn, remote, calendar_id)
                        except Exception as exc:
                            from kitty.web_server import APIError
                            if not isinstance(exc, APIError):
                                raise
                            result['errors'].append({'google_id': remote['id'], 'error': str(exc)})
                            continue
                    remote_id = remote.get('recurringEventId') or remote['id']
                    mapping = conn.execute('SELECT * FROM calendar_google_events WHERE calendar_id=? AND google_id=?', (calendar_id, remote_id)).fetchone()
                    if mapping:
                        event = local._event(conn, mapping['event_id'], include_deleted=True)
                        if remote.get('recurringEventId'):
                            saved = conn.execute('SELECT value FROM calendar_google_state WHERE key=?', (_exception_key(calendar_id, remote),)).fetchone()
                            changed = not saved or saved['value'] != _remote_signature(remote)
                        else:
                            changed = remote.get('etag') != mapping['etag']
                        if not changed:
                            continue
                        if event['revision'] != mapping['synced_revision']:
                            _record_conflict(conn, mapping, remote)
                            result['conflicts'] += 1
                            continue
                    _apply_remote(conn, calendar_id, remote, mapping)
                    result['pulled'] += 1
                if full:
                    for mapping in conn.execute('SELECT * FROM calendar_google_events WHERE calendar_id=?', (calendar_id,)).fetchall():
                        if mapping['google_id'] not in seen:
                            cancelled = {'id': mapping['google_id'], 'status': 'cancelled'}
                            if local._event(conn, mapping['event_id'], include_deleted=True)['revision'] != mapping['synced_revision']:
                                _record_conflict(conn, mapping, cancelled)
                                result['conflicts'] += 1
                            else:
                                _apply_remote(conn, calendar_id, cancelled, mapping)
                conn.execute('UPDATE calendar_calendars SET sync_token=? WHERE id=?', (page.get('nextSyncToken'), calendar_id))
            if direction != 'both':
                continue
            rows = conn.execute('SELECT e.*,m.google_id,m.etag,m.synced_revision FROM calendar_events e LEFT JOIN calendar_google_events m ON m.event_id=e.id WHERE e.calendar_id=? AND (m.event_id IS NULL OR e.revision!=m.synced_revision)', (calendar_id,)).fetchall()
            for row in rows:
                if not applyai and local.applyai_sourced(row['source_key']):
                    continue
                if conn.execute('SELECT 1 FROM calendar_google_conflicts WHERE event_id=? AND resolved_at IS NULL', (row['id'],)).fetchone():
                    continue
                if row['deleted'] and not row['google_id']:
                    continue
                if reviewed.get(row['id']) != row['revision'] or local._event(conn, row['id'], include_deleted=True)['revision'] != row['revision']:
                    result['errors'].append({'event_id': row['id'], 'error': 'Event changed after approval; prepare a fresh sync preview.'})
                    continue
                data = json.loads(row['data'])
                remote_path = path + '/' + enc(row['google_id']) if row['google_id'] else path
                params = {'sendUpdates': updates, 'supportsAttachments': 'true', 'conferenceDataVersion': 1}
                try:
                    if row['deleted']:
                        remote = request(client, 'DELETE', remote_path, params={'sendUpdates': updates}, etag=row['etag'])
                    else:
                        payload = local_to_remote(data)
                        # Updating a title must not silently replace provider email/default
                        # reminder policies with the local popup-only representation.
                        if row['google_id']:
                            saved = conn.execute('SELECT remote_json FROM calendar_google_events WHERE event_id=?', (row['id'],)).fetchone()
                            prior = json.loads(saved['remote_json']) if saved and saved['remote_json'] else {}
                            prior_local = remote_to_local(conn, prior, calendar_id) if prior.get('start') else None
                            if prior_local and data['reminders'] == prior_local['reminders'] and 'reminders' in prior:
                                payload['reminders'] = prior['reminders']
                        # A deterministic Google ID makes retry after transport failure idempotent.
                        if not row['google_id']:
                            payload['id'] = row['id']
                        remote = request(client, 'PATCH' if row['google_id'] else 'POST', remote_path, body=payload, params=params, etag=row['etag'])
                except RemoteError as exc:
                    if exc.status in {409, 412}:
                        remote = checked_request(client, 'GET', path + '/' + enc(row['google_id'] or row['id']))
                        mapping = {'event_id': row['id'], 'google_id': remote['id'], 'calendar_id': calendar_id}
                        _record_conflict(conn, mapping, remote)
                        result['conflicts'] += 1
                        continue
                    result['errors'].append({'event_id': row['id'], 'status': exc.status, 'error': 'Google rejected this event; review account capability, attendees, attachments and permissions.'})
                    continue
                google_id = row['google_id'] or remote['id']
                if not row['deleted']:
                    # Remember a successful master write even if a later instance
                    # update fails. Its revision stays dirty until every override lands.
                    conn.execute('INSERT INTO calendar_google_events(calendar_id,google_id,event_id,etag,synced_revision,base_json,remote_json) VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET etag=excluded.etag,base_json=excluded.base_json,remote_json=excluded.remote_json',
                                 (calendar_id, google_id, row['id'], remote.get('etag'), row['synced_revision'] or 0, json.dumps(remote), json.dumps(remote)))
                # Recurrence overrides use provider instance IDs (not guessed IDs).
                if not row['deleted']:
                    for ex in conn.execute('SELECT * FROM calendar_exceptions WHERE event_id=?', (row['id'],)).fetchall():
                        key_dt = local.instant(ex['occurrence'], data['timezone'], data['all_day'])
                        candidates = page_all(client, path + '/' + enc(google_id) + '/instances',
                                              {'timeMin': (key_dt - timedelta(days=2)).isoformat(), 'timeMax': (key_dt + timedelta(days=2)).isoformat(), 'showDeleted': 'true'})['items']
                        instance = next((v for v in candidates if local.stamp(local.instant(v.get('originalStartTime', {}).get('date') or v.get('originalStartTime', {}).get('dateTime', ''), data['timezone'], data['all_day']), data['all_day']) == ex['occurrence']), None)
                        if not instance:
                            result['errors'].append({'event_id': row['id'], 'error': 'Google recurrence instance unavailable; override not synchronized.'})
                            break
                        try:
                            if ex['data']:
                                override = json.loads(ex['data'])
                                replacement = local_to_remote(override)
                                replacement.pop('recurrence', None)
                                if override['reminders'] == remote_to_local(conn, instance, calendar_id)['reminders'] and 'reminders' in instance:
                                    replacement['reminders'] = instance['reminders']
                                applied = request(client, 'PATCH', path + '/' + enc(instance['id']), body=replacement, params=params, etag=instance.get('etag'))
                                _remember_exception(conn, calendar_id, applied)
                            elif instance.get('status') != 'cancelled':
                                request(client, 'DELETE', path + '/' + enc(instance['id']), params={'sendUpdates': updates}, etag=instance.get('etag'))
                                cancelled = {**instance, 'status': 'cancelled'}
                                _remember_exception(conn, calendar_id, cancelled)
                        except RemoteError as exc:
                            if exc.status in {409, 412}:
                                latest = checked_request(client, 'GET', path + '/' + enc(instance['id']))
                                _record_conflict(conn, {'event_id': row['id'], 'calendar_id': calendar_id}, latest)
                                result['conflicts'] += 1
                            else:
                                result['errors'].append({'event_id': row['id'], 'status': exc.status, 'error': 'Google rejected this recurrence override; review permissions and retry.'})
                            break
                    else:
                        conn.execute('INSERT INTO calendar_google_events(calendar_id,google_id,event_id,etag,synced_revision,base_json,remote_json) VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET etag=excluded.etag,synced_revision=excluded.synced_revision,base_json=excluded.base_json,remote_json=excluded.remote_json',
                                     (calendar_id, google_id, row['id'], remote.get('etag'), row['revision'], json.dumps(remote), json.dumps(remote)))
                        result['pushed'] += 1
                        continue
                else:
                    conn.execute('UPDATE calendar_google_events SET synced_revision=? WHERE event_id=?', (row['revision'], row['id']))
                    result['pushed'] += 1
    return result


def resolve_conflict(settings, conn, body):
    if body.get('confirmed') is not True:
        local.fail('Confirm the exact conflict resolution')
    row = conn.execute('SELECT * FROM calendar_google_conflicts WHERE id=? AND resolved_at IS NULL', (body.get('id'),)).fetchone()
    if not row:
        local.fail('Conflict not found', 404)
    event = local._event(conn, row['event_id'], include_deleted=True)
    if body.get('revision') != event['revision']:
        local.fail('Local event changed; reload conflict before resolving', 409)
    choice = body.get('choice')
    if choice not in {'local', 'remote', 'duplicate'}:
        local.fail('Choose local, remote, or duplicate')
    remote = json.loads(row['remote_json'])
    mapping = conn.execute('SELECT * FROM calendar_google_events WHERE event_id=?', (row['event_id'],)).fetchone()
    if not mapping:
        mapping = {'event_id': row['event_id'], 'calendar_id': row['calendar_id'], 'google_id': row['google_id'], 'synced_revision': 0, 'base_json': None}
    with session(settings) as client:
        path = 'calendars/' + enc(local._get_calendar(conn, row['calendar_id'])['google_id']) + '/events/' + enc(row['google_id'])
        try:
            latest = request(client, 'GET', path)
        except RemoteError as exc:
            if exc.status in {404, 410} and remote.get('status') == 'cancelled':
                latest = remote
            else:
                local.fail('Google conflict could not be refreshed; retry after checking authorization and access', 502)
    if latest.get('etag') != remote.get('etag'):
        _record_conflict(conn, row, latest)
        local.fail('Google changed again; review the refreshed conflict', 409)
    with transaction(conn):
        if local._event(conn, row['event_id'], include_deleted=True)['revision'] != body['revision']:
            local.fail('Local event changed during review; reload conflict', 409)
        if choice == 'duplicate' and not event['deleted']:
            data = {k: v for k, v in event.items() if k in local.EVENT_FIELDS}
            data.update(calendar_id='personal', title=data['title'] + ' (local conflict copy)', source_key=None)
            ident = local._insert(conn, local.normalize_event(conn, data))
            local._history(conn, 'event', ident, None, local._snapshot(conn, ident))
        if choice in {'remote', 'duplicate'}:
            _apply_remote(conn, row['calendar_id'], remote, mapping)
        elif remote.get('status') == 'cancelled':
            conn.execute('DELETE FROM calendar_google_events WHERE event_id=?', (row['event_id'],))
        elif not remote.get('recurringEventId'):
            # Keep local dirty for a separately approved push against latest ETag.
            conn.execute('INSERT INTO calendar_google_events(calendar_id,google_id,event_id,etag,synced_revision,base_json,remote_json) VALUES(?,?,?,?,0,?,?) ON CONFLICT(event_id) DO UPDATE SET etag=excluded.etag,base_json=excluded.base_json,remote_json=excluded.remote_json',
                         (row['calendar_id'], row['google_id'], row['event_id'], remote.get('etag'), json.dumps(remote), json.dumps(remote)))
        else:
            _remember_exception(conn, row['calendar_id'], remote)
        conn.execute('UPDATE calendar_google_conflicts SET resolved_at=? WHERE id=?', (local.now(), row['id']))
    return {'ok': True, 'notice': 'Local selection awaits a separately confirmed push.' if choice == 'local' else 'Conflict resolved locally.'}


def _required_text(value, name, limit=1024):
    if not local.text(value, name, limit):
        local.fail(name + ' is required')
    return value


def _watch_channel(body, data):
    if body.get('webhook_confirmed') is not True:
        local.fail('Confirm your public HTTPS webhook receiver is ready; rika! localhost is not a public receiver and does not provision or renew channels')
    _required_text(data.get('id'), 'Channel ID')
    _required_text(data.get('address'), 'Public HTTPS receiver address', 8192)
    try:
        parsed = urlsplit(data.get('address', ''))
        host = parsed.hostname
        valid = parsed.scheme == 'https' and bool(host) and not parsed.username and not parsed.password and not parsed.fragment
        if host:
            try:
                valid = valid and ipaddress.ip_address(host).is_global
            except ValueError:
                valid = valid and '.' in host and not host.endswith(('.localhost', '.local', '.internal'))
        parsed.port
    except (TypeError, ValueError):
        valid = False
    if not valid:
        local.fail('Watch address must be a public HTTPS receiver, not localhost or a private IP')
    if data.get('type') != 'web_hook':
        local.fail('Watch channel type must be web_hook')
    options = data.get('params', {})
    if not isinstance(options, dict) or set(options) - {'ttl'}:
        local.fail('Channel params accepts only ttl')
    ttl = options.get('ttl')
    if ttl is not None and (not isinstance(ttl, str) or len(ttl) > 10 or not ttl.isdecimal() or int(ttl) <= 0):
        local.fail('Channel ttl must be a positive seconds string')


def _event_recipients(*events):
    recipients = set()
    for event in events:
        attendees = event.get('attendees', [])
        if not isinstance(attendees, list) or any(not isinstance(v, dict) or not isinstance(v.get('email'), str) or not v['email'].strip() for v in attendees):
            local.fail('Review attendees as objects with exact email addresses')
        recipients.update(v['email'] for v in attendees)
        organizer = event.get('organizer', {})
        if isinstance(organizer, dict) and organizer.get('email'):
            recipients.add(organizer['email'])
    return sorted(recipients)


def _brief(value):
    """Identify the exact remote item a reviewed change affects."""
    keys = ('id', 'summary', 'title', 'status', 'start', 'end', 'due', 'completed', 'eventType', 'organizer', 'recurringEventId')
    return {key: value[key] for key in keys if key in value}


def _meet_request(body):
    """The proposal's Meet requestId: generated once at preview, so applying sends the reviewed one."""
    value = body.get('meet_request_id')
    return value if isinstance(value, str) and value else uuid4().hex


def _special_event(data, current=None, *, creating=False, replacing=False):
    current = current or {}
    kind = data.get('eventType', current.get('eventType', 'default'))
    if kind not in {'default', 'birthday', 'fromGmail', 'focusTime', 'outOfOffice', 'workingLocation'}:
        local.fail('Event type is not exposed by Calendar API; use Google Calendar for appointment schedules')
    if current and kind != current.get('eventType', 'default'):
        local.fail('An existing Google event type cannot be changed')
    if kind == 'fromGmail':
        if creating:
            local.fail('fromGmail events are created only by Google; no Gmail scope is requested')
        allowed = {'colorId', 'reminders', 'visibility', 'transparency', 'status', 'attendees', 'extendedProperties'}
        if any(key not in allowed and value != current.get(key) for key, value in data.items()):
            local.fail('Only documented editable fromGmail fields may change; source content remains Google-owned')
    if kind != 'birthday':
        return
    mutable = {'summary', 'colorId', 'reminders', 'start', 'end'}
    if not creating and any(key not in mutable and value != current.get(key) for key, value in data.items()):
        local.fail('Birthday updates accept only summary, colorId, reminders and eligible date changes')
    if creating:
        allowed = mutable | {'eventType', 'visibility', 'transparency', 'recurrence', 'birthdayProperties'}
        properties = data.get('birthdayProperties', {})
        if set(data) - allowed or data.get('visibility') != 'private' or data.get('transparency') != 'transparent':
            local.fail('Birthday creation requires only documented fields, private visibility and transparent availability')
        if not isinstance(properties, dict) or set(properties) - {'type', 'customTypeName', 'contact'} or properties.get('type', 'birthday') != 'birthday' or properties.get('customTypeName') or properties.get('contact'):
            local.fail('Birthday creation cannot set custom/contact/self birthday properties')
    if creating or replacing or 'start' in data or 'end' in data:
        start, end = data.get('start', current.get('start', {})), data.get('end', current.get('end', {}))
        try:
            first, last = date.fromisoformat(start['date']), date.fromisoformat(end['date'])
            valid = set(start) <= {'date', 'timeZone'} and set(end) <= {'date', 'timeZone'} and last - first == timedelta(days=1)
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            local.fail('Birthday dates must define an all-day event spanning exactly one day')
        properties = current.get('birthdayProperties', {})
        if not creating and (properties.get('contact') or properties.get('type') == 'self') and (start != current.get('start') or end != current.get('end')):
            local.fail('Contact/self birthday dates remain Google-owned; edit Google Contacts or your Google Account')
        if creating:
            rule = 'RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=-1' if first.month == 2 and first.day == 29 else 'RRULE:FREQ=YEARLY'
            if data.get('recurrence') != [rule]:
                local.fail('Birthday creation requires the documented annual recurrence (including February 29 handling)')


def _plan(settings, body):
    """Validate one exact public API request without touching the network."""
    resource = body.get('resource')
    action = body.get('action', 'list')
    if action not in PUBLIC_OPERATIONS.get(resource, []):
        local.fail('Unsupported action for this Google resource')
    read = action in {'list', 'get', 'instances', 'freebusy'}
    data = body.get('data', {})
    if not isinstance(data, dict):
        local.fail('Google operation data must be an object')
    etag = body.get('etag')
    if action in {'update', 'replace', 'delete', 'rsvp', 'move', 'clear'} and (not isinstance(etag, str) or not etag.strip()):
        local.fail('Load the resource first and include its ETag before changing it')
    method = {'list': 'GET', 'get': 'GET', 'create': 'POST', 'update': 'PATCH', 'replace': 'PUT', 'delete': 'DELETE',
              'rsvp': 'PATCH', 'instances': 'GET', 'freebusy': 'POST', 'move': 'POST', 'import': 'POST',
              'quickAdd': 'POST', 'watch': 'POST', 'clear': 'POST', 'stop': 'POST'}[action]
    if not isinstance(body.get('params', {}), dict):
        local.fail('Google query parameters must be an object')
    params = dict(body.get('params', {}))
    query_fields = set()
    item_action = action in {'get', 'update', 'replace', 'delete', 'rsvp', 'instances', 'move'}
    preflight = None
    if item_action and resource not in {'colors', 'settings', 'calendars'}:
        _required_text(body.get('id'), 'Google resource ID')
    if action in {'move', 'import', 'quickAdd', 'watch', 'clear'} and resource in {'events', 'acl', 'tasks', 'calendars'}:
        _required_text(body.get('calendar_id'), 'Source calendar/tasklist ID')
    tasks = False
    if resource == 'calendarList':
        path = 'users/me/calendarList' + ('/' + enc(body.get('id', '')) if item_action else '')
        allowed = {'id', 'summaryOverride', 'colorId', 'backgroundColor', 'foregroundColor', 'hidden', 'selected', 'defaultReminders', 'notificationSettings'}
        if action == 'list':
            query_fields = {'maxResults', 'minAccessRole', 'showDeleted', 'showHidden', 'syncToken'}
        elif action in {'create', 'update', 'replace'}:
            query_fields = {'colorRgbFormat'}
            params.setdefault('colorRgbFormat', 'true')
    elif resource == 'calendars':
        path = 'calendars' + ('/' + enc(body.get('calendar_id') if action == 'clear' else body.get('id', 'primary')) if item_action or action == 'clear' else '')
        allowed = {'summary', 'description', 'location', 'timeZone', 'labelProperties'}
        if action == 'clear':
            preflight, path = path, path + '/clear'
    elif resource in {'colors', 'settings'}:
        if action == 'list' and resource == 'settings':
            query_fields = {'maxResults', 'syncToken'}
        path = 'colors' if resource == 'colors' else 'users/me/settings' + ('/' + enc(body.get('id', 'timezone')) if action == 'get' else '')
        allowed = set()
    elif resource == 'events':
        path = 'calendars/' + enc(body.get('calendar_id', 'primary')) + '/events'
        if item_action:
            path += '/' + enc(body.get('id', ''))
        if action == 'instances':
            path += '/instances'
        allowed = {'summary', 'description', 'location', 'start', 'end', 'recurrence', 'attendees', 'visibility', 'transparency', 'reminders', 'attachments', 'conferenceData', 'eventType', 'focusTimeProperties', 'outOfOfficeProperties', 'workingLocationProperties', 'birthdayProperties', 'guestsCanInviteOthers', 'guestsCanModify', 'guestsCanSeeOtherGuests', 'colorId', 'eventLabelId', 'extendedProperties', 'status', 'sequence', 'source', 'attendeesOmitted', 'originalStartTime'}
        if action in {'list', 'instances'}:
            query_fields = {'timeMin', 'timeMax', 'maxResults', 'showDeleted', 'timeZone', 'maxAttendees', 'originalStart'}
            if action == 'list':
                query_fields |= {'q', 'singleEvents', 'updatedMin', 'orderBy', 'iCalUID', 'syncToken', 'eventTypes', 'privateExtendedProperty', 'sharedExtendedProperty', 'showHiddenInvitations'}
        elif action == 'get':
            query_fields = {'maxAttendees', 'timeZone'}
        elif action in {'create', 'update', 'replace', 'rsvp', 'import'}:
            query_fields = {'conferenceDataVersion', 'supportsAttachments', 'eventLabelVersion'}
            params.setdefault('supportsAttachments', 'true')
            params.setdefault('conferenceDataVersion', 1)
            if 'eventLabelId' in data and params.get('eventLabelVersion') != 1:
                local.fail('Event labels require the exact eventLabelVersion=1 query parameter')
            if action != 'import':
                query_fields |= {'sendUpdates', 'maxAttendees'}
        elif action == 'delete':
            query_fields = {'sendUpdates'}
        elif action == 'move':
            query_fields = {'destination', 'sendUpdates'}
            _required_text(params.get('destination'), 'Destination calendar ID')
            preflight, path = path, path + '/move'
        if action == 'import':
            path += '/import'
            allowed |= {'iCalUID', 'organizer'}
            _required_text(data.get('iCalUID'), 'Import iCalUID')
            if data.get('eventType', 'default') != 'default':
                local.fail('Import supports only default event type; do not silently convert special events')
        if action in {'create', 'replace', 'import'} and any(not isinstance(data.get(key), dict) or not data[key] for key in ('start', 'end')):
            local.fail('Event create/replace/import requires exact start and end objects')
        if action == 'quickAdd':
            path += '/quickAdd'
            allowed, query_fields = {'text'}, {'sendUpdates'}
            params['text'] = _required_text(data.get('text'), 'QuickAdd text', 8192)
            if '@' in params['text']:
                local.fail('QuickAdd cannot review embedded email addresses without a provider dry-run; use structured create for reviewed attendees')
            if params.get('sendUpdates') != 'none':
                local.fail('QuickAdd requires sendUpdates=none; use structured create for reviewed attendees')
        if action == 'rsvp' and set(data) != {'attendees'}:
            local.fail('RSVP accepts only the exact reviewed attendee response array')
        if not read and action not in {'watch', 'import'}:
            if params.get('sendUpdates') not in {'none', 'all', 'externalOnly'}:
                local.fail('Choose sendUpdates none/all/externalOnly explicitly')
    elif resource == 'freebusy' and action == 'freebusy':
        path = 'freeBusy'
        allowed = {'timeMin', 'timeMax', 'timeZone', 'items', 'groupExpansionMax', 'calendarExpansionMax'}
    elif resource == 'acl':
        path = 'calendars/' + enc(body.get('calendar_id', 'primary')) + '/acl'
        if item_action:
            path += '/' + enc(body.get('id', ''))
        allowed = {'scope', 'role'}
        if action == 'list':
            query_fields = {'maxResults', 'showDeleted', 'syncToken'}
        if action in {'create', 'update', 'replace'}:
            if data.get('role') and data['role'] not in {'none', 'freeBusyReader', 'reader', 'writerWithoutPrivateAccess', 'writer', 'owner'}:
                local.fail('Invalid Google sharing role')
            if not isinstance(data.get('scope'), dict):
                local.fail('Review exact sharing scope (type and value)')
            query_fields = {'sendNotifications'}
            params['sendNotifications'] = 'true' if body.get('notify') is True else 'false'
    elif resource in {'tasklists', 'tasks'}:
        tasks = True
        if not status(settings)['tasks_authorized']:
            local.fail('Authorize again with optional Google Tasks scope checked', 403)
        path = 'users/@me/lists' if resource == 'tasklists' else 'lists/' + enc(body.get('calendar_id', '@default')) + '/tasks'
        if item_action:
            path += '/' + enc(body.get('id', ''))
        allowed = {'title'} if resource == 'tasklists' else {'title', 'notes', 'due', 'status', 'completed'}
        if resource == 'tasks' and set(data) & {'repeat_frequency', 'repeat_interval', 'repeat_until'}:
            local.fail('Local task recurrence cannot be represented by the public Google Tasks API; keep recurring tasks local or manage Google repetition in its browser')
        if action == 'list':
            query_fields = {'maxResults'} if resource == 'tasklists' else {'maxResults', 'showDeleted', 'showHidden', 'showCompleted', 'showAssigned', 'dueMin', 'dueMax', 'completedMin', 'completedMax', 'updatedMin'}
        if resource == 'tasks' and action in {'create', 'move'}:
            query_fields = {'parent', 'previous'} | ({'destinationTasklist'} if action == 'move' else set())
            for key in query_fields & set(params):
                _required_text(params[key], key)
        if action == 'move':
            preflight, path = path, path + '/move'
        elif action == 'clear':
            preflight = 'users/@me/lists/' + enc(body.get('calendar_id'))
            path = 'lists/' + enc(body.get('calendar_id')) + '/clear'
    elif resource == 'channels':
        path, allowed = 'channels/stop', {'id', 'resourceId'}
        _required_text(data.get('id'), 'Channel ID')
        _required_text(data.get('resourceId'), 'Channel resourceId')
    else:
        local.fail('Unsupported Google resource/action')
    if action == 'watch':
        path += '/watch'
        allowed = {'id', 'type', 'address', 'params'}
        query_fields = {'eventTypes'} if resource == 'events' else set()
        _watch_channel(body, data)
    if action in {'move', 'clear', 'delete'}:
        allowed = set()
    if set(params) - query_fields - ({'text'} if action == 'quickAdd' else set()):
        local.fail('Unsupported Google query parameter for this resource/action')
    if set(data) - allowed:
        local.fail('Unsupported fields for this Google resource')
    if 'timeZone' in data:
        local.zone(data['timeZone'])
    if resource == 'events' and body.get('meet'):
        if action not in {'create', 'update', 'replace', 'import'}:
            local.fail('Meet creation requires event create/update/replace/import')
        data = {**data, 'conferenceData': {'createRequest': {'requestId': _meet_request(body), 'conferenceSolutionKey': {'type': 'hangoutsMeet'}}}}
    return {'resource': resource, 'action': action, 'read': read, 'method': method, 'path': path, 'params': params,
            'data': data, 'etag': etag, 'tasks': tasks, 'preflight': preflight}


def _review(client, plan, body):
    """Build the frozen review from live Google state; raises before any write."""
    resource, action, data, params = plan['resource'], plan['action'], plan['data'], plan['params']
    current = {}
    if action in {'update', 'replace', 'delete', 'rsvp', 'move', 'clear'}:
        current = checked_request(client, 'GET', plan['preflight'] or plan['path'], tasks=plan['tasks'])
        if current.get('etag') != plan['etag']:
            local.fail('Google resource changed; reload and review before applying your change', 412)
    listed, recipients, notes = [], [], []
    if action == 'clear':
        container = enc(body['calendar_id'])
        if resource == 'calendars':
            listed = [v for v in page_all(client, 'calendars/' + container + '/events')['items'] if v.get('status') != 'cancelled']
            recipients = _event_recipients(*listed)
            notes.append(f'Permanently deletes all {len(listed)} listed events on this calendar; rika! cannot undo Google changes.')
        else:
            listed = [v for v in page_all(client, 'lists/' + container + '/tasks', {'showCompleted': 'true', 'showHidden': 'false'}, tasks=True)['items']
                      if v.get('status') == 'completed' and not v.get('hidden')]
            notes.append(f'Hides the {len(listed)} listed completed tasks; incomplete tasks are unchanged.')
    if resource == 'events' and action != 'watch':
        recipients = _event_recipients(current, data)
        if action == 'move' and current.get('eventType', 'default') != 'default':
            local.fail('Only default events can move; special-event organizers remain Google-owned')
        if action in {'create', 'import', 'update', 'replace', 'rsvp'}:
            _special_event(data, current, creating=action in {'create', 'import'}, replacing=action == 'replace')
    if action == 'move':
        notes.append('Moves this item to the exact destination in params; for events the destination calendar becomes the organizer.')
    elif action == 'watch':
        notes.append(f"Google will POST change notifications to {data['address']} until the channel expires; rika! neither receives nor renews them.")
    elif action == 'quickAdd':
        notes.append("Google parses this text server-side with no dry run; inspect the created event afterwards.")
    elif action == 'import':
        notes.append('Imports a private copy by iCalUID; import has no sendUpdates option and does not invite listed attendees.')
    elif action == 'delete':
        notes.append('Deletes the remote resource; rika! cannot undo Google changes.')
    empty = action in {'move', 'clear', 'quickAdd'}
    return {'resource': resource, 'action': action, 'method': plan['method'],
            'url': (TASKS_BASE if plan['tasks'] else BASE) + plan['path'], 'params': params,
            'body': data if plan['method'] in {'POST', 'PATCH', 'PUT'} and not empty else None,
            'if_match': plan['etag'] or None, 'current': _brief(current) if current else None,
            'recipients': recipients, 'items': [_brief(v) for v in listed], 'notes': notes}


def _send(client, plan, payload):
    return checked_request(client, plan['method'], plan['path'], body=payload, params=plan['params'],
                           etag=plan['etag'], tasks=plan['tasks'])


def _require_clear_confirmation(body, items):
    if items and body.get('clear_confirmed') is not True:
        local.fail(f'Confirm clearing all {len(items)} listed items; this cannot be undone from rika!')


OPERATION_FIELDS = ('resource', 'action', 'calendar_id', 'id', 'etag', 'data', 'params', 'meet', 'notify', 'webhook_confirmed')
OPERATION_DRIFT = 'Google request or live target differs from the reviewed preview; prepare and approve a fresh preview'
OPERATION_FRESH = 'prepare and approve a fresh preview'


@dataclass(frozen=True)
class _Remote:
    """What a resource change's preconditions read: live Google state through this client."""

    settings: object
    client: object


def _operation_request(body):
    """The exact request fields of a resource-editor body (never its approval flags)."""
    return {key: body[key] for key in OPERATION_FIELDS if key in body}


def _live_review(ctx, payload):
    """The exact request, live target (ETag GET preflight), recipients and clear listing, read now."""
    return {'review': _review(ctx.client, _plan(ctx.settings, payload), payload)}


def _change_approved(ctx, effect):
    """Approving records the change; the reviewed body is sent after the claim commits."""
    return effect.preconditions['review']['body']


CHANGE = effects.REGISTRY.register(effects.Executable(
    name='google_resource_change', title='Google resource change', proposers=frozenset({'user'}),
    preconditions=_live_review, review='inline', remote=True, lifetime=EFFECT_LIFETIME, run=_change_approved,
))


def preview_operation(settings, conn, body, *, actor):
    """Propose one exact change whose preconditions freeze the live review for approval."""
    payload = _operation_request(body)
    if payload.get('meet'):
        payload['meet_request_id'] = uuid4().hex
    if _plan(settings, payload)['read']:
        local.fail('Read actions run directly; previews are for external changes')
    with session(settings) as client:
        try:
            effect = effects.propose(conn, CHANGE.name, payload, actor=actor, ctx=_Remote(settings, client))
        except effects.EffectError as exc:
            local.fail(str(exc), exc.status)
    return {**effect.preconditions['review'], 'effect': _effect_ref(effect)}


def operation(settings, conn, body, *, actor):
    """Explicit, exact reviewed Google resource operations; no arbitrary URL proxy."""
    proposal = _proposed(conn, body, CHANGE, OPERATION_FRESH) if 'effect_id' in body else None
    if proposal is not None and (sent := _operation_request(body)) and sent != _operation_request(proposal.payload):
        local.fail(OPERATION_DRIFT, 409)
    plan = _plan(settings, proposal.payload if proposal is not None else body)
    if plan['read']:
        with session(settings) as client:
            if plan['action'] in {'list', 'instances'} and plan['resource'] != 'colors':
                return page_all(client, plan['path'], plan['params'], tasks=plan['tasks'])
            return _send(client, plan, plan['data'] if plan['method'] == 'POST' else None)
    if body.get('confirmed') is not True or body.get('external_approved') is not True:
        local.fail('Review and explicitly approve this exact external change')
    if proposal is None:
        local.fail('Prepare the exact operation preview, review it, then approve that frozen request', 409)
    # The reviewed listing is confirmed here; approval then requires the live listing to equal it.
    listed = proposal.preconditions['review']['items']
    _require_clear_confirmation(body, listed)
    with session(settings) as client:
        try:
            _, reviewed_body = effects.approve_and_execute(
                conn, proposal.id, body.get('digest'), actor=actor, ctx=_Remote(settings, client),
                options={'clear_confirmed': True} if listed else None)
        except effects.EffectError as exc:
            _refused(exc, OPERATION_DRIFT, OPERATION_FRESH)
        return _send(client, plan, reviewed_body)
