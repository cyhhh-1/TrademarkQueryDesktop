"""CNIPA query orchestration over a browser-independent page port."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Protocol

from trademark_worker.cnipa.parser import parse_detail, parse_result_table
from trademark_worker.imaging.evidence import PixelBox, stack_rows
from trademark_worker.models import DetailRecord, LookupJob, NeedsUserAction, ResultRecord
from trademark_worker.normalization import applicant_field
from trademark_worker.vision import VisionRecognitionError


def document_pixel_box(metrics: Mapping[str, float]) -> PixelBox:
    """Convert viewport-relative metrics into full-page screenshot coordinates."""
    return PixelBox(
        float(metrics["x"]) + float(metrics["scrollX"]),
        float(metrics["y"]) + float(metrics["scrollY"]),
        float(metrics["width"]),
        float(metrics["height"]),
        float(metrics["scale"]),
    )


class PagePort(Protocol):
    def require_search_ready(self) -> None: ...

    def fill_search(
        self, mark: str, applicant: str | None, applicant_language: str | None
    ) -> None: ...

    def submit_search(self) -> None: ...

    def require_results_ready(self) -> None: ...

    def read_result_rows(self) -> Sequence[Sequence[str]]: ...

    def enrich_result_marks(
        self, records: Sequence[ResultRecord], expected_mark: str
    ) -> Sequence[ResultRecord]: ...

    def total_pages(self) -> int: ...

    def go_to_page(self, page_number: int) -> None: ...

    def refresh_results(self, page_number: int) -> None: ...

    def click_mark_cell(self, row_index: int, sequence: int) -> None: ...

    def require_detail_ready(self) -> None: ...

    def read_detail_labels(self) -> Mapping[str, str]: ...

    def capture_result_page(self, output: Path) -> Path: ...

    def capture_detail_page(self, output: Path) -> Path: ...

    def capture_result_row(self, row_index: int, output: Path) -> Path: ...

    def capture_result_header(self, output: Path) -> Path: ...

    def capture_result_footer(self, output: Path) -> Path: ...

    def result_row_box(self, row_index: int) -> PixelBox: ...


class CnipaClient:
    def __init__(self, page: PagePort) -> None:
        self.page = page

    @property
    def remote_model_call_count(self) -> int:
        return int(getattr(self.page, "remote_model_call_count", 0))

    def search(
        self,
        job: LookupJob,
        page_progress: Callable[[int, int], None] | None = None,
    ) -> list[ResultRecord]:
        records: list[ResultRecord] = []
        for page_records in self.search_pages(job, page_progress):
            records.extend(page_records)
        return records

    def search_pages(
        self,
        job: LookupJob,
        page_progress: Callable[[int, int], None] | None = None,
    ) -> Iterator[list[ResultRecord]]:
        """Yield one parsed result page at a time.

        Application lookups can inspect details between yields and stop as soon
        as a valid registration is found.  Uniqueness lookups continue to use
        ``search`` so they still collect every page before making a decision.
        """
        self.page.require_search_ready()
        language = applicant_field(job.applicant) if job.applicant else None
        self.page.fill_search(job.mark, job.applicant, language)
        self.page.submit_search()
        self.page.require_results_ready()

        total_pages = self.page.total_pages()
        for page_number in range(1, total_pages + 1):
            if page_progress is not None:
                page_progress(page_number, total_pages)
            if page_number > 1:
                self.page.go_to_page(page_number)
                self.page.require_results_ready()
            for attempt in range(3):
                try:
                    page_records = parse_result_table(
                        self.page.read_result_rows(), page_number=page_number
                    )
                    enriched = self.page.enrich_result_marks(page_records, job.mark)
                    yield list(enriched)
                    break
                except NeedsUserAction:
                    raise
                except VisionRecognitionError:
                    # OCR/model failures do not make CNIPA's already parsed
                    # table stale. Let the task-level retry defer this mark;
                    # refreshing here would reread the entire page repeatedly.
                    raise
                except Exception as exc:
                    if attempt >= 2:
                        raise RuntimeError(
                            f"商标结果第 {page_number} 页刷新重试后仍不可用：{exc}"
                        ) from exc
                    self.page.refresh_results(page_number)
                    self.page.require_results_ready()

    def open_detail(self, record: ResultRecord) -> DetailRecord:
        if record.row_index is None:
            raise ValueError("检索记录缺少结果行位置")
        self.page.go_to_page(record.page_number)
        self.page.require_results_ready()
        self.page.click_mark_cell(record.row_index, record.sequence)
        self.page.require_detail_ready()
        return parse_detail(self.page.read_detail_labels())

    def capture_exact_rows(
        self, records: Sequence[ResultRecord], output_dir: Path
    ) -> Path:
        if not records:
            raise ValueError("没有可截图的同名商标记录")
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        records_by_page: dict[int, list[tuple[int, ResultRecord]]] = defaultdict(list)
        for position, record in enumerate(records):
            if record.row_index is None:
                raise ValueError("检索记录缺少结果行位置")
            records_by_page[record.page_number].append((position, record))

        crops_by_position: dict[int, Path] = {}
        header: Path | None = None
        footer: Path | None = None
        for page_number, positioned in records_by_page.items():
            self.page.go_to_page(page_number)
            self.page.require_results_ready()
            page_dir = output_dir / f"page-{page_number}-rows"
            if header is None:
                try:
                    header = self.page.capture_result_header(
                        output_dir / "result-header.png"
                    )
                except NeedsUserAction:
                    raise
                except Exception:
                    header = None
            for row_position, (position, record) in enumerate(positioned, start=1):
                crop = self.page.capture_result_row(
                    record.row_index or 0,
                    page_dir / f"row-{row_position:04d}.png",
                )
                crops_by_position[position] = crop
        try:
            footer = self.page.capture_result_footer(output_dir / "result-footer.png")
        except NeedsUserAction:
            raise
        except Exception:
            footer = None

        ordered = [header] if header is not None else []
        ordered.extend(crops_by_position[index] for index in range(len(records)))
        if footer is not None:
            ordered.append(footer)
        return stack_rows(ordered, output_dir / "exact-results.png")

    def capture_detail(self, output: Path) -> Path:
        return self.page.capture_detail_page(Path(output))
