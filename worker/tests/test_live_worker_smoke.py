from __future__ import annotations

import importlib.util
from pathlib import Path
from queue import Queue
from types import SimpleNamespace


def _load_smoke_module():
    path = Path(__file__).parents[2] / "scripts" / "live-worker-smoke.py"
    spec = importlib.util.spec_from_file_location("live_worker_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_completed_event_fails_acceptance_when_any_row_failed() -> None:
    module = _load_smoke_module()

    accepted, message = module.accept_completed_event(
        {
            "type": "completed",
            "items": [
                {"row": 2, "mark": "成功商标", "status": "success"},
                {"row": 3, "mark": "失败商标", "status": "failed"},
            ],
        }
    )

    assert accepted is False
    assert "第 3 行" in message
    assert "失败商标" in message


def test_completed_event_passes_only_when_every_row_succeeded() -> None:
    module = _load_smoke_module()

    accepted, message = module.accept_completed_event(
        {
            "type": "completed",
            "items": [
                {"row": 2, "mark": "商标一", "status": "success"},
                {"row": 3, "mark": "商标二", "status": "success"},
            ],
        }
    )

    assert accepted is True
    assert "2/2" in message


def test_idle_live_worker_does_not_abort_running_batch() -> None:
    module = _load_smoke_module()
    process = SimpleNamespace(poll=lambda: None)

    assert module.next_task_event(Queue(), process, timeout=0.001) is None
