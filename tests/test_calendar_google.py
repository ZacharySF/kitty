"""Real adapter HTTP against a loopback provider; no Google account or credentials."""
from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
import requests

from kitty import web_calendar
from kitty.core import calendar, calendar_google as google
from kitty.web_server import APIError, WebApplication


class Provider:
    def __init__(self):
        self.calendar = {'id': 'remote', 'summary': 'Synthetic Google', 'timeZone': 'America/Chicago', 'etag': '"calendar1"'}
        self.subscription = {**self.calendar, 'backgroundColor': '#123abc', 'accessRole': 'owner', 'etag': '"subscription1"'}
        self.events = {}
        self.instances = {}
        self.tasklists = {}
        self.tasks = {}
        self.acl = {'rule1': {'id': 'rule1', 'scope': {'type': 'user', 'value': 'reader@example.invalid'}, 'role': 'reader', 'etag': '"acl1"'}}
        self.channels = {}
        self.moved_events = {}
        self.calls = []
        self.sequence = 0
        self.expire_token = False
        self.page_size = 100
        self.race_id = None
        self.fail = {}

    def stamp(self, value):
        self.sequence += 1
        return {**value, 'etag': f'"synthetic{self.sequence}"'}

    def add_event(self, ident='event1', **changes):
        value = self.stamp({'id': ident, 'summary': 'Remote study',
                            'start': {'dateTime': '2026-10-26T09:00:00-05:00', 'timeZone': 'America/Chicago'},
                            'end': {'dateTime': '2026-10-26T10:00:00-05:00', 'timeZone': 'America/Chicago'},
                            'reminders': {'useDefault': False, 'overrides': [{'method': 'popup', 'minutes': 10}]},
                            **changes})
        self.events[ident] = value
        return copy.deepcopy(value)

    @property
    def writes(self):
        return [v for v in self.calls if v['method'] != 'GET']

    def dispatch(self, call):
        method, path, data, query = (call[k] for k in ('method', 'path', 'data', 'query'))
        if (method, path) in self.fail:
            return self.fail.pop((method, path)), {'error': {'message': 'synthetic-private-provider-detail'}}
        if method == 'POST' and path in {
                '/calendar/v3/calendars/remote/events/watch', '/calendar/v3/calendars/remote/acl/watch',
                '/calendar/v3/users/me/calendarList/watch', '/calendar/v3/users/me/settings/watch'}:
            result = {'id': data['id'], 'resourceId': 'resource-' + data['id'], 'expiration': '1234567890000'}
            self.channels[data['id']] = result
            return 200, result
        if method == 'POST' and path == '/calendar/v3/channels/stop':
            if self.channels.get(data['id'], {}).get('resourceId') != data['resourceId']:
                return 404, {'error': 'unknown channel'}
            del self.channels[data['id']]
            return 204, None
        if method == 'POST' and path == '/calendar/v3/calendars/remote/clear':
            self.events.clear()
            return 204, None
        if path.startswith('/calendar/v3/calendars/remote/acl/'):
            ident = path.rsplit('/', 1)[-1]
            if ident in self.acl:
                return self.resource(method, self.acl[ident], data, call)
            return 404, {'error': 'missing rule'}
        if method == 'GET' and path == '/calendar/v3/users/me/calendarList':
            return 200, {'items': [self.subscription]}
        if path == '/calendar/v3/calendars/remote':
            return self.resource(method, self.calendar, data, call)
        if path == '/calendar/v3/users/me/calendarList/remote':
            return self.resource(method, self.subscription, data, call)
        prefix = '/calendar/v3/calendars/remote/events'
        if path == prefix and method == 'GET':
            if self.expire_token and 'syncToken' in query:
                self.expire_token = False
                return 410, {'error': 'expired'}
            values = list(self.events.values())
            offset = int(query.get('pageToken', ['0'])[0])
            page = {'items': values[offset:offset + self.page_size]}
            if offset + self.page_size < len(values):
                page['nextPageToken'] = str(offset + self.page_size)
            else:
                page['nextSyncToken'] = 'synthetic-sync-token'
            return 200, page
        if path.startswith(prefix):
            suffix = path[len(prefix):].strip('/')
            if method == 'POST' and suffix in {'import', 'quickAdd'}:
                ident = f'generated{self.sequence}'
                payload = data if suffix == 'import' else {'summary': query['text'][0], 'attendees': []}
                self.events[ident] = self.stamp({**payload, 'id': ident})
                return 200, self.events[ident]
            if method == 'POST' and suffix.endswith('/move'):
                ident = suffix.removesuffix('/move')
                if ident not in self.events:
                    return 404, {'error': 'missing'}
                moved = self.stamp(self.events.pop(ident))
                self.moved_events.setdefault(query['destination'][0], {})[ident] = moved
                return 200, moved
            if suffix.endswith('/instances') and method == 'GET':
                return 200, {'items': self.instances.get(suffix.split('/')[0], [])}
            instances = {v['id']: v for values in self.instances.values() for v in values}
            if method == 'POST' and not suffix:
                ident = data.get('id', f'generated{self.sequence}')
                if ident in self.events:
                    return 409, {'error': 'exists'}
                self.events[ident] = self.stamp({**data, 'id': ident})
                return 200, self.events[ident]
            current = self.events.get(suffix) or instances.get(suffix)
            if not current:
                return 404, {'error': 'missing'}
            if method == 'PATCH' and self.race_id == suffix:
                current.update(self.stamp({**current, 'summary': 'Changed during push'}))
                self.race_id = None
            return self.resource(method, current, data, call)
        if path.startswith('/tasks/v1/'):
            suffix = path.removeprefix('/tasks/v1/')
            if method == 'POST' and suffix == 'lists/list1/clear':
                for task in self.tasks.values():
                    if task.get('status') == 'completed':
                        task['hidden'] = True
                return 204, None
            if suffix.startswith('users/@me/lists'):
                prefix, store = 'users/@me/lists', self.tasklists
            elif suffix.startswith('lists/list1/tasks'):
                prefix, store = 'lists/list1/tasks', self.tasks
            else:
                return 404, {'error': 'unknown task list'}
            ident = suffix[len(prefix):].strip('/')
            if method == 'POST' and ident.endswith('/move'):
                ident = ident.removesuffix('/move')
                if ident not in store:
                    return 404, {'error': 'missing task'}
                store[ident] = self.stamp({**store[ident], **{key: values[0] for key, values in query.items()}})
                return 200, store[ident]
            if not ident and method == 'GET':
                return 200, {'items': list(store.values())}
            if not ident and method == 'POST':
                ident = f'item{self.sequence}'
                store[ident] = self.stamp({**data, 'id': ident})
                return 200, store[ident]
            if ident not in store:
                return 404, {'error': 'missing'}
            result = self.resource(method, store[ident], data, call)
            if method == 'DELETE' and result[0] == 204:
                del store[ident]
            return result
        if path == '/calendar/v3/colors' and method == 'GET':
            return 200, {'calendar': {'1': {'background': '#123abc'}}, 'event': {}}
        if path == '/calendar/v3/users/me/settings' and method == 'GET':
            return 200, {'items': [{'id': 'timezone', 'value': 'America/Chicago'}]}
        if path == '/calendar/v3/freeBusy' and method == 'POST':
            return 200, {'calendars': {'remote': {'busy': []}}}
        return 404, {'error': 'unknown synthetic resource'}

    def resource(self, method, current, data, call):
        if method == 'GET':
            return 200, current
        if call['etag'] != current.get('etag'):
            return 412, {'error': 'stale'}
        if method == 'PUT':
            ident = current.get('id')
            current.clear()
            current.update(self.stamp({**data, 'id': ident}))
            return 200, current
        if method == 'PATCH':
            current.update(self.stamp({**current, **data}))
            return 200, current
        if method == 'DELETE':
            current.update(self.stamp({**current, 'status': 'cancelled'}))
            return 204, None
        return 400, {'error': 'unsupported method'}


@pytest.fixture
def provider(monkeypatch):
    site = Provider()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            parsed = urlsplit(self.path)
            length = int(self.headers.get('Content-Length', '0'))
            data = json.loads(self.rfile.read(length)) if length else None
            call = {'method': self.command, 'path': unquote(parsed.path),
                    'query': parse_qs(parsed.query), 'data': data,
                    'etag': self.headers.get('If-Match')}
            site.calls.append(copy.deepcopy(call))
            status, result = site.dispatch(call)
            content = json.dumps(result).encode() if result is not None else b''
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = handle_request

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(google, 'BASE', origin + '/calendar/v3/')
    monkeypatch.setattr(google, 'TASKS_BASE', origin + '/tasks/v1/')
    monkeypatch.setattr(google, 'session', lambda settings: requests.Session())
    try:
        yield site
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def linked(workspace):
    return google.select_calendar(workspace.settings, workspace.conn, {'google_id': 'remote', 'confirmed': True})


def sync(workspace, cal, direction='pull', **changes):
    body = {'confirmed': True, 'calendar_ids': [cal['id']], 'direction': direction, **changes}
    if direction == 'both':
        body.setdefault('external_approved', True)
        body.setdefault('review', google.preview_sync(workspace.conn, body))
    return google.sync(workspace.settings, workspace.conn, body)


def local_event(workspace, cal, **changes):
    return calendar.create_event(workspace.conn, {'calendar_id': cal['id'], 'title': 'Local study',
                                                  'start': '2026-10-26T09:00:00-05:00',
                                                  'end': '2026-10-26T10:00:00-05:00',
                                                  'timezone': 'America/Chicago', **changes})


def edit(workspace, item, **changes):
    return calendar.edit_event(workspace.conn, item['id'], {'revision': item['revision'], 'changes': changes})


def approved(settings, body, **extra):
    """Apply exactly the server-built preview, as the UI does after review."""
    review = google.preview_operation(settings, body)
    return google.operation(settings, {**body, 'confirmed': True, 'external_approved': True, 'review': review, **extra})



def test_unconfigured_setup_and_private_oauth_contract(workspace):
    app = SimpleNamespace(settings=workspace.settings, _no_query=WebApplication._no_query)
    result = web_calendar.get(app, workspace.conn,
                              '/api/calendar/google/status', {})
    assert result['authorized'] is False and result['client_present'] is False
    assert any('Desktop' in step for step in result['steps'])
    assert any('Gmail' in step for step in result['steps'])
    assert any('not mirrored to Google Tasks' in limit for limit in result['limits'])
    with pytest.raises(APIError, match='not authorized'):
        google.session(workspace.settings)
    client = {'installed': {'client_id': 'synthetic.apps.googleusercontent.com', 'client_secret': 'synthetic-secret',
                            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                            'token_uri': 'https://oauth2.googleapis.com/token', 'redirect_uris': ['http://localhost']}}
    with pytest.raises(APIError, match='Confirm'):
        google.upload_client(workspace.settings, {'client': client})
    assert google.upload_client(workspace.settings, {'client': client, 'confirmed': True})['ok']
    saved, token = google.paths(workspace.settings)
    assert saved.stat().st_mode & 0o777 == 0o600 and not token.exists()
    with pytest.raises(APIError, match='official Google'):
        google.validate_client({'installed': {**client['installed'], 'token_uri': 'https://example.invalid/token'}})


def test_pagination_expired_token_and_repeat_pull_have_one_copy(workspace, provider):
    provider.add_event()
    provider.add_event('event2', summary='Second')
    provider.page_size = 1
    cal = linked(workspace)
    assert sync(workspace, cal)['pulled'] == 2
    assert len(calendar.occurrences(workspace.conn, '2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z')) == 2
    assert sync(workspace, cal)['pulled'] == 0
    provider.expire_token = True
    assert sync(workspace, cal)['pulled'] == 0
    assert any('pageToken' in call['query'] for call in provider.calls)
    assert any('syncToken' in call['query'] for call in provider.calls)
    assert provider.writes == []


def test_sync_create_update_delete_requires_fresh_all_date_recipient_review(workspace, provider):
    cal = linked(workspace)
    item = local_event(workspace, cal, start='2028-01-03T09:00:00-06:00', end='2028-01-03T10:00:00-06:00',
                       attendees=[{'email': 'guest@example.invalid', 'optional': True}])
    body = {'calendar_ids': [cal['id']], 'confirmed': True, 'direction': 'both'}
    with pytest.raises(APIError, match='Explicitly approve'):
        google.sync(workspace.settings, workspace.conn, body)
    with pytest.raises(APIError, match='fresh sync preview'):
        google.sync(workspace.settings, workspace.conn, {**body, 'external_approved': True})
    app = SimpleNamespace(modules=workspace.config.modules)
    review = web_calendar.post(app, workspace.conn, '/api/calendar/google/preview', body)
    assert review['events'][0]['attendees'] == ['guest@example.invalid']
    assert review['events'][0]['start'].startswith('2028-')
    item = edit(workspace, item, title='Reviewed later')
    with pytest.raises(APIError) as error:
        sync(workspace, cal, 'both', review=review)
    assert error.value.status == 409 and provider.writes == []
    assert sync(workspace, cal, 'both', send_updates='all')['pushed'] == 1
    created = provider.writes[-1]
    assert created['method'] == 'POST' and created['data']['id'] == item['id']
    assert created['data']['attendees'][0]['optional'] is True
    assert created['query']['sendUpdates'] == ['all']
    old_etag = provider.events[item['id']]['etag']
    item = edit(workspace, item, title='Changed locally')
    assert sync(workspace, cal, 'both')['pushed'] == 1
    assert provider.writes[-1]['etag'] == old_etag
    assert provider.events[item['id']]['summary'] == 'Changed locally'
    calendar.edit_event(workspace.conn, item['id'], {'revision': item['revision'], 'delete': True})
    assert sync(workspace, cal, 'both')['pushed'] == 1
    assert provider.writes[-1]['method'] == 'DELETE' and provider.writes[-1]['etag']
    assert provider.events[item['id']]['status'] == 'cancelled'
    assert sync(workspace, cal)['conflicts'] == 0


@pytest.mark.parametrize('choice', ['local', 'remote', 'duplicate'])
def test_divergent_edit_review_never_silently_pushes(workspace, provider, choice):
    provider.add_event()
    cal = linked(workspace)
    sync(workspace, cal)
    item = calendar.occurrences(workspace.conn, '2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z')[0]
    item = edit(workspace, item, title='Local version')
    remote = provider.add_event(summary='Google version')
    assert sync(workspace, cal, 'both')['conflicts'] == 1
    assert provider.writes == [] and calendar.get_event(workspace.conn, item['id'])['title'] == 'Local version'
    conflict = google.conflicts(workspace.conn)[0]
    google.resolve_conflict(workspace.settings, workspace.conn,
                            {'id': conflict['id'], 'revision': item['revision'], 'choice': choice, 'confirmed': True})
    assert google.conflicts(workspace.conn) == [] and provider.writes == []
    current = calendar.get_event(workspace.conn, item['id'])
    assert current['title'] == ('Local version' if choice == 'local' else 'Google version')
    if choice == 'local':
        assert sync(workspace, cal, 'both')['pushed'] == 1
        assert provider.writes[-1]['etag'] == remote['etag']
    elif choice == 'duplicate':
        copies = calendar.occurrences(workspace.conn, '2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z')
        assert any(v['calendar_id'] == 'personal' and v['title'] == 'Local version (local conflict copy)' for v in copies)


def test_conflicts_carry_google_version_as_a_calendar_event(workspace, provider):
    """Q62: the day grid draws Google's side from the same fields as kitty's, not raw JSON."""
    provider.add_event()
    cal = linked(workspace)
    sync(workspace, cal)
    item = calendar.occurrences(workspace.conn, '2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z')[0]
    edit(workspace, item, title='Local version')
    provider.add_event(summary='Google version', location='Room 204')
    sync(workspace, cal, 'both')
    conflict = google.conflicts(workspace.conn)[0]
    assert conflict['local']['title'] == 'Local version'
    assert conflict['remote']['title'] == 'Google version' and conflict['remote']['location'] == 'Room 204'
    assert conflict['remote']['start'] and conflict['remote']['end'] and 'all_day' in conflict['remote']
    provider.add_event(status='cancelled')
    sync(workspace, cal, 'both')
    assert google.conflicts(workspace.conn)[0]['remote'] is None


def test_provider_etag_race_becomes_actionable_conflict(workspace, provider):
    provider.add_event()
    cal = linked(workspace)
    sync(workspace, cal)
    item = calendar.occurrences(workspace.conn, '2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z')[0]
    item = edit(workspace, item, title='Local reviewed change')
    provider.race_id = 'event1'
    result = sync(workspace, cal, 'both')
    assert result['pushed'] == 0 and result['conflicts'] == 1
    assert calendar.get_event(workspace.conn, item['id'])['title'] == 'Local reviewed change'
    conflict = google.conflicts(workspace.conn)[0]
    provider.add_event(summary='Changed again during review')
    with pytest.raises(APIError) as error:
        google.resolve_conflict(workspace.settings, workspace.conn,
                                {'id': conflict['id'], 'revision': item['revision'], 'choice': 'remote', 'confirmed': True})
    assert error.value.status == 409
    assert google.conflicts(workspace.conn)[0]['remote_json']['summary'] == 'Changed again during review'


def test_unsupported_provider_recurrence_reports_limit_without_fake_import(workspace, provider):
    provider.add_event(recurrence=['RDATE:20261027T140000Z'])
    provider.add_event('normal')
    cal = linked(workspace)
    result = sync(workspace, cal)
    assert result['pulled'] == 1 and len(result['errors']) == 1
    assert 'RDATE' in result['errors'][0]['error']
    assert provider.writes == [] and len(provider.events) == 2


@pytest.mark.parametrize('reminders', [
    {'useDefault': True},
    {'useDefault': False, 'overrides': [{'method': 'email', 'minutes': 30}]},
])
def test_unrelated_local_edit_preserves_google_reminder_policy(workspace, provider, reminders):
    provider.add_event(reminders=reminders)
    cal = linked(workspace)
    sync(workspace, cal)
    item = calendar.occurrences(workspace.conn, '2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z')[0]
    edit(workspace, item, title='Only title changed')
    assert sync(workspace, cal, 'both')['pushed'] == 1
    assert provider.events['event1']['reminders'] == reminders


def test_missing_instance_retains_master_mapping_and_retries_update(workspace, provider):
    cal = linked(workspace)
    item = local_event(workspace, cal, rrule='FREQ=WEEKLY;COUNT=2')
    calendar.edit_event(workspace.conn, item['id'], {'revision': item['revision'], 'scope': 'this',
                                                   'occurrence': item['start'], 'changes': {'title': 'Override'}})
    result = sync(workspace, cal, 'both')
    assert result['pushed'] == 0 and 'instance unavailable' in result['errors'][0]['error']
    mapping = workspace.conn.execute('SELECT * FROM calendar_google_events WHERE event_id=?', (item['id'],)).fetchone()
    assert mapping is not None and mapping['google_id'] == item['id'] and mapping['synced_revision'] == 0
    provider.instances[item['id']] = [provider.stamp({**provider.events[item['id']], 'id': 'instance1',
                                                     'recurringEventId': item['id'],
                                                     'originalStartTime': provider.events[item['id']]['start']})]
    result = sync(workspace, cal, 'both')
    assert result['pushed'] == 1
    assert [call['method'] for call in provider.writes] == ['POST', 'PATCH', 'PATCH']
    assert provider.instances[item['id']][0]['summary'] == 'Override'
    assert provider.writes[-1]['etag']


@pytest.mark.parametrize('choice', ['local', 'remote'])
def test_calendar_metadata_conflict_is_resolved_before_event_sync(workspace, provider, choice):
    cal = linked(workspace)
    sync(workspace, cal)
    cal = calendar.save_calendar(workspace.conn, {'id': cal['id'], 'revision': cal['revision'], 'name': 'Local name'})
    provider.calendar.update(provider.stamp({**provider.calendar, 'summary': 'Remote name'}))
    result = sync(workspace, cal, 'both')
    assert result['conflicts'] == 1 and provider.writes == []
    conflict = google.status(workspace.settings, workspace.conn)['calendar_conflicts'][0]
    google.resolve_calendar(workspace.settings, workspace.conn,
                            {'calendar_id': cal['id'], 'revision': conflict['local']['revision'], 'choice': choice, 'confirmed': True})
    assert google.status(workspace.settings, workspace.conn)['calendar_conflicts'] == []
    assert provider.writes == []
    if choice == 'local':
        sync(workspace, cal, 'both')
        assert provider.calendar['summary'] == 'Local name'
        assert provider.writes[-1]['etag']
    else:
        assert calendar._get_calendar(workspace.conn, cal['id'])['name'] == 'Remote name'


@pytest.mark.parametrize('resource', ['tasklists', 'tasks'])
def test_optional_google_tasks_reviewed_crud_and_etags(workspace, provider, resource):
    body = {'resource': resource, 'action': 'create', 'calendar_id': 'list1', 'data': {'title': 'Synthetic task'},
            'confirmed': True, 'external_approved': True}
    with pytest.raises(APIError, match='optional Google Tasks'):
        google.operation(workspace.settings, body)
    assert provider.calls == []
    _, token = google.paths(workspace.settings)
    google._private_write(token, json.dumps({'scopes': [google.CALENDAR_SCOPE, google.TASKS_SCOPE]}))
    for approval in ('confirmed', 'external_approved'):
        with pytest.raises(APIError, match='explicitly approve'):
            google.operation(workspace.settings, {**body, approval: False})
    created = approved(workspace.settings, body)
    read = google.operation(workspace.settings, {'resource': resource, 'action': 'get', 'calendar_id': 'list1', 'id': created['id']})
    assert read['title'] == 'Synthetic task'
    listed = google.operation(workspace.settings, {'resource': resource, 'action': 'list', 'calendar_id': 'list1'})
    assert listed['items'][0]['id'] == created['id']
    update = {**body, 'action': 'update', 'id': created['id'], 'etag': read['etag'], 'data': {'title': 'Revised'}}
    changed = approved(workspace.settings, update)
    assert changed['title'] == 'Revised' and provider.writes[-1]['etag'] == read['etag']
    with pytest.raises(APIError) as error:
        approved(workspace.settings, update)
    assert error.value.status == 412
    assert approved(workspace.settings, {**body, 'action': 'delete', 'id': created['id'],
                                         'etag': changed['etag'], 'data': {}})['ok']
    assert google.operation(workspace.settings, {'resource': resource, 'action': 'list', 'calendar_id': 'list1'})['items'] == []


def test_event_resource_rsvp_attendee_confirmation_and_provider_error_privacy(workspace, provider):
    remote = provider.add_event(attendees=[{'email': 'self@example.invalid', 'responseStatus': 'needsAction'}])
    body = {'resource': 'events', 'action': 'rsvp', 'calendar_id': 'remote', 'id': remote['id'], 'etag': remote['etag'],
            'data': {'attendees': [{'email': 'self@example.invalid', 'responseStatus': 'accepted'}]},
            'params': {'sendUpdates': 'none'}, 'confirmed': True, 'external_approved': True}
    with pytest.raises(APIError, match='explicitly approve'):
        google.operation(workspace.settings, {**body, 'external_approved': False})
    with pytest.raises(APIError, match='ETag'):
        google.operation(workspace.settings, {**body, 'etag': None})
    assert provider.calls == []
    review = google.preview_operation(workspace.settings, body)
    assert review['recipients'] == ['self@example.invalid'] and review['if_match'] == remote['etag']
    result = google.operation(workspace.settings, {**body, 'review': review})
    assert result['attendees'][0]['responseStatus'] == 'accepted'
    assert provider.writes[-1]['query']['sendUpdates'] == ['none']
    provider.fail[('PATCH', '/calendar/v3/calendars/remote/events/event1')] = 403
    with pytest.raises(APIError) as error:
        approved(workspace.settings, {**body, 'etag': result['etag']})
    assert error.value.status == 403 and 'synthetic-private' not in str(error.value)


def test_readonly_resources_and_unsupported_actions_are_truthful(workspace, provider):
    assert google.operation(workspace.settings, {'resource': 'colors'})['calendar']['1']['background'] == '#123abc'
    assert google.operation(workspace.settings, {'resource': 'settings'})['items'][0]['id'] == 'timezone'
    assert google.operation(workspace.settings, {'resource': 'freebusy', 'action': 'freebusy',
                                                 'data': {'items': [{'id': 'remote'}], 'timeMin': '2026-10-01T00:00:00Z',
                                                          'timeMax': '2026-10-02T00:00:00Z'}})['calendars']['remote']['busy'] == []
    for resource, action in [('settings', 'update'), ('calendars', 'list'), ('tasklists', 'clear'), ('events', 'stop')]:
        with pytest.raises(APIError, match='Unsupported action'):
            google.operation(workspace.settings, {'resource': resource, 'action': action})


def test_public_event_resource_editor_creates_reads_updates_and_deletes_reviewed_payload(workspace, provider):
    class ImmediateTasks:
        def start(self, kind, work):
            return {'kind': kind, 'status': 'completed', 'result': work()}

    app = SimpleNamespace(settings=workspace.settings, tasks=ImmediateTasks(), modules=workspace.config.modules)
    body = {'resource': 'events', 'calendar_id': 'remote', 'action': 'create', 'meet': True,
            'params': {'sendUpdates': 'externalOnly'},
            'data': {'summary': 'Reviewed appointment', 'start': {'date': '2026-11-01'}, 'end': {'date': '2026-11-02'},
                     'attendees': [{'email': 'guest@example.invalid'}],
                     'attachments': [{'fileUrl': 'https://example.invalid/attachment', 'title': 'Synthetic link'}]}}
    review = web_calendar.post(app, workspace.conn, '/api/calendar/google/operation/preview', body)['task']['result']
    assert review['recipients'] == ['guest@example.invalid'] and provider.writes == []
    request_id = review['body']['conferenceData']['createRequest']['requestId']
    created = web_calendar.post(app, workspace.conn, '/api/calendar/google/operation',
                                {**body, 'review': review, 'confirmed': True, 'external_approved': True})['task']['result']
    assert created['summary'] == 'Reviewed appointment'
    assert created['conferenceData']['createRequest'] == {'requestId': request_id, 'conferenceSolutionKey': {'type': 'hangoutsMeet'}}
    assert provider.writes[-1]['query']['sendUpdates'] == ['externalOnly']
    assert provider.writes[-1]['query']['conferenceDataVersion'] == ['1']
    read = google.operation(workspace.settings, {'resource': 'events', 'calendar_id': 'remote', 'action': 'get', 'id': created['id']})
    changed = approved(workspace.settings, {**body, 'action': 'update', 'id': created['id'],
                                            'etag': read['etag'], 'meet': False, 'data': {'summary': 'Revised appointment'}})
    assert changed['summary'] == 'Revised appointment'
    assert provider.writes[-1]['etag'] == read['etag']
    assert approved(workspace.settings, {**body, 'action': 'delete', 'id': created['id'],
                                         'etag': changed['etag'], 'meet': False, 'data': {}})['ok']
    assert provider.events[created['id']]['status'] == 'cancelled'


def test_synchronized_event_cannot_silently_move_to_another_calendar(workspace, provider):
    provider.add_event()
    cal = linked(workspace)
    sync(workspace, cal)
    item = calendar.occurrences(workspace.conn, '2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z')[0]
    with pytest.raises(APIError, match='Moving synchronized events') as error:
        edit(workspace, item, calendar_id='personal')
    assert error.value.status == 409 and provider.writes == []
    assert calendar.get_event(workspace.conn, item['id'])['calendar_id'] == cal['id']


def test_remote_recurrence_master_and_exceptions_pull_in_master_order(workspace, provider):
    provider.add_event(recurrence=['RRULE:FREQ=WEEKLY;COUNT=3'])
    provider.add_event('changed-instance', recurringEventId='event1', summary='Moved Google occurrence',
                       originalStartTime={'dateTime': '2026-11-02T09:00:00-06:00'},
                       start={'dateTime': '2026-11-03T11:00:00-06:00', 'timeZone': 'America/Chicago'},
                       end={'dateTime': '2026-11-03T12:00:00-06:00', 'timeZone': 'America/Chicago'})
    provider.add_event('cancelled-instance', recurringEventId='event1', status='cancelled',
                       originalStartTime={'dateTime': '2026-11-09T09:00:00-06:00'})
    cal = linked(workspace)
    assert sync(workspace, cal)['pulled'] == 3
    values = calendar.occurrences(workspace.conn, '2026-10-01T00:00:00Z', '2026-12-01T00:00:00Z')
    assert len(values) == 2
    assert values[1]['title'] == 'Moved Google occurrence' and values[1]['start'].startswith('2026-11-03')
    assert sync(workspace, cal)['pulled'] == 0
    assert google.conflicts(workspace.conn) == [] and provider.writes == []


@pytest.mark.parametrize('race', [False, True])
def test_local_recurrence_overrides_use_real_instance_ids_and_etags(workspace, provider, race):
    remote = provider.add_event(recurrence=['RRULE:FREQ=WEEKLY;COUNT=3'])
    provider.instances['event1'] = [
        provider.stamp({**remote, 'id': 'real-instance2', 'recurrence': [], 'recurringEventId': 'event1',
                        'originalStartTime': {'dateTime': '2026-11-02T09:00:00-06:00'},
                        'start': {'dateTime': '2026-11-02T09:00:00-06:00', 'timeZone': 'America/Chicago'},
                        'end': {'dateTime': '2026-11-02T10:00:00-06:00', 'timeZone': 'America/Chicago'}}),
        provider.stamp({**remote, 'id': 'real-instance3', 'recurrence': [], 'recurringEventId': 'event1',
                        'originalStartTime': {'dateTime': '2026-11-09T09:00:00-06:00'},
                        'start': {'dateTime': '2026-11-09T09:00:00-06:00', 'timeZone': 'America/Chicago'},
                        'end': {'dateTime': '2026-11-09T10:00:00-06:00', 'timeZone': 'America/Chicago'}}),
    ]
    cal = linked(workspace)
    sync(workspace, cal)
    item = calendar.occurrences(workspace.conn, '2026-10-01T00:00:00Z', '2026-12-01T00:00:00Z')[0]
    item = calendar.edit_event(workspace.conn, item['id'], {'revision': item['revision'], 'scope': 'this',
                                                          'occurrence': '2026-11-02T09:00:00-06:00', 'changes': {'title': 'Local override'}})
    calendar.edit_event(workspace.conn, item['id'], {'revision': item['revision'], 'scope': 'this',
                                                     'occurrence': '2026-11-09T09:00:00-06:00', 'delete': True})
    if race:
        provider.race_id = 'real-instance2'
    result = sync(workspace, cal, 'both')
    if race:
        assert result['pushed'] == 0 and result['conflicts'] == 1
        assert google.conflicts(workspace.conn)[0]['google_id'] == 'real-instance2'
        assert provider.instances['event1'][0]['summary'] == 'Changed during push'
    else:
        assert result['pushed'] == 1 and result['conflicts'] == 0
        assert provider.instances['event1'][0]['summary'] == 'Local override'
        assert provider.instances['event1'][1]['status'] == 'cancelled'
        assert provider.writes[-1]['method'] == 'DELETE'
    assert all(v['etag'] for v in provider.writes)


def tasks_authorized(workspace, provider):
    _, token = google.paths(workspace.settings)
    google._private_write(token, json.dumps({'scopes': [google.CALENDAR_SCOPE, google.TASKS_SCOPE]}))
    provider.tasklists['list1'] = provider.stamp({'id': 'list1', 'title': 'Synthetic list'})


def test_operation_preview_requires_authorization_with_setup_guidance(workspace):
    body = {'resource': 'events', 'action': 'quickAdd', 'calendar_id': 'remote', 'data': {'text': 'Lunch tomorrow noon'},
            'params': {'sendUpdates': 'none'}}
    with pytest.raises(APIError, match='upload the client then authorize') as error:
        google.preview_operation(workspace.settings, body)
    assert error.value.status == 409
    with pytest.raises(APIError, match='Read actions run directly'):
        google.preview_operation(workspace.settings, {'resource': 'events', 'action': 'list', 'calendar_id': 'remote'})


def test_mutations_require_the_frozen_server_preview_and_reject_drift(workspace, provider):
    body = {'resource': 'events', 'action': 'create', 'calendar_id': 'remote', 'params': {'sendUpdates': 'none'},
            'data': {'summary': 'Reviewed', 'start': {'date': '2026-11-01'}, 'end': {'date': '2026-11-02'},
                     'attendees': [{'email': 'guest@example.invalid'}]},
            'confirmed': True, 'external_approved': True}
    with pytest.raises(APIError, match='Prepare the exact operation preview') as error:
        google.operation(workspace.settings, body)
    assert error.value.status == 409 and provider.calls == []
    review = google.preview_operation(workspace.settings, body)
    assert provider.writes == []
    tampered = [{**body, 'data': {**body['data'], 'summary': 'Changed after review'}},
                {**body, 'data': {**body['data'], 'attendees': [{'email': 'guest@example.invalid'}, {'email': 'extra@example.invalid'}]}},
                {**body, 'params': {'sendUpdates': 'all'}}]
    for changed in tampered:
        with pytest.raises(APIError, match='differs from the reviewed preview') as error:
            google.operation(workspace.settings, {**changed, 'review': review})
        assert error.value.status == 409
    assert provider.writes == []
    created = google.operation(workspace.settings, {**body, 'review': review})
    assert created['summary'] == 'Reviewed' and len(provider.writes) == 1


def test_event_move_previews_exact_source_target_and_recipients(workspace, provider):
    remote = provider.add_event(attendees=[{'email': 'guest@example.invalid'}], organizer={'email': 'owner@example.invalid'})
    body = {'resource': 'events', 'action': 'move', 'calendar_id': 'remote', 'id': remote['id'], 'etag': remote['etag'],
            'params': {'destination': 'archive@example.invalid', 'sendUpdates': 'none'}}
    with pytest.raises(APIError, match='Destination calendar ID'):
        google.preview_operation(workspace.settings, {**body, 'params': {'sendUpdates': 'none'}})
    with pytest.raises(APIError, match='explicitly approve'):
        google.operation(workspace.settings, {**body, 'confirmed': True})
    review = google.preview_operation(workspace.settings, body)
    assert review['method'] == 'POST' and review['url'].endswith('/calendars/remote/events/event1/move')
    assert review['body'] is None and review['if_match'] == remote['etag']
    assert review['current']['id'] == 'event1' and review['recipients'] == ['guest@example.invalid', 'owner@example.invalid']
    provider.events['event1'] = provider.stamp(provider.events['event1'])
    with pytest.raises(APIError) as error:
        google.operation(workspace.settings, {**body, 'review': review, 'confirmed': True, 'external_approved': True})
    assert error.value.status == 412 and provider.writes == []
    moved = approved(workspace.settings, {**body, 'etag': provider.events['event1']['etag']})
    write = provider.writes[-1]
    assert write['path'].endswith('/event1/move') and write['data'] is None
    assert write['query'] == {'destination': ['archive@example.invalid'], 'sendUpdates': ['none']}
    assert moved['id'] == 'event1' and 'event1' in provider.moved_events['archive@example.invalid']


def test_event_import_and_quick_add_send_exact_reviewed_payload(workspace, provider):
    data = {'iCalUID': 'synthetic-uid@kitty.local', 'summary': 'Imported copy', 'start': {'date': '2026-11-01'},
            'end': {'date': '2026-11-02'}, 'attendees': [{'email': 'guest@example.invalid'}]}
    body = {'resource': 'events', 'action': 'import', 'calendar_id': 'remote', 'data': data}
    with pytest.raises(APIError, match='Import iCalUID'):
        google.preview_operation(workspace.settings, {**body, 'data': {**data, 'iCalUID': ''}})
    with pytest.raises(APIError, match='only default event type'):
        google.preview_operation(workspace.settings, {**body, 'data': {**data, 'eventType': 'birthday'}})
    with pytest.raises(APIError, match='Unsupported Google query parameter'):
        google.preview_operation(workspace.settings, {**body, 'params': {'sendUpdates': 'all'}})
    review = google.preview_operation(workspace.settings, body)
    assert review['body'] == {**data} and review['recipients'] == ['guest@example.invalid']
    imported = approved(workspace.settings, body)
    assert imported['iCalUID'] == data['iCalUID']
    assert provider.writes[-1]['path'] == '/calendar/v3/calendars/remote/events/import'
    assert provider.writes[-1]['data'] == review['body'] and 'sendUpdates' not in provider.writes[-1]['query']

    quick = {'resource': 'events', 'action': 'quickAdd', 'calendar_id': 'remote',
             'data': {'text': 'Study block tomorrow 9am'}, 'params': {'sendUpdates': 'none'}}
    with pytest.raises(APIError, match='embedded email'):
        google.preview_operation(workspace.settings, {**quick, 'data': {'text': 'Meet guest@example.invalid at noon'}})
    with pytest.raises(APIError, match='sendUpdates=none'):
        google.preview_operation(workspace.settings, {**quick, 'params': {'sendUpdates': 'all'}})
    review = google.preview_operation(workspace.settings, quick)
    assert review['body'] is None and review['recipients'] == []
    assert review['params'] == {'sendUpdates': 'none', 'text': 'Study block tomorrow 9am'}
    created = approved(workspace.settings, quick)
    assert created['summary'] == 'Study block tomorrow 9am'
    assert provider.writes[-1]['path'].endswith('/events/quickAdd') and provider.writes[-1]['data'] is None
    assert provider.writes[-1]['query'] == {'sendUpdates': ['none'], 'text': ['Study block tomorrow 9am']}


@pytest.mark.parametrize('address', ['http://hooks.example.com/kitty', 'https://localhost/kitty', 'https://127.0.0.1/kitty',
                                     'https://192.168.1.4/kitty', 'https://receiver.local/kitty', 'https://user:pw@hooks.example.com/'])
def test_watch_rejects_non_public_https_receivers(workspace, provider, address):
    body = {'resource': 'events', 'action': 'watch', 'calendar_id': 'remote', 'webhook_confirmed': True,
            'data': {'id': 'channel1', 'type': 'web_hook', 'address': address}}
    with pytest.raises(APIError, match='public HTTPS receiver'):
        google.preview_operation(workspace.settings, body)
    assert provider.calls == []


def test_watch_requires_receiver_confirmation_and_channel_stop_uses_returned_ids(workspace, provider):
    channel = {'id': 'channel1', 'type': 'web_hook', 'address': 'https://hooks.example.com/kitty', 'params': {'ttl': '3600'}}
    body = {'resource': 'events', 'action': 'watch', 'calendar_id': 'remote', 'data': channel}
    with pytest.raises(APIError, match='public HTTPS webhook receiver is ready'):
        google.preview_operation(workspace.settings, body)
    body['webhook_confirmed'] = True
    review = google.preview_operation(workspace.settings, body)
    assert review['body'] == channel and 'https://hooks.example.com/kitty' in review['notes'][0]
    assert provider.writes == []
    result = approved(workspace.settings, body)
    assert provider.writes[-1]['path'] == '/calendar/v3/calendars/remote/events/watch' and provider.writes[-1]['data'] == channel
    stop = {'resource': 'channels', 'action': 'stop', 'data': {'id': result['id'], 'resourceId': result['resourceId']}}
    assert approved(workspace.settings, stop)['ok'] and provider.channels == {}


def test_google_tasks_move_and_clear_list_completed_items_before_confirmation(workspace, provider):
    tasks_authorized(workspace, provider)
    provider.tasks['open'] = provider.stamp({'id': 'open', 'title': 'Open task', 'status': 'needsAction'})
    provider.tasks['done'] = provider.stamp({'id': 'done', 'title': 'Done task', 'status': 'completed'})
    with pytest.raises(APIError, match='cannot be represented'):
        google.preview_operation(workspace.settings, {'resource': 'tasks', 'action': 'create', 'calendar_id': 'list1',
                                                      'data': {'title': 'Repeat', 'repeat_frequency': 'DAILY'}})
    move = {'resource': 'tasks', 'action': 'move', 'calendar_id': 'list1', 'id': 'open', 'etag': provider.tasks['open']['etag'],
            'params': {'destinationTasklist': 'list2', 'previous': 'done'}}
    review = google.preview_operation(workspace.settings, move)
    assert review['url'].endswith('/lists/list1/tasks/open/move') and review['body'] is None and review['current']['title'] == 'Open task'
    approved(workspace.settings, move)
    assert provider.writes[-1]['query'] == {'destinationTasklist': ['list2'], 'previous': ['done']}

    clear = {'resource': 'tasks', 'action': 'clear', 'calendar_id': 'list1', 'etag': provider.tasklists['list1']['etag']}
    review = google.preview_operation(workspace.settings, clear)
    assert [v['id'] for v in review['items']] == ['done'] and review['url'].endswith('/lists/list1/clear')
    with pytest.raises(APIError, match='Confirm clearing all 1 listed items'):
        google.operation(workspace.settings, {**clear, 'review': review, 'confirmed': True, 'external_approved': True})
    assert provider.writes[-1]['path'].endswith('/move')
    approved(workspace.settings, clear, clear_confirmed=True)
    assert provider.writes[-1]['path'] == '/tasks/v1/lists/list1/clear' and provider.tasks['done']['hidden'] is True
    assert google.preview_operation(workspace.settings, clear)['items'] == []
    assert approved(workspace.settings, clear)['ok']


def test_calendar_clear_lists_every_event_and_requires_explicit_confirmation(workspace, provider):
    provider.add_event(attendees=[{'email': 'guest@example.invalid'}])
    provider.add_event('event2', summary='Second')
    body = {'resource': 'calendars', 'action': 'clear', 'calendar_id': 'remote', 'etag': provider.calendar['etag']}
    with pytest.raises(APIError, match='ETag'):
        google.preview_operation(workspace.settings, {**body, 'etag': ''})
    review = google.preview_operation(workspace.settings, body)
    assert [v['id'] for v in review['items']] == ['event1', 'event2'] and review['recipients'] == ['guest@example.invalid']
    assert review['body'] is None and 'all 2 listed events' in review['notes'][0]
    request = {**body, 'review': review, 'confirmed': True, 'external_approved': True}
    with pytest.raises(APIError, match='Confirm clearing all 2 listed items'):
        google.operation(workspace.settings, request)
    provider.add_event('event3', summary='Added after review')
    with pytest.raises(APIError, match='differs from the reviewed preview') as error:
        google.operation(workspace.settings, {**request, 'clear_confirmed': True})
    assert error.value.status == 409 and provider.writes == []
    approved(workspace.settings, body, clear_confirmed=True)
    assert provider.writes[-1] == {'method': 'POST', 'path': '/calendar/v3/calendars/remote/clear', 'query': {},
                                   'data': None, 'etag': provider.calendar['etag']}
    assert provider.events == {}
    assert google.preview_operation(workspace.settings, body)['items'] == []
    assert approved(workspace.settings, body)['ok']


def test_palette_slots_and_subject_links_stay_local_through_google_sync(workspace, provider):
    conn = workspace.conn
    from kitty.core import study
    physics = study.save_entity(conn, 'subjects', {'name': 'Physics', 'color': '#31a2bc'})['item']['id']
    provider.add_event()
    cal = linked(workspace)
    sync(workspace, cal)
    cal = calendar._get_calendar(conn, cal['id'])
    assert cal['color'] == '#123abc'
    cal = calendar.save_calendar(conn, {'id': cal['id'], 'revision': cal['revision'], 'color': 'c4'})
    item = calendar.occurrences(conn, '2026-10-01T00:00:00Z', '2026-11-01T00:00:00Z')[0]
    edit(workspace, item, subject_id=physics, color='c2')
    sync(workspace, cal, 'both')
    assert provider.subscription['backgroundColor'] == '#123abc', 'Google only stores hex; a palette slot is local'
    assert not any(call['path'].endswith('/calendarList/remote') for call in provider.writes)
    provider.add_event(summary='Renamed in Google')
    sync(workspace, cal)
    pulled = calendar.get_event(conn, item['id'])
    assert (pulled['title'], pulled['subject_id'], pulled['color']) == ('Renamed in Google', physics, 'c2')
    assert calendar._get_calendar(conn, cal['id'])['color'] == 'c4'
    provider.subscription.update(provider.stamp({**provider.subscription, 'backgroundColor': '#abcdef'}))
    sync(workspace, cal)
    assert calendar._get_calendar(conn, cal['id'])['color'] == '#abcdef', 'a colour changed in Google still arrives'
