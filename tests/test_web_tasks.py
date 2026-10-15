"""Deterministic task draining without providers or live side effects."""
from __future__ import annotations

import threading

import pytest

from kitty.web_server import APIError
from kitty.web_tasks import TaskRunner


def test_close_waits_for_persistence_and_rejects_new_work(tmp_path, monkeypatch):
    runner = TaskRunner()
    entered = threading.Event()
    release = threading.Event()
    joining = threading.Event()
    closed = threading.Event()
    persisted = tmp_path / "batch-id"

    def work():
        entered.set()
        assert release.wait(5)
        persisted.write_text("synthetic-batch-id")
        return {"batch_id": "synthetic-batch-id"}

    def close():
        runner.close()
        closed.set()

    closer = threading.Thread(target=close)
    try:
        initial = runner.start("batch", work)
        assert entered.wait(5)
        assert initial["status"] == "running"
        worker = runner._threads[initial["id"]]
        assert not worker.daemon
        original_join = worker.join

        def observed_join(*args, **kwargs):
            joining.set()
            return original_join(*args, **kwargs)

        monkeypatch.setattr(worker, "join", observed_join)
        closer.start()
        assert joining.wait(5)
        assert not closed.is_set()
        assert not persisted.exists()
        assert runner.snapshot(initial["id"])["status"] == "running"
        with pytest.raises(APIError, match="shutting down") as error:
            runner.start("another", lambda: pytest.fail("Rejected work ran"))
        assert error.value.status == 503
        release.set()
        closer.join(5)
        assert not closer.is_alive()
        assert closed.is_set()
        assert persisted.read_text() == "synthetic-batch-id"
        final = runner.snapshot(initial["id"])
        assert final["status"] == "completed"
        assert final["result"] == {"batch_id": "synthetic-batch-id"}
        assert final["finished_at"] is not None
        assert not runner._threads
        runner.close()  # Draining twice is harmless and does not reopen starts.
        with pytest.raises(APIError, match="shutting down"):
            runner.start("batch", lambda: None)
    finally:
        release.set()
        if closer.ident is not None:
            closer.join(5)
        runner.close()


def test_start_failure_records_failure_and_allows_retry(monkeypatch):
    runner = TaskRunner()

    def fail_start(self):
        raise RuntimeError("sensitive implementation detail")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(threading.Thread, "start", fail_start)
            with pytest.raises(APIError, match="Unable to start") as error:
                runner.start("batch", lambda: pytest.fail("Unstarted work ran"))
            assert error.value.status == 503
        failed_id, = runner._records
        failed = runner.snapshot(failed_id)
        assert failed["status"] == "failed"
        assert failed["finished_at"] is not None
        assert failed["result"] is None
        assert "sensitive" not in failed["error"]
        assert not runner._threads
        retry = runner.start("batch", lambda: "persisted")
        runner.close()
        assert runner.snapshot(retry["id"])["result"] == "persisted"
    finally:
        runner.close()


def test_start_and_close_are_atomic(monkeypatch):
    runner = TaskRunner()
    starting = threading.Event()
    allow_start = threading.Event()
    close_entered = threading.Event()
    closed = threading.Event()
    persisted = threading.Event()
    results = []
    errors = []
    original_start = threading.Thread.start

    def held_start(thread):
        if thread.name == "kitty-batch":
            starting.set()
            assert allow_start.wait(5)
        return original_start(thread)

    def start():
        try:
            results.append(runner.start("batch", lambda: persisted.set()))
        except Exception as exc:
            errors.append(exc)

    def close():
        close_entered.set()
        try:
            runner.close()
            closed.set()
        except Exception as exc:
            errors.append(exc)

    starter = threading.Thread(target=start)
    closer = threading.Thread(target=close)
    monkeypatch.setattr(threading.Thread, "start", held_start)
    try:
        starter.start()
        assert starting.wait(5)
        closer.start()
        assert close_entered.wait(5)
        assert not closed.is_set()
        allow_start.set()
        starter.join(5)
        closer.join(5)
        assert not starter.is_alive()
        assert not closer.is_alive()
        assert not errors
        assert closed.is_set() and persisted.is_set()
        assert len(results) == 1
        assert runner.snapshot(results[0]["id"])["status"] == "completed"
        assert not runner._threads
    finally:
        allow_start.set()
        if starter.ident is not None:
            starter.join(5)
        if closer.ident is not None:
            closer.join(5)
        runner.close()


@pytest.mark.parametrize("failure", [APIError(409, "Safe conflict"), ValueError("private detail")])
def test_failed_work_is_drained_with_accurate_snapshot(failure):
    runner = TaskRunner()

    def work():
        raise failure

    try:
        initial = runner.start("batch", work)
        runner.close()
        final = runner.snapshot(initial["id"])
        assert final["status"] == "failed"
        assert final["result"] is None
        assert final["finished_at"] is not None
        assert "private detail" not in final["error"]
        if isinstance(failure, APIError):
            assert final["error"] == "Safe conflict"
        assert not runner._threads
    finally:
        runner.close()
