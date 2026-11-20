"""Workshop is available through authenticated HTTP and obeys module boundaries."""
from test_web_server import website


def test_workshop_round_trip_and_conflict(website):
    status, data, _ = website.request('/api/workshop')
    assert status == 200
    assert data['documents'] == []
    status, created, _ = website.request('/api/workshop', {
        'title': 'Synthetic workshop resume', 'format': 'html', 'source': '<p>Original content</p>'})
    assert status == 200
    doc = created['document']
    status, saved, _ = website.request('/api/workshop/' + doc['id'], {
        'source': '<p>Revised content</p>', 'base_revision': 1})
    assert status == 200
    assert saved['document']['revision'] == 2
    status, _, _ = website.request('/api/workshop/' + doc['id'], {
        'source': '<p>Old browser draft</p>', 'base_revision': 1})
    assert status == 409
    status, current, _ = website.request('/api/workshop/' + doc['id'])
    assert status == 200
    assert current['document']['source'] == '<p>Revised content</p>'
    assert website.request('/api/workshop-ai')[1]['settings']['enabled'] is False


def test_workshop_route_ownership():
    from kitty.web_server import route_module
    assert route_module('/api/workshop') == 'applyai'
    assert route_module('/api/workshop-ai/settings') == 'applyai'
    assert route_module('/api/workshop-batches') == 'applyai'
