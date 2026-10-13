from __future__ import annotations

from uuid import uuid4

from trademark_worker.instance_lock import WorkerInstanceLock


def test_worker_instance_lock_rejects_second_owner_and_releases() -> None:
    name = f"Local\\TrademarkQueryDesktop-Test-{uuid4().hex}"

    first = WorkerInstanceLock.acquire(name)
    assert first is not None
    try:
        assert WorkerInstanceLock.acquire(name) is None
    finally:
        first.close()

    replacement = WorkerInstanceLock.acquire(name)
    assert replacement is not None
    replacement.close()
