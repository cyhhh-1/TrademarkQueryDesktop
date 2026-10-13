"""Targeted field and evidence updates for supported workbooks."""

from __future__ import annotations

import os
from copy import copy
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter
from PIL import Image

from trademark_worker.models import LookupJob, LookupMode, RowResult
from trademark_worker.workbook.inspector import WorkbookInspection


class WorkbookOutputError(RuntimeError):
    pass


MAX_EMBEDDED_IMAGE_WIDTH_PIXELS = 720
MIN_SCREENSHOT_COLUMN_WIDTH = 90.0
MIN_APPLICANT_COLUMN_WIDTH = 28.0
MIN_TERM_COLUMN_WIDTH = 32.0
MAX_EXCEL_ROW_HEIGHT_POINTS = 409.0
PIXELS_PER_POINT = 4 / 3
EXECUTION_LOG_SHEET = "查询执行记录"
EXECUTION_LOG_HEADERS = ("原行号", "商标", "最终状态", "尝试次数", "说明")
QUERY_RESULT_HEADER = "查询结果"
QUERY_RESULT_SUCCESS = "成功"
MODEL_USAGE_HEADER = "是否调用大模型"


def validate_output(path: Path) -> None:
    path = Path(path)
    try:
        with path.open("rb") as stream:
            if stream.read(2) != b"PK":
                raise WorkbookOutputError("结果文件不是有效的 XLSX")
        workbook = load_workbook(path, read_only=False, data_only=False)
        workbook.close()
    except WorkbookOutputError:
        raise
    except Exception as exc:
        raise WorkbookOutputError("结果文件不是有效的 XLSX") from exc


def apply_row_result(
    working_xlsx: Path,
    inspection: WorkbookInspection,
    job: LookupJob,
    result: RowResult,
    evidence: Path | None,
) -> None:
    working_xlsx = Path(working_xlsx)
    evidence_path = Path(evidence) if evidence is not None else None
    if evidence_path is not None and not evidence_path.is_file():
        raise WorkbookOutputError("证据图片不存在")

    workbook = load_workbook(working_xlsx, read_only=False, data_only=False)
    image_buffer: BytesIO | None = None
    try:
        sheet = workbook[inspection.worksheet]
        if inspection.mode is LookupMode.APPLICATION:
            sheet.cell(job.row, inspection.columns["applicant"]).value = result.applicant
            sheet.cell(job.row, inspection.columns["term"]).value = result.term
            query_result_column = _ensure_query_result_column(
                sheet, inspection.columns["screenshot"]
            )
            sheet.cell(job.row, query_result_column).value = (
                QUERY_RESULT_SUCCESS if result.status == "success" else None
            )
        else:
            sheet.cell(job.row, inspection.columns["uniqueness"]).value = result.uniqueness
            if result.uniqueness == "是":
                sheet.cell(job.row, inspection.columns["applicant"]).value = result.applicant
                sheet.cell(job.row, inspection.columns["term"]).value = result.term
            else:
                sheet.cell(job.row, inspection.columns["applicant"]).value = None
                sheet.cell(job.row, inspection.columns["term"]).value = None

        _ensure_column_width(
            sheet,
            inspection.columns["applicant"],
            MIN_APPLICANT_COLUMN_WIDTH,
        )
        _ensure_column_width(
            sheet,
            inspection.columns["term"],
            MIN_TERM_COLUMN_WIDTH,
        )

        if evidence_path is not None:
            screenshot_column = inspection.columns["screenshot"]
            _remove_image_at(sheet, job.row, screenshot_column)
            with Image.open(evidence_path) as source:
                max_height_pixels = int(
                    MAX_EXCEL_ROW_HEIGHT_POINTS * PIXELS_PER_POINT
                )
                minimum_column_pixels = round(MIN_SCREENSHOT_COLUMN_WIDTH * 7 + 5)
                canvas_width = max(source.width, minimum_column_pixels)
                scale = min(
                    1.0,
                    MAX_EMBEDDED_IMAGE_WIDTH_PIXELS / canvas_width,
                    max_height_pixels / source.height,
                )
                image_width = max(1, round(canvas_width * scale))
                image_height = max(1, round(source.height * scale))
                prepared = source.convert("RGBA")
                # Keep the evidence pixels at their original resolution inside
                # the XLSX.  Only the drawing extent is scaled to Excel's row
                # height limit so a user can enlarge/extract a tall uniqueness
                # composite without discovering that its text was destroyed.
                canvas = Image.new("RGB", (canvas_width, source.height), "white")
                canvas.paste(
                    prepared,
                    ((canvas_width - source.width) // 2, 0),
                    prepared,
                )
                image_buffer = BytesIO()
                canvas.save(image_buffer, format="PNG")
                image_buffer.seek(0)
                prepared.close()
                canvas.close()

            excel_image = ExcelImage(image_buffer)
            excel_image.width = image_width
            excel_image.height = image_height
            required_height = excel_image.height / PIXELS_PER_POINT
            current_height = sheet.row_dimensions[job.row].height or 15
            sheet.row_dimensions[job.row].height = min(
                MAX_EXCEL_ROW_HEIGHT_POINTS,
                max(current_height, required_height),
            )
            column_letter = get_column_letter(screenshot_column)
            required_width = max(
                MIN_SCREENSHOT_COLUMN_WIDTH, (excel_image.width - 5) / 7
            )
            current_width = sheet.column_dimensions[column_letter].width or 13
            sheet.column_dimensions[column_letter].width = max(
                current_width, required_width
            )
            excel_image.anchor = TwoCellAnchor(
                editAs="twoCell",
                _from=AnchorMarker(
                    col=screenshot_column - 1,
                    row=job.row - 1,
                ),
                to=AnchorMarker(
                    col=screenshot_column,
                    row=job.row,
                ),
            )
            sheet.add_image(excel_image)

        _atomic_save(workbook, working_xlsx)
    finally:
        workbook.close()
        if image_buffer is not None:
            image_buffer.close()


def write_row_error(
    working_xlsx: Path,
    inspection: WorkbookInspection,
    job: LookupJob,
    message: str,
) -> None:
    """Write a final failure reason into the otherwise empty screenshot cell."""
    working_xlsx = Path(working_xlsx)
    workbook = load_workbook(working_xlsx, read_only=False, data_only=False)
    try:
        sheet = workbook[inspection.worksheet]
        screenshot_column = inspection.columns["screenshot"]
        if inspection.mode is LookupMode.APPLICATION:
            query_result_column = _ensure_query_result_column(
                sheet, screenshot_column
            )
            sheet.cell(job.row, query_result_column).value = None
        _remove_image_at(sheet, job.row, screenshot_column)
        cell = sheet.cell(job.row, screenshot_column)
        reason = str(message or "查询失败").strip() or "查询失败"
        cell.value = f"错误原因：{reason}"
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        _ensure_column_width(
            sheet,
            screenshot_column,
            MIN_SCREENSHOT_COLUMN_WIDTH,
        )
        current_height = sheet.row_dimensions[job.row].height or 15
        sheet.row_dimensions[job.row].height = max(current_height, 45)
        _atomic_save(workbook, working_xlsx)
    finally:
        workbook.close()


def _ensure_query_result_column(sheet, screenshot_column: int) -> int:
    """Add the application result column immediately after the screenshot."""

    target = screenshot_column + 1
    for column in range(1, sheet.max_column + 1):
        if str(sheet.cell(1, column).value or "").strip() == QUERY_RESULT_HEADER:
            return column

    header = sheet.cell(1, target)
    if header.value not in (None, ""):
        sheet.insert_cols(target, 1)
        header = sheet.cell(1, target)

    source = sheet.cell(1, screenshot_column)
    header.value = QUERY_RESULT_HEADER
    if source.has_style:
        header._style = copy(source._style)
    if source.font:
        header.font = copy(source.font)
    if source.fill:
        header.fill = copy(source.fill)
    if source.border:
        header.border = copy(source.border)
    if source.alignment:
        header.alignment = copy(source.alignment)
    if source.protection:
        header.protection = copy(source.protection)
    header.number_format = source.number_format
    sheet.column_dimensions[get_column_letter(target)].width = max(
        sheet.column_dimensions[get_column_letter(target)].width or 13,
        14,
    )

    if sheet.auto_filter.ref:
        from openpyxl.utils.cell import range_boundaries

        min_col, min_row, max_col, max_row = range_boundaries(sheet.auto_filter.ref)
        if max_col == screenshot_column:
            sheet.auto_filter.ref = (
                f"{get_column_letter(min_col)}{min_row}:"
                f"{get_column_letter(target)}{max_row}"
            )
    return target


def record_execution_status(
    working_xlsx: Path,
    job: LookupJob,
    status: str,
    attempts: int,
    message: str = "",
    *,
    inspection: WorkbookInspection | None = None,
    model_used: bool | None = None,
) -> None:
    """Upsert one employee-readable execution row without changing template columns."""
    workbook = load_workbook(working_xlsx, read_only=False, data_only=False)
    try:
        if inspection is not None and model_used is not None:
            result_sheet = workbook[inspection.worksheet]
            screenshot_column = inspection.columns["screenshot"]
            if inspection.mode is LookupMode.APPLICATION:
                _ensure_query_result_column(result_sheet, screenshot_column)
            usage_column = _ensure_model_usage_column(
                result_sheet, screenshot_column
            )
            usage_cell = result_sheet.cell(job.row, usage_column)
            usage_cell.value = (
                "是" if model_used or usage_cell.value == "是" else "否"
            )

        if EXECUTION_LOG_SHEET in workbook.sheetnames:
            sheet = workbook[EXECUTION_LOG_SHEET]
        else:
            sheet = workbook.create_sheet(EXECUTION_LOG_SHEET)
            sheet.append(EXECUTION_LOG_HEADERS)
            sheet.freeze_panes = "A2"
            widths = (12, 28, 14, 12, 64)
            for index, width in enumerate(widths, start=1):
                sheet.column_dimensions[get_column_letter(index)].width = width

        target_row = None
        for row in range(2, sheet.max_row + 1):
            if sheet.cell(row, 1).value == job.row:
                target_row = row
                break
        if target_row is None:
            target_row = sheet.max_row + 1

        status_labels = {
            "success": "成功",
            "not_found": "未找到",
            "failed": "失败",
            "retry_pending": "等待重试",
            "paused": "已暂停",
        }
        values = (
            job.row,
            job.mark,
            status_labels.get(status, status),
            attempts,
            message,
        )
        for column, value in enumerate(values, start=1):
            sheet.cell(target_row, column).value = value
        _atomic_save(workbook, Path(working_xlsx))
    finally:
        workbook.close()


def _ensure_model_usage_column(sheet, screenshot_column: int) -> int:
    for column in range(1, sheet.max_column + 1):
        if str(sheet.cell(1, column).value or "").strip() == MODEL_USAGE_HEADER:
            target = column
            break
    else:
        target = sheet.max_column + 1
        header = sheet.cell(1, target)
        source = sheet.cell(1, screenshot_column)
        header.value = MODEL_USAGE_HEADER
        if source.has_style:
            header._style = copy(source._style)
        sheet.column_dimensions[get_column_letter(target)].width = 20

    if sheet.auto_filter.ref:
        from openpyxl.utils.cell import range_boundaries

        min_col, min_row, max_col, max_row = range_boundaries(sheet.auto_filter.ref)
        sheet.auto_filter.ref = (
            f"{get_column_letter(min_col)}{min_row}:"
            f"{get_column_letter(max(max_col, target))}{max(max_row, sheet.max_row)}"
        )
    else:
        sheet.auto_filter.ref = f"A1:{get_column_letter(target)}{sheet.max_row}"
    return target


def _atomic_save(workbook, target: Path) -> None:
    temporary = target.with_suffix(".writing.xlsx")
    try:
        workbook.save(temporary)
        validate_output(temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _remove_image_at(sheet, row: int, column: int) -> None:
    target_row = row - 1
    target_column = column - 1
    retained = []
    for image in sheet._images:
        anchor = getattr(image, "anchor", None)
        marker = getattr(anchor, "_from", None)
        if marker is not None and marker.row == target_row and marker.col == target_column:
            continue
        retained.append(image)
    sheet._images = retained


def _ensure_column_width(sheet, column: int, minimum: float) -> None:
    column_letter = get_column_letter(column)
    current = sheet.column_dimensions[column_letter].width or 13
    sheet.column_dimensions[column_letter].width = max(current, minimum)
