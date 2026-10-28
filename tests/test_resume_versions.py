"""Consumer-visible candidate safety, immutable history and offline layout boundaries."""
from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from kitty.config import ResumeTermsFile
from kitty.core import applications, jobs, resumes, resume_versions as versions
from kitty.core.resume_render import freeze_html, render_pdf
from kitty.core.workspace import sync_config_to_db


HTML = '''<!DOCTYPE html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="resume.css"></head><body>
<h1>Synthetic Example</h1><h2>Skills</h2><p>Python, Rust</p>
<h2>Experience</h2><ul><li>Utilized Python for roughly 12 trials;
planned a sub-20 ms target, not achieved.</li></ul></body></html>'''
CSS = '@page { size: Letter; margin: 0.5in; } body { font: 11pt Arial; color: #111; }'


def _browser():
    if not (os.environ.get('KITTY_CHROMIUM') or any(shutil.which(n) for n in
            ('chromium', 'chromium-browser', 'google-chrome'))):
        pytest.skip('Offline Chromium is required for real candidate rendering')


@pytest.fixture
def space(workspace):
    _browser()
    workspace.track = next(t for t in workspace.config.tracks.tracks if t.default_resume)
    workspace.config = replace(workspace.config, resume_terms=ResumeTermsFile.model_validate({'groups': [
        {'name': 'languages', 'terms': [{'term': 'Python', 'aliases': ['Python']},
                                      {'term': 'Rust', 'aliases': ['Rust']}]}]}))
    workspace.source = workspace.settings.root / 'baseline.html'
    workspace.source.write_text(HTML)
    (workspace.settings.root / 'resume.css').write_text(CSS)
    snapshot_dir = workspace.settings.root / 'snapshot'
    snapshot_dir.mkdir()
    frozen, _ = freeze_html(HTML, workspace.source, snapshot_dir)
    snapshot = snapshot_dir / 'rendered.html'
    snapshot.write_text(frozen)
    baseline = workspace.settings.root / 'baseline.pdf'
    result = render_pdf(snapshot, baseline)
    assert result['valid'], result['errors']
    workspace.baseline = resumes.add_resume(workspace.conn, workspace.track.default_resume, baseline)
    workspace.baseline_bytes = baseline.read_bytes()
    for index in range(5):
        jobs.upsert_job(workspace.conn, company=f'Synthetic Employer {index}',
                       title='Engineering Intern', url=f'https://example.invalid/jobs/{index}',
                       jd_text='Rust required.' if index else 'Rust required. Python preferred.',
                       tracks=[workspace.track.name], eligibility='eligible')
    return workspace


def candidate(space, source=True):
    return versions.create_candidate(space.conn, space.config, space.settings, space.track.name,
                                     space.source if source else None)


def test_promotion_freezes_history_and_rollback_restores_original_after_config_sync(space):
    job_id = space.conn.execute('SELECT id FROM jobs ORDER BY id LIMIT 1').fetchone()[0]
    applied = applications.record_application(space.conn, job_id, resume_id=space.baseline.id,
                                              track=space.track.name)
    first = candidate(space, source=False)
    assert first['status'] == 'candidate' and first['validation']['valid']
    assert versions.default_resume(space.conn, space.config, space.track.name).id == space.baseline.id
    assert first['patches'] and first['resume_id'] is None
    promoted = versions.promote(space.conn, space.config, space.settings, first['id'])
    selected = versions.default_resume(space.conn, space.config, space.track.name)
    assert promoted['is_default'] and selected.id != space.baseline.id
    sync_config_to_db(space.conn, space.config)
    assert versions.default_resume(space.conn, space.config, space.track.name).id == selected.id
    assert space.conn.execute('SELECT resume_id FROM jobs WHERE id=?', (job_id,)).fetchone()[0] == space.baseline.id
    assert applications.get_application(space.conn, applied.id).resume_id == space.baseline.id
    frozen = resumes.get_resume(space.conn, space.baseline.id)
    assert Path(frozen.file_path).read_bytes() == space.baseline_bytes
    assert frozen.file_path != space.baseline.file_path
    # Re-registering an original path cannot redirect the historical frozen row.
    same = resumes.add_resume(space.conn, frozen.label, Path(space.baseline.file_path))
    assert same.file_path == frozen.file_path
    replacement = space.settings.root / 'replacement.pdf'
    replacement.write_bytes(b'changed content')
    for old in (frozen, selected):
        with pytest.raises(ValueError):
            resumes.add_resume(space.conn, old.label, replacement)
        with pytest.raises(ValueError):
            resumes.import_resume(space.conn, space.settings.root, replacement, old.label)
    restored = versions.rollback(space.conn, space.config, space.settings, space.track.name)
    assert restored['id'] == space.baseline.id
    assert versions.default_resume(space.conn, space.config, space.track.name).id == space.baseline.id
    assert versions.get_version(space.conn, space.settings, first['id'])['status'] == 'promoted'
    assert Path(selected.file_path).read_bytes() == Path(first['pdf_path']).read_bytes()
    with pytest.raises(ValueError):
        versions.rollback(space.conn, space.config, space.settings, space.track.name)


def test_registration_serializes_protection_with_concurrent_promotion(space, monkeypatch):
    item = candidate(space)
    other = sqlite3.connect(space.settings.db_path, isolation_level=None, timeout=0)
    other.row_factory = sqlite3.Row
    protection = resumes._protected
    attempted = []

    def promote_between_check_and_upsert(conn, resume_id):
        protected = protection(conn, resume_id)
        if conn is space.conn and resume_id == space.baseline.id:
            assert not protected
            # A second connection attempts the exact harmful interleaving,
            # without sleeps or thread-scheduling assumptions.
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                versions.promote(other, space.config, space.settings, item['id'])
            attempted.append(True)
        return protected

    try:
        with monkeypatch.context() as patch:
            patch.setattr(resumes, '_protected', promote_between_check_and_upsert)
            registered = resumes.add_resume(space.conn, space.baseline.label,
                                             Path(space.baseline.file_path))
        assert attempted == [True] and registered.id == space.baseline.id
        versions.promote(other, space.config, space.settings, item['id'])
        frozen = resumes.get_resume(space.conn, space.baseline.id)
        assert frozen.file_path != space.baseline.file_path
        assert Path(frozen.file_path).read_bytes() == space.baseline_bytes
        Path(space.baseline.file_path).write_bytes(b'changed after promotion')
        with pytest.raises(ValueError, match='historically selected'):
            resumes.add_resume(space.conn, space.baseline.label, Path(space.baseline.file_path))
        restored = versions.rollback(other, space.config, space.settings, space.track.name)
        assert restored['id'] == space.baseline.id
        selected = versions.default_resume(space.conn, space.config, space.track.name)
        assert selected.id == space.baseline.id
        assert Path(selected.file_path).read_bytes() == space.baseline_bytes
    finally:
        other.close()


def test_stale_default_source_styles_and_artifacts_block_promotion(space):
    first, second = candidate(space), candidate(space)
    versions.promote(space.conn, space.config, space.settings, first['id'])
    with pytest.raises(ValueError, match='default changed'):
        versions.promote(space.conn, space.config, space.settings, second['id'])
    versions.rollback(space.conn, space.config, space.settings, space.track.name)
    with pytest.raises(ValueError, match='default changed'):
        versions.promote(space.conn, space.config, space.settings, second['id'])
    current = candidate(space)
    space.source.write_text(HTML.replace('12 trials', '13 trials'))
    with pytest.raises(ValueError, match='source changed'):
        versions.promote(space.conn, space.config, space.settings, current['id'])
    space.source.write_text(HTML)
    stylesheet = space.settings.root / 'resume.css'
    stylesheet.write_text(CSS + ' h1 {color: black}')
    with pytest.raises(ValueError, match='stylesheet changed'):
        versions.promote(space.conn, space.config, space.settings, current['id'])
    stylesheet.write_text(CSS)
    pdf = Path(current['pdf_path'])
    pdf.chmod(0o644)
    pdf.write_bytes(b'altered PDF')
    assert not versions.get_version(space.conn, space.settings, current['id'])['validation']['valid']
    with pytest.raises(ValueError, match='artifact'):
        versions.promote(space.conn, space.config, space.settings, current['id'])


def test_rejected_candidate_stays_auditable_and_cannot_be_selected(space):
    item = candidate(space)
    hashes = dict(item['artifacts'])
    rejected = versions.reject(space.conn, space.settings, item['id'])
    assert rejected['status'] == 'rejected' and rejected['artifacts'] == hashes
    assert versions.default_resume(space.conn, space.config, space.track.name).id == space.baseline.id
    assert [v['id'] for v in versions.list_versions(space.conn, space.settings, space.track.name)] == [item['id']]
    with pytest.raises(ValueError):
        versions.promote(space.conn, space.config, space.settings, item['id'])


def test_nested_promotions_roll_back_without_rewriting_version_bytes(space):
    first = candidate(space)
    one = versions.promote(space.conn, space.config, space.settings, first['id'])
    # A known promoted source is inferred from its frozen, self-contained HTML.
    unchanged = candidate(space, source=False)
    assert unchanged['source_path'].endswith('/rendered.html')
    assert unchanged['validation']['valid']
    second = candidate(space)
    two = versions.promote(space.conn, space.config, space.settings, second['id'])
    assert two['resume_id'] != one['resume_id']
    assert versions.rollback(space.conn, space.config, space.settings, space.track.name)['id'] == one['resume_id']
    assert versions.rollback(space.conn, space.config, space.settings, space.track.name)['id'] == space.baseline.id
    for item in (one, two):
        stored = versions.get_version(space.conn, space.settings, item['id'])
        assert stored['status'] == 'promoted' and stored['validation']['valid']
        assert not stored['is_default']


def test_failed_layout_is_a_reviewable_candidate_not_a_default(space):
    (space.settings.root / 'resume.css').write_text(CSS + ' p, li {font-size:6pt}')
    item = candidate(space)
    assert item['status'] == 'candidate' and not item['validation']['valid']
    assert Path(item['pdf_path']).is_file()
    with pytest.raises(ValueError, match='validation'):
        versions.promote(space.conn, space.config, space.settings, item['id'])
    assert versions.default_resume(space.conn, space.config, space.track.name).id == space.baseline.id


def test_inferred_source_must_match_registered_pdf(space):
    space.source.write_text(HTML.replace('12 trials', '19 trials'))
    with pytest.raises(ValueError, match='does not match'):
        candidate(space, source=False)


def test_import_filename_collision_preserves_previously_registered_bytes(workspace):
    folder = workspace.settings.root / 'resumes'
    folder.mkdir()
    original = folder / 'resume.pdf'
    original.write_bytes(b'original')
    first = resumes.add_resume(workspace.conn, 'old', original)
    incoming = workspace.settings.root / 'incoming'
    incoming.mkdir()
    changed = incoming / original.name
    changed.write_bytes(b'new')
    second = resumes.import_resume(workspace.conn, workspace.settings.root, changed, 'new')
    assert Path(first.file_path).read_bytes() == b'original'
    assert Path(second.file_path).read_bytes() == b'new'
    assert first.file_path != second.file_path


@pytest.mark.parametrize('markup', [
    '<script>fetch("https://example.invalid")</script>',
    '<img src="file:///etc/passwd">',
    '<p onclick="alert(1)">text</p>',
    '<link rel="stylesheet" href="https://example.invalid/style.css">',
    '<style>body {background: url(file:///etc/passwd)}</style>',
    '<style>@import "https://example.invalid/style.css";</style>',
    r'<style>body {background: u\72l(https://example.invalid)}</style>',
    '<meta http-equiv="refresh" content="0;url=https://example.invalid">',
])
def test_unsafe_resume_never_reaches_browser(tmp_path, markup):
    with pytest.raises(ValueError):
        freeze_html(markup, tmp_path / 'source.html', tmp_path)


@pytest.mark.parametrize('style', [
    'li:first-child::before {content: "Planned: "}',
    'li:first-child:before { CoNtEnT /**/: "Planned: " !important }',
    r'li:first-child::before { c\6f ntent: "Planned: " }',
    'li::marker {content: attr(data-claim)}',
    'li::before {content: counter(claims)}',
    'li {counter-increment: claims}',
    '@counter-style claims {system: cyclic; symbols: "Planned: ";} li {list-style: claims}',
    'li {list-style-type: "Planned: "}',
])
def test_generated_claims_never_reach_renderer(tmp_path, style):
    markup = f'<html><head><style>{style}</style></head><body><ul><li>Rust work</li></ul></body></html>'
    with pytest.raises(ValueError):
        freeze_html(markup, tmp_path / 'source.html', tmp_path)


def test_positional_generated_qualifier_cannot_create_promotable_candidate(space):
    space.source.write_text(HTML.replace(
        '<h2>Experience</h2><ul><li>Utilized Python for roughly 12 trials;\n'
        'planned a sub-20 ms target, not achieved.</li>',
        '<h2>Projects</h2><ul><li>Built Python tools.</li><li>Built Rust tools.</li>'))
    # Rust demand would move Rust first, transferring "Planned" off Python.
    (space.settings.root / 'resume.css').write_text(
        CSS + ' li:first-child::before {content: "Planned: "}')
    with pytest.raises(ValueError, match='generated content'):
        candidate(space)
    assert versions.list_versions(space.conn, space.settings, space.track.name) == []
    assert versions.default_resume(space.conn, space.config, space.track.name).id == space.baseline.id


@pytest.mark.parametrize('style,text', [
    ('font-size: 6pt', 'Unreadably small text'),
    ('height: 8px; overflow: hidden', 'Clipped readable text'),
    ('position: absolute; left: -100px', 'Off-page text'),
    ('color: white', 'Invisible white text'),
    ('height: 1600px', 'Two-page content'),
])
def test_layout_blocks_small_clipped_hidden_or_multipage_output(tmp_path, style, text):
    _browser()
    source = tmp_path / 'source.html'
    source.write_text(f'<!DOCTYPE html><html><body><p style="{style}">{text}</p></body></html>')
    result = render_pdf(source, tmp_path / 'candidate.pdf')
    assert result['valid'] is False
    assert result['errors']


def test_flex_alignment_css_preserves_visible_claims(tmp_path):
    _browser()
    source = tmp_path / 'source.html'
    source.write_text(
        '<!DOCTYPE html><html><head><style>' + CSS +
        ' .row {display:flex;align-items:baseline;justify-content:space-between;gap:8px}'
        '</style></head><body><div class="row">'
        '<p>Built Python tools.</p><p>Built Rust tools.</p></div></body></html>')
    result = render_pdf(source, tmp_path / 'candidate.pdf')
    assert result['valid'], result['errors']
    assert 'Built Python tools.' in result['extracted_text']
    assert 'Built Rust tools.' in result['extracted_text']


def test_browser_that_exits_at_startup_reports_its_exit_status_and_stderr(tmp_path, monkeypatch):
    browser = tmp_path / 'chromium'
    browser.write_text('#!/bin/sh\necho "No usable sandbox! user namespaces are disabled" >&2\nexit 7\n')
    browser.chmod(0o755)
    monkeypatch.setenv('KITTY_CHROMIUM', str(browser))
    source = tmp_path / 'source.html'
    source.write_text('<!DOCTYPE html><html><body><p>Synthetic</p></body></html>')
    with pytest.raises(RuntimeError, match=r'exited with status 7.*No usable sandbox'):
        render_pdf(source, tmp_path / 'candidate.pdf')
