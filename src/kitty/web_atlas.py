"""Read-only /api/atlas: the home schema of sections and per-section schemas.

GET /api/atlas is a schema of SECTIONS only: one exact count per section kind,
never individual jobs, applications, opportunities or other records. A count is
``None`` when its table is absent (an older database) or cannot be computed.

GET /api/atlas/sections/{jobs,flashcards,notes,study,opportunities} graphs expose
real entity relationships. Jobs and flashcards include every actual item, not a
paged sample. Flashcards use one node per logical note, with generated card
counts, and include archived items just like the browser. Other sections retain
bounded samples; ``total`` counts eligible nodes and ``shown`` counts returned
nodes (including hubs). Missing tables contribute no invented entities.

``?focus=<entity>:<ref>`` narrows a graph: jobs ``track:<name>``, flashcards
``deck:<id>``, notes ``notebook:<id>`` / ``folder:<id>``, study ``subject:<id>`` and
opportunities ``category:<name>``. ``?search=<text>`` (notes, study, flashcards, jobs)
returns the section's records matching it in the search index (kitty.core.search),
titles and contents alike, with their hubs. Only ids, titles and short details are
returned: no file paths, URLs, note bodies, snippets or tokens. Flashcard note labels
use their type and stable note id.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta

from kitty.core import calendar, backlinks, search
from kitty.modules import MODULE_IDS

EVENT_WINDOW = timedelta(days=30)
SECTION_LIMIT = 180
SECTION_GROUP_LIMIT = 60
LABEL_LIMIT = 120
FOCUS_LIMIT = 200
# kind -> (label, workspace view id in web/src/lib/workspace.ts)
KINDS: dict[str, tuple[str, str]] = {
    'subject': ('Study subjects', 'study'),
    'deck': ('Flashcard decks', 'flashcards'),
    'notebook': ('Notebooks', 'notes'),
    'schema': ('Mind maps', 'schema'),
    'event': ('Upcoming events', 'calendar'),
    'job': ('Jobs', 'jobs'),
    'application': ('Applications', 'pipeline'),
    'opportunity': ('Opportunities', 'opportunities'),
}
HOME_COUNTS = {
    'subject': 'SELECT COUNT(*) FROM study_subjects',
    'deck': 'SELECT COUNT(*) FROM flash_decks WHERE archived = 0',
    'notebook': 'SELECT COUNT(*) FROM note_notebooks',
    'schema': 'SELECT COUNT(*) FROM schema_maps',
    'job': 'SELECT COUNT(*) FROM jobs',
    'application': 'SELECT COUNT(*) FROM applications',
    'opportunity': "SELECT COUNT(*) FROM opportunities WHERE status <> 'skipped'",
}
#: Owning module of each home kind; a kind is neither listed nor counted without it.
KIND_MODULES = {'subject': 'workspace', 'deck': 'workspace', 'notebook': 'workspace',
                'schema': 'workspace', 'event': 'workspace', 'opportunity': 'workspace',
                'job': 'applyai', 'application': 'applyai'}


def _api_error(status: int, message: str):
    from kitty.web_server import APIError  # web_server imports this module
    return APIError(status, message)


def _text(value, limit=LABEL_LIMIT) -> str:
    text = ' '.join(str(value or '').split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _missing(exc: sqlite3.OperationalError) -> bool:
    return 'no such table' in str(exc) or 'no such column' in str(exc)


def _home_count(conn, sql) -> int | None:
    try:
        return conn.execute(sql).fetchone()[0]
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return None
        raise


def _upcoming_events(conn, now, applyai=True) -> int | None:
    try:
        occurrences = calendar.occurrences(conn, now.isoformat(), (now + EVENT_WINDOW).isoformat(), applyai=applyai)
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return None
        raise
    except Exception as exc:  # e.g. too many occurrences to expand: unknown, not zero
        if type(exc).__name__ == 'APIError':
            return None
        raise
    return len({event['id'] for event in occurrences})


def atlas(conn: sqlite3.Connection, now: datetime | None = None, modules=MODULE_IDS) -> dict:
    """Section counts for the active modules only; others are neither listed nor queried."""
    moment = now or datetime.now(UTC)
    kinds = {kind: spec for kind, spec in KINDS.items() if KIND_MODULES[kind] in modules}
    counts = {kind: _home_count(conn, sql) for kind, sql in HOME_COUNTS.items() if kind in kinds}
    if 'event' in kinds:
        counts['event'] = _upcoming_events(conn, moment, 'applyai' in modules)
    return {'sections': [{'kind': kind, 'label': label, 'view': view, 'count': counts[kind]}
                         for kind, (label, view) in kinds.items()]}


def _section_rows(conn, sql, params=()):
    """Missing migrations are empty; unrelated SQL failures remain visible."""
    try:
        return [tuple(row) for row in conn.execute(sql, params)]
    except sqlite3.OperationalError as exc:
        if _missing(exc):
            return []
        raise


def _count(conn, sql, params=()):
    rows = _section_rows(conn, sql, params)
    return rows[0][0] if rows else 0


def _node(entity, ref, label, group, detail='', *, hub=False):
    return {'id': f'{entity}:{ref}', 'entity': entity, 'ref': ref,
            'label': _text(label) or 'Untitled', 'group': str(group),
            'kind': 'hub' if hub else entity, 'size': 4 if hub else 1,
            'detail': _text(detail)}


def _link(source, target, label):
    return {'source': source, 'target': target, 'label': label, 'directed': True}


def _section_result(nodes, links, total):
    return {'nodes': nodes, 'links': links, 'total': total, 'shown': len(nodes)}


def _placeholders(values) -> str:
    return ','.join('?' for _ in values) or 'NULL'


def _not_found(entity):
    return _api_error(404, f'That {entity} no longer exists')


def _job_node(ident, title, company, state, group):
    return _node('job', ident, f'{company} — {title}' if company else title, group, state)


def _job_section(conn, focus=None):
    if focus:
        return _job_track(conn, focus[1])
    tracks_where = '(t.active = 1 OR EXISTS (SELECT 1 FROM job_tracks jt WHERE jt.track = t.name))'
    tracks = _section_rows(conn, f'''SELECT t.name, (
        SELECT COUNT(*) FROM job_tracks jt JOIN jobs j ON j.id = jt.job_id WHERE jt.track = t.name)
        FROM tracks t WHERE {tracks_where}
        ORDER BY t.active DESC, t.sort_order, t.name''')
    nodes = [_node('track', name, name, name, f'{count} jobs', hub=True)
             for name, count in tracks]
    return _section_result(nodes, [], len(nodes))


def _job_track(conn, track):
    if not _section_rows(conn, 'SELECT 1 FROM tracks WHERE name = ?', (track,)):
        raise _not_found('track')
    count = _count(conn, '''SELECT COUNT(*) FROM job_tracks jt JOIN jobs j ON j.id = jt.job_id
        WHERE jt.track = ?''', (track,))
    jobs = _section_rows(conn, '''SELECT j.id, j.title, c.display_name, j.queue_state
        FROM job_tracks jt JOIN jobs j ON j.id = jt.job_id LEFT JOIN companies c ON c.id = j.company_id
        WHERE jt.track = ? ORDER BY j.updated_at DESC, j.id DESC''', (track,))
    nodes = [_node('track', track, track, track, f'{count} jobs · {len(jobs)} shown', hub=True)]
    nodes.extend(_job_node(ident, title, company, state, track) for ident, title, company, state in jobs)
    links = [_link(f'track:{track}', f'job:{row[0]}', 'matched track') for row in jobs]
    return _section_result(nodes, links, 1 + count)


def _flashcard_section(conn, focus=None):
    decks = _section_rows(conn, '''SELECT id, name, parent_id, archived FROM flash_decks
        ORDER BY name, id''')
    deck_ids = {row[0] for row in decks}
    if focus:
        if focus[1] not in deck_ids:
            raise _not_found('deck')
        children = {}
        for ident, _, parent, _ in decks:
            children.setdefault(parent, []).append(ident)
        deck_ids = set()
        pending = [focus[1]]
        while pending:
            ident = pending.pop()
            if ident in deck_ids:
                continue
            deck_ids.add(ident)
            pending.extend(children.get(ident, ()))
        decks = [row for row in decks if row[0] in deck_ids]
    # Browser semantics include archived notes/decks and suspended/buried cards.
    # A note remains a real editable item even when it generates no cards.
    notes = _section_rows(conn, '''SELECT n.id, n.deck_id, t.name, n.archived, COUNT(c.id)
        FROM flash_notes n JOIN flash_types t ON t.id = n.type_id
        JOIN flash_decks d ON d.id = n.deck_id
        LEFT JOIN flash_cards c ON c.note_id = n.id
        GROUP BY n.id ORDER BY n.id''')
    notes = [row for row in notes if row[1] in deck_ids]
    counts = {}
    for _, deck, _, _, cards in notes:
        note_count, card_count = counts.get(deck, (0, 0))
        counts[deck] = (note_count + 1, card_count + cards)
    nodes, links = [], []
    for ident, name, parent, archived in decks:
        note_count, card_count = counts.get(ident, (0, 0))
        detail = f'{note_count} notes · {card_count} cards' + (' · archived' if archived else '')
        nodes.append(_node('deck', ident, name, f'deck:{ident}', detail, hub=True))
        if parent in deck_ids:
            links.append(_link(f'deck:{parent}', f'deck:{ident}', 'subdeck'))
    archived_decks = {ident for ident, _, _, archived in decks if archived}
    for ident, deck, typ, archived, cards in notes:
        detail = f'{cards} generated cards' + (' · archived' if archived or deck in archived_decks else '')
        nodes.append(_node('flashcard_note', ident, f'{typ} · Note {ident}', f'deck:{deck}', detail))
        links.append(_link(f'deck:{deck}', f'flashcard_note:{ident}', 'contains note'))
    return _section_result(nodes, links, len(nodes))


def _note_section(conn, focus=None):
    if focus:
        entity, ref = focus
        return _notebook_pages(conn, ref) if entity == 'notebook' else _folder_children(conn, ref)
    total = sum(_count(conn, f'SELECT COUNT(*) FROM {table}')
                for table in ('note_folders', 'note_notebooks', 'note_page_index'))
    # Depth-first order ensures every sampled folder retains its sampled ancestors.
    # The depth guard also bounds traversal of unusually deep imported hierarchies.
    folders = _section_rows(conn, '''WITH RECURSIVE tree(id, title, parent_id, path, depth) AS (
        SELECT id, title, parent_id, printf('%020d', id), 0 FROM note_folders WHERE parent_id IS NULL
        UNION ALL
        SELECT f.id, f.title, f.parent_id, tree.path || '/' || printf('%020d', f.id), tree.depth + 1
        FROM note_folders f JOIN tree ON f.parent_id = tree.id WHERE tree.depth < ?
    ) SELECT id, title, parent_id,
        (SELECT COUNT(*) FROM note_folders f WHERE f.parent_id = tree.id),
        (SELECT COUNT(*) FROM note_notebooks n WHERE n.folder_id = tree.id)
        FROM tree ORDER BY path LIMIT ?''', (SECTION_GROUP_LIMIT, SECTION_GROUP_LIMIT))
    folder_ids = [row[0] for row in folders]
    columns = {row[1] for row in _section_rows(conn, 'PRAGMA table_info(note_notebooks)')}
    folder_column = 'n.folder_id' if 'folder_id' in columns else 'NULL'
    notebooks = _section_rows(conn, f'''SELECT n.id, n.title, {folder_column},
        (SELECT COUNT(*) FROM note_page_index p WHERE p.notebook_id = n.id)
        FROM note_notebooks n
        WHERE {folder_column} IS NULL OR {folder_column} IN ({_placeholders(folder_ids)})
        ORDER BY n.updated_at DESC, n.id DESC LIMIT ?''', (*folder_ids, SECTION_GROUP_LIMIT))
    notebook_ids = [row[0] for row in notebooks]
    pages = _section_rows(conn, f'''SELECT id, title, notebook_id FROM note_page_index
        WHERE notebook_id IN ({_placeholders(notebook_ids)})
        ORDER BY updated_at DESC, id DESC LIMIT ?''',
        (*notebook_ids, SECTION_LIMIT - len(folders) - len(notebooks))) if notebook_ids else []
    nodes, links, groups = [], [], {}
    for ident, title, parent, children, books in folders:
        group = groups.get(parent, f'folder:{ident}')
        groups[ident] = group
        nodes.append(_node('folder', ident, title, group, f'{children} folders · {books} notebooks', hub=True))
        if parent in groups:
            links.append(_link(f'folder:{parent}', f'folder:{ident}', 'contains'))
    shown_pages = {}
    for _, _, notebook in pages:
        shown_pages[notebook] = shown_pages.get(notebook, 0) + 1
    book_groups = {}
    for ident, title, folder, count in notebooks:
        group = groups.get(folder, f'notebook:{ident}')
        book_groups[ident] = group
        nodes.append(_node('notebook', ident, title, group,
                           f'{count} pages · {shown_pages.get(ident, 0)} shown', hub=True))
        if folder in groups:
            links.append(_link(f'folder:{folder}', f'notebook:{ident}', 'contains'))
    for ident, title, notebook in pages:
        nodes.append(_node('page', ident, title, book_groups[notebook]))
        links.append(_link(f'notebook:{notebook}', f'page:{ident}', 'contains'))
    links.extend(backlinks.page_connections(conn, [page[0] for page in pages]))
    return _section_result(nodes, links, total)


def _notebook_pages(conn, notebook):
    found = _section_rows(conn, '''SELECT n.title, (SELECT COUNT(*) FROM note_page_index p WHERE p.notebook_id = n.id)
        FROM note_notebooks n WHERE n.id = ?''', (notebook,))
    if not found:
        raise _not_found('notebook')
    title, count = found[0]
    group = f'notebook:{notebook}'
    pages = _section_rows(conn, '''SELECT id, title FROM note_page_index WHERE notebook_id = ?
        ORDER BY updated_at DESC, id DESC LIMIT ?''', (notebook, SECTION_LIMIT - 1))
    nodes = [_node('notebook', notebook, title, group, f'{count} pages · {len(pages)} shown', hub=True)]
    nodes.extend(_node('page', ident, page, group) for ident, page in pages)
    links = [_link(group, f'page:{ident}', 'contains') for ident, _ in pages]
    links.extend(backlinks.page_connections(conn, [page[0] for page in pages]))
    return _section_result(nodes, links, 1 + count)


def _folder_children(conn, folder):
    found = _section_rows(conn, 'SELECT title FROM note_folders WHERE id = ?', (folder,))
    if not found:
        raise _not_found('folder')
    group = f'folder:{folder}'
    total_children = _count(conn, 'SELECT COUNT(*) FROM note_folders WHERE parent_id = ?', (folder,))
    total_books = _count(conn, 'SELECT COUNT(*) FROM note_notebooks WHERE folder_id = ?', (folder,))
    children = _section_rows(conn, '''SELECT f.id, f.title,
        (SELECT COUNT(*) FROM note_folders c WHERE c.parent_id = f.id),
        (SELECT COUNT(*) FROM note_notebooks n WHERE n.folder_id = f.id)
        FROM note_folders f WHERE f.parent_id = ? ORDER BY f.title COLLATE NOCASE, f.id LIMIT ?''',
        (folder, SECTION_GROUP_LIMIT))
    books = _section_rows(conn, '''SELECT n.id, n.title, (SELECT COUNT(*) FROM note_page_index p WHERE p.notebook_id = n.id)
        FROM note_notebooks n WHERE n.folder_id = ? ORDER BY n.updated_at DESC, n.id DESC LIMIT ?''',
        (folder, SECTION_GROUP_LIMIT))
    nodes = [_node('folder', folder, found[0][0], group,
                   f'{total_children} folders · {total_books} notebooks', hub=True)]
    links = []
    for ident, title, sub, count in children:
        nodes.append(_node('folder', ident, title, group, f'{sub} folders · {count} notebooks', hub=True))
        links.append(_link(group, f'folder:{ident}', 'contains'))
    for ident, title, count in books:
        nodes.append(_node('notebook', ident, title, group, f'{count} pages', hub=True))
        links.append(_link(group, f'notebook:{ident}', 'contains'))
    return _section_result(nodes, links, 1 + total_children + total_books)


# Schema source references are validated when maps are authored. Only direct,
# explicit edges to a currently existing subject establish a study relationship;
# matching labels or merely sharing a map never implies one.
STUDY_CONCEPTS = '''WITH map_nodes AS (
    SELECT m.id AS map_id, json_extract(n.value, '$.id') AS node_id,
        json_extract(n.value, '$.kind') AS kind, json_extract(n.value, '$.label') AS label,
        json_extract(n.value, '$.source.kind') AS source_kind,
        json_extract(n.value, '$.source.id') AS source_id
    FROM schema_maps m, json_each(
        CASE WHEN json_valid(m.graph_json) THEN m.graph_json ELSE '{}' END, '$.nodes') n
    WHERE m.mode = 'study'
), map_edges AS (
    SELECT m.id AS map_id, json_extract(e.value, '$.from') AS source,
        json_extract(e.value, '$.to') AS target
    FROM schema_maps m, json_each(
        CASE WHEN json_valid(m.graph_json) THEN m.graph_json ELSE '{}' END, '$.edges') e
    WHERE m.mode = 'study'
), related AS (
    SELECT DISTINCT c.map_id, c.node_id AS concept_id, c.label, s.id AS subject_id
    FROM map_nodes a
    JOIN study_subjects s ON s.id = a.source_id
    JOIN map_edges e ON e.map_id = a.map_id AND (e.source = a.node_id OR e.target = a.node_id)
    JOIN map_nodes c ON c.map_id = a.map_id
        AND c.node_id = CASE WHEN e.source = a.node_id THEN e.target ELSE e.source END
    WHERE c.kind IN ('topic', 'idea', 'skill') AND a.kind = 'class' AND a.source_kind = 'subject'
) '''


def _study_section(conn, focus=None):
    subject_filter = 'WHERE s.id = ?' if focus else ''
    subject_params = (focus[1],) if focus else ()
    if focus:
        if not _section_rows(conn, 'SELECT 1 FROM study_subjects WHERE id = ?', subject_params):
            raise _not_found('subject')
        total_subjects = 1
        total_projects = _count(conn, 'SELECT COUNT(*) FROM study_projects WHERE subject_id = ?', subject_params)
        total_concepts = _count(conn, STUDY_CONCEPTS + '''SELECT COUNT(*) FROM
            (SELECT DISTINCT map_id, concept_id FROM related WHERE subject_id = ?)''', subject_params)
    else:
        total_subjects = _count(conn, 'SELECT COUNT(*) FROM study_subjects')
        total_projects = _count(conn, 'SELECT COUNT(*) FROM study_projects')
        total_concepts = _count(conn, STUDY_CONCEPTS +
                                'SELECT COUNT(*) FROM (SELECT DISTINCT map_id, concept_id FROM related)')
    has_projects = _count(conn, "SELECT COUNT(*) FROM sqlite_schema WHERE type = 'table' AND name = 'study_projects'")
    project_count = '(SELECT COUNT(*) FROM study_projects p WHERE p.subject_id = s.id)' if has_projects else '0'
    subjects = _section_rows(conn, f'''SELECT s.id, s.name, {project_count}
        FROM study_subjects s {subject_filter} ORDER BY s.name COLLATE NOCASE, s.id LIMIT ?''',
        (*subject_params, SECTION_GROUP_LIMIT))
    ids = [row[0] for row in subjects]
    subject_ids = set(ids)
    placeholders = _placeholders(ids)
    independent = '' if focus else 'subject_id IS NULL OR '
    projects = _section_rows(conn, f'''SELECT id, name, subject_id FROM study_projects
        WHERE {independent}subject_id IN ({placeholders})
        ORDER BY name COLLATE NOCASE, id LIMIT ?''', (*ids, SECTION_GROUP_LIMIT))
    concepts = _section_rows(conn, STUDY_CONCEPTS + f'''SELECT map_id, concept_id, label
        FROM related WHERE subject_id IN ({placeholders})
        GROUP BY map_id, concept_id ORDER BY label COLLATE NOCASE, map_id, concept_id LIMIT ?''',
        (*ids, SECTION_LIMIT - len(subjects) - len(projects)))
    concept_refs = [(map_id, key) for map_id, key, _ in concepts]
    relations = _section_rows(conn, STUDY_CONCEPTS + f'''SELECT map_id, concept_id, subject_id
        FROM related WHERE subject_id IN ({placeholders})
        AND (map_id, concept_id) IN ({','.join('(?,?)' for _ in concept_refs)})
        ORDER BY map_id, concept_id, subject_id''',
        (*ids, *(value for ref in concept_refs for value in ref))) if concept_refs else []
    links, concept_groups, shown_projects = [], {}, {}
    for ident, _, subject in projects:
        if subject in subject_ids:
            shown_projects[subject] = shown_projects.get(subject, 0) + 1
            links.append(_link(f'subject:{subject}', f'project:{ident}', 'contains'))
    for map_id, key, subject in relations:
        ref = f'{map_id}:{key}'
        concept_groups.setdefault(ref, f'subject:{subject}')
        links.append(_link(f'subject:{subject}', f'concept:{ref}', 'linked concept'))
    nodes = [_node('subject', ident, name, f'subject:{ident}',
                   f'{count} projects · {shown_projects.get(ident, 0)} shown', hub=True)
             for ident, name, count in subjects]
    nodes.extend(_node('project', ident, name, f'subject:{subject}' if subject in subject_ids else 'Independent projects')
                 for ident, name, subject in projects)
    nodes.extend(_node('concept', f'{map_id}:{key}', label, concept_groups[f'{map_id}:{key}'],
                       'Explicit subject connection in a study schema map')
                 for map_id, key, label in concepts)
    return _section_result(nodes, links, total_subjects + total_projects + total_concepts)


def _opportunity_sources(conn):
    """Monitored providers as `sources()` would list them, without seeding (read-only)."""
    from kitty.core.opportunity_sources import DEFAULTS as OPPORTUNITY_SOURCES

    rows = _section_rows(conn, '''SELECT id, name, category, enabled, last_status, last_count
        FROM opportunity_sources''')
    if not rows and not _count(conn, "SELECT COUNT(*) FROM sqlite_schema WHERE type = 'table'"
                                     " AND name = 'opportunity_sources'"):
        return []
    known = {row[0] for row in rows}
    rows.extend((ident, name, category, 1, 'never', 0)
                for ident, name, _, _, category, _ in OPPORTUNITY_SOURCES if ident not in known)
    return sorted(rows, key=lambda row: (str(row[1]).casefold(), row[0]))


def _opportunity_section(conn, focus=None):
    live = "status <> 'skipped'"
    counts = dict(_section_rows(conn, f'''SELECT category, COUNT(*) FROM opportunities
        WHERE {live} GROUP BY category'''))
    sources = _opportunity_sources(conn)
    categories = sorted({*counts, *(row[2] for row in sources)}, key=lambda name: (str(name).casefold(), name))
    total = len(categories) + len(sources) + sum(counts.values())
    if focus:
        if focus[1] not in categories:
            raise _not_found('category')
        categories = [focus[1]]
        sources = [row for row in sources if row[2] == focus[1]]
        total = 1 + len(sources) + counts.get(focus[1], 0)
    categories = categories[:SECTION_GROUP_LIMIT]
    shown_categories = set(categories)
    sources = [row for row in sources if row[2] in shown_categories][:SECTION_GROUP_LIMIT]
    items = _section_rows(conn, f'''SELECT id, title, category, source, status, favorite FROM opportunities
        WHERE {live} AND category IN ({_placeholders(categories)})
        ORDER BY favorite DESC, updated_at DESC, id DESC LIMIT ?''',
        (*categories, SECTION_LIMIT - len(categories) - len(sources))) if categories else []
    shown = {}
    for row in items:
        shown[row[2]] = shown.get(row[2], 0) + 1
    monitors = {}
    for row in sources:
        monitors[row[2]] = monitors.get(row[2], 0) + 1
    nodes = [_node('category', name, name, f'category:{name}',
                   f'{counts.get(name, 0)} opportunities · {shown.get(name, 0)} shown'
                   f' · {monitors.get(name, 0)} sources', hub=True)
             for name in categories]
    links = []
    source_ids = set()
    for ident, name, category, enabled, status, count in sources:
        source_ids.add(ident)
        nodes.append(_node('source', ident, name, f'category:{category}',
                           f"{'monitored' if enabled else 'paused'} · {status} · {count} records", hub=True))
        links.append(_link(f'category:{category}', f'source:{ident}', 'monitored source'))
    for ident, title, category, source, status, favorite in items:
        nodes.append(_node('opportunity', ident, title, f'category:{category}',
                           f'{status} · saved' if favorite else status))
        links.append(_link(f'category:{category}', f'opportunity:{ident}', 'category'))
        if source in source_ids:
            links.append(_link(f'source:{source}', f'opportunity:{ident}', 'discovered by'))
    return _section_result(nodes, links, total)


SECTIONS = {'jobs': _job_section, 'flashcards': _flashcard_section, 'notes': _note_section,
            'study': _study_section, 'opportunities': _opportunity_section}
FOCUS = {'jobs': {'track'}, 'flashcards': {'deck'}, 'notes': {'notebook', 'folder'},
         'study': {'subject'}, 'opportunities': {'category'}}
NUMERIC_FOCUS = {'deck', 'notebook', 'folder', 'subject'}


def _focus(section, query):
    if not query:
        return None
    value = query.get('focus')
    if set(query) != {'focus'} or not isinstance(value, str):
        raise _api_error(400, 'Section graphs accept only a focus=<entity>:<ref> parameter')
    entity, separator, ref = value.partition(':')
    if not separator or entity not in FOCUS[section] or not ref.strip() or len(ref) > FOCUS_LIMIT:
        allowed = ', '.join(f'{name}:<ref>' for name in sorted(FOCUS[section]))
        raise _api_error(400, f'The {section} graph can focus on {allowed}')
    if entity in NUMERIC_FOCUS:
        if not re.fullmatch(r'[1-9][0-9]{0,17}', ref):
            raise _api_error(400, f'A {entity} focus needs a numeric id')
        return entity, int(ref)
    return entity, ref


#: Result kinds of kitty.core.search each section's ``?search=`` looks through.
SEARCH_KINDS = {'notes': ['note_page'], 'study': ['study_subject', 'study_project'],
                'flashcards': ['flashcard_deck', 'flashcard_note'], 'jobs': ['job']}


def _search_section(conn, section, text, modules):
    """The section's records that match `text` in the search index (titles and contents), as graph nodes.

    Nodes carry the same labels as the section graph (never bodies or snippets), with
    their notebook, subject, deck or track hub; ``truncated`` says more than
    ``SECTION_LIMIT`` records matched and ``total`` counts them.
    """
    if section not in SEARCH_KINDS:
        raise _api_error(400, f'The {section} graph has no search')
    try:
        found = search.search(conn, text, limit=SECTION_LIMIT, kinds=SEARCH_KINDS[section], modules=modules)
    except ValueError as exc:
        raise _api_error(400, str(exc)) from None
    ids = {}
    for hit in found['hits']:
        ids.setdefault(hit['kind'], []).append(hit['id'])
    nodes, links, hubs = [], [], set()

    def hub(entity, ref, label, detail=''):
        if (entity, ref) not in hubs:
            hubs.add((entity, ref))
            nodes.append(_node(entity, ref, label, f'{entity}:{ref}', detail, hub=True))

    if section == 'notes':
        pages = ids.get('note_page', [])
        for ident, title, notebook, book in _section_rows(conn, f'''SELECT p.id, p.title, n.id, n.title FROM note_page_index p
                JOIN note_notebooks n ON n.id = p.notebook_id WHERE p.id IN ({_placeholders(pages)})''', pages):
            hub('notebook', notebook, book)
            nodes.append(_node('page', ident, title, f'notebook:{notebook}'))
            links.append(_link(f'notebook:{notebook}', f'page:{ident}', 'contains'))
    elif section == 'study':
        for ident in ids.get('study_subject', []):
            hub('subject', ident, next(hit['title'] for hit in found['hits'] if hit['kind'] == 'study_subject' and hit['id'] == ident))
        projects = ids.get('study_project', [])
        for ident, name, subject, subject_name in _section_rows(conn, f'''SELECT p.id, p.name, s.id, s.name FROM study_projects p
                LEFT JOIN study_subjects s ON s.id = p.subject_id WHERE p.id IN ({_placeholders(projects)})''', projects):
            if subject is not None:
                hub('subject', subject, subject_name)
                links.append(_link(f'subject:{subject}', f'project:{ident}', 'contains'))
            nodes.append(_node('project', ident, name, f'subject:{subject}' if subject is not None else 'Independent projects'))
    elif section == 'flashcards':
        decks = ids.get('flashcard_deck', [])
        for ident, name in _section_rows(conn, f'SELECT id, name FROM flash_decks WHERE id IN ({_placeholders(decks)})', decks):
            hub('deck', ident, name)
        cards = ids.get('flashcard_note', [])
        for ident, deck, name, typ in _section_rows(conn, f'''SELECT n.id, d.id, d.name, t.name FROM flash_notes n
                JOIN flash_decks d ON d.id = n.deck_id JOIN flash_types t ON t.id = n.type_id
                WHERE n.id IN ({_placeholders(cards)})''', cards):
            hub('deck', deck, name)
            nodes.append(_node('flashcard_note', ident, f'{typ} · Note {ident}', f'deck:{deck}'))
            links.append(_link(f'deck:{deck}', f'flashcard_note:{ident}', 'contains note'))
    else:
        jobs = ids.get('job', [])
        tracks = {}
        for ident, track in _section_rows(conn, f'SELECT job_id, track FROM job_tracks WHERE job_id IN ({_placeholders(jobs)}) '
                                                'ORDER BY track', jobs):
            tracks.setdefault(ident, []).append(track)
        for ident, title, company, state in _section_rows(conn, f'''SELECT j.id, j.title, c.display_name, j.queue_state FROM jobs j
                LEFT JOIN companies c ON c.id = j.company_id WHERE j.id IN ({_placeholders(jobs)})''', jobs):
            for track in tracks.get(ident, []):
                hub('track', track, track)
                links.append(_link(f'track:{track}', f'job:{ident}', 'matched track'))
            nodes.append(_job_node(ident, title, company, state, tracks[ident][0] if ident in tracks else 'Jobs'))
    return {**_section_result(nodes, links, found['total']), 'search': text, 'truncated': found['truncated']}


def get(app, conn, path, query):
    if path == '/api/atlas':
        app._no_query(query)
        return atlas(conn, modules=app.modules)
    section = path.removeprefix('/api/atlas/sections/')
    if path != f'/api/atlas/sections/{section}' or section not in SECTIONS:
        return None
    if 'search' in query:
        if set(query) != {'search'}:
            raise _api_error(400, 'A section search takes only search=<text>')
        return _search_section(conn, section, query['search'], getattr(app, 'modules', MODULE_IDS))
    return SECTIONS[section](conn, _focus(section, query))


def post(app, conn, path, body):
    return None
