from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest
from openpyxl import Workbook


WORKER_ROOT = Path(__file__).parents[1]
PACKAGED_WORKER = (
    WORKER_ROOT / "dist" / "trademark-worker" / "trademark-worker.exe"
)


@pytest.mark.skipif(
    not PACKAGED_WORKER.is_file(), reason="packaged Worker has not been built"
)
def test_packaged_worker_can_start_playwright_driver() -> None:
    completed = subprocess.run(
        [PACKAGED_WORKER, "--playwright-smoke"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert completed.stdout.decode("utf-8").strip() == "chromium"


@pytest.mark.skipif(
    not PACKAGED_WORKER.is_file(), reason="packaged Worker has not been built"
)
def test_packaged_worker_can_run_local_ocr_model() -> None:
    completed = subprocess.run(
        [PACKAGED_WORKER, "--ocr-smoke"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    assert completed.stdout.decode("utf-8").strip() == "rapidocr"


@pytest.mark.skipif(
    not PACKAGED_WORKER.is_file(), reason="packaged Worker has not been built"
)
def test_packaged_worker_uses_utf8_for_chinese_workbook_paths(
    tmp_path: Path,
) -> None:
    source = tmp_path / "商标申请.xlsx"
    workbook = Workbook()
    workbook.active.append(["商标名称", "商标申请人", "专用权期限", "截图"])
    workbook.active.append(["云杉月付", "北京云杉网络技术有限公司", "", ""])
    workbook.save(source)

    commands = [
        {"protocol": 1, "type": "inspect_file", "path": str(source)},
        {"protocol": 1, "type": "shutdown"},
    ]
    payload = "".join(
        json.dumps(command, ensure_ascii=False, separators=(",", ":")) + "\n"
        for command in commands
    ).encode("utf-8")
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"

    completed = subprocess.run(
        [PACKAGED_WORKER],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    events = [
        json.loads(line)
        for line in completed.stdout.decode("utf-8").splitlines()
        if line.strip()
    ]
    inspected = next(event for event in events if event["type"] == "file_inspected")
    assert inspected["path"] == str(source)
    assert inspected["mode"] == "application"
    assert inspected["rows"] == [
        {
            "row": 2,
            "mark": "云杉月付",
            "applicant": "北京云杉网络技术有限公司",
        }
    ]
