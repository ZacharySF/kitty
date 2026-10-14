"""Named, credential-free configuration editing and local workspace utilities.

Editors expose validated data, never raw files/comments. Hidden authentication
and source options survive saves. Each replacement requires the exact content
version reviewed by the owner and is written atomically on the same filesystem.
CSV approvals retain server-side resolved dates while rechecking workspace
conflicts; content-addressed filenames keep upload provenance distinct.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import yaml
from pydantic import ValidationError

from applyai.config import CONFIG_FILES
from applyai.core import doctor, importer, notify, questions, schedule, today
from applyai.db import connect

EDITABLE_SECTIONS = frozenset({
    'profile', 'tracks', 'sources', 'watchlist', 'answers', 'skills',
    'conferences', 'views', 'resume_terms', 'triage', 'apply',
})
SECTIONS = {name.removesuffix('.yaml'): model for name, model, _, _ in CONFIG_FILES
            if name.removesuffix('.yaml') in EDITABLE_SECTIONS}
MAX_TEXT = 45_000
BASIC_FIELDS = frozenset({
    'name', 'email', 'phone', 'school', 'major', 'standing', 'graduation',
    'home_city', 'home_state', 'country', 'degree',
})
BASIC_OPTIONAL_FIELDS = frozenset({'phone', 'country', 'degree'})
MAX_CONFIG_TEXT = 1_000_000
SOURCE_OPTIONS = frozenset({
    'search_queries', 'max_results_per_query', 'parallel_boards', 'sites',
    'results_wanted', 'hours_old', 'max_terms_per_track', 'max_terms_by_track',
    'locations', 'queries', 'results_per_page', 'max_pages', 'max_pages_per_query',
    'max_postings_per_board', 'allow_cities', 'boards', 'channels',
    'max_directory_rows', 'directory_urls', 'max_details', 'max_inactive_days',
    'enable_search', 'max_queries', 'max_results', 'seed_urls',
})
SENSITIVE = re.compile(r'password|passwd|secret|token|credential|authorization|cookie|api.?key|private.?key', re.I)


def _error(status: int, message: str):
    from applyai.web_server import APIError
    return APIError(status, message)


def _keys(data: dict, allowed: set[str]) -> None:
    if not isinstance(data, dict) or set(data) - allowed:
        raise _error(400, 'Unknown request field')


def _confirm(body: dict) -> None:
    if body.get('confirm') is not True:
        raise _error(400, 'Explicit confirmation is required')


def _text(value: Any, label: str, *, maximum: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or '\x00' in value:
        raise _error(400, f'{label} must be text without null characters')
    try:
        if len(value.encode('utf-8')) > maximum:
            raise _error(400, f'{label} is too large')
    except UnicodeError:
        raise _error(400, f'{label} must be valid Unicode') from None
    return value


def _revision(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise _error(400, 'A valid content version is required')
    return value


def _section(value: Any) -> str:
    if not isinstance(value, str) or value not in SECTIONS:
        raise _error(400, 'Unknown configuration section')
    return value


def _path(app, section: str) -> Path:
    path = app.settings.config_dir / f'{_section(section)}.yaml'
    if path.is_symlink():
        raise _error(409, 'Symbolic-link configuration cannot be edited on the website')
    return path


def _raw(path: Path) -> bytes | None:
    try:
        if path.stat().st_size > MAX_CONFIG_TEXT or not path.is_file():
            raise _error(400, 'Configuration file is unavailable or too large')
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        raise _error(409, 'Configuration file cannot be read') from None


def _version(raw: bytes | None) -> str:
    return hashlib.sha256(b'missing' if raw is None else b'file:' + raw).hexdigest()


def _validate(section: str, data: Any):
    try:
        return SECTIONS[section].model_validate(data)
    except ValidationError as exc:
        # Never echo rejected values or validator messages containing them.
        problems = ', '.join(
            f"{'.'.join(str(x) for x in item['loc']) or section} ({item['type']})"
            for item in exc.errors(include_input=False)[:10]
        )
        raise _error(400, f'Invalid configuration: {problems}') from None


def _parse(text: str) -> Any:
    try:
        # Aliases are unnecessary for these small editor documents; reject them
        # to avoid recursive graphs and alias-expansion resource exhaustion.
        if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(text)):
            raise _error(400, 'YAML aliases are not supported in the editor')
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        suffix = f' near line {mark.line + 1}' if mark else ''
        raise _error(400, 'Invalid YAML' + suffix) from None


def _current(app, section: str, raw: bytes | None) -> dict:
    if raw is None:
        return getattr(app.config, section).model_dump(mode='json')
    try:
        data = _parse(raw.decode('utf-8'))
    except UnicodeError:
        raise _error(400, 'Configuration must be UTF-8') from None
    return _validate(section, data).model_dump(mode='json')


def _public_value(value: Any) -> bool:
    if isinstance(value, dict):
        return all(isinstance(k, str) and not SENSITIVE.search(k) and _public_value(v)
                   for k, v in value.items())
    if isinstance(value, list):
        return all(_public_value(item) for item in value)
    if isinstance(value, str):
        if re.search(r'(?i)(?:bearer\s+|-----BEGIN .*PRIVATE KEY|(?:password|token|secret|api_key)\s*[:=])', value):
            return False
        if '://' in value:
            try:
                parsed = urlsplit(value)
                return not parsed.username and not parsed.password and not any(
                    SENSITIVE.search(k) for k, _ in parse_qsl(parsed.query)
                )
            except ValueError:
                return False
    return True


def _visible(section: str, data: dict) -> dict:
    # JSON cloning keeps the stored values untouched while removing hidden data.
    result = json.loads(json.dumps(data))
    if section == 'apply':
        result.pop('authentication', None)
    if section == 'sources':
        for source in result['sources'].values():
            source['options'] = {k: v for k, v in source['options'].items()
                                 if k in SOURCE_OPTIONS and _public_value(v)}
    return result


def _document(app, section: str) -> dict:
    raw = _raw(_path(app, section))
    data = _visible(section, _current(app, section, raw))
    return {'section': section, 'version': _version(raw),
            'text': yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
            'notice': 'Authentication and secret/unrecognized source options are hidden and preserved. Comments are not included in this normalized editor.'}


def _basics(app) -> dict:
    raw = _raw(_path(app, 'profile'))
    data = _current(app, 'profile', raw)
    return {'version': _version(raw),
            'fields': {field: data[field] if data[field] is not None else ''
                       for field in sorted(BASIC_FIELDS)}}


def _replace(path: Path, content: bytes) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix='.web-config-', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _save(app, section: str, data: dict, version: str) -> dict:
    path = _path(app, section)
    original = _raw(path)
    if not hmac.compare_digest(_version(original), version):
        raise _error(409, 'Configuration changed since it was loaded. Reload and review before saving.')
    current = _current(app, section, original)
    if not isinstance(data, dict):
        raise _error(400, 'Configuration must be a YAML mapping')
    if section == 'apply':
        if 'authentication' in data:
            raise _error(400, 'Authentication settings cannot be edited here')
        data['authentication'] = current['authentication']
    if section == 'sources':
        # Validate structure before traversing free-form source options.
        data = _validate(section, data).model_dump(mode='json')
        for name, source in data['sources'].items():
            if any(k not in SOURCE_OPTIONS or not _public_value(v)
                   for k, v in source['options'].items()):
                raise _error(400, 'Only public, allowlisted source options can be edited here')
            old_options = current['sources'].get(name, {}).get('options', {})
            hidden = {k: v for k, v in old_options.items()
                      if k not in SOURCE_OPTIONS or not _public_value(v)}
            if hidden.keys() & source['options'].keys():
                raise _error(400, 'A hidden source option cannot be overwritten here')
            source['options'].update(hidden)
        # Removing a source also deletes its credentials, so require local editing.
        for name in current['sources'].keys() - data['sources'].keys():
            options = current['sources'][name]['options']
            if any(k not in SOURCE_OPTIONS or not _public_value(v) for k, v in options.items()):
                raise _error(400, 'Sources with hidden options cannot be removed here')
    parsed = _validate(section, data)
    content = yaml.safe_dump(parsed.model_dump(mode='json'), sort_keys=False, allow_unicode=True).encode('utf-8')
    if _version(_raw(path)) != version:
        raise _error(409, 'Configuration changed during editing. Reload before saving.')
    try:
        _replace(path, content)
        app.reload_config()
    except Exception as exc:
        # A failed reload is not a successful save. Restore the original atomically.
        if original is None:
            path.unlink(missing_ok=True)
        else:
            _replace(path, original)
        raise _error(409, f'Configuration save failed ({type(exc).__name__}); original restored') from None
    return _document(app, section)


def set_source_enabled(app, name: str, enabled: bool) -> dict:
    """Called under the website mutation lock after explicit confirmation."""
    if not isinstance(name, str) or type(enabled) is not bool:
        raise _error(400, 'A source name and boolean enabled value are required')
    doc = _document(app, 'sources')
    data = _parse(doc['text'])
    if name not in data['sources']:
        raise _error(404, 'Unknown source')
    data['sources'][name]['enabled'] = enabled
    _save(app, 'sources', data, doc['version'])
    return {'name': name, 'enabled': enabled}


def _import_plan(app, conn, body: dict, *, approved_dates: tuple[str | None, ...] | None = None):
    text = _text(body.get('csv'), 'CSV')
    mapping = body.get('mapping', {})
    if not isinstance(mapping, dict) or set(mapping) - importer.DEFAULT_ALIASES.keys() or any(
        not isinstance(v, str) or not v or len(v) > 200 for v in mapping.values()
    ):
        raise _error(400, 'Mapping must map recognized import fields to CSV column names')
    try:
        headers = next(csv.reader(io.StringIO(text)), [])
        if not headers or any(column not in headers for column in mapping.values()):
            raise _error(400, 'CSV header is missing or a mapped column does not exist')
        with TemporaryDirectory(prefix='applyai-web-import-') as temporary:
            path = Path(temporary) / f'uploaded-{hashlib.sha256(text.encode("utf-8")).hexdigest()}.csv'
            path.write_text(text, encoding='utf-8')
            plan = importer.plan_import(conn, path, overrides=mapping, valid_tracks=app.config.track_names)
        if len(plan.rows) > 1000:
            raise _error(400, 'Import at most 1000 CSV rows at a time')
    except (csv.Error, ValueError, TypeError):
        raise _error(400, 'CSV could not be parsed; check its columns and dates') from None
    # Dates come only from a server-retained, signed preview, never the client.
    # Replanning still checks current workspace actions and all other row data.
    if approved_dates is not None and len(approved_dates) == len(plan.rows):
        for row, applied_at in zip(plan.rows, approved_dates):
            row.applied_at = applied_at
    result = {'mapping': plan.mapping, 'unmapped_columns': plan.unmapped_columns,
              'rows': [asdict(row) for row in plan.rows],
              'counts': {key: sum(row.action == key and row.ok for row in plan.rows)
                         for key in ('create', 'update', 'skip')}}
    result['counts']['problems'] = len(plan.problems)
    if not hasattr(app, '_profile_import_key'):
        app._profile_import_key = secrets.token_bytes(32)
    digest = json.dumps({'csv': text, 'mapping': mapping, 'preview': result,
                         'tracks': app.config.track_names}, sort_keys=True).encode()
    result['version'] = hmac.new(app._profile_import_key, digest, hashlib.sha256).hexdigest()
    return plan, result


def get(app, conn, path: str, query: dict) -> dict | None:
    if path not in {'/api/profile', '/api/profile/basics', '/api/profile/config', '/api/profile/doctor'}:
        return None
    _keys(query, {'section'} if path.endswith('/config') else set())
    if path.endswith('/basics'):
        return _basics(app)
    if path.endswith('/config'):
        return _document(app, _section(query.get('section')))
    if path.endswith('/doctor'):
        # Config validation messages can include arbitrary user-provided values;
        # replace failed details rather than ever echoing a potential secret.
        checks = []
        try:
            for check in doctor.run_all(app.settings, conn):
                item = asdict(check)
                if item['name'].startswith('config/') and not item['ok']:
                    item['detail'] = 'Invalid configuration; review the named section locally.'
                    item['fix'] = 'Correct the file using its public example schema.'
                checks.append(item)
        except Exception as exc:
            raise _error(409, f'Local diagnostics failed ({type(exc).__name__})') from None
        return {'checks': checks}
    return {'sections': list(SECTIONS), 'profile': app.config.profile.model_dump(mode='json'),
            'tracks': [{'name': t.name, 'weekly_target': t.weekly_target,
                        'default_resume': t.default_resume} for t in app.config.tracks.tracks],
            'questions': [{'id': q.id, 'text': q.text, 'times_seen': q.times_seen}
                          for q in questions.list_questions(conn)],
            'answers_count': len(app.config.answers.answers),
            'notifier': notify.notifier_configured(),
            'triage_cap': app.config.triage.max_cost_usd_per_run}


def post(app, conn, path: str, body: dict) -> dict | None:
    if path == '/api/profile/basics':
        _keys(body, {'fields', 'version', 'confirm'})
        _confirm(body)
        version = _revision(body.get('version'))
        fields = body.get('fields')
        _keys(fields, BASIC_FIELDS)
        current = _current(app, 'profile', _raw(_path(app, 'profile')))
        for field, value in fields.items():
            value = _text(value, field, maximum=5000)
            current[field] = None if field in BASIC_OPTIONAL_FIELDS and value == '' else value
        _save(app, 'profile', current, version)
        return _basics(app)
    if path == '/api/profile/config':
        _keys(body, {'section', 'text', 'version', 'confirm'})
        _confirm(body)
        section = _section(body.get('section'))
        saved = _save(app, section, _parse(_text(body.get('text'), 'YAML', maximum=MAX_CONFIG_TEXT)),
                      _revision(body.get('version')))
        if section == 'answers':
            questions.refresh_answered_flags(conn, app.config)
        return saved
    if path == '/api/profile/answer':
        _keys(body, {'question', 'answer', 'version', 'confirm'})
        _confirm(body)
        question = _text(body.get('question'), 'Question', maximum=5000).strip()
        answer = _text(body.get('answer'), 'Answer', maximum=10000).strip()
        if not question or not answer:
            raise _error(400, 'Write both a question and your own answer')
        doc = _document(app, 'answers')
        data = _parse(doc['text'])
        from applyai.normalize import norm_question
        if any(norm_question(item['question']) == norm_question(question) for item in data['answers']):
            raise _error(409, 'This question already has an answer; edit the answers section to change it')
        data['answers'].append({'question': question, 'answer': answer})
        saved = _save(app, 'answers', data, _revision(body.get('version')))
        questions.refresh_answered_flags(conn, app.config)
        return saved
    if path in {'/api/profile/import/preview', '/api/profile/import/confirm'}:
        confirmed = path.endswith('/confirm')
        _keys(body, {'csv', 'mapping', 'version', 'confirm'} if confirmed else {'csv', 'mapping'})
        if not hasattr(app, '_profile_import_dates'):
            app._profile_import_dates = {}
        approved_dates = None
        if confirmed:
            _confirm(body)
            version = _revision(body.get('version'))
            approved_dates = app._profile_import_dates.get(version)
            if approved_dates is None:
                raise _error(409, 'Import preview expired. Preview again before importing.')
        plan, result = _import_plan(app, conn, body, approved_dates=approved_dates)
        if not confirmed:
            # Bound memory without exposing or trusting resolved dates in requests.
            if len(app._profile_import_dates) >= 128:
                del app._profile_import_dates[next(iter(app._profile_import_dates))]
            app._profile_import_dates[result['version']] = tuple(row.applied_at for row in plan.rows)
            return result
        if not hmac.compare_digest(version, result['version']):
            raise _error(409, 'CSV, mapping or workspace changed. Preview again before importing.')
        return {'stats': importer.apply_import(conn, plan), 'problems_skipped': len(plan.problems)}
    if path == '/api/profile/schedule':
        _keys(body, {'format', 'times', 'with_triage', 'confirm_paid'})
        renderers = {'home-manager': schedule.home_manager, 'systemd': schedule.systemd_units,
                     'crontab': schedule.crontab}
        format_name = body.get('format')
        if not isinstance(format_name, str) or format_name not in renderers:
            raise _error(400, 'Choose home-manager, systemd or crontab')
        times = body.get('times', list(schedule.DEFAULT_TIMES))
        if not isinstance(times, list) or not 1 <= len(times) <= 12 or any(
            not isinstance(t, str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', t) for t in times
        ):
            raise _error(400, 'Supply 1–12 local times in HH:MM format')
        paid = body.get('with_triage', False)
        if type(paid) is not bool:
            raise _error(400, 'with_triage must be a boolean')
        if paid and body.get('confirm_paid') is not True:
            raise _error(400, 'A paid schedule requires explicit cost approval')
        # Core output embeds the owner-selected workspace and executable paths.
        # Reject shell/Nix interpolation instead of returning unsafe instructions.
        import shutil
        for value in (str(app.settings.root), shutil.which('uv') or 'uv'):
            if not re.fullmatch(r'[A-Za-z0-9_./-]+', value):
                raise _error(400, 'Schedule output requires a workspace and uv path without shell metacharacters or spaces')
        return {'format': format_name, 'text': renderers[format_name](
            app.settings.root, tuple(times), with_triage=paid), 'installed': False,
            'triage_cap': app.config.triage.max_cost_usd_per_run}
    if path == '/api/profile/notify':
        _keys(body, {'confirm'})
        _confirm(body)
        if app.settings.demo:
            raise _error(403, 'Notifications are disabled in the demo workspace')
        kind = notify.notifier_configured()
        if kind is None:
            raise _error(409, 'No notification provider is configured locally')
        settings, config = app.settings, app.config

        def work():
            with closing(connect(settings.db_path)) as owned:
                text = today.digest_text(today.build_digest(owned, config))
            result = notify.send_digest(text)
            if not result or not result.startswith('sent to '):
                raise RuntimeError('Notification delivery failed; check the local provider configuration')
            # Core success/error strings may include private topics or chat IDs.
            return {'sent': True, 'provider': kind}

        return {'task': app.tasks.start('profile-notify', work)}
    return None
