"""Process-local operations drained on ordinary shutdown, but not on force-kill."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from applyai.dates import now_iso


class TaskRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._closing = False

    def start(self, kind: str, work: Callable[[], Any]) -> dict[str, Any]:
        from applyai.web_server import APIError

        with self._lock:
            if self._closing:
                raise APIError(503, 'Background operations are shutting down')
            if any(record['kind'] == kind and record['status'] == 'running'
                   for record in self._records.values()):
                raise APIError(409, f'{kind} is already running')
            record = {'id': uuid4().hex, 'kind': kind, 'status': 'running',
                      'started_at': now_iso(), 'finished_at': None,
                      'result': None, 'error': None}
            self._records[record['id']] = record
            initial = dict(record)
            thread = threading.Thread(target=self._run, args=(record['id'], work),
                                      daemon=False, name=f'applyai-{kind}')
            self._threads[record['id']] = thread
            try:
                # Publish and start under the same lock used by close: a drain
                # must never join an unstarted thread or miss accepted work.
                thread.start()
            except Exception as exc:
                self._threads.pop(record['id'])
                record.update(status='failed', finished_at=now_iso(),
                              error='Unable to start background operation')
                raise APIError(503, record['error']) from exc
        return initial

    def close(self) -> None:
        """Reject new work and wait for accepted callbacks and their persistence."""
        with self._lock:
            self._closing = True
            threads = tuple(self._threads.values())
        # Workers need the lock to publish their final snapshots.
        for thread in threads:
            thread.join()

    def _run(self, task_id: str, work: Callable[[], Any]) -> None:
        from applyai.web_server import APIError

        try:
            result = work()
            update = {'status': 'completed', 'result': result, 'error': None}
        except APIError as exc:
            update = {'status': 'failed', 'result': None, 'error': str(exc)}
        except Exception as exc:
            # External SDK errors may contain tokens, private paths, or response bodies.
            update = {'status': 'failed', 'result': None,
                      'error': f'Operation failed ({type(exc).__name__}). Check the operation prerequisites and local diagnostics.'}
        with self._lock:
            self._records[task_id].update(update, finished_at=now_iso())
            self._threads.pop(task_id)

    def snapshot(self, task_id: str) -> dict[str, Any]:
        from applyai.web_server import APIError

        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise APIError(404, 'Background operation not found')
            return dict(record)
