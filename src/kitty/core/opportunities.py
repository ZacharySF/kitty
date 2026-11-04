"""Independent opportunity review and project records; never writes job rows."""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kitty.dates import now_iso
from kitty.db.connection import transaction

STATUSES = ('discovered', 'new', 'saved', 'queued', 'applied', 'participating', 'completed', 'skipped')
FACT_FIELDS = ('title', 'url', 'category', 'description', 'location', 'remote', 'start', 'end', 'deadline', 'timezone', 'availability')


def fail(message, status=400):
    from kitty.web_server import APIError
    raise APIError(status, message)


def text(value, name, maximum=20000):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        fail(f'{name} must be text of at most {maximum} characters')
    try:
        value.encode('utf-8')
    except UnicodeError:
        fail(f'{name} contains invalid Unicode')
    return value.strip()


def canonical_url(value):
    value = text(value, 'URL', 4000)
    if not value:
        return ''
    try:
        parts = urlsplit(value)
        parts.port
    except ValueError:
        fail('Use a valid public URL')
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        fail('Use a public http or https URL without credentials')
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit(('https', parts.netloc.lower(), parts.path.rstrip('/') or '/', urlencode(sorted(query)), ''))


def valid_date(value):
    if value is None or value == '':
        return None
    value = text(value, 'Date', 100)
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            return date.fromisoformat(value).isoformat()
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            fail('Timed dates require an explicit UTC offset; date-only uses YYYY-MM-DD')
        return parsed.isoformat()
    except ValueError:
        fail('Use YYYY-MM-DD or an aware ISO8601 timestamp')


def validate_fields(body):
    result = {}
    for key, value in body.items():
        if key in ('title', 'category', 'location', 'notes', 'description', 'timezone'):
            result[key] = text(value, key, 20000 if key in ('notes', 'description') else 500)
            if key in ('title', 'category') and not result[key]:
                fail(f'{key} cannot be empty')
        elif key == 'url':
            result[key] = canonical_url(value)
        elif key in ('deadline', 'start', 'end'):
            result[key] = valid_date(value)
        elif key in ('favorite', 'remote'):
            if type(value) is not bool and not (key == 'remote' and value is None):
                fail(f'{key} must be a boolean' + (' or null' if key == 'remote' else ''))
            result[key] = value
        elif key == 'status':
            if value not in STATUSES:
                fail('Unknown opportunity review status')
            result[key] = value
        elif key == 'availability':
            if value not in ('unknown', 'future', 'active', 'closed'):
                fail('Unknown availability')
            result[key] = value
        elif key == 'tags':
            if not isinstance(value, list) or len(value) > 100:
                fail('Tags must be a list of at most 100 strings')
            result[key] = json.dumps(list(dict.fromkeys(text(v, 'Tag', 100) for v in value)))
        else:
            fail(f'Unknown opportunity field: {key}')
    if 'timezone' in result:
        try:
            ZoneInfo(result['timezone'])
        except (ZoneInfoNotFoundError, ValueError):
            fail('Use a valid IANA timezone')
    return result


def _decode(row):
    result = dict(row)
    for key in ('tags', 'overrides'):
        result[key] = json.loads(result[key])
    for key in ('favorite', 'deadline_override'):
        result[key] = bool(result[key])
    result['remote'] = bool(result['remote']) if result['remote'] is not None else None
    return result


def detail(conn, ident):
    row = conn.execute('SELECT * FROM opportunities WHERE id=?', (ident,)).fetchone()
    if row is None:
        fail('Opportunity not found', 404)
    result = _decode(row)
    result['evidence'] = [{**dict(r), 'facts': json.loads(r['facts'])} for r in conn.execute(
        'SELECT * FROM opportunity_evidence WHERE opportunity_id=? ORDER BY observed_at DESC', (ident,))]
    project = conn.execute('SELECT * FROM opportunity_projects WHERE opportunity_id=?', (ident,)).fetchone()
    result['project'] = None if project is None else {**dict(project), 'items': [dict(r) for r in conn.execute(
        'SELECT * FROM opportunity_project_items WHERE project_id=? ORDER BY kind,due,id', (project['id'],))]}
    return result


def _revision(row, revision):
    if type(revision) is not int or revision != row['revision']:
        fail('Record changed; reload before saving', 409)


def _insert_manual(conn, body):
    fields = validate_fields(body)
    if not fields.get('title'):
        fail('A title is required')
    identity = fields.get('url') or 'manual:' + uuid4().hex
    if fields.get('url') and conn.execute('SELECT 1 FROM opportunities WHERE url=?', (fields['url'],)).fetchone():
        fail('This canonical URL is already in your opportunities', 409)
    fields.update(identity=identity, source='manual', created_at=now_iso(), updated_at=now_iso(),
                  overrides=json.dumps([k for k in fields if k in FACT_FIELDS]),
                  deadline_override=int('deadline' in fields))
    return conn.execute(f"INSERT INTO opportunities ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", tuple(fields.values())).lastrowid


def create(conn, body):
    with transaction(conn):
        ident = _insert_manual(conn, body)
    return detail(conn, ident)


def import_items(conn, items):
    if not isinstance(items, list) or not 1 <= len(items) <= 500 or any(not isinstance(item, dict) for item in items):
        fail('Import requires 1–500 JSON opportunity objects')
    with transaction(conn):
        ids = [_insert_manual(conn, item) for item in items]
    return {'items': [detail(conn, ident) for ident in ids], 'count': len(ids)}


def update(conn, ident, body, revision):
    fields = validate_fields(body)
    with transaction(conn):
        current = detail(conn, ident)
        _revision(current, revision)
        if fields.get('url') and conn.execute('SELECT 1 FROM opportunities WHERE url=? AND id<>?', (fields['url'], ident)).fetchone():
            fail('This canonical URL is already in your opportunities', 409)
        if 'url' in fields and current['source'] == 'manual':
            fields['identity'] = fields['url'] or 'manual:' + uuid4().hex
        overrides = set(current['overrides']) | (set(fields) & set(FACT_FIELDS))
        fields.update(overrides=json.dumps(sorted(overrides)), updated_at=now_iso(), revision=revision + 1,
                      deadline_override=int('deadline' in overrides))
        conn.execute(f"UPDATE opportunities SET {','.join(k+'=?' for k in fields)} WHERE id=?", (*fields.values(), ident))
    return detail(conn, ident)


def remove(conn, ident, revision):
    with transaction(conn):
        _revision(detail(conn, ident), revision)
        conn.execute('DELETE FROM opportunities WHERE id=?', (ident,))
    return {'ok': True}


def favorites(conn, selections, value):
    if type(value) is not bool or not isinstance(selections, list) or not 1 <= len(selections) <= 500:
        fail('Select 1–500 opportunities and a boolean favorite value')
    if any(not isinstance(s, dict) or set(s) != {'id', 'revision'} or type(s['id']) is not int for s in selections):
        fail('Selections require id and revision')
    if len({s['id'] for s in selections}) != len(selections):
        fail('Selections must be unique')
    with transaction(conn):
        for selection in selections:
            _revision(detail(conn, selection['id']), selection['revision'])
        for selection in selections:
            conn.execute("UPDATE opportunities SET favorite=?,status=CASE WHEN ?=1 AND status IN ('new','discovered') THEN 'saved' ELSE status END,revision=revision+1,updated_at=? WHERE id=?",
                         (value, value, now_iso(), selection['id']))
    return {'ok': True, 'count': len(selections)}


def settings(conn):
    row = dict(conn.execute('SELECT * FROM opportunity_settings WHERE id=1').fetchone())
    row['profile'] = json.loads(row['profile'])
    row['enabled'] = bool(row['enabled'])
    return row


def save_settings(conn, body, root):
    """Monitoring settings. ``enabled`` and ``interval_hours`` live in the build file (Q8,
    ``settings.opportunities``); the monitoring profile (major, track, interests,
    locations) is applicant data and never leaves this workspace's database."""
    from kitty.core.builds import project, store

    if set(body) - {'revision', 'profile', 'enabled', 'interval_hours'}:
        fail('Unknown monitoring setting')
    changes = {}
    build_values = {}
    if 'profile' in body:
        profile = body['profile']
        if not isinstance(profile, dict) or set(profile) != {'major', 'track', 'interests', 'locations', 'prefer_remote'}:
            fail('Profile requires major, track, interests, locations, prefer_remote')
        profile = dict(profile)
        for key in ('major', 'track'):
            profile[key] = text(profile[key], key, 200)
        for key in ('interests', 'locations'):
            if not isinstance(profile[key], list) or len(profile[key]) > 50:
                fail(f'{key} must be a list of at most 50 values')
            profile[key] = list(dict.fromkeys(text(v, key, 100) for v in profile[key] if text(v, key, 100)))
        if type(profile['prefer_remote']) is not bool:
            fail('prefer_remote must be boolean')
        changes['profile'] = json.dumps(profile)
    if 'enabled' in body:
        if type(body['enabled']) is not bool:
            fail('enabled must be boolean')
        build_values['monitoring'] = body['enabled']
    if 'interval_hours' in body:
        if type(body['interval_hours']) is not int or not 6 <= body['interval_hours'] <= 168:
            fail('Monitoring interval must be 6–168 hours')
        build_values['interval_hours'] = body['interval_hours']
    with project.editing(conn, root) as build:
        current = settings(conn)
        _revision(current, body.get('revision'))
        if changes:
            changes['revision'] = current['revision'] + 1
            conn.execute(f"UPDATE opportunity_settings SET {','.join(k+'=?' for k in changes)} WHERE id=1", tuple(changes.values()))
        if build_values:
            # Saving the schedule (re)starts it now, as before builds.
            enabled = build_values.get('monitoring', current['enabled'])
            conn.execute('UPDATE opportunity_settings SET next_run=? WHERE id=1', (now_iso() if enabled else None,))
            store.section(build, 'settings', 'opportunities').update(build_values)
    return settings(conn)


def rank(item, profile):
    haystack = ' '.join(str(item.get(k) or '') for k in ('title', 'description', 'category', 'source')).casefold()
    reasons = []
    for label, values in (('Interest', profile['interests']), ('Major', [profile['major']]), ('Track', [profile['track']])):
        for value in values:
            if value and value.casefold() in haystack:
                reasons.append(f'{label} match: {value}')
    for location in profile['locations']:
        if location.casefold() in item['location'].casefold():
            reasons.append(f'Location match: {location}')
    if profile['prefer_remote'] and item['remote']:
        reasons.append('Remote preference match')
    return len(reasons) * 10, reasons


def availability(item, now=None):
    now = now or datetime.now(timezone.utc)
    if item.get('availability') == 'closed':
        return 'closed'
    def instant(value):
        if not value:
            return None
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value) + timedelta(days=1), datetime.min.time(), ZoneInfo(item['timezone']))
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    end, start, deadline = (instant(item.get(k)) for k in ('end', 'start', 'deadline'))
    if (end and end < now) or (deadline and deadline < now and not start):
        return 'closed'
    if start and start > now:
        return 'future'
    if start and end and start <= now <= end:
        return 'active'
    if deadline and deadline >= now:
        return 'future'
    return 'unknown'


def list_items(conn, query):
    allowed = {'search', 'source', 'category', 'status', 'location', 'remote', 'deadline', 'favorites', 'availability'}
    if set(query) - allowed:
        fail('Unknown opportunity filter')
    clauses, args = [], []
    for key in ('source', 'category', 'status'):
        if query.get(key):
            clauses.append(f'{key}=?'); args.append(query[key])
    for key, columns in (('search', "title||' '||description||' '||notes||' '||tags"), ('location', 'location')):
        if query.get(key):
            clauses.append(f'instr(lower({columns}),lower(?))>0'); args.append(query[key])
    for key, column in (('remote', 'remote'), ('favorites', 'favorite')):
        if query.get(key):
            if query[key] not in ('true', 'false', 'unknown') or (key == 'favorites' and query[key] == 'unknown'):
                fail('Invalid boolean filter')
            clauses.append(f'{column} IS NULL' if query[key] == 'unknown' else f'{column}=?')
            if query[key] != 'unknown': args.append(query[key] == 'true')
    if query.get('deadline'):
        if query['deadline'] == 'unknown': clauses.append('deadline IS NULL')
        else:
            until = valid_date(query['deadline'])
            clauses.append('substr(deadline,1,10)<=?'); args.append(until[:10])
    profile = settings(conn)['profile']
    rows = [_decode(row) for row in conn.execute('SELECT * FROM opportunities' + (' WHERE ' + ' AND '.join(clauses) if clauses else ''), args)]
    for item in rows:
        item['availability'] = availability(item)
        item['score'], item['reasons'] = rank(item, profile)
    if query.get('availability'):
        if query['availability'] not in ('unknown', 'future', 'active', 'closed'): fail('Invalid availability filter')
        rows = [r for r in rows if r['availability'] == query['availability']]
    rows.sort(key=lambda r: (-r['score'], r['deadline'] or '9999', -r['id']))
    return rows


def upsert_discovered(conn, source, items, observed_at):
    count = 0
    with transaction(conn):
        for incoming in items:
            incoming = dict(incoming)
            provider_id = incoming.get('provider_id')
            fields = validate_fields({k: v for k, v in incoming.items() if k in FACT_FIELDS})
            # A recurring organizer homepage alone is not an event identity. Year
            # separates annual cycles; provider IDs preserve identity on date corrections.
            cycle = (fields.get('start') or '')[:4]
            identity = fields.get('url', '') + ('::' + cycle if cycle else '')
            provider_key = f'provider:{source}:{provider_id}' if provider_id else identity
            if not identity:
                identity = provider_key
            row = conn.execute('SELECT o.* FROM opportunities o JOIN opportunity_identities i ON i.opportunity_id=o.id WHERE i.identity IN (?,?) ORDER BY CASE WHEN i.identity=? THEN 0 ELSE 1 END LIMIT 1', (provider_key, identity, provider_key)).fetchone()
            if row is None:
                row = conn.execute('SELECT * FROM opportunities WHERE identity=? OR (source=? AND url=?) ORDER BY id LIMIT 1',
                                   (identity, 'manual', fields.get('url', ''))).fetchone()
            if row is None:
                fields.update(identity=identity, source=source, created_at=observed_at, updated_at=observed_at)
                ident = conn.execute(f"INSERT INTO opportunities ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", tuple(fields.values())).lastrowid
            else:
                ident = row['id']
                # Evidence from a cache cannot replace a newer observation.
                previous = conn.execute('SELECT max(observed_at) FROM opportunity_evidence WHERE opportunity_id=?', (ident,)).fetchone()[0]
                if not previous or previous <= observed_at:
                    overrides = json.loads(row['overrides'])
                    updates = {k: v for k, v in fields.items() if k not in overrides}
                    updates.update(updated_at=now_iso(), revision=row['revision'] + 1)
                    conn.execute(f"UPDATE opportunities SET {','.join(k+'=?' for k in updates)} WHERE id=?", (*updates.values(), ident))
            conn.execute('INSERT OR IGNORE INTO opportunity_identities(identity,opportunity_id) VALUES(?,?)', (provider_key, ident))
            conn.execute('INSERT OR IGNORE INTO opportunity_identities(identity,opportunity_id) VALUES(?,?)', (identity, ident))
            conn.execute('INSERT INTO opportunity_evidence(opportunity_id,source,url,observed_at,facts) VALUES(?,?,?,?,?) ON CONFLICT(opportunity_id,source,url) DO UPDATE SET observed_at=excluded.observed_at,facts=excluded.facts WHERE excluded.observed_at>=opportunity_evidence.observed_at',
                         (ident, source, incoming.get('evidence_url') or fields.get('url', ''), observed_at, json.dumps(incoming)))
            count += 1
    return count


def project(conn, ident, body):
    with transaction(conn):
        item = detail(conn, ident)
        _revision(item, body.get('revision'))
        if item['project'] is None:
            conn.execute('INSERT INTO opportunity_projects(opportunity_id,title) VALUES(?,?)', (ident, item['title']))
            conn.execute("UPDATE opportunities SET revision=revision+1,updated_at=? WHERE id=?", (now_iso(), ident))
    return detail(conn, ident)


def project_item(conn, ident, body):
    allowed = {'revision', 'id', 'kind', 'title', 'due', 'url', 'notes', 'done', 'delete'}
    if set(body) - allowed: fail('Unknown project item field')
    with transaction(conn):
        opportunity = detail(conn, ident)
        p = opportunity['project']
        if p is None: fail('Create the project first', 409)
        _revision(p, body.get('revision'))
        existing = None
        if 'id' in body:
            if type(body['id']) is not int: fail('Item id must be an integer')
            existing = conn.execute('SELECT * FROM opportunity_project_items WHERE id=? AND project_id=?', (body['id'], p['id'])).fetchone()
            if existing is None: fail('Project item not found', 404)
        if body.get('delete') is True:
            if existing is None: fail('Select an item to delete')
            conn.execute('DELETE FROM opportunity_project_items WHERE id=?', (existing['id'],))
        else:
            fields = dict(existing) if existing else {'kind': 'task', 'title': '', 'due': None, 'url': '', 'notes': '', 'done': 0}
            fields = {k: fields[k] for k in ('kind', 'title', 'due', 'url', 'notes', 'done')}
            for key in fields:
                if key in body: fields[key] = body[key]
            if fields['kind'] not in ('task', 'milestone', 'resource'): fail('Invalid project item kind')
            fields['title'] = text(fields['title'], 'Title', 500)
            if not fields['title']: fail('A title is required')
            fields['notes'] = text(fields['notes'], 'Notes')
            fields['url'] = canonical_url(fields['url'])
            fields['due'] = valid_date(fields['due'])
            if fields['done'] not in (0, 1, False, True): fail('done must be boolean')
            if existing:
                conn.execute(f"UPDATE opportunity_project_items SET {','.join(k+'=?' for k in fields)},revision=revision+1 WHERE id=?", (*fields.values(), existing['id']))
            else:
                conn.execute(f"INSERT INTO opportunity_project_items(project_id,{','.join(fields)}) VALUES(?,{','.join('?' for _ in fields)})", (p['id'], *fields.values()))
        conn.execute('UPDATE opportunity_projects SET revision=revision+1 WHERE id=?', (p['id'],))
    return detail(conn, ident)


def update_project(conn, ident, body):
    if set(body) - {'revision', 'title', 'notes'}: fail('Unknown project field')
    with transaction(conn):
        p = detail(conn, ident)['project']
        if p is None: fail('Project not found', 404)
        _revision(p, body.get('revision'))
        title = text(body.get('title', p['title']), 'Title', 500)
        if not title: fail('A title is required')
        conn.execute('UPDATE opportunity_projects SET title=?,notes=?,revision=revision+1 WHERE id=?',
                     (title, text(body.get('notes', p['notes']), 'Notes'), p['id']))
    return detail(conn, ident)


def export_deadline(conn, ident):
    from kitty.core.calendar import create_event
    item = detail(conn, ident)
    deadline = item['deadline']
    if not deadline: fail('No verified or manually supplied deadline; set one before exporting')
    all_day = len(deadline) == 10
    end = ((date.fromisoformat(deadline) + timedelta(days=1)).isoformat() if all_day else
           (datetime.fromisoformat(deadline) + timedelta(minutes=30)).isoformat())
    return create_event(conn, {'title': item['title'] + ' — deadline', 'start': deadline, 'end': end,
                              'timezone': item['timezone'], 'all_day': all_day,
                              'description': item['notes'] or item['description'], 'source_url': item['url'],
                              'source_key': f'opportunity:{ident}:deadline'})
