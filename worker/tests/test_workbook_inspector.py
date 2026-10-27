from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest
from openpyxl import Workbook

from trademark_worker.models import LookupMode
from trademark_worker.workbook.inspector import (
    WorkbookInputError,
    inspect_workbook,
    load_jobs,
    prepare_working_copy,
)
from trademark_worker.workbook.writer import record_execution_status


def make_xlsx(
    tmp_path: Path, name: str, headers: list[str], rows: list[list[object]]
) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.create_sheet("Sheet2")
    path = tmp_path / name
    workbook.save(path)
    return path


def test_detects_application_template_by_headers(tmp_path: Path) -> None:
    path = make_xlsx(
        tmp_path,
        "application.xlsx",
        ["商标名称", "商标申请人", "专用权期限", "截图"],
        [["云杉月付", "北京云杉网络技术有限公司", None, None]],
    )

    inspection = inspect_workbook(path)

    assert inspection.mode is LookupMode.APPLICATION
    assert inspection.worksheet == "Sheet1"
    assert inspection.columns == {
        "mark": 1,
        "applicant": 2,
        "term": 3,
        "screenshot": 4,
    }
    assert inspection.task_count == 1


def test_detects_uniqueness_template_with_real_multiline_headers(
    tmp_path: Path,
) -> None:
    path = make_xlsx(
        tmp_path,
        "uniqueness.xlsx",
        [
            "商标名称",
            "是否唯一性\n（当申请公司有多家时填写“否”",
            "商标申请人\n当是否唯一性“否”时此处空白",
            "商标有效期",
            "截图",
        ],
        [["青禾月付", None, None, None, None]],
    )

    inspection = inspect_workbook(path)

    assert inspection.mode is LookupMode.UNIQUENESS
    assert inspection.columns == {
        "mark": 1,
        "uniqueness": 2,
        "applicant": 3,
        "term": 4,
        "screenshot": 5,
    }


def test_working_copy_does_not_change_source(tmp_path: Path) -> None:
    source = make_xlsx(
        tmp_path,
        "商标申请.xlsx",
        ["商标名称", "商标申请人", "专用权期限", "截图"],
        [["云杉月付", "北京云杉网络技术有限公司", None, None]],
    )
    digest = sha256(source.read_bytes()).hexdigest()

    working = prepare_working_copy(source, tmp_path / "run")

    assert working == tmp_path / "run" / "output" / "商标申请_已查询.xlsx"
    assert (tmp_path / "run" / "input" / "商标申请.xlsx").exists()
    assert sha256(source.read_bytes()).hexdigest() == digest
    assert sha256(working.read_bytes()).hexdigest() == digest


def test_read_only_source_produces_writable_result_copy(tmp_path: Path) -> None:
    source = make_xlsx(
        tmp_path,
        "只读商标申请.xlsx",
        ["商标名称", "商标申请人", "专用权期限", "截图"],
        [["陶器网", "义乌市示例电子商务有限公司", None, None]],
    )
    source.chmod(0o444)
    original_hash = sha256(source.read_bytes()).hexdigest()
    inspection = inspect_workbook(source)
    job = load_jobs(source, inspection)[0]

    working = prepare_working_copy(source, tmp_path / "run")
    record_execution_status(working, job, "success", 1)

    assert sha256(source.read_bytes()).hexdigest() == original_hash
    assert not source.stat().st_mode & 0o200
    assert working.stat().st_mode & 0o200


def test_load_jobs_reads_every_nonempty_mark_and_applicant(tmp_path: Path) -> None:
    path = make_xlsx(
        tmp_path,
        "application.xlsx",
        ["商标名称", "商标申请人", "专用权期限", "截图"],
        [
            ["云杉月付", "北京云杉网络技术有限公司", None, None],
            [None, None, None, None],
            ["英文商标", "Example Holdings Ltd.", None, None],
        ],
    )
    inspection = inspect_workbook(path)

    jobs = load_jobs(path, inspection, overwrite=False)

    assert [(job.row, job.mark, job.applicant) for job in jobs] == [
        (2, "云杉月付", "北京云杉网络技术有限公司"),
        (4, "英文商标", "Example Holdings Ltd."),
    ]


def test_existing_result_is_skipped_unless_overwrite_enabled(tmp_path: Path) -> None:
    path = make_xlsx(
        tmp_path,
        "application.xlsx",
        ["商标名称", "商标申请人", "专用权期限", "截图"],
        [["云杉月付", "北京云杉网络技术有限公司", "2023至2033", None]],
    )
    inspection = inspect_workbook(path)

    assert load_jobs(path, inspection, overwrite=False) == []
    assert len(load_jobs(path, inspection, overwrite=True)) == 1


def test_missing_required_header_has_stable_code(tmp_path: Path) -> None:
    path = make_xlsx(
        tmp_path,
        "bad.xlsx",
        ["商标名称", "商标申请人", "截图"],
        [["云杉月付", "北京云杉网络技术有限公司", None]],
    )

    with pytest.raises(WorkbookInputError) as captured:
        inspect_workbook(path)

    assert captured.value.code == "TEMPLATE_MISSING_HEADER"
    assert "专用权期限" in str(captured.value)


def test_application_row_without_applicant_is_rejected(tmp_path: Path) -> None:
    path = make_xlsx(
        tmp_path,
        "application.xlsx",
        ["商标名称", "商标申请人", "专用权期限", "截图"],
        [["云杉月付", None, None, None]],
    )
    inspection = inspect_workbook(path)

    with pytest.raises(WorkbookInputError, match="第 2 行缺少商标申请人"):
        load_jobs(path, inspection, overwrite=False)


def test_rejects_non_xlsx_file(tmp_path: Path) -> None:
    path = tmp_path / "input.xlsm"
    path.write_bytes(b"not a workbook")

    with pytest.raises(WorkbookInputError) as captured:
        inspect_workbook(path)

    assert captured.value.code == "UNSUPPORTED_FILE_TYPE"
