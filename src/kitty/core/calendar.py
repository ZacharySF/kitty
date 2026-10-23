"""Local calendars, RFC5545 recurrence, revisioned editing and reviewed imports.

Dates are exclusive-end RFC5545 dates; times carry offsets and a separate IANA
zone. Source-linked items never write back to applications or opportunities.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrulestr
from dateutil.tz import datetime_exists

from kitty.db.connection import transaction

UTC = timezone.utc
EVENT_FIELDS = {'title', 'start', 'end', 'timezone', 'all_day', 'description', 'location', 'url',
                'rrule', 'exdates', 'attendees', 'privacy', 'busy', 'reminders', 'calendar_id',
                'source_key', 'source_url', 'attachments', 'event_type', 'google_options'}
TASK_FIELDS = {'title', 'description', 'due', 'timezone', 'priority', 'status', 'tags', 'project', 'calendar_id',
               'repeat_frequency', 'repeat_interval', 'repeat_until'}
#: Source-key prefixes of calendar copies imported from ApplyAI data. Without that
#: module they are hidden (and never retired or synced); the rows are kept.
APPLYAI_SOURCES = frozenset({'job', 'application', 'status', 'reminder'})
#: Prefixes `import_jobs` refreshes, i.e. retires when the source no longer lists them.
REFRESHED_SOURCES = APPLYAI_SOURCES | {'opportunity'}


def fail(message, status=400):
    from kitty.web_server import APIError
    raise APIError(status, message)


def now():
    return datetime.now(UTC).isoformat()


def text(value, name, limit=20000):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        fail(f'{name} must be text of at most {limit} characters')
    return value.strip()


def zone(value):
    value = text(value, 'Timezone', 100)
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        fail('Timezone must be an IANA zone such as America/Chicago')


def instant(value, tz='UTC', all_day=False):
    value = text(value, 'Date', 100)
    try:
        if all_day:
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                fail('All-day values must be YYYY-MM-DD dates')
            return datetime.combine(date.fromisoformat(value), datetime.min.time(), zone(tz))
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            fail('Timed dates require an explicit UTC offset')
        return parsed.astimezone(zone(tz))
    except ValueError:
        fail('Invalid ISO8601 date or timestamp')


def wall_instant(value, tz, fold=0):
    """Resolve direct local input. Reject DST gaps; caller chooses ambiguous fold."""
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is not None:
            return instant(value, tz).isoformat()
        dt = dt.replace(tzinfo=zone(tz), fold=int(fold))
        if not datetime_exists(dt):
            fail('This local time does not exist during the DST transition')
        if fold not in (0, 1):
            fail('DST fold must be 0 or 1')
        return dt.isoformat()
    except (ValueError, TypeError):
        fail('Invalid local date/time')


def stamp(dt, all_day):
    return dt.date().isoformat() if all_day else dt.isoformat()


def _rule(data):
    """Date-only UNTIL is inclusive in the event zone, not naive UTC."""
    rule_text = data['rrule']
    match = re.search(r'(?:^|;)UNTIL=(\d{8})(?:;|$)', rule_text)
    if match:
        if not data['all_day']:
            fail('Timed UNTIL requires a UTC timestamp')
        day = datetime.strptime(match[1], '%Y%m%d').date()
        cutoff = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone(data['timezone'])) - timedelta(seconds=1)
        rule_text = rule_text.replace('UNTIL=' + match[1], 'UNTIL=' + cutoff.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ'))
    return rrulestr(rule_text, dtstart=instant(data['start'], data['timezone'], data['all_day']))


def _url(value):
    from urllib.parse import urlsplit
    value = text(value, 'URL', 4096)
    if value and (urlsplit(value).scheme not in {'https', 'http'} or not urlsplit(value).netloc):
        fail('URLs must use http or https')
    return value


def _get_calendar(conn, ident):
    row = conn.execute('SELECT * FROM calendar_calendars WHERE id=? AND deleted=0', (ident,)).fetchone()
    if row is None:
        fail('Calendar not found', 404)
    return dict(row)


def calendars(conn):
    return [dict(row) for row in conn.execute('SELECT * FROM calendar_calendars WHERE deleted=0 ORDER BY name')]


def save_calendar(conn, body):
    ident = body.get('id') or uuid4().hex
    old = _get_calendar(conn, ident) if body.get('id') else None
    name = text(body.get('name', old['name'] if old else ''), 'Calendar name', 200)
    if not name:
        fail('Calendar name is required')
    color = body.get('color', old['color'] if old else '#6d5dfc')
    if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
        fail('Color must be a six-digit hex color')
    tz = body.get('timezone', old['timezone'] if old else 'UTC')
    zone(tz)
    visible = body.get('visible', bool(old['visible']) if old else True)
    if not isinstance(visible, bool):
        fail('Visibility must be boolean')
    with transaction(conn):
        if old:
            if body.get('revision') != old['revision']:
                fail('Calendar changed; reload before editing', 409)
            conn.execute('UPDATE calendar_calendars SET name=?,color=?,timezone=?,visible=?,revision=revision+1,updated_at=? WHERE id=?',
                         (name, color, tz, int(visible), now(), ident))
        else:
            conn.execute('INSERT INTO calendar_calendars(id,name,color,timezone,visible,updated_at) VALUES(?,?,?,?,?,?)',
                         (ident, name, color, tz, int(visible), now()))
    return _get_calendar(conn, ident)


def delete_calendar(conn, body):
    cal = _get_calendar(conn, body.get('id'))
    if cal['id'] == 'personal':
        fail('Personal is the default calendar and cannot be deleted')
    if body.get('revision') != cal['revision']:
        fail('Calendar changed; reload before deleting', 409)
    if conn.execute('SELECT 1 FROM calendar_events WHERE calendar_id=? AND deleted=0 LIMIT 1', (cal['id'],)).fetchone():
        fail('Move or delete this calendar’s events before deleting it', 409)
    if conn.execute('SELECT 1 FROM calendar_tasks WHERE deleted=0 AND json_extract(data, \'$.calendar_id\')=? LIMIT 1', (cal['id'],)).fetchone():
        fail('Move or delete this calendar’s tasks before deleting it', 409)
    conn.execute('UPDATE calendar_calendars SET deleted=1,revision=revision+1,updated_at=? WHERE id=?', (now(), cal['id']))
    return {'ok': True}


def normalize_event(conn, body):
    unknown = set(body) - EVENT_FIELDS
    if unknown:
        fail('Unknown event fields: ' + ', '.join(sorted(unknown)))
    out = dict(body)
    out['calendar_id'] = out.get('calendar_id') or 'personal'
    _get_calendar(conn, out['calendar_id'])
    out['timezone'] = out.get('timezone') or _get_calendar(conn, out['calendar_id'])['timezone']
    zone(out['timezone'])
    out['title'] = text(out.get('title', ''), 'Title', 500)
    if not out['title']:
        fail('Title is required')
    out['all_day'] = out.get('all_day', False)
    if not isinstance(out['all_day'], bool):
        fail('All-day must be boolean')
    start, end = (instant(out.get(key, ''), out['timezone'], out['all_day']) for key in ('start', 'end'))
    if end.astimezone(UTC) <= start.astimezone(UTC):
        fail('End must be after start (all-day end is exclusive)')
    if end - start > timedelta(days=3660):
        fail('An event must span less than ten years')
    out['start'], out['end'] = stamp(start, out['all_day']), stamp(end, out['all_day'])
    for key in ('description', 'location'):
        out[key] = text(out.get(key, ''), key)
    for key in ('url', 'source_url'):
        out[key] = _url(out.get(key, ''))
    source_key = out.get('source_key')
    out['source_key'] = text('' if source_key is None else source_key, 'Source key', 500) or None
    out['privacy'] = out.get('privacy', 'default')
    if out['privacy'] not in {'default', 'public', 'private', 'confidential'}:
        fail('Invalid privacy value')
    out['busy'] = out.get('busy', True)
    if not isinstance(out['busy'], bool):
        fail('Busy must be boolean')
    out['event_type'] = out.get('event_type', 'default')
    if out['event_type'] not in {'default', 'focusTime', 'outOfOffice', 'workingLocation'}:
        fail('Unsupported event type; appointment schedules are not exposed by Calendar API')
    out['google_options'] = out.get('google_options', {})
    if not isinstance(out['google_options'], dict) or set(out['google_options']) - {'focusTimeProperties', 'outOfOfficeProperties', 'workingLocationProperties', 'conferenceData'}:
        fail('Invalid Google event properties')
    out['reminders'] = out.get('reminders', [10])
    if not isinstance(out['reminders'], list) or len(out['reminders']) > 5 or any(type(v) is not int or not 0 <= v <= 40320 for v in out['reminders']):
        fail('Reminders must be up to five minute offsets between 0 and 40320')
    out['attendees'] = out.get('attendees', [])
    if not isinstance(out['attendees'], list) or len(out['attendees']) > 200:
        fail('Attendees must be an array of at most 200 people')
    clean = []
    for attendee in out['attendees']:
        if not isinstance(attendee, dict) or set(attendee) - {'email', 'name', 'response', 'optional'}:
            fail('Invalid attendee')
        email = text(attendee.get('email', ''), 'Attendee email', 320)
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            fail('Invalid attendee email')
        response = attendee.get('response', 'needsAction')
        if response not in {'needsAction', 'accepted', 'declined', 'tentative'}:
            fail('Invalid RSVP')
        clean.append({'email': email, 'name': text(attendee.get('name', ''), 'Attendee name', 200),
                      'response': response, 'optional': bool(attendee.get('optional', False))})
    out['attendees'] = clean
    out['attachments'] = out.get('attachments', [])
    if not isinstance(out['attachments'], list) or len(out['attachments']) > 25:
        fail('Attachments must be an array of at most 25 links')
    if any(not isinstance(a, dict) or set(a) - {'title', 'url'} for a in out['attachments']):
        fail('Each attachment must contain a title and URL')
    out['attachments'] = [{'title': text(a.get('title', ''), 'Attachment title', 200), 'url': _url(a.get('url', ''))}
                          for a in out['attachments']]
    out['rrule'] = text(out.get('rrule', ''), 'Recurrence', 2000).removeprefix('RRULE:')
    if out['rrule']:
        if '\n' in out['rrule'] or not re.fullmatch(r'[A-Z0-9=;,+:\-]+', out['rrule']):
            fail('Use a single RFC5545 RRULE')
        if 'COUNT=' in out['rrule'] and 'UNTIL=' in out['rrule']:
            fail('RRULE cannot combine COUNT and UNTIL')
        try:
            rule = _rule(out)
            # Disallow pathological sub-day recurrences: calendar supports daily and coarser.
            if rule._freq < 0 or rule._freq > 3 or rule._interval < 1:
                fail('Supported recurrence frequencies: DAILY, WEEKLY, MONTHLY, YEARLY')
            if rule._count is not None and not 1 <= rule._count <= 100000:
                fail('Recurrence count must be between 1 and 100000')
        except (ValueError, TypeError, OverflowError):
            fail('Invalid RRULE; timed UNTIL must be UTC, e.g. 20271001T000000Z')
    out['exdates'] = out.get('exdates', [])
    if not isinstance(out['exdates'], list) or len(out['exdates']) > 10000:
        fail('Exclusions must be an array of dates')
    out['exdates'] = sorted({stamp(instant(v, out['timezone'], out['all_day']), out['all_day']) for v in out['exdates']})
    return out


def _event(conn, ident, include_deleted=False):
    row = conn.execute('SELECT * FROM calendar_events WHERE id=?', (ident,)).fetchone()
    if row is None or (row['deleted'] and not include_deleted):
        fail('Event not found', 404)
    return {**json.loads(row['data']), 'id': row['id'], 'revision': row['revision'], 'deleted': bool(row['deleted']),
            'updated_at': row['updated_at'], 'source_overridden': bool(row['source_overridden'])}


def applyai_sourced(source_key):
    """Whether a calendar copy was imported from ApplyAI data (jobs, applications, reminders)."""
    return (source_key or '').split(':', 1)[0] in APPLYAI_SOURCES


def ensure_visible(conn, ident, *, applyai=True):
    """404 for an ApplyAI-sourced copy while that module is off; the row itself is kept."""
    row = conn.execute('SELECT source_key FROM calendar_events WHERE id=?', (ident,)).fetchone()
    if row is not None and not applyai and applyai_sourced(row['source_key']):
        fail('Event not found', 404)


def get_event(conn, ident, *, applyai=True):
    ensure_visible(conn, ident, applyai=applyai)
    return _event(conn, ident)


def _snapshot(conn, ident):
    row = conn.execute('SELECT * FROM calendar_events WHERE id=?', (ident,)).fetchone()
    if row is None:
        return None
    return {'row': dict(row), 'exceptions': [dict(v) for v in conn.execute('SELECT * FROM calendar_exceptions WHERE event_id=?', (ident,))]}


def _history(conn, entity, ident, before, after):
    cur = conn.execute('INSERT INTO calendar_history(entity,entity_id,before_json,after_json,created_at) VALUES(?,?,?,?,?)',
                       (entity, ident, json.dumps(before), json.dumps(after), now()))
    return cur.lastrowid


def _insert(conn, data, ident=None):
    ident = ident or uuid4().hex
    conn.execute('INSERT INTO calendar_events(id,calendar_id,title,start,end,timezone,all_day,data,source_key,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (ident, data['calendar_id'], data['title'], data['start'], data['end'], data['timezone'], int(data['all_day']),
                  json.dumps(data), data['source_key'], now()))
    return ident


def _update(conn, ident, data, deleted=False, manual=True):
    conn.execute('UPDATE calendar_events SET calendar_id=?,title=?,start=?,end=?,timezone=?,all_day=?,data=?,deleted=?,revision=revision+1,source_overridden=MAX(source_overridden,?),updated_at=? WHERE id=?',
                 (data['calendar_id'], data['title'], data['start'], data['end'], data['timezone'], int(data['all_day']),
                  json.dumps(data), int(deleted), int(manual and bool(data['source_key'])), now(), ident))


def create_event(conn, body):
    data = normalize_event(conn, body)
    with transaction(conn):
        if data['source_key']:
            row = conn.execute('SELECT id FROM calendar_events WHERE source_key=?', (data['source_key'],)).fetchone()
            if row:
                return _event(conn, row['id'], include_deleted=True)
        ident = _insert(conn, data)
        undo = _history(conn, 'event', ident, None, _snapshot(conn, ident))
    return {**_event(conn, ident), 'undo_id': undo}


def _starts(data, lower, upper):
    base = instant(data['start'], data['timezone'], data['all_day'])
    if not data['rrule']:
        return [base] if lower <= base <= upper else []
    rule = _rule(data)
    count = rule._count
    # RFC5545 says nonexistent local times are omitted and do not consume COUNT.
    sequence = rule.replace(count=None) if count is not None else rule
    values, valid = [], 0
    for occurrence in sequence if count is not None else sequence.xafter(lower, inc=True):
        # dateutil reconstructs wall components and drops DTSTART's fold.
        if base.fold and occurrence.replace(tzinfo=None) == base.replace(tzinfo=None):
            occurrence = base
        if occurrence > upper:
            break
        if not datetime_exists(occurrence):
            continue
        valid += 1
        if count is not None and valid > count:
            break
        if occurrence >= lower:
            values.append(occurrence)
        if len(values) > 10000:
            fail('Range contains too many occurrences; select a shorter range')
    return values


def _duration(data):
    start = instant(data['start'], data['timezone'], data['all_day'])
    end = instant(data['end'], data['timezone'], data['all_day'])
    return end - start if data['all_day'] else end.astimezone(UTC) - start.astimezone(UTC)


def _occurrence_end(data, start, duration):
    # Timed DTEND establishes exact elapsed duration; date-only ends are nominal.
    end = start + duration if data['all_day'] else (start.astimezone(UTC) + duration).astimezone(zone(data['timezone']))
    return stamp(end, data['all_day'])


def occurrences(conn, start, end, *, search='', calendar_id=None, include_hidden=False, applyai=True):
    lower = instant(start)
    upper = instant(end)
    if upper <= lower or upper - lower > timedelta(days=740):
        fail('Choose a positive calendar range of at most two years')
    rows = conn.execute('SELECT e.* FROM calendar_events e JOIN calendar_calendars c ON c.id=e.calendar_id WHERE e.deleted=0 AND c.deleted=0' +
                        ('' if include_hidden else ' AND c.visible=1') + (' AND e.calendar_id=?' if calendar_id else ''),
                        (calendar_id,) if calendar_id else ())
    output = []
    for row in rows:
        if not applyai and applyai_sourced(row['source_key']):
            continue
        data = json.loads(row['data'])
        if search.casefold() not in ' '.join([data['title'], data['description'], data['location']]).casefold():
            continue
        duration = _duration(data)
        exceptions = {v['occurrence']: json.loads(v['data']) if v['data'] else None
                      for v in conn.execute('SELECT * FROM calendar_exceptions WHERE event_id=?', (row['id'],))}
        # Edited occurrences can move into the requested window from outside it.
        candidates = {stamp(dt, data['all_day']): dt for dt in _starts(data, lower - duration - timedelta(days=2), upper)}
        for key in exceptions:
            candidates.setdefault(key, instant(key, data['timezone'], data['all_day']))
        for key, dt in candidates.items():
            if key in data['exdates']:
                continue
            if key in exceptions:
                item = exceptions[key]
                if item is None:
                    continue
                item = dict(item)
            else:
                item = {**data, 'start': stamp(dt, data['all_day']), 'end': _occurrence_end(data, dt, duration)}
            a = instant(item['start'], item['timezone'], item['all_day'])
            b = instant(item['end'], item['timezone'], item['all_day'])
            if a < upper and b > lower:
                output.append({**item, 'id': row['id'], 'revision': row['revision'], 'occurrence': key,
                               'series_start': data['start'], 'series_end': data['end'], 'is_exception': key in exceptions,
                               'rrule': data['rrule'], 'exdates': data['exdates'],
                               'source_overridden': bool(row['source_overridden'])})
            if len(output) > 20000:
                fail('Too many events; choose a shorter range')
    return sorted(output, key=lambda e: instant(e['start'], e['timezone'], e['all_day']).astimezone(UTC))


def _remap_exceptions(conn, ident, old, updated, exceptions):
    """Preserve overrides when shifting series wall time, including DST."""
    a = instant(old['start'], old['timezone'], old['all_day']).replace(tzinfo=None)
    b = instant(updated['start'], updated['timezone'], updated['all_day']).replace(tzinfo=None)
    delta = b - a
    def shifted(value):
        original = instant(value, old['timezone'], old['all_day'])
        wall = original.replace(tzinfo=None) + delta
        dt = wall.replace(tzinfo=zone(updated['timezone']), fold=original.fold)
        if not datetime_exists(dt):
            fail('This series shift would move an exception into a DST gap; edit that occurrence first', 409)
        return stamp(dt, updated['all_day'])
    if updated['exdates'] == old['exdates']:
        updated['exdates'] = [shifted(v) for v in old['exdates']]
    replacements = []
    for ex in exceptions:
        key = shifted(ex['occurrence'])
        dt = instant(key, updated['timezone'], updated['all_day'])
        if not _starts(updated, dt, dt):
            fail('New recurrence would invalidate an edited occurrence; keep its recurrence boundary or edit that occurrence first', 409)
        replacement = json.loads(ex['data']) if ex['data'] else None
        if replacement:
            replacement = normalize_event(conn, {**replacement, 'calendar_id': updated['calendar_id'],
                                                  'start': shifted(replacement['start']), 'end': shifted(replacement['end']),
                                                  'timezone': updated['timezone'], 'all_day': updated['all_day']})
        replacements.append((ident, key, json.dumps(replacement) if replacement else None))
    conn.execute('DELETE FROM calendar_exceptions WHERE event_id=?', (ident,))
    conn.executemany('INSERT INTO calendar_exceptions VALUES(?,?,?)', replacements)


def _assert_occurrence(data, value):
    dt = instant(value, data['timezone'], data['all_day'])
    key = stamp(dt, data['all_day'])
    if key in data['exdates'] or not any(v.astimezone(UTC) == dt.astimezone(UTC) for v in _starts(data, dt, dt)):
        fail('Selected occurrence is not in this series')
    return dt, key


def _truncate(rule_text, cutoff, all_day):
    parts = [p for p in rule_text.split(';') if not p.startswith(('COUNT=', 'UNTIL='))]
    # DTSTART is aware even for dates; UTC UNTIL preserves midnight in that zone.
    parts.append('UNTIL=' + ((cutoff.date() - timedelta(days=1)).strftime('%Y%m%d') if all_day else
                            (cutoff.astimezone(UTC) - timedelta(seconds=1)).strftime('%Y%m%dT%H%M%SZ')))
    return ';'.join(parts)


def edit_event(conn, ident, body):
    if set(body) - {'revision', 'scope', 'occurrence', 'changes', 'delete'}:
        fail('Unknown edit field')
    current = _event(conn, ident)
    if body.get('revision') != current['revision']:
        fail('Event changed; reload and review before saving', 409)
    changes = body.get('changes', {})
    if not isinstance(changes, dict) or set(changes) - EVENT_FIELDS:
        fail('Invalid event changes')
    if 'source_key' in changes and changes['source_key'] != current.get('source_key'):
        fail('Stable source linkage cannot be edited')
    scope = body.get('scope', 'all')
    if scope not in {'all', 'this', 'future'}:
        fail('Scope must be this, future, or all')
    if current['rrule'] and scope == 'this' and any(key in changes and changes[key] != current[key] for key in ('calendar_id', 'rrule', 'exdates')):
        fail('An individual occurrence cannot change its calendar or series recurrence; choose all/future scope')
    if changes.get('calendar_id', current['calendar_id']) != current['calendar_id'] and conn.execute('SELECT 1 FROM calendar_google_events WHERE event_id=?', (ident,)).fetchone():
        fail('Moving synchronized events between calendars is not supported; unlink the Google calendar first or create a separate local copy', 409)
    deleted = body.get('delete', False)
    if not isinstance(deleted, bool):
        fail('Delete must be boolean')
    data = {k: v for k, v in current.items() if k in EVENT_FIELDS}
    with transaction(conn):
        before = _snapshot(conn, ident)
        # Recheck inside the transaction, not only against a caller's stale object.
        if before['row']['revision'] != body['revision']:
            fail('Event changed; reload before saving', 409)
        new_ident = None
        if scope == 'all' or not data['rrule']:
            updated = normalize_event(conn, {**data, **changes})
            if not deleted:
                _remap_exceptions(conn, ident, data, updated, before['exceptions'])
            _update(conn, ident, updated, deleted)
        else:
            dt, key = _assert_occurrence(data, body.get('occurrence', ''))
            duration = _duration(data)
            if scope == 'this':
                existing = conn.execute('SELECT data FROM calendar_exceptions WHERE event_id=? AND occurrence=?', (ident, key)).fetchone()
                original = json.loads(existing['data']) if existing and existing['data'] else {**data, 'start': key, 'end': _occurrence_end(data, dt, duration)}
                updated = normalize_event(conn, {**original, **changes, 'rrule': '', 'exdates': []})
                conn.execute('INSERT INTO calendar_exceptions(event_id,occurrence,data) VALUES(?,?,?) ON CONFLICT(event_id,occurrence) DO UPDATE SET data=excluded.data',
                             (ident, key, None if deleted else json.dumps(updated)))
                _update(conn, ident, data)
            else:
                if dt == instant(data['start'], data['timezone'], data['all_day']):
                    updated = normalize_event(conn, {**data, **changes})
                    if not deleted:
                        _remap_exceptions(conn, ident, data, updated, before['exceptions'])
                    _update(conn, ident, updated, deleted)
                else:
                    original_rule = data['rrule']
                    new_rule = original_rule
                    count_match = re.search(r'(?:^|;)COUNT=(\d+)', original_rule)
                    if count_match:
                        prior = len(_starts(data, instant(data['start'], data['timezone'], data['all_day']), dt.astimezone(UTC) - timedelta(microseconds=1)))
                        new_rule = re.sub(r'COUNT=\d+', 'COUNT=' + str(int(count_match[1]) - prior), original_rule)
                    later_exdates = [v for v in data['exdates'] if instant(v, data['timezone'], data['all_day']) >= dt]
                    _update(conn, ident, normalize_event(conn, {**data, 'rrule': _truncate(original_rule, dt, data['all_day']),
                                                              'exdates': [v for v in data['exdates'] if v not in later_exdates]}))
                    if not deleted:
                        updated = normalize_event(conn, {**data, 'start': key, 'end': _occurrence_end(data, dt, duration),
                                                        'rrule': new_rule, 'exdates': later_exdates, 'source_key': None, **changes})
                        new_ident = _insert(conn, updated)
                    later_exceptions = [ex for ex in before['exceptions'] if instant(ex['occurrence'], data['timezone'], data['all_day']) >= dt]
                    if new_ident:
                        _remap_exceptions(conn, new_ident, {**data, 'start': key, 'end': _occurrence_end(data, dt, duration), 'exdates': later_exdates}, updated, later_exceptions)
                        conn.execute('UPDATE calendar_events SET data=? WHERE id=?', (json.dumps(updated), new_ident))
                    for ex in later_exceptions:
                        conn.execute('DELETE FROM calendar_exceptions WHERE event_id=? AND occurrence=?', (ident, ex['occurrence']))
        after = _snapshot(conn, ident)
        if new_ident:
            after['created'] = _snapshot(conn, new_ident)
        undo = _history(conn, 'event', ident, before, after)
    return {**_event(conn, ident, include_deleted=True), 'undo_id': undo, 'future_event': _event(conn, new_ident) if new_ident else None}


def undo(conn, history_id):
    with transaction(conn, immediate=True):
        row = conn.execute('SELECT * FROM calendar_history WHERE id=? AND undone=0', (history_id,)).fetchone()
        if not row:
            fail('Undo is no longer available', 404)
        before, after = json.loads(row['before_json']), json.loads(row['after_json'])
        if row['entity'] == 'task':
            current = conn.execute('SELECT * FROM calendar_tasks WHERE id=?', (row['entity_id'],)).fetchone()
            if not current or current['revision'] != after['revision']:
                fail('Task changed since that action; cannot undo safely', 409)
            if after.get('created'):
                created = conn.execute('SELECT * FROM calendar_tasks WHERE id=?', (after['created']['id'],)).fetchone()
                if not created or created['deleted'] or created['revision'] != after['created']['revision']:
                    fail('Next task changed since completion; cannot undo safely', 409)
                conn.execute('UPDATE calendar_tasks SET deleted=1,revision=revision+1,updated_at=? WHERE id=?',
                             (now(), created['id']))
            conn.execute('UPDATE calendar_tasks SET data=?,deleted=?,repeat_anchor=?,task_series_id=?,previous_task_id=?,revision=revision+1,updated_at=? WHERE id=?',
                         (before['data'] if before else current['data'], before['deleted'] if before else 1,
                          before.get('repeat_anchor', '') if before else current['repeat_anchor'],
                          before.get('task_series_id') if before else current['task_series_id'],
                          before.get('previous_task_id') if before else current['previous_task_id'], now(), row['entity_id']))
        else:
            current = _snapshot(conn, row['entity_id'])
            if current['row']['revision'] != after['row']['revision']:
                fail('Event changed since that action; cannot undo safely', 409)
            if after.get('created'):
                created = _snapshot(conn, after['created']['row']['id'])
                if created['row']['revision'] != after['created']['row']['revision']:
                    fail('Future series changed; cannot undo safely', 409)
                conn.execute('UPDATE calendar_events SET deleted=1,revision=revision+1,updated_at=? WHERE id=?', (now(), created['row']['id']))
            if before:
                old = before['row']
                conn.execute('UPDATE calendar_events SET calendar_id=?,title=?,start=?,end=?,timezone=?,all_day=?,data=?,deleted=?,source_overridden=?,revision=revision+1,updated_at=? WHERE id=?',
                             tuple(old[k] for k in ('calendar_id', 'title', 'start', 'end', 'timezone', 'all_day', 'data', 'deleted', 'source_overridden')) + (now(), row['entity_id']))
                conn.execute('DELETE FROM calendar_exceptions WHERE event_id=?', (row['entity_id'],))
                conn.executemany('INSERT INTO calendar_exceptions(event_id,occurrence,data) VALUES(?,?,?)',
                                 [(v['event_id'], v['occurrence'], v['data']) for v in before['exceptions']])
            else:
                conn.execute('UPDATE calendar_events SET deleted=1,revision=revision+1,updated_at=? WHERE id=?', (now(), row['entity_id']))
        conn.execute('UPDATE calendar_history SET undone=1 WHERE id=?', (history_id,))
    return {'ok': True}


def _task_output(row):
    return {'repeat_frequency': '', 'repeat_interval': 1, 'repeat_until': '',
            **json.loads(row['data']), 'id': row['id'], 'revision': row['revision'], 'updated_at': row['updated_at']}


def tasks(conn, search=''):
    output = []
    for row in conn.execute('SELECT * FROM calendar_tasks WHERE deleted=0 ORDER BY updated_at DESC'):
        item = _task_output(row)
        if search.casefold() in ' '.join([item['title'], item['description'], item['project'], *item['tags']]).casefold():
            output.append(item)
    return output


def _normalize_task(conn, body, previous, completed_at):
    data = {**previous, **{k: v for k, v in body.items() if k in TASK_FIELDS}}
    data['title'] = text(data.get('title', ''), 'Title', 500)
    if not data['title']:
        fail('Task title is required')
    data['description'] = text(data.get('description', ''), 'Description')
    data['timezone'] = data.get('timezone', 'UTC')
    zone(data['timezone'])
    data['calendar_id'] = data.get('calendar_id', 'personal')
    _get_calendar(conn, data['calendar_id'])
    data['due'] = text(data.get('due', ''), 'Due', 100)
    if data['due']:
        all_day = len(data['due']) == 10
        data['due'] = stamp(instant(data['due'], data['timezone'], all_day), all_day)
    data['repeat_frequency'] = text(data.get('repeat_frequency', ''), 'Repeat frequency', 20)
    if data['repeat_frequency'] not in {'', 'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY'}:
        fail('Repeat frequency must be DAILY, WEEKLY, MONTHLY, YEARLY, or empty')
    data['repeat_interval'] = data.get('repeat_interval', 1)
    if type(data['repeat_interval']) is not int or data['repeat_interval'] < 1:
        fail('Repeat interval must be an integer of at least 1')
    data['repeat_until'] = text(data.get('repeat_until', ''), 'Repeat stop date', 10)
    if data['repeat_until']:
        instant(data['repeat_until'], data['timezone'], True)
    if data['repeat_frequency'] and not data['due']:
        fail('Repeating tasks require a due date or time')
    if data['repeat_frequency'] and data['repeat_until'] and data['repeat_until'] < data['due'][:10]:
        fail('Repeat stop date must be on or after the task due date')
    data['priority'] = data.get('priority', 'normal')
    data['status'] = data.get('status', 'todo')
    if data['priority'] not in {'low', 'normal', 'high', 'urgent'} or data['status'] not in {'todo', 'doing', 'done', 'cancelled'}:
        fail('Invalid priority or status')
    data['completed_at'] = (previous.get('completed_at') or completed_at) if data['status'] == 'done' else None
    data['project'] = text(data.get('project', ''), 'Project', 200)
    tags = data.get('tags', [])
    if not isinstance(tags, list) or len(tags) > 50:
        fail('Tags must be an array of at most 50 values')
    data['tags'] = list(dict.fromkeys(text(v, 'Tag', 100) for v in tags if v))
    return data


def _next_task_due(data, anchor_value, completed_at):
    """Skip missed dates; clamp month ends from the anchor, not the last instance."""
    all_day = len(data['due']) == 10
    anchor = instant(anchor_value, data['timezone'], all_day)
    due = instant(data['due'], data['timezone'], all_day)
    completed = instant(completed_at, data['timezone'])
    boundary = max(due.astimezone(UTC), completed.astimezone(UTC)).astimezone(anchor.tzinfo)
    frequency, interval = data['repeat_frequency'], data['repeat_interval']
    if frequency in {'DAILY', 'WEEKLY'}:
        step = interval * (7 if frequency == 'WEEKLY' else 1)
        index = max(1, (boundary.date() - anchor.date()).days // step)
        unit = 'days'
    elif frequency == 'MONTHLY':
        step, unit = interval, 'months'
        index = max(1, ((boundary.year - anchor.year) * 12 + boundary.month - anchor.month) // step)
    else:
        step, unit = interval, 'years'
        index = max(1, (boundary.year - anchor.year) // step)
    while True:
        try:
            candidate = (anchor + relativedelta(**{unit: index * step})).replace(fold=anchor.fold)
        except (OverflowError, ValueError):
            return None  # No representable future instance within datetime's range.
        if data['repeat_until'] and candidate.date().isoformat() > data['repeat_until']:
            return None
        future = candidate.date() > boundary.date() if all_day else candidate.astimezone(UTC) > boundary.astimezone(UTC)
        if future and datetime_exists(candidate):
            return stamp(candidate, all_day)
        index += 1


def save_task(conn, body):
    if set(body) - TASK_FIELDS - {'id', 'revision', 'delete'}:
        fail('Unknown task field')
    if not isinstance(body.get('delete', False), bool):
        fail('Delete must be boolean')
    ident = body.get('id') or uuid4().hex
    next_task = None
    with transaction(conn, immediate=True):
        row = conn.execute('SELECT * FROM calendar_tasks WHERE id=?', (ident,)).fetchone()
        if body.get('id') and (not row or row['deleted']):
            fail('Task not found', 404)
        if row and body.get('revision') != row['revision']:
            fail('Task changed; reload before saving', 409)
        previous = json.loads(row['data']) if row else {}
        changed_at = now()
        data = _normalize_task(conn, body, previous, changed_at)
        completed = data['status'] == 'done' and previous.get('status') != 'done'
        successor = conn.execute('SELECT id FROM calendar_tasks WHERE previous_task_id=? AND deleted=0', (ident,)).fetchone()
        if previous.get('status') == 'done':
            if any(key in body and data[key] != previous.get(key, '' if key != 'repeat_interval' else 1)
                   for key in ('repeat_frequency', 'repeat_interval', 'repeat_until')):
                fail('Edit repetition on the next pending task, not completed history', 409)
            if data['status'] != 'done' and successor:
                fail('This completion has a next task; use undo to reopen it safely', 409)
        anchor = row['repeat_anchor'] if row else ''
        series_id = row['task_series_id'] if row else None
        if data['repeat_frequency']:
            if not anchor or (previous.get('status') != 'done' and
                              any(data[key] != previous.get(key) for key in ('due', 'timezone', 'repeat_frequency', 'repeat_interval'))):
                anchor = data['due']
            series_id = series_id or ident
        elif previous.get('status') != 'done':
            anchor = ''
        if row:
            conn.execute('UPDATE calendar_tasks SET data=?,repeat_anchor=?,task_series_id=?,revision=revision+1,deleted=?,updated_at=? WHERE id=? AND revision=?',
                         (json.dumps(data), anchor, series_id, int(body.get('delete', False)), changed_at, ident, row['revision']))
        else:
            conn.execute('INSERT INTO calendar_tasks(id,data,repeat_anchor,task_series_id,updated_at) VALUES(?,?,?,?,?)',
                         (ident, json.dumps(data), anchor, series_id, changed_at))
        after = dict(conn.execute('SELECT * FROM calendar_tasks WHERE id=?', (ident,)).fetchone())
        if completed and data['repeat_frequency'] and not body.get('delete'):
            next_due = _next_task_due(data, anchor, changed_at)
            if next_due and not successor:
                next_id = uuid4().hex
                next_data = {**data, 'due': next_due, 'status': 'todo', 'completed_at': None}
                conn.execute('INSERT INTO calendar_tasks(id,data,repeat_anchor,task_series_id,previous_task_id,updated_at) VALUES(?,?,?,?,?,?)',
                             (next_id, json.dumps(next_data), anchor, series_id, ident, changed_at))
                created = dict(conn.execute('SELECT * FROM calendar_tasks WHERE id=?', (next_id,)).fetchone())
                after['created'] = created
                next_task = _task_output(created)
        undo_id = _history(conn, 'task', ident, dict(row) if row else None, after)
    return {**_task_output(after), 'undo_id': undo_id, 'next_task': next_task}


def availability(events, start, end, *, exclude_id=None):
    lower, upper = instant(start), instant(end)
    busy = sorted([(max(lower, instant(e['start'], e['timezone'], e['all_day'])), min(upper, instant(e['end'], e['timezone'], e['all_day'])), e['id'])
                   for e in events if e['busy'] and e['id'] != exclude_id], key=lambda v: v[0].astimezone(UTC))
    merged, conflicts = [], []
    for a, b, ident in busy:
        if b <= a:
            continue
        if merged and a.astimezone(UTC) < merged[-1][1].astimezone(UTC):
            conflicts.append({'event_id': ident, 'with': merged[-1][2], 'start': a.isoformat(), 'end': min(b, merged[-1][1]).isoformat()})
        if merged and a.astimezone(UTC) <= merged[-1][1].astimezone(UTC):
            merged[-1][1] = max(b, merged[-1][1])
            merged[-1][2].append(ident)
        else:
            merged.append([a, b, [ident]])
    free = []
    cursor = lower
    for a, b, _ in merged:
        if cursor < a:
            free.append({'start': cursor.isoformat(), 'end': a.isoformat()})
        cursor = max(cursor, b)
    if cursor < upper:
        free.append({'start': cursor.isoformat(), 'end': upper.isoformat()})
    return {'busy': [{'start': a.isoformat(), 'end': b.isoformat(), 'events': ids} for a, b, ids in merged], 'free': free, 'conflicts': conflicts}


def due_reminders(conn, *, at=None, applyai=True):
    at = at or datetime.now(UTC)
    events = occurrences(conn, (at - timedelta(days=1)).isoformat(), (at + timedelta(days=29)).isoformat(), applyai=applyai)
    dismissed = {(r['event_id'], r['occurrence'], r['minutes']) for r in conn.execute('SELECT * FROM calendar_reminder_dismissals')}
    output = []
    for e in events:
        start = instant(e['start'], e['timezone'], e['all_day'])
        for minutes in e['reminders']:
            due = (start.astimezone(UTC) - timedelta(minutes=minutes)).astimezone(start.tzinfo)
            if at - timedelta(days=1) <= due <= at and (e['id'], e['occurrence'], minutes) not in dismissed:
                output.append({'event_id': e['id'], 'occurrence': e['occurrence'], 'minutes': minutes, 'title': e['title'], 'due': due.isoformat()})
    return output


def dismiss_reminder(conn, body):
    _event(conn, body.get('event_id'))
    occurrence = text(body.get('occurrence', ''), 'Occurrence', 100)
    minutes = body.get('minutes')
    if type(minutes) is not int or not 0 <= minutes <= 40320:
        fail('Invalid reminder minutes')
    conn.execute('INSERT OR IGNORE INTO calendar_reminder_dismissals VALUES(?,?,?)', (body['event_id'], occurrence, minutes))
    return {'ok': True}


def activity(conn, start, end, tz='UTC', *, applyai=True):
    lower, upper, z = instant(start), instant(end), zone(tz)
    counts = {}
    for table, field, kind in [('calendar_history', 'created_at', 'calendar_edits'), ('applications', 'applied_at', 'applications'),
                               ('status_events', 'occurred_at', 'status_updates')]:
        if not applyai and table != 'calendar_history':
            continue
        for row in conn.execute(f'SELECT {field} AS at FROM {table}' + (" WHERE state='confirmed'" if table == 'status_events' else '')):
            try:
                dt = instant(row['at'])
            except Exception:
                continue
            if lower <= dt < upper:
                day = dt.astimezone(z).date().isoformat()
                counts.setdefault(day, {})[kind] = counts.setdefault(day, {}).get(kind, 0) + 1
    for row in conn.execute('SELECT data,updated_at FROM calendar_tasks WHERE deleted=0'):
        task = json.loads(row['data'])
        if task['status'] == 'done' and task.get('completed_at'):
            dt = instant(task['completed_at'])
            if lower <= dt < upper:
                day = dt.astimezone(z).date().isoformat()
                counts.setdefault(day, {})['tasks_completed'] = counts.setdefault(day, {}).get('tasks_completed', 0) + 1
    return [{'date': day, **values} for day, values in sorted(counts.items())]


def _source_event(conn, body):
    """Refresh authoritative source dates unless user edited/deleted the linked copy."""
    data = normalize_event(conn, body)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    row = conn.execute('SELECT * FROM calendar_events WHERE source_key=?', (data['source_key'],)).fetchone()
    with transaction(conn):
        if row:
            if row['source_overridden'] or (not row['deleted'] and row['source_fingerprint'] == digest):
                return False
            _update(conn, row['id'], data, manual=False)
            conn.execute('UPDATE calendar_events SET source_fingerprint=? WHERE id=?', (digest, row['id']))
        else:
            ident = _insert(conn, data)
            conn.execute('UPDATE calendar_events SET source_fingerprint=? WHERE id=?', (digest, ident))
    return True


def import_jobs(conn, *, applyai=True):
    """Refresh source-dated calendar copies; job, application and reminder dates only with ApplyAI."""
    changed = 0
    errors = []
    entries = []
    if applyai:
        for row in conn.execute('SELECT j.*,c.display_name AS company FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.deadline_at IS NOT NULL'):
            entries.append((f'job:{row["id"]}:deadline', f'Deadline · {row["company"]} · {row["title"]}', row['deadline_at'], row['url'], 'Job deadline (source date)', 'UTC'))
        for row in conn.execute('SELECT a.*,j.title,j.url,c.display_name AS company FROM applications a JOIN jobs j ON j.id=a.job_id JOIN companies c ON c.id=j.company_id'):
            for field, label in [('applied_at', 'Applied'), ('next_action_at', 'Next action')]:
                if row[field]:
                    entries.append((f'application:{row["id"]}:{field}', f'{label} · {row["company"]} · {row["title"]}', row[field], row['url'], row['next_action_note'] or '' if field == 'next_action_at' else 'Application submission', 'UTC'))
        for row in conn.execute("SELECT s.*,j.title,j.url,c.display_name AS company FROM status_events s JOIN applications a ON a.id=s.application_id JOIN jobs j ON j.id=a.job_id JOIN companies c ON c.id=j.company_id WHERE s.state='confirmed' AND s.to_status='interview' AND s.undoes_event_id IS NULL"):
            entries.append((f'status:{row["id"]}:interview', f'Interview status · {row["company"]} · {row["title"]}', row['occurred_at'], row['url'], 'Confirmed interview status activity; not an inferred interview appointment.', 'UTC'))
        for row in conn.execute("SELECT r.*,j.title,j.url,c.display_name AS company FROM reminders r LEFT JOIN jobs j ON j.id=r.job_id LEFT JOIN companies c ON c.id=j.company_id WHERE r.done_at IS NULL"):
            entries.append((f'reminder:{row["id"]}', f'{row["kind"].title()} · {row["company"] or "Reminder"} · {row["title"] or ""}', row['due_at'], row['url'] or '', row['note'] or 'Existing reminder; not a source deadline.', 'UTC'))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='opportunities'").fetchone():
        for row in conn.execute('SELECT id,title,url,deadline,timezone,description FROM opportunities WHERE deadline IS NOT NULL'):
            entries.append((f'opportunity:{row["id"]}:deadline', f'Deadline · {row["title"]}', row['deadline'], row['url'], row['description'] or '', row['timezone'] or 'UTC'))
    for key, title, value, url, description, tz in entries:
        try:
            all_day = len(value) == 10
            dt = instant(value, tz, all_day)
            changed += int(_source_event(conn, {'title': title, 'start': stamp(dt, all_day), 'end': stamp(dt + (timedelta(days=1) if all_day else timedelta(minutes=30)), all_day),
                                               'timezone': tz, 'all_day': all_day, 'source_key': key, 'source_url': url,
                                               'description': description, 'busy': False, 'reminders': []}))
        except Exception as exc:
            from kitty.web_server import APIError
            if not isinstance(exc, APIError):
                raise
            errors.append({'source_key': key, 'error': str(exc)})
    active = {entry[0] for entry in entries}
    with transaction(conn):
        for row in conn.execute("SELECT * FROM calendar_events WHERE deleted=0 AND source_overridden=0 AND source_key IS NOT NULL").fetchall():
            # Without ApplyAI its copies are left exactly as they are, not retired.
            if row['source_fingerprint'] and row['source_key'].split(':', 1)[0] in (REFRESHED_SOURCES if applyai else {'opportunity'}) and row['source_key'] not in active:
                _update(conn, row['id'], json.loads(row['data']), deleted=True, manual=False)
                changed += 1
    return {'changed': changed, 'review_errors': errors, 'source_count': len(entries)}


def preview_csv(conn, content, tz='UTC', *, applyai=True):
    content = text(content, 'CSV', 8 * 1024 * 1024)
    zone(tz)
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames or not {'title', 'date'}.issubset(reader.fieldnames):
        fail('CSV needs title,date columns; optional job_id,kind,timezone,url,description')
    output = []
    for index, row in enumerate(reader, 2):
        if len(output) >= 5000:
            fail('Import is limited to 5000 rows')
        try:
            z = row.get('timezone') or tz
            dt = instant(row['date'], z, len(row['date']) == 10)
            all_day = len(row['date']) == 10
            if row.get('job_id'):
                if not applyai:
                    fail('job_id needs the ApplyAI module, which is disabled')
                if not row['job_id'].isdigit() or not conn.execute('SELECT 1 FROM jobs WHERE id=?', (int(row['job_id']),)).fetchone():
                    fail('job_id does not exist')
            key = 'csv:' + hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
            data = normalize_event(conn, {'title': row['title'], 'start': stamp(dt, all_day),
                                          'end': stamp(dt + (timedelta(days=1) if all_day else timedelta(minutes=30)), all_day),
                                          'timezone': z, 'all_day': all_day, 'source_key': key, 'source_url': row.get('url', ''),
                                          'description': (row.get('description') or '') + (f'\nApplication job #{row["job_id"]} · {row.get("kind", "application date")}' if row.get('job_id') else ''), 'busy': False})
            output.append({'row': index, 'event': data, 'duplicate': bool(conn.execute('SELECT 1 FROM calendar_events WHERE source_key=?', (key,)).fetchone())})
        except Exception as exc:
            from kitty.web_server import APIError
            if not isinstance(exc, APIError):
                raise
            output.append({'row': index, 'error': str(exc)})
    return {'items': output, 'notice': 'Calendar copies only: imported dates never update application records.'}


def preview_ics(conn, content, tz='UTC', calendar_id='personal'):
    from icalendar import Calendar
    content = text(content, 'ICS', 8 * 1024 * 1024)
    z = zone(tz)
    try:
        parsed = Calendar.from_ical(content)
    except Exception:
        fail('Invalid ICS file')
    output = []
    masters = {}
    components = [c for c in parsed.walk() if c.name == 'VEVENT']
    if len(components) > 5000:
        fail('Import is limited to 5000 events')
    for index, component in enumerate(components):
        try:
            if component.get('RECURRENCE-ID'):
                continue
            a = component.decoded('DTSTART')
            b = component.decoded('DTEND') if component.get('DTEND') else a + (component.decoded('DURATION') if component.get('DURATION') else timedelta(days=1) if not isinstance(a, datetime) else timedelta(hours=1))
            all_day = not isinstance(a, datetime)
            if not all_day:
                a = a.replace(tzinfo=z) if a.tzinfo is None else a
                b = b.replace(tzinfo=z) if b.tzinfo is None else b
            timezone_name = getattr(a.tzinfo, 'key', None) if not all_day else tz
            timezone_name = timezone_name or str(component['DTSTART'].params.get('TZID') or tz)
            zone(timezone_name)
            uid = str(component.get('UID') or hashlib.sha256(component.to_ical()).hexdigest())
            exdates = []
            ex = component.get('EXDATE', [])
            for prop in ex if isinstance(ex, list) else [ex]:
                for entry in prop.dts:
                    v = entry.dt
                    if isinstance(v, datetime) and v.tzinfo is None:
                        v = v.replace(tzinfo=zone(timezone_name))
                    exdates.append(v.isoformat())
            attendees = component.get('ATTENDEE', [])
            attendees = attendees if isinstance(attendees, list) else [attendees]
            data = normalize_event(conn, {'title': str(component.get('SUMMARY') or 'Untitled event'), 'calendar_id': calendar_id,
                                          'start': a.isoformat(), 'end': b.isoformat(), 'all_day': all_day, 'timezone': timezone_name,
                                          'rrule': component['RRULE'].to_ical().decode() if component.get('RRULE') else '', 'exdates': exdates,
                                          'description': str(component.get('DESCRIPTION') or ''), 'location': str(component.get('LOCATION') or ''),
                                          'url': str(component.get('URL') or ''), 'source_key': 'ics:' + uid,
                                          'privacy': str(component.get('CLASS') or 'default').lower(), 'busy': str(component.get('TRANSP') or '') != 'TRANSPARENT',
                                          'attendees': [{'email': str(v).removeprefix('mailto:').removeprefix('MAILTO:'), 'name': str(v.params.get('CN', '')),
                                                         'response': {'ACCEPTED': 'accepted', 'DECLINED': 'declined', 'TENTATIVE': 'tentative'}.get(str(v.params.get('PARTSTAT', '')), 'needsAction')} for v in attendees]})
            alarms = [c for c in component.subcomponents if c.name == 'VALARM']
            if alarms:
                minutes = []
                for alarm in alarms:
                    trigger = alarm.decoded('TRIGGER') if alarm.get('TRIGGER') else None
                    if isinstance(trigger, timedelta) and trigger <= timedelta(0):
                        minutes.append(int(-trigger.total_seconds() // 60))
                data['reminders'] = minutes[:5]
            masters[uid] = {'row': index + 1, 'event': data, 'exceptions': [], 'duplicate': bool(conn.execute('SELECT 1 FROM calendar_events WHERE source_key=?', ('ics:' + uid,)).fetchone())}
            output.append(masters[uid])
        except Exception as exc:
            output.append({'row': index + 1, 'error': str(exc) if hasattr(exc, 'status') else 'Invalid ICS event fields'})
    for component in components:
        if not component.get('RECURRENCE-ID'):
            continue
        uid = str(component.get('UID') or '')
        if uid not in masters:
            output.append({'error': 'Recurrence exception has no master event in this import'})
            continue
        try:
            data = masters[uid]['event']
            key_dt = component.decoded('RECURRENCE-ID')
            if isinstance(key_dt, datetime) and key_dt.tzinfo is None:
                key_dt = key_dt.replace(tzinfo=zone(data['timezone']))
            key = stamp(instant(key_dt.isoformat(), data['timezone'], data['all_day']), data['all_day'])
            if str(component.get('STATUS')) == 'CANCELLED':
                replacement = None
            else:
                a, b = component.decoded('DTSTART'), component.decoded('DTEND')
                if isinstance(a, datetime):
                    a = a.replace(tzinfo=zone(data['timezone'])) if a.tzinfo is None else a
                    b = b.replace(tzinfo=zone(data['timezone'])) if b.tzinfo is None else b
                replacement = normalize_event(conn, {**data, 'title': str(component.get('SUMMARY') or data['title']),
                                                     'start': a.isoformat(), 'end': b.isoformat(), 'rrule': '', 'exdates': [],
                                                     'description': str(component.get('DESCRIPTION') or data['description'])})
            masters[uid]['exceptions'].append({'occurrence': key, 'event': replacement})
        except Exception:
            output.append({'error': 'Invalid recurrence exception'})
    return {'items': output, 'notice': 'Review every row. Existing UID-linked events are skipped; private ICS contents remain local.'}


def import_reviewed(conn, items):
    if not isinstance(items, list) or len(items) > 5000:
        fail('Import must contain at most 5000 reviewed items')
    # Validate the entire selected import before making any changes.
    normalized = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('event'), dict):
            fail('Each reviewed item requires an event')
        data = normalize_event(conn, item['event'])
        exceptions = []
        for ex in item.get('exceptions', []):
            _, key = _assert_occurrence(data, ex.get('occurrence', ''))
            exceptions.append((key, normalize_event(conn, ex['event']) if ex.get('event') else None))
        normalized.append((data, exceptions))
    created, duplicates, undo_ids = [], [], []
    with transaction(conn):
        for data, exceptions in normalized:
            existing = conn.execute('SELECT id FROM calendar_events WHERE source_key=?', (data['source_key'],)).fetchone() if data['source_key'] else None
            if existing:
                duplicates.append(existing['id'])
                continue
            ident = _insert(conn, data)
            for key, replacement in exceptions:
                conn.execute('INSERT INTO calendar_exceptions VALUES(?,?,?)', (ident, key, json.dumps(replacement) if replacement else None))
            undo_ids.append(_history(conn, 'event', ident, None, _snapshot(conn, ident)))
            created.append(ident)
    return {'created': created, 'duplicates': duplicates, 'undo_ids': undo_ids}


def export_ics(conn, calendar_id=None, *, applyai=True):
    from icalendar import Calendar, Event, Alarm
    result = Calendar()
    result.add('prodid', '-//kitty!//Local calendar//EN')
    result.add('version', '2.0')
    rows = conn.execute('SELECT * FROM calendar_events WHERE deleted=0' + (' AND calendar_id=?' if calendar_id else ''), (calendar_id,) if calendar_id else ())
    for row in rows:
        if not applyai and applyai_sourced(row['source_key']):
            continue
        data = json.loads(row['data'])
        uid = data['source_key'][4:] if (data['source_key'] or '').startswith('ics:') else row['id'] + '@kitty.local'
        def component(item, recurrence=None, cancelled=False):
            event = Event()
            event.add('uid', uid)
            event.add('dtstamp', instant(row['updated_at']))
            event.add('sequence', row['revision'])
            if recurrence:
                dt = instant(recurrence, data['timezone'], data['all_day'])
                event.add('recurrence-id', dt.date() if data['all_day'] else dt)
            if cancelled:
                event.add('status', 'CANCELLED')
                return event
            for prop, key in [('summary', 'title'), ('description', 'description'), ('location', 'location'), ('url', 'url')]:
                if item.get(key):
                    event.add(prop, item[key])
            for prop, key in [('dtstart', 'start'), ('dtend', 'end')]:
                dt = instant(item[key], item['timezone'], item['all_day'])
                event.add(prop, dt.date() if item['all_day'] else dt)
            event.add('transp', 'OPAQUE' if item['busy'] else 'TRANSPARENT')
            if item['privacy'] != 'default':
                event.add('class', item['privacy'].upper())
            if not recurrence and item['rrule']:
                from icalendar import vRecur
                event.add('rrule', vRecur.from_ical(item['rrule']))
            if not recurrence and item['exdates']:
                event.add('exdate', [instant(v, item['timezone'], item['all_day']).date() if item['all_day'] else instant(v, item['timezone']) for v in item['exdates']])
            for attendee in item['attendees']:
                event.add('attendee', 'mailto:' + attendee['email'], parameters={'CN': attendee['name'], 'PARTSTAT': {'accepted': 'ACCEPTED', 'declined': 'DECLINED', 'tentative': 'TENTATIVE', 'needsAction': 'NEEDS-ACTION'}[attendee['response']]})
            for minutes in item['reminders']:
                alarm = Alarm()
                alarm.add('action', 'DISPLAY')
                alarm.add('description', item['title'])
                alarm.add('trigger', -timedelta(minutes=minutes))
                event.add_component(alarm)
            return event
        result.add_component(component(data))
        for ex in conn.execute('SELECT * FROM calendar_exceptions WHERE event_id=?', (row['id'],)):
            result.add_component(component(json.loads(ex['data']) if ex['data'] else data, ex['occurrence'], ex['data'] is None))
    return {'content': result.to_ical().decode('utf-8'), 'filename': 'kitty-calendar.ics', 'mime': 'text/calendar'}
