from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import threading
import time


def accept_completed_event(event: dict[str, object]) -> tuple[bool, str]:
    """Accept a live run only when every input row finished successfully."""
    raw_items = event.get("items", [])
    items = raw_items if isinstance(raw_items, list) else []
    failures: list[str] = []
    for raw_item in items:
        item = raw_item if isinstance(raw_item, dict) else {}
        if item.get("status") == "success":
            continue
        row = item.get("row", "?")
        mark = item.get("mark", "")
        status = item.get("status", "missing")
        message = item.get("message", "")
        failures.append(
            f"第 {row} 行 {mark!s}（{status!s}）{': ' + str(message) if message else ''}"
        )
    if not items:
        return False, "completed 事件中没有任何行结果"
    if failures:
        return False, "存在未成功行：" + "；".join(failures)
    return True, f"全部 {len(items)}/{len(items)} 行成功"


def next_task_event(
    events: Queue[dict[str, object]], process: subprocess.Popen[str], timeout: float
) -> dict[str, object] | None:
    """An idle-but-running worker must not fail a long CNIPA batch."""
    try:
        return events.get(timeout=timeout)
    except Empty as exc:
        if process.poll() is None:
            return None
        stderr = process.stderr.read() if process.stderr else ""
        raise TimeoutError(f"worker exited without a completed event; stderr={stderr}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one live XLSX through the packaged Worker.")
    parser.add_argument("worker", type=Path)
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--allow-failed-rows",
        action="store_true",
        help="仅用于验证容错流程；正式验收不要启用。",
    )
    args = parser.parse_args()

    worker = args.worker.resolve()
    xlsx = args.xlsx.resolve()
    if not worker.is_file() or not xlsx.is_file():
        raise FileNotFoundError(f"worker={worker}, xlsx={xlsx}")

    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    process = subprocess.Popen(
        [str(worker)],
        cwd=str(worker.parent),
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
        bufsize=1,
    )
    events: Queue[dict[str, object]] = Queue()

    def read_events() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            events.put(json.loads(line))

    threading.Thread(target=read_events, daemon=True).start()

    def send(payload: dict[str, object]) -> None:
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def receive(timeout: float = 30.0) -> dict[str, object]:
        try:
            event = events.get(timeout=timeout)
        except Empty as exc:
            stderr = process.stderr.read() if process.poll() is not None and process.stderr else ""
            raise TimeoutError(f"worker event timed out; stderr={stderr}") from exc
        print(json.dumps(event, ensure_ascii=True))
        return event

    try:
        hello = receive()
        if hello.get("type") != "hello" or not hello.get("worker_version"):
            raise AssertionError(f"unexpected worker hello: {hello}")

        send({"type": "inspect_file", "protocol": 1, "path": str(xlsx)})
        inspection = receive()
        if inspection.get("type") != "file_inspected":
            raise AssertionError(f"input inspection failed: {inspection}")

        send({"type": "open_browser", "protocol": 1})
        receive(timeout=45)
        browser_state = ""
        for _ in range(6):
            time.sleep(2)
            send({"type": "connect_browser", "protocol": 1})
            event = receive(timeout=45)
            browser_state = str(event.get("state", ""))
            if browser_state == "READY":
                break
        if browser_state != "READY":
            print(f"LIVE_LOGIN_REQUIRED state={browser_state}")
            return 2

        run_id = f"live-smoke-{int(time.time())}"
        send(
            {
                "type": "start",
                "protocol": 1,
                "run_id": run_id,
                "path": str(xlsx),
                "overwrite": False,
            }
        )
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            event = next_task_event(
                events, process, timeout=min(10, max(1, deadline - time.monotonic()))
            )
            if event is None:
                print("LIVE_WAITING", flush=True)
                continue
            print(json.dumps(event, ensure_ascii=True), flush=True)
            if event.get("type") == "completed":
                print("LIVE_COMPLETED", json.dumps(event, ensure_ascii=True))
                accepted, message = accept_completed_event(event)
                print("LIVE_ACCEPTANCE", message)
                if accepted or args.allow_failed_rows:
                    return 0
                return 3
            if event.get("type") == "error":
                raise AssertionError(f"live task failed: {event}")
        raise TimeoutError("live XLSX task did not complete before timeout")
    finally:
        if process.poll() is None:
            try:
                send({"type": "shutdown", "protocol": 1})
                process.wait(timeout=25)
            except Exception:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
