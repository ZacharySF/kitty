"""Consumer-visible review expiry, budget reservations, and private key handling."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kitty import web_ai
from kitty.web_server import APIError
from kitty.web_tasks import TaskRunner


@pytest.fixture
def ai(workspace):
    app = SimpleNamespace(settings=workspace.settings, tasks=TaskRunner())
    web_ai._write_key(app, 'synthetic-openrouter-test-key')
    workspace.conn.execute('INSERT INTO ai_models VALUES(?,?,?,?,?,?)',
                           ('synthetic/text', 'Synthetic text model', '0.000001', '0.000002', 100000, web_ai._now().isoformat()))
    workspace.conn.execute("UPDATE ai_settings SET model='synthetic/text',daily_budget_usd='0.02' WHERE id=1")
    workspace.conn.commit()
    yield app
    app.tasks.close()


def preview(ai, workspace):
    return web_ai.post(ai, workspace.conn, '/api/ai/preview', {'purpose': 'summarize', 'text': 'Confirmed source text. Unknown dates remain unknown.'})


def test_key_is_private_and_settings_staleness_cannot_replace_it(ai, workspace):
    status = web_ai.get(ai, workspace.conn, '/api/ai/status', {})
    assert 'synthetic-openrouter-test-key' not in str(status)
    assert web_ai._key_path(ai).stat().st_mode & 0o077 == 0
    web_ai.post(ai, workspace.conn, '/api/ai/settings', {'revision': status['revision'], 'daily_budget_usd': 2, 'confirmed': True})
    with pytest.raises(APIError) as error:
        web_ai.post(ai, workspace.conn, '/api/ai/settings', {'revision': status['revision'], 'key': 'stale-replacement-test-key', 'confirmed': True})
    assert error.value.status == 409
    assert web_ai._key(ai) == 'synthetic-openrouter-test-key'
    workspace.settings.root.joinpath('.private', 'outside').write_text('outside-content')
    web_ai._key_path(ai).unlink()
    web_ai._key_path(ai).symlink_to('outside')
    with pytest.raises(APIError):
        web_ai.post(ai, workspace.conn, '/api/ai/settings', {'revision': status['revision'] + 1, 'key': 'new-synthetic-test-key', 'confirmed': True})
    assert workspace.settings.root.joinpath('.private', 'outside').read_text() == 'outside-content'


def test_unapproved_underfunded_expired_and_stale_reviews_stay_unspent(ai, workspace, monkeypatch):
    def unexpected(*args):
        pytest.fail('Rejected review started external work')
    monkeypatch.setattr(ai.tasks, 'start', unexpected)
    plan = preview(ai, workspace)
    base = {'review_id': plan['review_id'], 'budget_usd': 0.02, 'confirmed': True}
    for body in ({**base, 'confirmed': False}, {**base, 'budget_usd': 0}):
        with pytest.raises(APIError):
            web_ai.post(ai, workspace.conn, '/api/ai/run', body)
    assert web_ai._spent(workspace.conn) == 0
    workspace.conn.execute('UPDATE ai_reviews SET expires_at=? WHERE id=?', ((web_ai._now() - timedelta(seconds=1)).isoformat(), plan['review_id']))
    workspace.conn.commit()
    with pytest.raises(APIError):
        web_ai.post(ai, workspace.conn, '/api/ai/run', base)
    fresh = preview(ai, workspace)
    web_ai._write_key(ai, 'synthetic-changed-private-key')
    with pytest.raises(APIError):
        web_ai.post(ai, workspace.conn, '/api/ai/run', {**base, 'review_id': fresh['review_id']})
    assert web_ai._spent(workspace.conn) == 0


def test_failed_request_consumes_review_and_remains_reserved(ai, workspace, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('private response content must not leak')
    monkeypatch.setattr(web_ai.httpx, 'Client', unavailable)
    plan = preview(ai, workspace)
    task = web_ai.post(ai, workspace.conn, '/api/ai/run', {'review_id': plan['review_id'], 'confirmed': True, 'budget_usd': 0.02})['task']
    ai.tasks.close()
    result = ai.tasks.snapshot(task['id'])
    assert result['status'] == 'failed'
    assert 'private response content' not in result['error']
    assert web_ai._spent(workspace.conn) == Decimal(str(plan['maximum_cost_usd']))
    with pytest.raises(APIError) as error:
        web_ai.post(ai, workspace.conn, '/api/ai/run', {'review_id': plan['review_id'], 'confirmed': True, 'budget_usd': 0.02})
    assert error.value.status == 409
    assert workspace.conn.execute('SELECT state FROM ai_reviews WHERE id=?', (plan['review_id'],)).fetchone()['state'] == 'failed'


def test_aggregate_reservations_block_overspending(ai, workspace, monkeypatch):
    first = preview(ai, workspace)
    cost = Decimal(str(first['maximum_cost_usd']))
    workspace.conn.execute("UPDATE ai_settings SET daily_budget_usd=? WHERE id=1", (str(cost * Decimal('1.5')),))
    workspace.conn.execute("UPDATE ai_reviews SET state='failed',reserved_usd=? WHERE id=?", (str(cost), first['review_id']))
    workspace.conn.commit()
    next_plan = preview(ai, workspace)
    monkeypatch.setattr(ai.tasks, 'start', lambda *args: pytest.fail('Over-budget request started'))
    with pytest.raises(APIError) as error:
        web_ai.post(ai, workspace.conn, '/api/ai/run', {'review_id': next_plan['review_id'], 'confirmed': True, 'budget_usd': 0.02})
    assert error.value.status == 409
    assert workspace.conn.execute('SELECT state FROM ai_reviews WHERE id=?', (next_plan['review_id'],)).fetchone()['state'] == 'pending'
