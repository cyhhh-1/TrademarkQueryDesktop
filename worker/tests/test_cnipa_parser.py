from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from PIL import Image

from trademark_worker.cnipa.client import CnipaClient, document_pixel_box
from trademark_worker.cnipa.parser import (
    parse_detail,
    parse_detail_html,
    parse_result_html,
    parse_result_table,
)
from trademark_worker.imaging.evidence import PixelBox
from trademark_worker.models import DetailRecord, LookupJob, ResultRecord
from trademark_worker.vision import VisionRecognitionError


FIXTURES = Path(__file__).parent / "fixtures" / "html"


def test_document_pixel_box_includes_page_scroll_offset() -> None:
    box = document_pixel_box(
        {
            "x": 12.5,
            "y": 30.0,
            "width": 500.0,
            "height": 88.0,
            "scrollX": 4.0,
            "scrollY": 760.0,
            "scale": 1.25,
        }
    )

    assert box == PixelBox(16.5, 790.0, 500.0, 88.0, 1.25)


def test_parses_complete_result_row() -> None:
    rows = [
        ["序号", "申请/注册号", "国际分类", "申请日期", "商标", "申请人名称"],
        ["2", "93296493", "35", "2026年08月03日", "美团", "北京三快科技有限公司"],
    ]

    assert parse_result_table(rows) == [
        ResultRecord(
            2,
            "93296493",
            "35",
            "2026年08月03日",
            "美团",
            "北京三快科技有限公司",
            page_number=1,
            row_index=0,
        )
    ]


def test_result_parser_ignores_unrelated_tables_and_reads_fixture() -> None:
    html = "<table><tr><td>页脚</td></tr></table>" + (
        FIXTURES / "application-list.html"
    ).read_text(encoding="utf-8")

    records = parse_result_html(html)

    assert [(item.sequence, item.registration_no, item.mark) for item in records] == [
        (1, "66227440", "抖音月付")
    ]


def test_active_detail_reads_chinese_applicant_and_term() -> None:
    detail = parse_detail(
        {
            "商标": "抖音月付",
            "申请/注册号": "66227440",
            "申请人名称（中文）": "北京字跳网络技术有限公司",
            "专用权期限": "2023年08月28日至2033年08月27日",
        }
    )

    assert detail == DetailRecord(
        mark="抖音月付",
        registration_no="66227440",
        applicant="北京字跳网络技术有限公司",
        term="2023年08月28日至2033年08月27日",
    )


def test_pending_detail_has_no_term() -> None:
    detail = parse_detail(
        {"商标": "抖音月付", "申请/注册号": "1", "申请人名称（英文）": "Example Inc."}
    )

    assert detail.term is None
    assert detail.applicant == "Example Inc."


def test_detail_html_reads_adjacent_label_values() -> None:
    detail = parse_detail_html(
        (FIXTURES / "detail-active.html").read_text(encoding="utf-8")
    )

    assert detail.mark == "抖音月付"
    assert detail.applicant == "北京字跳网络技术有限公司"
    assert detail.term == "2023年08月28日 至 2033年08月27日"


class FakePagePort:
    def __init__(self) -> None:
        self.pages: dict[int, list[list[str]]] = {
            1: [
                ["序号", "申请/注册号", "国际分类", "申请日期", "商标", "申请人名称"],
                ["1", "9300001", "35", "2026年08月03日", "美团月付服务", "甲公司"],
                ["2", "93296493", "35", "2026年08月03日", "美团", "北京三快科技有限公司"],
            ],
            2: [
                ["序号", "申请/注册号", "国际分类", "申请日期", "商标", "申请人名称"],
                ["10", "93296492", "25", "2026年08月03日", "美团", "北京三快科技有限公司"],
            ],
        }
        self.page_number = 1
        self.visited_pages: list[int] = []
        self.filled: tuple[str, str | None, str | None] | None = None
        self.clicked: tuple[int, str] | None = None
        self.events: list[str] = []
        self.result_captures = 0
        self.detail_captures = 0
        self.enriched_pages: list[tuple[int, str]] = []
        self.detail_labels: Mapping[str, str] = {
            "商标": "美团",
            "申请/注册号": "93296493",
            "申请人名称（中文）": "北京三快科技有限公司",
            "专用权期限": "2026年至2036年",
        }

    def require_search_ready(self) -> None:
        self.events.append("search_ready")

    def fill_search(
        self, mark: str, applicant: str | None, applicant_language: str | None
    ) -> None:
        self.filled = (mark, applicant, applicant_language)

    def submit_search(self) -> None:
        self.page_number = 1
        self.events.append("submitted")

    def require_results_ready(self) -> None:
        self.events.append(f"results_ready:{self.page_number}")

    def read_result_rows(self) -> Sequence[Sequence[str]]:
        self.visited_pages.append(self.page_number)
        return self.pages[self.page_number]

    def enrich_result_marks(
        self, records: Sequence[ResultRecord], expected_mark: str
    ) -> Sequence[ResultRecord]:
        self.enriched_pages.append((self.page_number, expected_mark))
        return records

    def total_pages(self) -> int:
        return len(self.pages)

    def go_to_page(self, page_number: int) -> None:
        self.page_number = page_number

    def refresh_results(self, page_number: int) -> None:
        self.events.append(f"refreshed:{page_number}")
        self.page_number = page_number

    def click_mark_cell(self, row_index: int, sequence: int) -> None:
        self.clicked = (sequence, "mark_cell")

    def require_detail_ready(self) -> None:
        self.events.append("detail_ready")

    def read_detail_labels(self) -> Mapping[str, str]:
        return self.detail_labels

    def capture_result_page(self, output: Path) -> Path:
        self.result_captures += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (240, 180), "white").save(output)
        return output

    def capture_detail_page(self, output: Path) -> Path:
        self.detail_captures += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (240, 180), "white").save(output)
        return output

    def capture_result_row(self, row_index: int, output: Path) -> Path:
        self.result_captures += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (200, 28), "white").save(output)
        return output

    def capture_result_header(self, output: Path) -> Path:
        self.result_captures += 1
        Image.new("RGB", (200, 40), "white").save(output)
        return output

    def capture_result_footer(self, output: Path) -> Path:
        self.result_captures += 1
        Image.new("RGB", (200, 40), "white").save(output)
        return output

    def result_row_box(self, row_index: int) -> PixelBox:
        return PixelBox(5, 10 + row_index * 30, 200, 28)


def test_uniqueness_search_visits_every_page_and_keeps_page_identity() -> None:
    port = FakePagePort()
    progress: list[tuple[int, int]] = []

    records = CnipaClient(port).search(
        LookupJob.uniqueness(2, "美团"),
        page_progress=lambda current, total: progress.append((current, total)),
    )

    assert port.visited_pages == [1, 2]
    assert [item.registration_no for item in records] == [
        "9300001",
        "93296493",
        "93296492",
    ]
    assert [(item.page_number, item.row_index) for item in records] == [
        (1, 0),
        (1, 1),
        (2, 0),
    ]
    assert port.filled == ("美团", None, None)
    assert port.enriched_pages == [(1, "美团"), (2, "美团")]
    assert progress == [(1, 2), (2, 2)]


def test_application_page_stream_does_not_load_later_pages_after_early_stop() -> None:
    port = FakePagePort()
    pages = CnipaClient(port).search_pages(
        LookupJob.application(2, "美团", "北京三快科技有限公司")
    )

    first_page = next(pages)
    pages.close()

    assert [item.registration_no for item in first_page] == ["9300001", "93296493"]
    assert port.visited_pages == [1]
    assert port.filled == ("美团", "北京三快科技有限公司", "zh")


def test_search_refreshes_a_transiently_broken_result_page() -> None:
    class TransientPage(FakePagePort):
        failed = False

        def read_result_rows(self):
            if self.page_number == 1 and not self.failed:
                self.failed = True
                raise RuntimeError("网站结果表异常")
            return super().read_result_rows()

    port = TransientPage()

    records = CnipaClient(port).search(LookupJob.uniqueness(2, "美团"))

    assert len(records) == 3
    assert "refreshed:1" in port.events


def test_search_does_not_refresh_results_for_ocr_timeout() -> None:
    class OcrTimeoutPage(FakePagePort):
        def enrich_result_marks(self, records, expected_mark):
            del records, expected_mark
            raise VisionRecognitionError("本地及多模态识别均未完成")

    port = OcrTimeoutPage()

    try:
        CnipaClient(port).search(LookupJob.uniqueness(2, "扶馨陪诊"))
    except VisionRecognitionError:
        pass
    else:
        raise AssertionError("OCR timeout must be deferred without refreshing the page")

    assert not any(event.startswith("refreshed:") for event in port.events)


def test_application_uses_chinese_applicant_field() -> None:
    port = FakePagePort()

    CnipaClient(port).search(
        LookupJob.application(2, "抖音月付", "北京字跳网络技术有限公司")
    )

    assert port.filled == ("抖音月付", "北京字跳网络技术有限公司", "zh")


def test_application_opens_mark_cell_not_registration_link() -> None:
    port = FakePagePort()
    record = ResultRecord(
        2,
        "66227440",
        "36",
        "2022年07月27日",
        "抖音月付",
        "北京字跳网络技术有限公司",
        page_number=1,
        row_index=0,
    )

    detail = CnipaClient(port).open_detail(record)

    assert port.clicked == (2, "mark_cell")
    assert detail.registration_no == "93296493"
    assert "detail_ready" in port.events


def test_capture_exact_rows_preserves_record_order_across_pages(tmp_path: Path) -> None:
    port = FakePagePort()
    client = CnipaClient(port)
    records = [
        replace(parse_result_table(port.pages[2], page_number=2)[0]),
        replace(parse_result_table(port.pages[1], page_number=1)[1]),
    ]

    composite = client.capture_exact_rows(records, tmp_path)

    with Image.open(composite) as image:
        assert image.size == (200, 172)
    assert composite.name == "exact-results.png"
    assert port.result_captures == 4
    assert port.detail_captures == 0


def test_optional_result_context_failure_keeps_representative_rows(
    tmp_path: Path,
) -> None:
    class BrokenContextPort(FakePagePort):
        def capture_result_header(self, output: Path) -> Path:
            raise RuntimeError("上下文不在截图范围内")

        def capture_result_footer(self, output: Path) -> Path:
            raise RuntimeError("上下文不在截图范围内")

    port = BrokenContextPort()
    client = CnipaClient(port)
    records = [replace(parse_result_table(port.pages[1], page_number=1)[1])]

    composite = client.capture_exact_rows(records, tmp_path)

    assert composite.is_file()
    with Image.open(composite) as image:
        assert image.size == (200, 28)


def test_capture_detail_uses_detail_page_not_result_page(tmp_path: Path) -> None:
    port = FakePagePort()
    output = tmp_path / "detail.png"

    assert CnipaClient(port).capture_detail(output) == output
    assert output.is_file()
    assert port.detail_captures == 1
    assert port.result_captures == 0
