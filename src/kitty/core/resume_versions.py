"""Evidence-backed candidates; only explicit promotion changes future defaults."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from html.parser import HTMLParser

from kitty.core import resumes
from kitty.core.resume_render import freeze_html, render_pdf
from kitty.dates import now_iso
from kitty.models import Resume


@contextmanager
def _atomic(conn):
    # Savepoints compose with callers that already own a transaction.
    conn.execute('SAVEPOINT resume_decision')
    try:
        yield
    except BaseException:
        conn.execute('ROLLBACK TO resume_decision')
        conn.execute('RELEASE resume_decision')
        raise
    else:
        conn.execute('RELEASE resume_decision')


def _path(settings, path) -> Path:
    path = Path(path).expanduser()
    return (path if path.is_absolute() else settings.root / path).resolve()


def default_resume(conn, config, track: str) -> Resume | None:
    """An explicit override wins over YAML, without touching previous choices."""
    row = conn.execute('SELECT resume_id FROM resume_default_overrides WHERE track=?', (track,)).fetchone()
    if row is not None:
        return resumes.get_resume(conn, row['resume_id']) if row['resume_id'] is not None else None
    configured = config.track(track)
    return resumes.get_resume_by_label(conn, configured.default_resume) if configured and configured.default_resume else None


def _root(settings) -> Path:
    # Keep demo/alternate database version stores separate as well as gitignored.
    key = hashlib.sha256(str(settings.db_path.resolve()).encode()).hexdigest()[:16]
    return settings.data_dir / 'resume_versions' / key


def _selected_state(conn, config, settings, track) -> dict:
    selected = default_resume(conn, config, track)
    override = conn.execute('SELECT history_id FROM resume_default_overrides WHERE track=?', (track,)).fetchone()
    if selected:
        path = _path(settings, selected.file_path)
        if not path.is_file() or resumes.file_hash(path) != selected.file_hash:
            raise ValueError('Default resume bytes changed or are missing; review and register the source before improving')
    event = conn.execute("SELECT MAX(id) FROM resume_version_history WHERE track=? AND action!='reject'",
                         (track,)).fetchone()[0]
    return {'resume_id': selected.id if selected else None,
            'file_hash': selected.file_hash if selected else None,
            'history_id': override['history_id'] if override else None, 'decision_id': event}


def _matches_pdf(source: Path, pdf: Path) -> bool:
    """Filenames alone do not establish that an inferred source is the resume."""
    from pypdf import PdfReader

    class Text(HTMLParser):
        def __init__(self):
            super().__init__()
            self.body = False
            self.style = False
            self.parts = []

        def handle_starttag(self, tag, attrs):
            if tag == 'body':
                self.body = True
            if tag in {'style', 'script'}:
                self.style = True

        def handle_endtag(self, tag):
            if tag == 'body':
                self.body = False
            if tag in {'style', 'script'}:
                self.style = False

        def handle_data(self, data):
            if self.body and not self.style:
                self.parts.append(data)

    def normalized(text):
        return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold() if c.isalnum())

    parser = Text()
    parser.feed(source.read_text(encoding='utf-8'))
    try:
        text = ''.join(page.extract_text() or '' for page in PdfReader(pdf).pages)
    except Exception:
        return False
    original = normalized(''.join(parser.parts))
    return bool(original) and original == normalized(text)


def _source(conn, settings, selected, explicit) -> Path:
    if explicit is not None:
        source = _path(settings, explicit)
    elif selected:
        stored = conn.execute('SELECT manifest_path, manifest_hash FROM resume_versions WHERE resume_id=?',
                              (selected.id,)).fetchone()
        if stored:
            manifest = Path(stored['manifest_path'])
            if resumes.file_hash(manifest) != stored['manifest_hash']:
                raise ValueError('Stored source manifest changed; restore the immutable artifacts')
            data = json.loads(manifest.read_text(encoding='utf-8'))
            if _integrity(data):
                raise ValueError('Stored source artifacts changed; restore the immutable artifacts')
            source = manifest.parent / 'rendered.html'
        else:
            frozen = conn.execute('SELECT original_path FROM resume_artifacts WHERE resume_id=?',
                                  (selected.id,)).fetchone()
            pdf = _path(settings, frozen['original_path'] if frozen else selected.file_path)
            matches = {p.resolve() for p in (pdf.with_suffix('.html'), pdf.parent / 'src' / (pdf.stem + '.html'))
                       if p.is_file()}
            if len(matches) != 1:
                raise ValueError('Cannot infer an unambiguous resume HTML source; pass --source PATH')
            source = matches.pop()
            if not _matches_pdf(source, _path(settings, selected.file_path)):
                raise ValueError('Inferred HTML does not match registered PDF text; explicitly select a reviewed --source PATH')
    else:
        raise ValueError('No registered default source; pass --source PATH')
    if source.suffix.lower() not in {'.html', '.htm'} or not source.is_file():
        raise ValueError('Resume editing requires an existing local HTML source; pass --source PATH')
    return source


def _seal(folder: Path) -> None:
    for path in folder.iterdir():
        if path.is_file():
            path.chmod(0o444)
    folder.chmod(0o500)


def create_candidate(conn, config, settings, track: str, source: Path | None = None,
                     *, min_postings: int = 5) -> dict:
    """Freeze an evidence-backed proposal and render only its PDF; never select it."""
    from kitty.core.resume_editor import propose

    if config.track(track) is None:
        raise ValueError(f'Unknown track: {track}')
    baseline = _selected_state(conn, config, settings, track)
    source = _source(conn, settings, default_resume(conn, config, track), source)
    proposal = propose(conn, config, settings, track, source, min_postings=min_postings)
    original = source.read_bytes()
    if hashlib.sha256(original).hexdigest() != proposal['source_hash']:
        raise ValueError('Resume source changed while proposing; generate a fresh candidate')
    if original.decode('utf-8') != proposal['original_html']:
        raise ValueError('Proposal source does not match the current source bytes')
    version_id = str(uuid.uuid4())
    folder = _root(settings) / version_id
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        snapshot, resources = freeze_html(proposal['candidate_html'], source, folder)
        (folder / 'source.html').write_bytes(original)
        (folder / 'candidate.html').write_text(proposal['candidate_html'], encoding='utf-8')
        (folder / 'rendered.html').write_text(snapshot, encoding='utf-8')
        pdf = folder / 'candidate.pdf'
        try:
            validation = render_pdf(folder / 'rendered.html', pdf)
        except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
            validation = {'valid': False, 'errors': [str(exc)], 'offline': True}
        artifacts = {path.name: resumes.file_hash(path) for path in folder.iterdir() if path.is_file()}
        manifest = {**proposal, 'id': version_id, 'track': track, 'source_path': str(source),
                    'created_at': now_iso(), 'pdf_path': str(pdf),
                    'manifest_path': str(folder / 'manifest.json'), 'validation': validation,
                    'resources': resources, 'artifacts': artifacts, 'baseline': baseline}
        manifest_path = folder / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        conn.execute('''INSERT INTO resume_versions
            (id,track,status,source_path,source_hash,pdf_path,manifest_path,manifest_hash,created_at)
            VALUES (?,?,'candidate',?,?,?,?,?,?)''',
            (version_id, track, str(source), proposal['source_hash'], str(pdf), str(manifest_path),
             resumes.file_hash(manifest_path), manifest['created_at']))
        _seal(folder)
    except BaseException:
        # Only this newly allocated UUID directory belongs to this operation.
        if not conn.execute('SELECT 1 FROM resume_versions WHERE id=?', (version_id,)).fetchone():
            shutil.rmtree(folder)
        raise
    return get_version(conn, settings, version_id)


def _load(conn, version_id):
    row = conn.execute('SELECT * FROM resume_versions WHERE id=?', (version_id,)).fetchone()
    if row is None:
        raise ValueError(f'Unknown resume version: {version_id}')
    path = Path(row['manifest_path'])
    if not path.is_file() or resumes.file_hash(path) != row['manifest_hash']:
        raise ValueError('Resume manifest is missing or changed; immutable audit integrity failed')
    return row, json.loads(path.read_text(encoding='utf-8'))


def _integrity(manifest: dict) -> list[str]:
    folder = Path(manifest['manifest_path']).parent
    errors = []
    for name, digest in manifest['artifacts'].items():
        path = folder / name
        if not path.is_file() or resumes.file_hash(path) != digest:
            errors.append(f'Immutable artifact missing or changed: {name}')
    return errors


def get_version(conn, settings, version_id: str) -> dict:
    row, manifest = _load(conn, version_id)
    result = {**manifest, 'status': row['status'], 'resume_id': row['resume_id']}
    errors = _integrity(manifest)
    result['validation'] = {**manifest['validation'],
                            'valid': bool(manifest['validation']['valid']) and not errors,
                            'errors': [*manifest['validation'].get('errors', []), *errors]}
    result['history'] = [dict(event) for event in conn.execute(
        'SELECT * FROM resume_version_history WHERE track=? ORDER BY id', (row['track'],))]
    result['is_default'] = bool(row['resume_id'] is not None and conn.execute(
        'SELECT 1 FROM resume_default_overrides WHERE track=? AND resume_id=?',
        (row['track'], row['resume_id'])).fetchone())
    return result


def list_versions(conn, settings, track: str | None = None) -> list[dict]:
    rows = conn.execute('SELECT id FROM resume_versions' + (' WHERE track=?' if track else '') +
                        ' ORDER BY created_at DESC, rowid DESC', (track,) if track else ())
    return [get_version(conn, settings, row['id']) for row in rows.fetchall()]


def _freeze_resume(conn, settings, resume: Resume | None) -> None:
    if resume is None:
        return
    stored = conn.execute('SELECT * FROM resume_artifacts WHERE resume_id=?', (resume.id,)).fetchone()
    if stored:
        if not Path(stored['file_path']).is_file() or resumes.file_hash(Path(stored['file_path'])) != stored['file_hash']:
            raise ValueError('Previously frozen resume bytes changed; cannot change defaults')
        return
    original = _path(settings, resume.file_path)
    raw = original.read_bytes()
    if hashlib.sha256(raw).hexdigest() != resume.file_hash:
        raise ValueError('Baseline resume changed; generate a fresh candidate after reviewing it')
    folder = _root(settings) / 'baselines'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / f'{resume.id}-{resume.file_hash}{original.suffix}'
    if path.exists():
        if resumes.file_hash(path) != resume.file_hash:
            raise ValueError('Frozen baseline hash mismatch')
    else:
        with path.open('xb') as handle:
            handle.write(raw)
        path.chmod(0o444)
    conn.execute('INSERT INTO resume_artifacts VALUES (?,?,?,?)',
                 (resume.id, str(path), resume.file_hash, str(original)))
    # Preserve the existing identity and byte content, including all historical
    # job/application references. Only its storage location becomes immutable.
    conn.execute('UPDATE resumes SET file_path=? WHERE id=?', (str(path), resume.id))


def promote(conn, config, settings, version_id: str) -> dict:
    with _atomic(conn):
        # Acquire SQLite's write lock before reading the compare-and-swap state.
        conn.execute('UPDATE resume_versions SET status=status WHERE id=?', (version_id,))
        row, manifest = _load(conn, version_id)
        if row['status'] != 'candidate':
            raise ValueError('Only a pending candidate can be promoted')
        errors = _integrity(manifest)
        if not manifest['validation']['valid'] or errors:
            raise ValueError('Candidate failed validation: ' + '; '.join(errors or manifest['validation']['errors']))
        if not manifest.get('patches'):
            raise ValueError('Candidate contains no evidence-backed changes to promote')
        source = Path(row['source_path'])
        if not source.is_file() or resumes.file_hash(source) != row['source_hash']:
            raise ValueError('Resume source changed since review; generate a fresh candidate')
        for resource in manifest['resources']:
            path = Path(resource['source_path'])
            if not path.is_file() or resumes.file_hash(path) != resource['hash']:
                raise ValueError('Resume stylesheet changed since review; generate a fresh candidate')
        current = _selected_state(conn, config, settings, row['track'])
        if current != manifest['baseline']:
            raise ValueError('Track default changed since candidate creation; generate a fresh candidate')
        previous = default_resume(conn, config, row['track'])
        _freeze_resume(conn, settings, previous)
        selected = resumes.add_resume(conn, f"{row['track']}@{version_id}", Path(row['pdf_path']),
                                      description='Reviewed local evidence-backed resume version')
        if selected.file_hash != manifest['artifacts'].get('candidate.pdf'):
            raise ValueError('Candidate PDF changed during promotion')
        conn.execute('INSERT INTO resume_artifacts VALUES (?,?,?,?)',
                     (selected.id, selected.file_path, selected.file_hash, row['pdf_path']))
        conn.execute("UPDATE resume_versions SET status='promoted',resume_id=? WHERE id=?", (selected.id, version_id))
        cursor = conn.execute('''INSERT INTO resume_version_history
            (track,action,version_id,from_resume_id,to_resume_id,previous_history_id,created_at)
            VALUES (?,'promote',?,?,?,?,?)''',
            (row['track'], version_id, current['resume_id'], selected.id, current['history_id'], now_iso()))
        conn.execute('''INSERT INTO resume_default_overrides VALUES (?,?,?)
            ON CONFLICT(track) DO UPDATE SET resume_id=excluded.resume_id,history_id=excluded.history_id''',
            (row['track'], selected.id, cursor.lastrowid))
    return get_version(conn, settings, version_id)


def reject(conn, settings, version_id: str) -> dict:
    with _atomic(conn):
        conn.execute('UPDATE resume_versions SET status=status WHERE id=?', (version_id,))
        row, _ = _load(conn, version_id)
        if row['status'] != 'candidate':
            raise ValueError('Only a pending candidate can be rejected')
        conn.execute("UPDATE resume_versions SET status='rejected' WHERE id=? AND status='candidate'", (version_id,))
        conn.execute('''INSERT INTO resume_version_history (track,action,version_id,created_at)
                        VALUES (?,'reject',?,?)''', (row['track'], version_id, now_iso()))
    return get_version(conn, settings, version_id)


def rollback(conn, config, settings, track: str) -> dict:
    with _atomic(conn):
        conn.execute('UPDATE resume_default_overrides SET resume_id=resume_id WHERE track=?', (track,))
        override = conn.execute('SELECT * FROM resume_default_overrides WHERE track=?', (track,)).fetchone()
        if override is None or override['history_id'] is None:
            raise ValueError('No prior promoted default to roll back to')
        event = conn.execute('SELECT * FROM resume_version_history WHERE id=?', (override['history_id'],)).fetchone()
        selected = resumes.get_resume(conn, event['from_resume_id']) if event['from_resume_id'] is not None else None
        _freeze_resume(conn, settings, selected)
        conn.execute('UPDATE resume_default_overrides SET resume_id=?,history_id=? WHERE track=?',
                     (event['from_resume_id'], event['previous_history_id'], track))
        conn.execute('''INSERT INTO resume_version_history
            (track,action,version_id,from_resume_id,to_resume_id,previous_history_id,created_at)
            VALUES (?,'rollback',?,?,?,?,?)''',
            (track, event['version_id'], override['resume_id'], event['from_resume_id'], override['history_id'], now_iso()))
    return {'track': track, 'id': selected.id if selected else None,
            'label': selected.label if selected else None}
