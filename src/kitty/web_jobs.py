"""Website job routes that stay on the worker, and the config projection the front reads.

rika-server answers the job lists and their selection, one job and its actions, the bulk
actions, the tool panel, the options, the pipeline and the reminders itself
(server/src/jobs/). What stays here:

- ``GET /api/dashboard``: it refreshes the screening questions' answered flags against the
  answers file (``questions.refresh_if_changed``) before counting them.
- ``GET /api/pipeline/funnel``: how far applications got (``pipeline.stage_reach``), the
  strip above Track's board.
- ``GET /api/jobs/fit?ids=1,2,…``: for up to one page of jobs, the posting skills and what
  covers each (``workshop.ats.fit``: the resume or a confirmed Workshop fact) and the pay the
  posting lists (``core.pay``); Find's table asks once per page, the job detail for one job.
- ``POST /api/jobs/metadata``: a public page fetched in the task runner.
- ``POST /api/jobs/add``: a posting through ``jobs.upsert_job``, the dedupe and evidence write
  path discovery shares.
- ``GET /api/internal/jobs/config`` and ``GET /api/internal/jobs/automation``: what the front's
  job routes read from this process (the validated config, which only this process reloads,
  and the browser controller's state). Pages never reach them: the front refuses every
  ``/api/internal`` route.
"""
from __future__ import annotations

import dataclasses
from urllib.parse import urlsplit

from kitty.core import applications, jobs, pay, pipeline, today
from kitty.dates import parse_user_date
from kitty.db.connection import transaction

CONFIG = '/api/internal/jobs/config'
AUTOMATION = '/api/internal/jobs/automation'


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


def config_projection(config) -> dict:
    """The validated config the front's job routes read (server/src/jobs/config.rs)."""
    return {
        'tracks': [{'name': track.name, 'prefer_cities': list(track.location_rules.prefer_cities),
                    'default_resume': track.default_resume} for track in config.tracks.tracks],
        'open_to_remote': config.profile.open_to_remote,
        'views': [view.model_dump(mode='json') for view in config.views.views],
        'followup_days': list(config.apply.followup_days),
    }


def get(app, conn, path, query):
    from kitty.web_server import _job_json
    if path == CONFIG:
        app._no_query(query)
        return config_projection(app.config)
    if path == AUTOMATION:
        app._no_query(query)
        browser = app.automation.snapshot()
        return {'running': browser['running'], 'job_ids': list(browser['job_ids'])}
    if path == '/api/pipeline/funnel':
        app._no_query(query)
        return {'tracks': [dataclasses.asdict(reach) for reach in pipeline.stage_reach(conn)]}
    if path == '/api/jobs/fit':
        return _fit(app, conn, query)
    if path == '/api/dashboard':
        from kitty.core import questions, stats
        from kitty.core.triage.run import pending_batches
        app._no_query(query)
        digest = today.build_digest(conn, app.config)
        funnel = pipeline.funnel(conn)
        questions.refresh_if_changed(conn, app.config)
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
    return None


#: One page of Find's table (its batch size).
FIT_IDS = 100


def _fit(app, conn, query):
    from kitty.core.workshop import ats
    from kitty.web_server import APIError
    raw = query.get('ids', '') if set(query) <= {'ids'} else ''
    parts = raw.split(',') if raw else []
    if not parts or len(parts) > FIT_IDS or not all(
            part.isascii() and part.isdigit() and part[0] != '0' and len(part) <= 18 for part in parts):
        raise APIError(400, f'ids must be 1 to {FIT_IDS} job ids separated by commas')
    ids = list(dict.fromkeys(int(part) for part in parts))
    listed = pay.listed(conn, ids)
    return {'jobs': [{**report, 'pay': listed.get(report['job_id'])}
                     for report in ats.fit(conn, app.settings, app.config.resume_terms, ids)]}


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
    return None
