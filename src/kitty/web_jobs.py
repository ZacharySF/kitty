"""Website job decisions, saved views, pipeline and reminder workflows."""
from __future__ import annotations

import dataclasses
import re
import sqlite3
from urllib.parse import urlsplit

from kitty.core import applications, favorites, jobs, pipeline, reminders, sources, today
from kitty.dates import parse_user_date
from kitty.db.connection import transaction

QUEUE_STATES = {'new', 'queued', 'later', 'skipped', 'not_eligible'}
STATUSES = {'applied', 'oa', 'interview', 'offer', 'rejected', 'ghosted', 'withdrawn'}
ELIGIBILITY = {'eligible', 'boosted', 'ambiguous', 'unknown', 'ineligible'}


def _text(value, name, limit=20000):
    from kitty.web_server import APIError
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise APIError(400, f'{name} must be text of at most {limit} characters')
    return value.strip()


def _date(value, *, optional=False):
    from kitty.web_server import APIError
    text = _text(value, 'Date', 100)
    if not text and optional:
        return None
    parsed = parse_user_date(text)
    if parsed is None:
        raise APIError(400, 'Use a date such as YYYY-MM-DD, today, or +7')
    return parsed


def get(app, conn, path, query):
    from kitty.web_server import APIError, _integer, _job_json
    if path == '/api/dashboard':
        from kitty.core import questions, stats
        from kitty.core.triage.run import pending_batches
        app._no_query(query)
        digest = today.build_digest(conn, app.config)
        funnel = pipeline.funnel(conn)
        return {
            'day': digest.day,
            'weekly': [{**dataclasses.asdict(item), 'remaining': item.remaining} for item in digest.weekly],
            'new_by_track': digest.new_by_track,
            'funnel': {**dataclasses.asdict(funnel), 'response_rate': funnel.response_rate, 'advance_rate': funnel.advance_rate},
            'reminders': [dataclasses.asdict(item) for item in digest.reminders],
            'deadlines': [_job_json(item) for item in digest.deadlines],
            'stale': [dataclasses.asdict(item) for item in digest.stale],
            'openings': [{'company': item.company, 'opens_on': item.opens_on.isoformat(), 'basis': item.basis} for item in digest.opens_soon],
            'source_problems': [{'name': item.name, 'status': item.last_status} for item in digest.source_problems],
            'proposals': len(applications.proposed_events(conn)),
            'questions': len(questions.list_questions(conn)),
            'pending_batches': len(pending_batches(conn)),
            'tiers': stats.counts_by_tier(conn), 'states': stats.counts_by_queue_state(conn),
        }
    if path == '/api/job-options':
        app._no_query(query)
        return {'views': [{'key': view.key, 'title': view.title} for view in app.config.views.views],
                'sources': [source.name for source in sources.list_sources(conn)]}
    if path in {'/api/jobs', '/api/jobs/selection'}:
        fields = {'view', 'search', 'track', 'tier', 'offset', 'limit', 'saved', 'source',
                  'eligibility', 'status', 'state', 'closed', 'order', 'company', 'age', 'favorites'}
        if set(query) - fields:
            raise APIError(400, 'Unknown jobs query parameter')
        view = query.get('view', 'jobs')
        filt = app.job_filter('queue' if view == 'apply' else view)
        if view in {'apply', 'inbox'}:
            filt.eligibility = jobs.NOT_FILTERED_OUT
        if view == 'apply':
            filt.queue_states = ['new', 'queued']
            filt.tiers = ['A', 'B']
        if view == 'inbox':
            filt.posted_within_days = 14
        saved = query.get('saved', '')
        if saved:
            key = _integer(saved, 'Saved view', 0, 9)
            configured = next((v for v in app.config.views.views if v.key == key), None)
            if configured is None:
                raise APIError(400, 'Saved view not found')
            selected = jobs.JobFilter.from_view_filters(configured.filters)
            for field in dataclasses.fields(selected):
                value = getattr(selected, field.name)
                if field.name not in {'order', 'limit', 'prefer_cities', 'prefer_remote'} and value is not None and value != ():
                    if field.name in {'include_closed', 'not_applied', 'applied_only', 'favorites_only'}:
                        setattr(filt, field.name, getattr(filt, field.name) or value)
                    elif field.name in {'queue_states', 'tracks', 'tiers', 'statuses', 'eligibility', 'sources'} and getattr(filt, field.name):
                        setattr(filt, field.name, list(set(value) & set(getattr(filt, field.name))) or ['__no_match__'])
                    else:
                        setattr(filt, field.name, value)
        search = _text(query.get('search', ''), 'Search', 500)
        company = query.get('company', '0')
        if company not in {'0', '1'}:
            raise APIError(400, 'Company filter must be 0 or 1')
        if search:
            if company == '1':
                filt.company_text = search
            else:
                filt.text = search
        if 'age' in query:
            filt.posted_within_days = _integer(query['age'], 'Age', 1, 36500) if query['age'] else None
        if query.get('favorites', '') not in {'', '1'}:
            raise APIError(400, 'Favorites must be 1 when enabled')
        if query.get('favorites') == '1':
            filt.favorites_only = True
        filters = [('track', 'tracks', set(app.config.track_names)), ('tier', 'tiers', {'A', 'B', 'C'}),
                   ('status', 'statuses', STATUSES), ('state', 'queue_states', QUEUE_STATES | {'applied'}),
                   ('eligibility', 'eligibility', ELIGIBILITY),
                   ('source', 'sources', {s.name for s in sources.list_sources(conn)})]
        for parameter, field, allowed in filters:
            value = query.get(parameter, '')
            if value:
                if value not in allowed:
                    raise APIError(400, f'Unknown {parameter}')
                existing = getattr(filt, field)
                setattr(filt, field, [value] if not existing or value in existing else ['__no_match__'])
        closed = query.get('closed', '')
        if closed not in {'', '0', '1'}:
            raise APIError(400, 'Closed must be 0 or 1')
        if closed:
            filt.include_closed = closed == '1'
        order = query.get('order', '')
        if order:
            if order not in {'best', 'newest', 'company', 'deadline', 'score', 'track'}:
                raise APIError(400, 'Unknown ordering')
            filt.order = order
        if view == 'apply':
            filt.include_closed = False
        offset = _integer(query.get('offset', '0'), 'offset', 0, 100000)
        limit = _integer(query.get('limit', '30'), 'limit', 1, 100)
        total = jobs.count_jobs(conn, filt)
        if path == '/api/jobs/selection':
            if total > 10000:
                raise APIError(400, 'Narrow the search to at most 10,000 jobs before selecting all')
            filt.limit = 10000
            return {'ids': [job.id for job in jobs.list_jobs(conn, filt)], 'total': total}
        if view == 'apply' and (not order or order == 'best'):
            filt.limit = None
            ranked = jobs.list_jobs(conn, filt)
            ranked.sort(key=lambda job: (job.fill_state == 'ready', job.tier != 'A'))
            page = ranked[offset:offset + limit]
        else:
            filt.limit = offset + limit
            page = jobs.list_jobs(conn, filt)[offset:] if offset < total else []
        return {'jobs': [_job_json(job) for job in page], 'total': total}
    if path == '/api/pipeline':
        if set(query) - {'track'}:
            raise APIError(400, 'Unknown pipeline parameter')
        track = query.get('track') or None
        if track and track not in app.config.track_names:
            raise APIError(400, 'Unknown track')
        board = pipeline.board(conn, track=track)
        funnel = pipeline.funnel(conn, track=track)
        return {'columns': {status: [dataclasses.asdict(card) for card in cards] for status, cards in board.items()},
                'funnel': {**dataclasses.asdict(funnel), 'response_rate': funnel.response_rate, 'advance_rate': funnel.advance_rate}}
    if path == '/api/reminders':
        app._no_query(query)
        due = today.due_reminders(conn, today.now().date())
        upcoming = []
        for reminder in reminders.upcoming_reminders(conn, limit=100):
            job = jobs.get_job(conn, reminder.job_id) if reminder.job_id else None
            upcoming.append({**dataclasses.asdict(reminder), 'label': f'{job.company_name} — {job.title}' if job else reminder.note or 'Reminder'})
        return {'due': [dataclasses.asdict(item) for item in due], 'upcoming': upcoming}
    match = re.fullmatch(r'/api/job-tools/([1-9][0-9]{0,17})', path)
    if match:
        app._no_query(query)
        job = app._job(conn, int(match[1]))
        application = applications.get_application_for_job(conn, job.id)
        return {'tier': job.tier, 'queue_state': job.queue_state, 'deadline_at': job.deadline_at,
                'tracks': job.tracks, 'eligibility': job.eligibility, 'apply_url': job.apply_url or job.url,
                'sources': [dataclasses.asdict(source) for source in job.sources],
                'evidence': {field: getattr(job, field) for field in (
                    'tier_source', 'first_seen_at', 'last_seen_at', 'closed_at', 'is_active',
                    'school_selective_note', 'verification_status', 'verified_at',
                    'verification_checked_at', 'verification_reason', 'fill_attempted_at',
                    'fill_ready_at', 'edit_notes', 'like_score', 'like_terms')},
                'freshness': job.freshness,
                'application': dataclasses.asdict(application) if application else None,
                'can_undo': bool(application and applications.last_undoable_event(conn, application.id)),
                'followups': [dataclasses.asdict(r) for r in reminders.followups_for(conn, application.id)] if application else []}
    return None


def post(app, conn, path, body):
    from kitty.web_server import APIError
    if path == '/api/jobs/metadata':
        if set(body) != {'url', 'confirmed'} or body.get('confirmed') is not True:
            raise APIError(400, 'Confirm the public metadata request')
        if app.settings.demo:
            raise APIError(409, 'Public metadata requests are disabled in demo mode')
        url = _text(body.get('url'), 'URL', 4000)
        def fetch_metadata():
            import html
            from kitty.core import urlmeta
            from kitty.core.discovery.http import PoliteHttp
            from kitty.core.discovery.public_pages import PublicPages
            from kitty.normalize import company_from_url
            http = PoliteHttp(app.settings.cache_dir / 'web-metadata', min_interval=2, cache_ttl_minutes=60)
            try:
                with PublicPages(http) as pages:
                    page = pages.fetch(url)
                match = urlmeta._TITLE.search(page.text)
                if not match:
                    raise APIError(422, 'This public page has no title; enter the job details manually')
                page_title = html.unescape(match.group(1)).strip()
                title_guess, company_guess = urlmeta._split_title(page_title, company_from_url(page.url))
                return {'page_title': page_title, 'title_guess': title_guess, 'company_guess': company_guess}
            finally:
                http.close()
        return {'task': app.tasks.start('job-metadata', fetch_metadata)}
    if path == '/api/jobs/add':
        if set(body) - {'company', 'title', 'url', 'location', 'track', 'tier', 'description', 'deadline', 'submitted', 'resume_id'}:
            raise APIError(400, 'Unknown new-job field')
        company = _text(body.get('company'), 'Company', 250)
        title = _text(body.get('title'), 'Title', 500)
        url = _text(body.get('url'), 'URL', 4000)
        try:
            parsed = urlsplit(url)
        except ValueError:
            raise APIError(400, 'Enter a valid HTTP(S) posting URL') from None
        if not company or not title or parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            raise APIError(400, 'Company, title and an HTTP(S) posting URL without credentials are required')
        track = _text(body.get('track', ''), 'Track', 100)
        tier = body.get('tier') or None
        if track and track not in app.config.track_names or tier not in (None, 'A', 'B', 'C'):
            raise APIError(400, 'Unknown track or tier')
        existing = jobs.find_by_url(conn, url)
        if existing:
            return {'ok': True, 'id': existing.id, 'created': False}
        location = _text(body.get('location', ''), 'Location', 500)
        description = _text(body.get('description', ''), 'Description')
        submitted = body.get('submitted', False)
        if type(submitted) is not bool:
            raise APIError(400, 'Submission confirmation must be a boolean')
        deadline = _date(body.get('deadline', ''), optional=True)
        resume_id = body.get('resume_id')
        if resume_id is not None:
            from kitty.core import resumes
            from kitty.web_server import _pdf_bytes
            if type(resume_id) is not int or not 1 <= resume_id <= 2**63 - 1:
                raise APIError(400, 'Invalid resume ID')
            resume = resumes.get_resume(conn, resume_id)
            if resume is None or not resume.active:
                raise APIError(400, 'Choose an active registered resume')
            _pdf_bytes(app.settings, resume)
        with transaction(conn):
            job, created = jobs.upsert_job(conn, company=company, title=title, url=url,
                                           location=location or None, jd_text=description or None,
                                           tracks=[track] if track else (), deadline_at=deadline)
            if tier:
                jobs.set_tier(conn, job.id, tier, reason='Manually chosen in the website')
            if resume_id is not None:
                jobs.set_resume(conn, job.id, resume_id)
            if submitted:
                from kitty.core import followups
                from kitty.core.apply.filler import choose_resume
                chosen = choose_resume(conn, app.config, jobs.get_job(conn, job.id))
                followups.mark_applied(conn, app.config, job.id,
                                       resume_id=chosen.id if chosen else None, track=track or None)
        return {'ok': True, 'id': job.id, 'created': created}
    if path == '/api/jobs/bulk':
        if set(body) - {'ids', 'action', 'value'}:
            raise APIError(400, 'Unknown bulk action field')
        ids = body.get('ids')
        if not isinstance(ids, list) or not 1 <= len(ids) <= 10000 or any(type(i) is not int or not 1 <= i <= 2**63 - 1 for i in ids):
            raise APIError(400, 'Select between 1 and 10,000 job IDs')
        selected = [app._job(conn, job_id) for job_id in dict.fromkeys(ids)]
        action, value = body.get('action'), body.get('value')
        if action == 'tier':
            if value not in ('A', 'B', 'C', None):
                raise APIError(400, 'Choose tier A, B, C or unranked')
        elif action == 'state':
            if not isinstance(value, str) or value not in QUEUE_STATES:
                raise APIError(400, 'Choose a valid queue state')
            if any(job.application_id for job in selected):
                raise APIError(409, 'Applied jobs retain their application state')
        elif action == 'not_eligible':
            if any(job.application_id for job in selected):
                raise APIError(409, 'Applied jobs retain their application state')
        elif action == 'add_track':
            if not isinstance(value, str) or value not in app.config.track_names:
                raise APIError(400, 'Choose a configured track')
        elif action == 'favorite':
            if type(value) is not bool and value != 'toggle':
                raise APIError(400, 'Favorite must be a boolean or toggle')
            if value == 'toggle':
                value = not all(job.favorited_at for job in selected)
        elif action == 'delete':
            if value is not True:
                raise APIError(400, 'Confirm job deletion first')
            if any(job.application_id for job in selected):
                raise APIError(409, 'Submitted applications and their history are protected from job deletion')
            browser = app.automation.snapshot()
            if browser['running'] and set(browser['job_ids']) & {job.id for job in selected}:
                raise APIError(409, 'Stop preparation for these jobs before deleting them; retained Firefox tabs stay open')
        else:
            raise APIError(400, 'Unknown bulk action')
        target_ids = [job.id for job in selected]
        # These core operations already own their atomic transaction.
        if action == 'state':
            favorites.set_state(conn, target_ids, value)
        elif action == 'not_eligible':
            favorites.mark_not_eligible(conn, target_ids)
        elif action == 'favorite':
            (favorites.favorite if value else favorites.unfavorite)(conn, target_ids)
        elif action == 'delete':
            with transaction(conn):
                for job_id in target_ids:
                    if applications.get_application_for_job(conn, job_id) is not None:
                        raise APIError(409, 'Submitted applications and their history are protected from job deletion')
                # Reusable answers survive deletion of the posting that first prompted them.
                conn.executemany('UPDATE screening_questions SET first_seen_job_id=NULL WHERE first_seen_job_id=?',
                                 [(job_id,) for job_id in target_ids])
                conn.executemany('DELETE FROM jobs WHERE id=?', [(job_id,) for job_id in target_ids])
        else:
            with transaction(conn):
                for job in selected:
                    if action == 'add_track':
                        jobs.link_track(conn, job.id, value, reason='Manually added in the website')
                    else:
                        jobs.set_tier(conn, job.id, value, reason='Manually chosen in the website')
        return {'ok': True, 'changed': len(selected), **({'favorite': value} if action == 'favorite' else {})}
    if path == '/api/reminders':
        if set(body) - {'date', 'note', 'job_id'}:
            raise APIError(400, 'Unknown reminder field')
        job_id = body.get('job_id')
        if job_id is not None:
            if type(job_id) is not int or not 1 <= job_id <= 2**63 - 1:
                raise APIError(400, 'Invalid job ID')
            app._job(conn, job_id)
        reminder = reminders.create_reminder(conn, kind='custom', due_at=_date(body.get('date')),
                                              note=_text(body.get('note', ''), 'Note'), job_id=job_id)
        return {'ok': True, 'id': reminder.id}
    if path == '/api/reminders/restore':
        row = body.get('reminder')
        fields = {'id', 'job_id', 'application_id', 'kind', 'due_at', 'note', 'done_at', 'created_at'}
        if set(body) != {'reminder'} or not isinstance(row, dict) or set(row) != fields:
            raise APIError(400, 'Expected the deleted reminder to restore')
        ids_ok = all(row[key] is None or type(row[key]) is int and 1 <= row[key] <= 2**63 - 1
                     for key in ('job_id', 'application_id')) and type(row['id']) is int and 1 <= row['id'] <= 2**63 - 1
        texts_ok = all(isinstance(row[key], str) and len(row[key]) <= 100 for key in ('due_at', 'created_at')) \
            and all(row[key] is None or isinstance(row[key], str) for key in ('note', 'done_at'))
        if not ids_ok or not texts_ok or row['kind'] not in {'followup', 'deadline', 'opens', 'custom'}:
            raise APIError(400, 'Expected the deleted reminder to restore')
        if row['note'] is not None:
            _text(row['note'], 'Note')
        if conn.execute('SELECT id FROM reminders WHERE id=?', (row['id'],)).fetchone():
            raise APIError(409, 'That reminder already exists')
        try:
            reminders.restore_reminder(conn, row)
        except sqlite3.IntegrityError:
            raise APIError(409, 'Its job or application no longer exists') from None
        return {'ok': True}
    match = re.fullmatch(r'/api/reminders/([1-9][0-9]{0,17})(?:/(complete|snooze|delete))?', path)
    if match:
        ident, action = int(match[1]), match[2]
        if action and body:
            raise APIError(400, 'Expected empty object')
        if not action and (not body or set(body) - {'date', 'note'}):
            raise APIError(400, 'Change the reminder date or note')
        if not conn.execute('SELECT id FROM reminders WHERE id=?', (ident,)).fetchone():
            raise APIError(404, 'Reminder not found')
        if action == 'complete':
            reminders.complete_reminder(conn, ident)
        elif action == 'snooze':
            previous, due_at = reminders.snooze_reminder(conn, ident)
            return {'ok': True, 'previous': previous, 'due_at': due_at}
        elif action == 'delete':
            return {'ok': True, 'reminder': reminders.delete_reminder(conn, ident)}
        else:
            reminders.update_reminder(conn, ident, due_at=_date(body['date']) if 'date' in body else None,
                                      note=_text(body['note'], 'Note') if 'note' in body else None)
        return {'ok': True}
    match = re.fullmatch(r'/api/job-tools/([1-9][0-9]{0,17})', path)
    if not match:
        return None
    if set(body) - {'action', 'value', 'note'}:
        raise APIError(400, 'Unknown job action field')
    job = app._job(conn, int(match[1]))
    application = applications.get_application_for_job(conn, job.id)
    action, value = body.get('action'), body.get('value')
    with transaction(conn):
        if action == 'deadline':
            jobs.set_deadline(conn, job.id, _date(value, optional=True))
        elif action == 'next_action':
            if application is None:
                raise APIError(409, 'Record your submission first')
            applications.set_next_action(conn, application.id, due_at=_date(value, optional=True),
                                         note=_text(body.get('note', ''), 'Note') or None)
        elif action == 'undo':
            if application is None or applications.undo_last_status(conn, application.id) is None:
                raise APIError(409, 'No status change can be undone; the original submission stays')
        elif action == 'tracks':
            if not isinstance(value, list) or any(not isinstance(t, str) or t not in app.config.track_names for t in value):
                raise APIError(400, 'Choose configured tracks')
            for track in set(job.tracks) - set(value):
                jobs.unlink_track(conn, job.id, track)
            for track in set(value) - set(job.tracks):
                jobs.link_track(conn, job.id, track, reason='Manually assigned in the website')
        else:
            raise APIError(400, 'Unknown job action')
    return {'ok': True}
