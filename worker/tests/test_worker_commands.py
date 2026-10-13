from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from openpyxl import Workbook

from trademark_worker import __version__
from trademark_worker.__main__ import main


WORKER_ROOT = Path(__file__).parents[1]
PYTHON = Path(os.environ.get("TRADEMARK_WORKER_PYTHON", sys.executable))


class WorkerProcess:
    def __init__(self) -> None:
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONPATH"] = str(WORKER_ROOT / "src")
        self.process = subprocess.Popen(
            [str(PYTHON), "-m", "trademark_worker"],
            cwd=WORKER_ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

    def send(self, payload: dict[str, object]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def read(self) -> dict[str, object]:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        assert line, self.process.stderr.read() if self.process.stderr else ""
        return json.loads(line)

    def close(self) -> None:
        if self.process.poll() is None:
            self.send({"type": "shutdown", "protocol": 1})
            self.process.wait(timeout=5)


def make_application_xlsx(tmp_path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["商标名称", "商标申请人", "专用权期限", "截图"])
    sheet.append(["抖音月付", "北京字跳网络技术有限公司", None, None])
    path = tmp_path / "商标申请.xlsx"
    workbook.save(path)
    return path


def test_worker_emits_inspection_then_shuts_down(tmp_path: Path) -> None:
    worker = WorkerProcess()
    try:
        assert worker.read()["type"] == "hello"
        worker.send(
            {
                "type": "inspect_file",
                "protocol": 1,
                "path": str(make_application_xlsx(tmp_path)),
            }
        )

        event = worker.read()

        assert event["type"] == "file_inspected"
        assert event["mode"] == "application"
        assert event["task_count"] == 1
        assert event["size"] > 0
        assert event["rows"] == [
            {"row": 2, "mark": "抖音月付", "applicant": "北京字跳网络技术有限公司"}
        ]
        worker.send({"type": "shutdown", "protocol": 1})
        assert worker.process.wait(timeout=5) == 0
    finally:
        worker.close()


def test_bad_command_becomes_structured_error() -> None:
    worker = WorkerProcess()
    try:
        worker.read()
        worker.send({"type": "unknown", "protocol": 1})

        event = worker.read()

        assert event["type"] == "error"
        assert event["code"] == "UNKNOWN_COMMAND"
    finally:
        worker.close()


def test_inspect_requires_path_without_crashing_worker() -> None:
    worker = WorkerProcess()
    try:
        worker.read()
        worker.send({"type": "inspect_file", "protocol": 1})

        event = worker.read()

        assert event == {
            "type": "error",
            "code": "INVALID_COMMAND",
            "message": "inspect_file 命令缺少 path",
        }
        worker.send({"type": "shutdown", "protocol": 1})
        assert worker.process.wait(timeout=5) == 0
    finally:
        worker.close()


def test_version_flag_prints_version_without_starting_protocol(capsys) -> None:
    assert main(["--version"]) == 0

    assert capsys.readouterr().out == f"{__version__}\n"
