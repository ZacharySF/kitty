"""Profile adapter tests use only isolated public-example workspaces."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import yaml

from applyai.config import load_config
from applyai.core import questions
from applyai.core.workspace import sync_config_to_db
from applyai.web_profile import get, post, set_source_enabled
from applyai.web_server import APIError


@pytest.fixture
def profile_app(workspace):
    app = SimpleNamespace(settings=workspace.settings, config=workspace.config)

    def reload_config():
        app.config = load_config(workspace.settings.config_dir)
        sync_config_to_db(workspace.conn, app.config)

    app.reload_config = reload_config
    return app


def document(app, conn, section):
    return get(app, conn, '/api/profile/config', {'section': section})


def save(app, conn, doc, text=None, **extra):
    return post(app, conn, '/api/profile/config', {
        'section': doc['section'], 'version': doc['version'],
        'text': doc['text'] if text is None else text, 'confirm': True, **extra,
    })


def test_named_sections_never_resolve_arbitrary_files(profile_app, workspace):
    for section in ('../.env', 'credentials', 'apply.yaml', '/etc/passwd'):
        with pytest.raises(APIError):
            document(profile_app, workspace.conn, section)
    with pytest.raises(APIError):
        get(profile_app, workspace.conn, '/api/profile', {'path': '.env'})
    assert get(profile_app, workspace.conn, '/api/unowned', {}) is None
    assert post(profile_app, workspace.conn, '/api/unowned', {}) is None


def test_validated_atomic_save_and_stale_version(profile_app, workspace):
    doc = document(profile_app, workspace.conn, 'profile')
    edited = yaml.safe_load(doc['text'])
    edited['name'] = 'Synthetic Applicant'
    result = save(profile_app, workspace.conn, doc, yaml.safe_dump(edited))
    assert result['version'] != doc['version']
    assert profile_app.config.profile.name == 'Synthetic Applicant'
    assert not list(workspace.settings.config_dir.glob('.web-config-*'))
    with pytest.raises(APIError) as stale:
        save(profile_app, workspace.conn, doc)
    assert stale.value.status == 409
    before = (workspace.settings.config_dir / 'profile.yaml').read_bytes()
    with pytest.raises(APIError):
        save(profile_app, workspace.conn, result, 'unknown: invalid')
    assert (workspace.settings.config_dir / 'profile.yaml').read_bytes() == before


def test_basic_profile_save_preserves_other_fields_and_files(profile_app, workspace):
    config_dir = workspace.settings.config_dir
    before = profile_app.config.profile.model_dump(mode='json')
    other_files = {path.name: path.read_bytes() for path in config_dir.glob('*.yaml')
                   if path.name != 'profile.yaml'}
    doc = get(profile_app, workspace.conn, '/api/profile/basics', {})
    result = post(profile_app, workspace.conn, '/api/profile/basics', {
        'fields': {'name': 'Verified Applicant', 'phone': '', 'home_city': 'Verified City'},
        'version': doc['version'], 'confirm': True,
    })
    expected = {**before, 'name': 'Verified Applicant', 'phone': None, 'home_city': 'Verified City'}
    assert profile_app.config.profile.model_dump(mode='json') == expected
    assert result['fields']['phone'] == ''
    assert result['fields']['name'] == 'Verified Applicant'
    assert result['version'] != doc['version']
    assert {name: (config_dir / name).read_bytes() for name in other_files} == other_files
    assert not list(config_dir.glob('.web-config-*'))


def test_basic_profile_rejects_stale_form_and_yaml_edits(profile_app, workspace):
    basics = get(profile_app, workspace.conn, '/api/profile/basics', {})
    yaml_doc = document(profile_app, workspace.conn, 'profile')
    post(profile_app, workspace.conn, '/api/profile/basics', {
        'fields': {'name': 'Verified Name'}, 'version': basics['version'], 'confirm': True,
    })
    before = (workspace.settings.config_dir / 'profile.yaml').read_bytes()
    with pytest.raises(APIError) as stale:
        post(profile_app, workspace.conn, '/api/profile/basics', {
            'fields': {'name': 'Stale Name'}, 'version': basics['version'], 'confirm': True,
        })
    assert stale.value.status == 409
    with pytest.raises(APIError) as stale_yaml:
        save(profile_app, workspace.conn, yaml_doc)
    assert stale_yaml.value.status == 409
    assert (workspace.settings.config_dir / 'profile.yaml').read_bytes() == before


@pytest.mark.parametrize('fields', [
    {'name': None}, {'email': 42}, {'phone': []}, {'name': 'invalid\x00name'},
    {'work_authorization': 'inferred'}, {'open_to_remote': True}, {'unknown': 'value'},
    {'name': 'x' * 5001}, [], None,
])
def test_basic_profile_rejects_invalid_values_without_writing(profile_app, workspace, fields):
    doc = get(profile_app, workspace.conn, '/api/profile/basics', {})
    path = workspace.settings.config_dir / 'profile.yaml'
    before = path.read_bytes()
    with pytest.raises(APIError) as invalid:
        post(profile_app, workspace.conn, '/api/profile/basics', {
            'fields': fields, 'version': doc['version'], 'confirm': True,
        })
    assert invalid.value.status == 400
    assert path.read_bytes() == before


def test_basic_profile_requires_confirmation_and_preserves_blank_facts(profile_app, workspace):
    doc = get(profile_app, workspace.conn, '/api/profile/basics', {})
    payload = {'fields': {key: '' for key in doc['fields']}, 'version': doc['version']}
    with pytest.raises(APIError) as unconfirmed:
        post(profile_app, workspace.conn, '/api/profile/basics', payload)
    assert unconfirmed.value.status == 400
    authorization = profile_app.config.profile.work_authorization
    result = post(profile_app, workspace.conn, '/api/profile/basics', {**payload, 'confirm': True})
    assert all(value == '' for value in result['fields'].values())
    assert profile_app.config.profile.work_authorization == authorization


def test_external_file_change_requires_reload(profile_app, workspace):
    doc = document(profile_app, workspace.conn, 'tracks')
    path = workspace.settings.config_dir / 'tracks.yaml'
    path.write_text(path.read_text() + '\n# synthetic external edit\n')
    with pytest.raises(APIError) as exc:
        save(profile_app, workspace.conn, doc)
    assert exc.value.status == 409


def test_failed_reload_restores_original(profile_app, workspace):
    doc = document(profile_app, workspace.conn, 'profile')
    path = workspace.settings.config_dir / 'profile.yaml'
    original = path.read_bytes()

    def fail():
        raise RuntimeError('synthetic-private-string')

    profile_app.reload_config = fail
    with pytest.raises(APIError) as exc:
        save(profile_app, workspace.conn, doc)
    assert path.read_bytes() == original
    assert 'synthetic-private-string' not in str(exc.value)


def test_hidden_authentication_and_source_secrets_survive_save(profile_app, workspace):
    config_dir = workspace.settings.config_dir
    (config_dir / 'apply.yaml').write_text(yaml.safe_dump({
        'authentication': {'enabled': True, 'approved_origins': ['https://example.invalid']},
    }))
    sources_path = config_dir / 'sources.yaml'
    sources = yaml.safe_load(sources_path.read_text())
    sources['sources']['manual']['options'] = {
        'api_key': 'synthetic-secret-token', 'max_pages': 7,
        'seed_urls': ['https://example.invalid/?token=synthetic-token'],
    }
    sources_path.write_text('# synthetic-secret-comment\n' + yaml.safe_dump(sources))
    profile_app.reload_config()
    doc = document(profile_app, workspace.conn, 'sources')
    assert 'synthetic-secret' not in json.dumps(doc)
    assert 'synthetic-token' not in json.dumps(doc)
    assert 'api_key' not in doc['text']
    assert 'max_pages: 7' in doc['text']
    save(profile_app, workspace.conn, doc)
    assert profile_app.config.sources.sources['manual'].options['api_key'] == 'synthetic-secret-token'
    set_source_enabled(profile_app, 'manual', False)
    assert profile_app.config.sources.sources['manual'].enabled is False
    assert profile_app.config.sources.sources['manual'].options['seed_urls'] == ['https://example.invalid/?token=synthetic-token']
    apply_doc = document(profile_app, workspace.conn, 'apply')
    assert 'authentication' not in apply_doc['text']
    save(profile_app, workspace.conn, apply_doc)
    assert profile_app.config.apply.authentication.enabled
    with pytest.raises(APIError):
        save(profile_app, workspace.conn, document(profile_app, workspace.conn, 'apply'), 'authentication: {}')
    doc = document(profile_app, workspace.conn, 'sources')
    modified = yaml.safe_load(doc['text'])
    modified['sources']['manual']['options']['api_key'] = 'synthetic-write-attempt'
    with pytest.raises(APIError):
        save(profile_app, workspace.conn, doc, yaml.safe_dump(modified))


def test_symlinks_are_not_followed(profile_app, workspace, tmp_path):
    path = workspace.settings.config_dir / 'views.yaml'
    original = path.read_text()
    target = tmp_path / 'synthetic-external.yaml'
    target.write_text(original)
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(APIError):
        document(profile_app, workspace.conn, 'views')


@pytest.mark.parametrize('extra', [{'confirm': False}, {'confirm': 1}, {'unknown': True}])
def test_save_requires_exact_confirmation_and_known_fields(profile_app, workspace, extra):
    doc = document(profile_app, workspace.conn, 'profile')
    with pytest.raises(APIError):
        save(profile_app, workspace.conn, doc, **extra)


def test_answers_are_user_authored_and_refresh_question_queue(profile_app, workspace):
    questions.log_question(workspace.conn, 'Synthetic screening question?')
    doc = document(profile_app, workspace.conn, 'answers')
    post(profile_app, workspace.conn, '/api/profile/answer', {
        'question': 'Synthetic screening question?', 'answer': 'My exact synthetic answer',
        'version': doc['version'], 'confirm': True,
    })
    assert profile_app.config.answer_for('Synthetic screening question?').answer == 'My exact synthetic answer'
    assert not questions.list_questions(workspace.conn)
    assert profile_app.config.answer_for('Unanswered synthetic question?') is None


def test_import_preview_confirm_and_stale_rejection(profile_app, workspace):
    payload = {'csv': 'company,title,status,notes\nSynthetic Company,Synthetic Role,interview,User historical record\n', 'mapping': {}}
    preview = post(profile_app, workspace.conn, '/api/profile/import/preview', payload)
    assert preview['counts']['create'] == 1
    assert workspace.conn.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 0
    with pytest.raises(APIError):
        post(profile_app, workspace.conn, '/api/profile/import/confirm', {**payload, 'version': preview['version']})
    result = post(profile_app, workspace.conn, '/api/profile/import/confirm', {
        **payload, 'version': preview['version'], 'confirm': True,
    })
    assert result['stats']['applications_created'] == 1
    assert workspace.conn.execute('SELECT status FROM applications').fetchone()[0] == 'interview'
    with pytest.raises(APIError) as exc:
        post(profile_app, workspace.conn, '/api/profile/import/confirm', {
            **payload, 'version': preview['version'], 'confirm': True,
        })
    assert exc.value.status == 409
    refreshed = post(profile_app, workspace.conn, '/api/profile/import/preview', payload)
    assert refreshed['counts']['skip'] == 1


def test_import_preview_is_bound_to_content_and_mapping(profile_app, workspace):
    payload = {'csv': 'company,title\nSynthetic Employer,Historical Role\n'}
    preview = post(profile_app, workspace.conn, '/api/profile/import/preview', payload)
    with pytest.raises(APIError):
        post(profile_app, workspace.conn, '/api/profile/import/confirm', {
            'csv': payload['csv'].replace('Historical Role', 'Changed Role'),
            'version': preview['version'], 'confirm': True,
        })
    with pytest.raises(APIError):
        post(profile_app, workspace.conn, '/api/profile/import/preview', {
            **payload, 'mapping': {'company': 'Missing column'},
        })


def test_distinct_uploads_keep_their_own_source_provenance(profile_app, workspace):
    uploads = [
        'company,title\nSynthetic First,First Role\n',
        'company,title\nSynthetic Second,Second Role\n',
    ]
    for text in uploads:
        payload = {'csv': text}
        preview = post(profile_app, workspace.conn, '/api/profile/import/preview', payload)
        post(profile_app, workspace.conn, '/api/profile/import/confirm', {
            **payload, 'version': preview['version'], 'confirm': True,
        })
    sources = workspace.conn.execute(
        'SELECT c.display_name AS company, j.title, s.source_job_id, s.raw_json '
        'FROM job_sources s JOIN jobs j ON j.id = s.job_id '
        'JOIN companies c ON c.id = j.company_id ORDER BY c.display_name'
    ).fetchall()
    assert len(sources) == 2
    for row, text in zip(sources, uploads):
        company, title = text.splitlines()[1].split(',')
        basename = f'uploaded-{hashlib.sha256(text.encode()).hexdigest()}.csv'
        assert (row['company'], row['title']) == (company, title)
        assert row['source_job_id'] == f'import:{basename}:2'
        assert json.loads(row['raw_json']) == {'csv_line': 2, 'file': basename}


@pytest.mark.parametrize('relative,days', [('today', 0), ('tomorrow', 1), ('+7', 7)])
def test_relative_import_date_stays_bound_to_preview(
    profile_app, workspace, monkeypatch, relative, days,
):
    reference = datetime(2026, 1, 2, 23, 59, 59, tzinfo=timezone.utc)
    monkeypatch.setattr('applyai.dates.now', lambda: reference)
    payload = {'csv': f'company,title,applied_at\nSynthetic Employer,Historical Role,{relative}\n'}
    preview = post(profile_app, workspace.conn, '/api/profile/import/preview', payload)
    expected = (reference + timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert preview['rows'][0]['applied_at'] == expected
    reference += timedelta(days=2, seconds=10)
    post(profile_app, workspace.conn, '/api/profile/import/confirm', {
        **payload, 'version': preview['version'], 'confirm': True,
    })
    assert workspace.conn.execute('SELECT applied_at FROM applications').fetchone()[0] == expected


def test_relative_import_still_rejects_changed_workspace(profile_app, workspace, monkeypatch):
    from applyai.core import jobs

    reference = datetime(2026, 1, 2, tzinfo=timezone.utc)
    monkeypatch.setattr('applyai.dates.now', lambda: reference)
    payload = {'csv': 'company,title,applied_at\nSynthetic Employer,Historical Role,today\n'}
    preview = post(profile_app, workspace.conn, '/api/profile/import/preview', payload)
    jobs.upsert_job(
        workspace.conn, company='Synthetic Employer', title='Historical Role',
        url='https://example.invalid/job', source='manual', source_job_id='external',
    )
    reference += timedelta(seconds=10)
    with pytest.raises(APIError) as exc:
        post(profile_app, workspace.conn, '/api/profile/import/confirm', {
            **payload, 'version': preview['version'], 'confirm': True,
        })
    assert exc.value.status == 409
    assert workspace.conn.execute('SELECT COUNT(*) FROM applications').fetchone()[0] == 0


def test_large_loaded_configuration_remains_editable(profile_app, workspace):
    path = workspace.settings.config_dir / 'answers.yaml'
    path.write_text(yaml.safe_dump({
        'answers': [{'question': f'Synthetic question {i}?', 'answer': 'x' * 1000}
                    for i in range(60)],
    }))
    profile_app.reload_config()
    doc = document(profile_app, workspace.conn, 'answers')
    assert len(doc['text'].encode()) > 45_000
    edited = yaml.safe_load(doc['text'])
    edited['answers'][0]['answer'] = 'User revised answer'
    saved = save(profile_app, workspace.conn, doc, yaml.safe_dump(edited))
    assert yaml.safe_load(saved['text']) == edited
    assert profile_app.config.answers.answers[0].answer == 'User revised answer'
    with pytest.raises(APIError):
        post(profile_app, workspace.conn, '/api/profile/import/preview', {
            'csv': 'company,title\nSynthetic,' + 'x' * 45_000,
        })


def test_schedule_is_output_only_and_paid_requires_approval(profile_app, workspace, monkeypatch):
    monkeypatch.setattr('shutil.which', lambda _: '/usr/bin/uv')
    payload = {'format': 'systemd', 'times': ['07:00'], 'with_triage': False}
    result = post(profile_app, workspace.conn, '/api/profile/schedule', payload)
    assert result['installed'] is False
    assert 'OnCalendar=*-*-* 07:00:00' in result['text']
    assert '--batch' not in result['text']
    with pytest.raises(APIError):
        post(profile_app, workspace.conn, '/api/profile/schedule', {**payload, 'times': ['25:99']})
    with pytest.raises(APIError):
        post(profile_app, workspace.conn, '/api/profile/schedule', {**payload, 'with_triage': True})
    approved = post(profile_app, workspace.conn, '/api/profile/schedule', {
        **payload, 'with_triage': True, 'confirm_paid': True,
    })
    assert '--batch --yes' in approved['text']


def test_notifications_are_explicit_and_demo_blocked(profile_app, workspace, monkeypatch):
    from applyai.core import notify
    sent = []
    monkeypatch.setattr(notify, 'notifier_configured', lambda: 'ntfy')
    monkeypatch.setattr(notify, 'send_digest', lambda text: sent.append(text) or 'sent to ntfy topic synthetic-private-topic')
    profile_app.tasks = SimpleNamespace(start=lambda kind, work: {'kind': kind, 'result': work()})
    with pytest.raises(APIError):
        post(profile_app, workspace.conn, '/api/profile/notify', {})
    profile_app.settings = replace(profile_app.settings, demo=True)
    with pytest.raises(APIError):
        post(profile_app, workspace.conn, '/api/profile/notify', {'confirm': True})
    assert sent == []
    profile_app.settings = replace(profile_app.settings, demo=False)
    result = post(profile_app, workspace.conn, '/api/profile/notify', {'confirm': True})
    assert len(sent) == 1
    assert result['task']['result'] == {'sent': True, 'provider': 'ntfy'}
    assert 'synthetic-private-topic' not in json.dumps(result)
