"""Local deterministic related-reference API."""
from kitty.core import backlinks


def get(app, conn, path, query):
    return None


def post(app, conn, path, body):
    if path != '/api/backlinks/related':
        return None
    from kitty.web_server import APIError
    if not isinstance(body, dict) or set(body) - {'text', 'exclude'} or 'text' not in body:
        raise APIError(400, 'Expected text and optional exclude')
    try:
        return backlinks.related(conn, body['text'], body.get('exclude'))
    except ValueError as exc:
        raise APIError(400, str(exc)) from None
