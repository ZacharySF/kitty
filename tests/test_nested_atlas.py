"""Notebook drill-down and automatically derived local graph connections."""
from kitty import web_atlas
from kitty.core import notes


def page(conn, notebook, title, text):
    result = notes.create_page(conn, notebook, title)
    document = notes.empty_document()
    document['elements'] = [{'id': 'body', 'kind': 'text', 'layer': 'default', 'x': 0, 'y': 0,
                             'w': 100, 'h': 100, 'color': '#123456', 'width': 1, 'text': text}]
    notes.save_page(conn, result['id'], result['revision'], document, title)
    return result['id']


def test_notebook_map_derives_connections_without_mutating_notes(workspace):
    conn = workspace.conn
    notebook = notes.create_notebook(conn, 'Biology')['id']
    first = page(conn, notebook, 'Respiration', 'Mitochondrial respiration produces ATP energy')
    second = page(conn, notebook, 'Cell energy', 'Mitochondrial respiration produces ATP; see [[Respiration]]')
    third = page(conn, notebook, 'Unrelated', 'Architectural stone columns and limestone')
    outside = page(conn, notes.create_notebook(conn, 'Other')['id'], 'Outside', 'Mitochondrial respiration ATP')
    before = notes.page(conn, first)
    query = {'focus': f'notebook:{notebook}'}
    graph = web_atlas.get(None, conn, '/api/atlas/sections/notes', query)
    assert {n['ref'] for n in graph['nodes'] if n['entity'] == 'page'} == {first, second, third}
    related = [link for link in graph['links'] if link['label'] != 'contains']
    assert len(related) == 1
    assert {related[0]['source'], related[0]['target']} == {f'page:{first}', f'page:{second}'}
    assert related[0]['label'] == 'linked note'
    assert all(f'page:{outside}' not in (link['source'], link['target']) for link in graph['links'])
    assert notes.page(conn, first) == before


def test_empty_opportunities_still_show_real_monitored_structure(workspace):
    conn = workspace.conn
    graph = web_atlas.get(None, conn, '/api/atlas/sections/opportunities', {})
    assert not any(node['entity'] == 'opportunity' for node in graph['nodes'])
    sources = [node for node in graph['nodes'] if node['entity'] == 'source']
    assert sources
    category = next(node for node in graph['nodes'] if node['entity'] == 'category')
    focused = web_atlas.get(None, conn, '/api/atlas/sections/opportunities', {'focus': category['id']})
    assert {node['group'] for node in focused['nodes']} == {category['id']}
