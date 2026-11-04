"""Isolated consumer fixtures: no real provider calls, credentials, jobs or user DBs."""
from types import SimpleNamespace
import json

import httpx
import pytest

from kitty.core import opportunities as core, opportunity_sources as providers
from kitty.core.discovery.http import PoliteHttp
from kitty.web_server import APIError
from kitty import web_opportunities as web
from kitty.web_tasks import TaskRunner

# Trimmed public payload shapes observed at primary endpoints on 2026-10-14.
IEEE = [{"uid": "08fe6a28-6a7a-4fe0-97d7-0ae62f9783da", "title": "Technothon",
         "description": "", "start": "2026-10-16T14:00:00.000Z", "end": "2026-10-18T22:00:00.000Z",
         "location": "WEB", "type": "Event", "rsvpUrl": "https://portal.ieeetamu.org/members/registration?rsvp=private"}]
MLH_EVENT = {"id": "01a03900-e3b1-9c45-b1e1-8bb40989cb8e", "name": "TAMU Datathon", "status": "pending",
             "startsAt": "2026-11-07T15:00:00Z", "endsAt": "2026-11-08T22:00:00Z",
             "url": "/events/tamu-datathon-ce/prizes", "location": "College Station, Texas", "formatType": "physical",
             "websiteUrl": "https://tamudatathon.com/", "customFields": {"underserved_types": [], "hackathon_focus": []}}
TAMU_ICAL = b'''BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//TAMU//NONSGML v1.0//EN\r\nBEGIN:VEVENT\r\nDTSTART:20261021T220000Z\r\nDTEND:20261022T010000Z\r\nSUMMARY:Problems Worth Solving\r\nDESCRIPTION:Explore problems that could be the basis for a great busine\r\n ss.\r\nUID:20261021T220000Z-395649@calendar.tamu.edu\r\nURL:https://calendar.tamu.edu/engineering/event/395649-problems-worth-solving\r\nX-LIVEWHALE-TIMEZONE:America/Chicago\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'''


def source(ident):
    row = next(row for row in providers.DEFAULTS if row[0] == ident)
    return dict(zip(('id', 'name', 'url', 'kind', 'category', 'timezone'), row))


def mlh_page(events=None):
    data = {'props': {'upcomingEvents': events or [MLH_EVENT], 'pastEvents': []},
            'scrollProps': {'pastEvents': {'nextPage': 2}}}
    return '<script data-page="app" type="application/json">' + json.dumps(data) + '</script>'


def manual(conn, **fields):
    return core.create(conn, {'title': 'Local quantitative project', 'category': 'quant', **fields})


def test_source_parsers_keep_authoritative_dates_separate_from_deadlines():
    ieee = providers.parse_ieee(IEEE, source('ieee-tamu'))[0]
    assert ieee['start'] == '2026-10-16T14:00:00+00:00'
    assert ieee['deadline'] is None
    assert 'portal.' not in ieee['url']
    tamu = providers.parse_ical(TAMU_ICAL, source('tamu-engineering'))[0]
    assert tamu['title'] == 'Problems Worth Solving'
    assert tamu['timezone'] == 'America/Chicago'
    assert 'business' in tamu['description']
    assert tamu['deadline'] is None
    mlh, partial = providers.parse_mlh(mlh_page(), source('mlh'))
    assert partial is True
    assert mlh[0]['url'] == 'https://tamudatathon.com/'
    assert mlh[0]['evidence_url'] == 'https://mlh.com/events'
    assert mlh[0]['deadline'] is None
    assert 'Eligibility not evaluated' in mlh[0]['description']


def test_closed_cycles_and_unknown_pages_are_not_promoted_to_open():
    imc, partial = providers.parse_page('<h1>Prosperity 4 finished</h1><p>Trade and code.</p>', source('imc-prosperity'))
    assert partial and imc[0]['availability'] == 'closed'
    assert imc[0]['start'] is None and imc[0]['deadline'] is None
    rotman, partial = providers.parse_page('<h1>RITC 2027</h1><p>Details coming soon</p>', source('rotman'))
    assert partial and rotman[0]['availability'] == 'unknown'
    stale = dict(MLH_EVENT, startsAt='2001-11-07T15:00:00Z', endsAt='2001-11-08T22:00:00Z')
    items, _ = providers.parse_mlh(mlh_page([stale]), source('mlh'))
    assert items[0]['availability'] == 'closed'


def test_jsonld_rss_and_jane_street_public_cards_are_consumed():
    event = {'@context': 'https://schema.org', '@type': 'Event', 'name': 'Hackathon', 'url': 'https://example.devpost.com/',
             'startDate': '2099-06-03', 'endDate': '2099-06-04', 'applicationDeadline': '2099-05-20', 'location': {'name': 'Online'}}
    items, partial = providers.parse_page('<script type="application/ld+json">' + json.dumps({'@graph': [event]}) + '</script>', source('devpost'))
    assert not partial and items[0]['deadline'] == '2099-05-20'
    rss = providers.parse_rss('<rss><channel><item><title>Hack</title><link>https://hack.devpost.com</link><pubDate>2001-01-01</pubDate><description>Build!</description></item></channel></rss>', source('devpost'))
    assert rss[0]['start'] is None and rss[0]['deadline'] is None
    jane, partial = providers.parse_page('<a href="/join-jane-street/programs-and-events/see/">PROGRAM SEE ACCEPTING APPLICATIONS University students explore math. Learn More</a>', source('jane-street'))
    assert partial and jane[0]['title'] == 'SEE' and jane[0]['availability'] == 'unknown'


def test_unknown_or_naive_provider_dates_remain_unknown():
    assert providers.parsed_date('May 3') is None
    assert providers.parsed_date('2027-05-03T13:00:00') is None
    assert providers.parsed_date('2027-05-03') == '2027-05-03'
    assert core.availability({'start': '2099-01-01', 'timezone': 'America/Chicago'}) == 'future'
    assert core.availability({'start': '2000-01-01', 'end': '2000-01-02', 'timezone': 'UTC'}) == 'closed'


def test_discover_fetches_primary_paths_through_polite_http_and_exposes_blocked_sources(workspace, tmp_path):
    requested = []
    def transport(request):
        requested.append(str(request.url))
        if request.url.host == 'ieeetamu.org': return httpx.Response(200, json=IEEE)
        if request.url.host == 'mlh.com': return httpx.Response(200, text=mlh_page())
        if request.url.host == 'calendar.tamu.edu': return httpx.Response(200, content=TAMU_ICAL)
        return httpx.Response(403)
    def factory(s):
        return PoliteHttp(tmp_path / s['id'], min_interval=0, cache_ttl_minutes=10,
                          client=httpx.Client(transport=httpx.MockTransport(transport)))
    result = providers.discover(workspace.conn, tmp_path, http_factory=factory)
    assert set(requested) == {row[2] for row in providers.DEFAULTS}
    assert result['partial'] is True
    assert {r['source']: r['status'] for r in result['sources']}['devpost'] == 'blocked'
    assert {r['source']: r['status'] for r in result['sources']}['mlh'] == 'partial'
    assert core.list_items(workspace.conn, {'source': 'ieee-tamu'})[0]['title'] == 'Technothon'
    health = next(s for s in providers.sources(workspace.conn) if s['id'] == 'citadel')
    assert health['last_error'] and health['last_success'] is None


def test_refresh_retains_review_notes_tags_deadline_and_project(workspace):
    conn = workspace.conn
    found = providers.parse_ieee(IEEE, source('ieee-tamu'))
    core.upsert_discovered(conn, 'ieee-tamu', found, '2026-10-14T10:00:00+00:00')
    item = core.list_items(conn, {})[0]
    item = core.update(conn, item['id'], {'notes': 'Ask advisor', 'tags': ['hardware'], 'deadline': '2026-10-15', 'timezone': 'America/Chicago', 'status': 'queued'}, item['revision'])
    core.favorites(conn, [{'id': item['id'], 'revision': item['revision']}], True)
    item = core.detail(conn, item['id'])
    item = core.project(conn, item['id'], {'revision': item['revision']})
    item = core.project_item(conn, item['id'], {'revision': item['project']['revision'], 'kind': 'milestone', 'title': 'Prepare demo', 'due': '2026-10-15'})
    found[0].update(title='Technothon updated', deadline='2026-10-16', location='Updated room')
    core.upsert_discovered(conn, 'ieee-tamu', found, '2026-10-14T11:00:00+00:00')
    refreshed = core.detail(conn, item['id'])
    assert refreshed['title'] == 'Technothon updated'
    assert refreshed['deadline'] == '2026-10-15' and refreshed['deadline_override'] is True
    assert refreshed['notes'] == 'Ask advisor' and refreshed['tags'] == ['hardware']
    assert refreshed['favorite'] and refreshed['status'] == 'queued'
    assert refreshed['project']['items'][0]['title'] == 'Prepare demo'
    assert refreshed['evidence'][0]['facts']['deadline'] == '2026-10-16'
    assert conn.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0


def test_canonical_identity_deduplicates_tracks_and_provider_date_corrections(workspace):
    conn = workspace.conn
    item = providers.base(source('mlh'), 'Annual Hack', 'https://hack.example/?utm_source=mlh', start='2099-01-01', provider_id='stable')
    core.upsert_discovered(conn, 'mlh', [item], '2026-10-14T10:00:00+00:00')
    same = dict(item, url='https://hack.example/', start='2099-01-02')
    core.upsert_discovered(conn, 'devpost', [same], '2026-10-14T11:00:00+00:00')
    assert len(core.list_items(conn, {})) == 1
    corrected = dict(item, start='2100-01-01')
    core.upsert_discovered(conn, 'mlh', [corrected], '2026-10-14T12:00:00+00:00')
    assert len(core.list_items(conn, {})) == 1
    assert core.list_items(conn, {})[0]['start'] == '2100-01-01'
    next_cycle = dict(item, start='2101-01-01', provider_id='next-cycle')
    core.upsert_discovered(conn, 'mlh', [next_cycle], '2026-10-14T13:00:00+00:00')
    assert len(core.list_items(conn, {})) == 2


def test_old_cached_evidence_cannot_replace_newer_facts(workspace):
    conn = workspace.conn
    item = providers.base(source('mlh'), 'New title', 'https://hack.example/', provider_id='event')
    core.upsert_discovered(conn, 'mlh', [item], '2026-10-14T12:00:00+00:00')
    core.upsert_discovered(conn, 'mlh', [dict(item, title='Stale title')], '2026-10-13T12:00:00+00:00')
    current = core.detail(conn, core.list_items(conn, {})[0]['id'])
    assert current['title'] == 'New title'
    assert current['evidence'][0]['facts']['title'] == 'New title'


def test_atomic_multifavorite_and_optimistic_edits(workspace):
    conn = workspace.conn
    a, b = manual(conn), manual(conn, title='Other')
    with pytest.raises(APIError) as failure:
        core.favorites(conn, [{'id': a['id'], 'revision': a['revision']}, {'id': b['id'], 'revision': 0}], True)
    assert failure.value.status == 409
    assert not core.detail(conn, a['id'])['favorite']
    core.favorites(conn, [{'id': a['id'], 'revision': a['revision']}, {'id': b['id'], 'revision': b['revision']}], True)
    assert len(core.list_items(conn, {'favorites': 'true', 'status': 'saved'})) == 2
    with pytest.raises(APIError): core.update(conn, a['id'], {'notes': 'stale'}, a['revision'])


def test_import_is_atomic_and_urls_are_canonical(workspace):
    conn = workspace.conn
    with pytest.raises(APIError):
        core.import_items(conn, [{'title': 'First', 'url': 'https://example.com/?utm_source=x'}, {'title': 'Duplicate', 'url': 'https://example.com/'}])
    assert core.list_items(conn, {}) == []
    with pytest.raises(APIError): core.import_items(conn, [{'title': 'First'}, {'title': 'Invalid', 'deadline': 'tomorrow'}])
    assert core.list_items(conn, {}) == []
    imported = core.import_items(conn, [{'title': 'One'}, {'title': 'Two'}])
    assert imported['count'] == 2


def test_search_filters_and_deterministic_reasons_do_not_assert_eligibility(workspace):
    conn = workspace.conn
    core.save_settings(conn, {'revision': 1, 'profile': {'major': 'engineering', 'track': 'quant', 'interests': ['hardware'], 'locations': ['College Station'], 'prefer_remote': True}}, workspace.settings.root)
    a = manual(conn, title='Engineering hardware', description='Quant workshop', location='College Station', remote=True, deadline='2099-01-01', tags=['IEEE'])
    manual(conn, title='Other')
    results = core.list_items(conn, {'search': 'IEEE', 'category': 'quant', 'location': 'station', 'remote': 'true', 'deadline': '2099-02-01', 'availability': 'future'})
    assert [r['id'] for r in results] == [a['id']]
    assert results[0]['score'] == 50
    assert len(results[0]['reasons']) == 5
    assert 'eligibility' not in results[0]


def test_project_task_milestone_resource_crud_and_revisions(workspace):
    conn = workspace.conn
    item = manual(conn)
    item = core.project(conn, item['id'], {'revision': item['revision']})
    for kind in ('task', 'milestone', 'resource'):
        item = core.project_item(conn, item['id'], {'revision': item['project']['revision'], 'kind': kind, 'title': kind, 'url': 'https://example.com/' if kind == 'resource' else '', 'due': '2099-01-01'})
    p = item['project']
    assert len(p['items']) == 3
    target = p['items'][0]
    item = core.project_item(conn, item['id'], {'revision': p['revision'], 'id': target['id'], 'done': True})
    assert next(i for i in item['project']['items'] if i['id'] == target['id'])['done'] == 1
    with pytest.raises(APIError): core.project_item(conn, item['id'], {'revision': p['revision'], 'id': target['id'], 'delete': True})
    item = core.update_project(conn, item['id'], {'revision': item['project']['revision'], 'title': 'Updated project', 'notes': 'Milestones scoped'})
    assert item['project']['title'] == 'Updated project'
    item = core.project_item(conn, item['id'], {'revision': item['project']['revision'], 'id': target['id'], 'delete': True})
    assert len(item['project']['items']) == 2
    core.remove(conn, item['id'], item['revision'])
    assert conn.execute('SELECT count(*) FROM opportunity_project_items').fetchone()[0] == 0


def test_schedule_opt_in_status_source_configuration_and_demo_boundaries(workspace):
    conn = workspace.conn
    assert core.settings(conn)['enabled'] is False
    config = core.save_settings(conn, {'revision': 1, 'enabled': True, 'interval_hours': 6}, workspace.settings.root)
    assert config['next_run'] and config['enabled']
    config = core.save_settings(conn, {'revision': config['revision'], 'enabled': False}, workspace.settings.root)
    assert config['next_run'] is None
    s = providers.sources(conn)[0]
    with pytest.raises(APIError): providers.save_source(conn, s['id'], {'revision': s['revision'], 'url': 'http://127.0.0.1/private'})
    changed = providers.save_source(conn, s['id'], {'revision': s['revision'], 'enabled': False})
    assert not changed['enabled']
    app = SimpleNamespace(settings=SimpleNamespace(demo=True))
    with pytest.raises(APIError): web.post(app, conn, '/api/opportunities/discover', {'confirmed': True})
    with pytest.raises(APIError): web.post(app, conn, '/api/opportunities/settings', {'revision': config['revision'], 'enabled': True})
    assert web.post(app, conn, '/api/opportunities', {'title': 'Demo local'})['title'] == 'Demo local'
    assert web.get(app, conn, '/api/opportunities', {})['total'] == 1


def test_background_monitor_trigger_updates_persisted_run_status(workspace, monkeypatch):
    app = SimpleNamespace(settings=workspace.settings, tasks=TaskRunner())
    monkeypatch.setattr(providers, 'discover', lambda conn, cache, selected: {'sources': [], 'count': 0, 'partial': False})
    task = web.post(app, workspace.conn, '/api/opportunities/discover', {'confirmed': True})['task']
    app.tasks.close()
    assert app.tasks.snapshot(task['id'])['status'] == 'completed'
    assert core.settings(workspace.conn)['last_run']
    assert core.settings(workspace.conn)['next_run'] is None


def test_calendar_export_uses_real_calendar_core_date_only_exclusive_end(workspace):
    conn = workspace.conn
    item = manual(conn, deadline='2099-01-11', timezone='America/Chicago', url='https://example.com/')
    first = core.export_deadline(conn, item['id'])
    second = core.export_deadline(conn, item['id'])
    assert first['id'] == second['id']
    assert first['start'] == '2099-01-11' and first['end'] == '2099-01-12'
    assert first['all_day'] and first['source_key'] == f"opportunity:{item['id']}:deadline"
    unknown = manual(conn)
    with pytest.raises(APIError): core.export_deadline(conn, unknown['id'])


@pytest.mark.parametrize('body', [{'status': 'job-applied'}, {'deadline': '2027-01-01T12:00:00'}, {'remote': 'yes'}, {'timezone': 'not/a/timezone'}, {'tags': 'not a list'}, {'url': 'javascript:alert(1)'}])
def test_manual_input_validation(workspace, body):
    with pytest.raises(APIError): manual(workspace.conn, **body)


def test_only_unique_labeled_year_bearing_deadlines_become_calendar_dates():
    assert providers.explicit_deadline('Application deadline: January 11, 2027') == '2027-01-11'
    assert providers.explicit_deadline('Applications close on May 3') is None
    assert providers.explicit_deadline('Published January 11, 2027') is None
    assert providers.explicit_deadline('Application deadline: January 11, 2027. Apply by February 2, 2027') is None


def test_monitor_lifecycle_creates_no_default_thread_and_stops_on_disable(workspace):
    app = SimpleNamespace(settings=workspace.settings, tasks=TaskRunner())
    web.start(app)
    assert getattr(app, 'opportunity_monitor', None) is None
    config = core.settings(workspace.conn)
    enabled = web.post(app, workspace.conn, '/api/opportunities/settings', {'revision': config['revision'], 'enabled': True})
    try:
        assert app.opportunity_monitor[1].is_alive()
        web.post(app, workspace.conn, '/api/opportunities/settings', {'revision': enabled['revision'], 'enabled': False})
        assert app.opportunity_monitor is None
    finally:
        web.stop(app)
        app.tasks.close()
