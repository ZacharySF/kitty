"""Offline, revision-addressed rendering and immutable library registration.

Compiling, the bubblewrap sandbox, PDF inspection and rasterizing run in the Rust
``kitty-workshop`` engine (workshop-engine/). This module prepares job folders from saved
snapshots, keys and locks the preview cache, re-verifies the engine's artifacts and
publishes them, and registers exports in the resume library.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from kitty.dates import now_iso
from . import store
from .store import WorkshopError

#: A source checkout's engine build; installed apps set KITTY_WORKSHOP to the bundled sidecar.
_DEVELOPMENT_ENGINE = Path(__file__).resolve().parents[4] / 'workshop-engine' / 'target' / 'release' / 'kitty-workshop'
_MISSING_ENGINE = ('The Rust workshop engine (kitty-workshop) is missing; build workshop-engine '
                   '(cargo build --release) or set KITTY_WORKSHOP to its absolute path')
_ENGINE_IDS = (('chromium', 'HTML / Chromium'), ('typst', 'Typst'), ('tectonic', 'LaTeX / Tectonic'),
               ('pdflatex', 'pdfLaTeX'), ('lualatex', 'LuaLaTeX'))
_PAGE = re.compile(r'page-[1-9][0-9]?\.png')
#: Three sandbox stages of at most 95 s each, plus start-up.
COMPILE_TIMEOUT = 300


def engine_path():
    """The engine binary. A configured but unusable KITTY_WORKSHOP never falls back."""
    configured = os.environ.get('KITTY_WORKSHOP', '').strip()
    path = Path(configured) if configured else _DEVELOPMENT_ENGINE
    if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
        return path
    return None


def run_engine(command, payload=None, *, timeout=60):
    """One engine command: JSON request on stdin, ``{"ok": ..., ...}`` reply on stdout."""
    binary = engine_path()
    if binary is None:
        raise WorkshopError(_MISSING_ENGINE, 503)
    try:
        completed = subprocess.run([str(binary), command], input=json.dumps(payload or {}).encode(),
                                   capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise WorkshopError('Workshop engine exceeded its time limit', 422) from exc
    except OSError as exc:
        raise WorkshopError(f'Workshop engine could not start: {exc}', 503) from exc
    try:
        reply = json.loads(completed.stdout)
    except ValueError:
        reply = None
    if completed.returncode or not isinstance(reply, dict):
        detail = completed.stderr.decode('utf-8', errors='replace')[-2000:]
        raise WorkshopError('Workshop engine failed: ' + (detail or f'exit status {completed.returncode}'), 500)
    if not reply.get('ok'):
        raise WorkshopError(str(reply.get('error') or 'Workshop engine refused the request'), int(reply.get('status') or 500))
    return reply['result']


def capabilities(settings):
    try:
        return run_engine('capabilities')
    except WorkshopError as exc:
        return {'engines': [{'id': name, 'label': label, 'available': False, 'reason': str(exc), 'runtime': None}
                            for name, label in _ENGINE_IDS],
                'formats': ['html', 'typst', 'latex'], 'sandbox': False,
                'ocr': {'available': False, 'reason': str(exc)},
                'imports': ['source', 'zip', 'pdf', 'docx', 'markdown', 'json-resume']}


def preferred_engine(settings, fmt):
    """The engine a new document should use: an available one, else the format default."""
    available = {item['id'] for item in capabilities(settings)['engines'] if item['available']}
    order = {'latex': ('tectonic', 'pdflatex', 'lualatex')}.get(fmt, (store.FORMATS[fmt][1],))
    return next((engine for engine in order if engine in available), store.FORMATS[fmt][1])


def _read(path, maximum=store.MAX_UPLOAD):
    """An engine cannot redirect the host reader with a symlink or special file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise WorkshopError('Worker artifact exceeds limits or is not a regular file', 409)
            return stream.read(maximum + 1)
    except OSError as exc:
        raise WorkshopError('Worker artifact is missing or unsafe', 409) from exc


def _runtime(capability, engine):
    """The engine's runtime digest (compiler, bundle, fonts, engine binary) plus the Python
    files that shape a job before it reaches the engine."""
    from kitty.core import resume_render
    item = next((entry for entry in capability['engines'] if entry['id'] == engine), None)
    records = [item and item.get('runtime')]
    for path in (Path(__file__), Path(resume_render.__file__)):
        info = path.stat()
        records.append((str(path), info.st_size, info.st_mtime_ns))
    return records


def cache_key(doc, capability=None):
    content = {key: doc[key] for key in ('id', 'revision', 'source', 'assets', 'entrypoint', 'format', 'engine')}
    content['runtime'] = _runtime(capability or capabilities(None), doc['engine'])
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _cache(settings, doc, capability=None):
    return Path(settings.data_dir) / 'workshop' / 'render' / cache_key(doc, capability)


def _result(folder, doc):
    result = json.loads(_read(folder / 'result.json', 2 * 1024**2))
    if result.get('doc_id') != doc['id'] or result.get('revision') != doc['revision'] or not result.get('ok'):
        raise WorkshopError('Compile snapshot identity mismatch', 409)
    pdf = _read(folder / 'output.pdf')
    if not pdf.startswith(b'%PDF-') or hashlib.sha256(pdf).hexdigest() != result.get('pdf_hash'):
        raise WorkshopError('Compiled PDF identity changed; compile this revision again', 409)
    return result


def _prepare(folder, doc):
    """Write the saved snapshot as a project folder, and the frozen page for HTML."""
    project = folder / 'project'
    project.mkdir()
    for name, encoded in store.validate_assets(doc['assets'], doc['entrypoint']).items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(store.decode_data(encoded))
    source = project / store.safe_path(doc['entrypoint'])
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(store.text(doc['source'], 'source', maximum=store.MAX_SOURCE, empty=True))
    if doc['format'] == 'html':
        from kitty.core import resume_render
        frozen, _ = resume_render.freeze_html(doc['source'], source, folder)
        (folder / 'frozen.html').write_text(frozen)


def compile_doc(settings, doc):
    result = {'doc_id': doc['id'], 'revision': doc['revision'], 'ok': False,
              'diagnostics': [], 'pages': 0, 'pdf_hash': None, 'checks': {}}
    try:
        capability = capabilities(settings)
        engine = next((item for item in capability['engines'] if item['id'] == doc['engine']), None)
        if not engine or not engine['available']:
            raise WorkshopError(engine['reason'] if engine else 'Unsupported document engine', 503)
        target = _cache(settings, doc, capability)
        target.parent.mkdir(parents=True, exist_ok=True)
        with (target.parent / (target.name + '.lock')).open('a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if target.exists():
                return _result(target, doc)
            with tempfile.TemporaryDirectory(prefix='.job-', dir=target.parent) as temporary:
                folder = Path(temporary)
                _prepare(folder, doc)
                compiled = run_engine('compile', {'folder': str(folder), 'format': doc['format'],
                                                  'engine': doc['engine'], 'entrypoint': doc['entrypoint']},
                                      timeout=COMPILE_TIMEOUT)
                # Re-read and re-hash: the engine's report is checked against the bytes published.
                pdf = _read(folder / 'output.pdf')
                if not pdf.startswith(b'%PDF-') or hashlib.sha256(pdf).hexdigest() != compiled['pdf_hash']:
                    raise WorkshopError('Compiled PDF changed after inspection', 422)
                pages = compiled['pages']
                expected = {f'page-{page}.png' for page in range(1, pages + 1)}
                if set(compiled['page_hashes']) != expected or not all(_PAGE.fullmatch(name) for name in expected):
                    raise WorkshopError('Workshop engine reported unexpected previews', 422)
                images = {}
                for name, digest in compiled['page_hashes'].items():
                    images[name] = _read(folder / name)
                    if hashlib.sha256(images[name]).hexdigest() != digest:
                        raise WorkshopError('Preview PNG changed after inspection', 422)
                result.update(ok=True, pages=pages, pdf_hash=compiled['pdf_hash'], checks=compiled['checks'],
                              page_hashes=compiled['page_hashes'], diagnostics=compiled['checks']['warnings'])
                # Publish only final artifacts, never source, profiles or engine scratch files.
                published = folder / 'published'
                published.mkdir()
                (published / 'output.pdf').write_bytes(pdf)
                for name, raw in images.items():
                    (published / name).write_bytes(raw)
                (published / 'result.json').write_text(json.dumps(result))
                published.rename(target)
                return result
    except (WorkshopError, OSError, ValueError, KeyError, TypeError) as exc:
        result['diagnostics'] = [str(exc)]
        return result


def compiled_pdf(settings, doc, revision=None):
    if revision is not None and store.positive_int(revision) != doc['revision']:
        raise WorkshopError('Requested revision does not match the supplied saved snapshot', 409)
    folder = _cache(settings, doc)
    _result(folder, doc)
    return _read(folder / 'output.pdf')


def compiled_checks(settings, doc):
    """The readability and ATS report of a revision's cached, verified compile."""
    return _result(_cache(settings, doc), doc)['checks']


def compiled_page(settings, doc, page):
    store.positive_int(page, 'page')
    folder = _cache(settings, doc)
    result = _result(folder, doc)
    if page > result['pages']:
        raise WorkshopError('Preview page not found', 404)
    name = f'page-{page}.png'
    raw = _read(folder / name)
    if hashlib.sha256(raw).hexdigest() != result['page_hashes'].get(name):
        raise WorkshopError('Preview PNG identity changed; compile again', 409)
    return raw


def export_resume(conn, settings, doc_id, *, revision, label, job_id=None):
    from kitty.core import resumes
    store.positive_int(revision)
    store.text(label, 'label', maximum=200)
    doc = store.get_revision(conn, doc_id, revision)
    data = compiled_pdf(settings, doc, revision)
    digest = hashlib.sha256(data).hexdigest()
    with store.atomic(conn):
        store._job(conn, job_id)
        existing_export = conn.execute('SELECT * FROM workshop_exports WHERE doc_id=? AND revision=?',
                                       (doc_id, revision)).fetchone()
        if existing_export:
            registered = resumes.get_resume(conn, existing_export['resume_id'])
            if (existing_export['pdf_hash'] != digest or not registered or registered.file_hash != digest
                    or hashlib.sha256(_read(Path(registered.file_path))).hexdigest() != digest):
                raise WorkshopError('Previously exported PDF identity changed; restore the original bytes', 409)
            return {key: existing_export[key] for key in ('resume_id', 'doc_id', 'revision', 'pdf_hash')}
        previous = resumes.get_resume_by_label(conn, label)
        if previous and previous.file_hash != digest:
            raise WorkshopError('Use a new label: workshop exports never replace registered resume bytes', 409)
        try:
            with tempfile.TemporaryDirectory(prefix='kitty-workshop-export-') as temporary:
                path = Path(temporary) / (digest + '.pdf')
                path.write_bytes(data)
                registered = resumes.import_resume(conn, settings.root, path, label,
                    description=f'Workshop {doc_id}, immutable revision {revision}')
        except (ValueError, OSError) as exc:
            raise WorkshopError(str(exc), 409) from exc
        conn.execute('INSERT INTO workshop_exports(doc_id,revision,resume_id,pdf_hash,job_id,created_at) VALUES(?,?,?,?,?,?)',
                     (doc_id, revision, registered.id, digest, job_id, now_iso()))
        return {'resume_id': registered.id, 'doc_id': doc_id, 'revision': revision, 'pdf_hash': digest}
