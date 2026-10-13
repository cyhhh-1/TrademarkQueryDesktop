"""Exercise the installed desktop client's real UI and wait for its output."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from pywinauto import Application


def wait_until(predicate, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise TimeoutError(label)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("process_id", type=int)
    parser.add_argument("--timeout", type=int, default=900)
    arguments = parser.parse_args()

    tasks_root = Path(os.environ["LOCALAPPDATA"]) / "TrademarkQueryDesktop" / "tasks"
    existing = {path.name for path in tasks_root.iterdir() if path.is_dir()} if tasks_root.exists() else set()
    window = Application(backend="uia").connect(process=arguments.process_id).window(
        title_re=".*商标查询助手.*"
    )
    window.restore()
    window.set_focus()

    start = window.child_window(title="开始查询", control_type="Button")
    if not start.is_enabled():
        check_buttons = [
            button for button in window.descendants(control_type="Button")
            if button.window_text() == "我已登录，检查并进入查询"
        ]
        if check_buttons:
            check_buttons[0].invoke()
        wait_until(lambda: start.is_enabled(), 45, "login preflight did not enable Start")
    print("PREFLIGHT_READY", flush=True)
    start.invoke()
    wait_until(
        lambda: tasks_root.exists() and any(
            path.is_dir() and path.name not in existing for path in tasks_root.iterdir()
        ),
        30,
        "desktop did not create a task",
    )
    fresh = [path for path in tasks_root.iterdir() if path.is_dir() and path.name not in existing]
    task_dir = max(fresh, key=lambda path: path.stat().st_ctime)
    print("TASK_DIR", task_dir, flush=True)

    minimize_buttons = [
        button for button in window.descendants(control_type="Button")
        if button.window_text() == "最小化" and button.is_visible()
    ]
    if not minimize_buttons:
        raise AssertionError("desktop minimize button is missing")
    minimize_buttons[0].invoke()
    wait_until(window.is_minimized, 10, "desktop minimize button did not work")
    print("MINIMIZED", flush=True)

    result_path = task_dir / "result.json"
    deadline = time.monotonic() + arguments.timeout
    last_status = None
    while time.monotonic() < deadline:
        if result_path.is_file():
            summary = json.loads(result_path.read_text(encoding="utf-8"))
            print("SUMMARY", json.dumps(summary, ensure_ascii=True), flush=True)
            return
        checkpoint_path = task_dir / "checkpoint.json"
        if checkpoint_path.is_file():
            try:
                checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                status = (checkpoint.get("state"), tuple(checkpoint.get("completed_rows", [])))
                if status != last_status:
                    print("CHECKPOINT", status, flush=True)
                    last_status = status
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(2)
    raise TimeoutError(f"desktop task timed out: {task_dir}")


if __name__ == "__main__":
    main()
