"""Drive the employee's authenticated Edge tab without activating its window.

CNIPA's own form and result click handlers populate sessionStorage.  We invoke
those handlers while suppressing only their popup, then navigate the *same*
tab.  This keeps the user's login and the site's normal query protocol intact.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import re
import time
from typing import Any, Mapping, Sequence

from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from trademark_worker.browser.uia import detail_labels_from_rows, page_counter
from trademark_worker.imaging.evidence import PixelBox
from trademark_worker.local_ocr import LocalMarkRecognizer
from trademark_worker.models import BrowserState, NeedsUserAction, ResultRecord
from trademark_worker.vision import MarkVisionRecognizer, VisionRecognitionError


CNIPA_ORIGIN = "https://wcjs.sbj.cnipa.gov.cn"
RESULTS_API_PATH = "/api/so/com"
RESULTS_STABILITY_SECONDS = 0.6
RESULTS_HTTP_403_REFRESH_DELAY_MS = 3000
EXPIRED_PAGE_REFRESH_DELAY_MS = 1000
MAX_EXPIRED_PAGE_REFRESHES = 10
MARK_OCR_BATCH_SIZE = 15


class TransientSiteError(RuntimeError):
    """A CNIPA temporary error page that is safe to refresh with backoff."""


class ExpiredPageError(TransientSiteError):
    """CNIPA displayed its temporary expired-page response."""
_OPEN_WITHOUT_POPUP = """(element) => {
    const original = window.open;
    const preventAnchorNavigation = event => event.preventDefault();
    window.open = () => null;
    if (element instanceof HTMLAnchorElement) {
        element.addEventListener('click', preventAnchorNavigation, {capture: true});
    }
    try { element.click(); return sessionStorage.getItem('params'); }
    finally {
        element.removeEventListener('click', preventAnchorNavigation, true);
        window.open = original;
    }
}"""


def capture_page_image(page: Any) -> Image.Image:
    """Capture the full active renderer, including when Edge is off-screen."""
    session = page.context.new_cdp_session(page)
    try:
        response = session.send(
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": False, "captureBeyondViewport": True},
        )
    finally:
        session.detach()
    encoded = response.get("data")
    if not encoded:
        raise RuntimeError("Edge 后台截图没有返回图片")
    with Image.open(BytesIO(base64.b64decode(encoded))) as source:
        image = source.convert("RGB")
    if image.width < 10 or image.height < 10:
        image.close()
        raise RuntimeError("Edge 后台截图为空")
    return image


def capture_full_page_image(page: Any) -> Image.Image:
    """Stitch renderer viewports while keeping the off-screen Edge unfocused."""
    metrics_script = (
        "({viewportWidth:innerWidth,viewportHeight:innerHeight,"
        "documentHeight:document.documentElement.scrollHeight,scrollY})"
    )
    metrics = page.evaluate(metrics_script)
    viewport_height = max(1, int(metrics["viewportHeight"]))
    document_height = max(viewport_height, int(metrics["documentHeight"]))
    original_scroll = float(metrics["scrollY"])
    tiles: list[tuple[float, Image.Image]] = []
    try:
        for target in range(0, document_height, viewport_height):
            page.evaluate("y => window.scrollTo(0, y)", target)
            page.wait_for_timeout(100)
            actual_scroll = float(page.evaluate(metrics_script)["scrollY"])
            if tiles and abs(actual_scroll - tiles[-1][0]) < 1:
                continue
            tile = capture_page_image(page)
            tiles.append((actual_scroll, tile))
            scale = tile.width / max(1, float(metrics["viewportWidth"]))
            full_height = round(document_height * scale)
            if tile.height >= full_height - 2:
                tiles.pop()
                return tile

        if not tiles:
            raise RuntimeError("未能截取商标详情页面")
        scale = tiles[0][1].width / max(1, float(metrics["viewportWidth"]))
        canvas = Image.new("RGB", (tiles[0][1].width, round(document_height * scale)), "white")
        previous_end = 0.0
        for index, (scroll_y, tile) in enumerate(tiles):
            if index == 0:
                canvas.paste(tile, (0, round(scroll_y * scale)))
            else:
                # The renderer can draw a viewport border in the first pixels
                # of a screenshot. Join halfway through the shared content.
                split = max(scroll_y, (scroll_y + previous_end) / 2)
                destination_y = round(split * scale)
                source_y = max(0, destination_y - round(scroll_y * scale))
                part = tile.crop((0, source_y, tile.width, tile.height))
                try:
                    canvas.paste(part, (0, destination_y))
                finally:
                    part.close()
            previous_end = scroll_y + tile.height / scale
        return canvas
    finally:
        page.evaluate("y => window.scrollTo(0, y)", original_scroll)
        for _scroll_y, tile in tiles:
            tile.close()


def detail_evidence_crop_box(
    *,
    table_bounds: Sequence[Mapping[str, float]],
    content_bottom: float,
    image_width: int,
    image_height: int,
    document_width: float,
) -> tuple[int, int, int, int]:
    """Keep the CNIPA detail record and status, without browser edges or footer."""
    scale = image_width / max(1, document_width)
    left = max(0, round((min(item["x"] for item in table_bounds) - 95) * scale))
    right = min(
        image_width,
        round((max(item["x"] + item["width"] for item in table_bounds) + 20) * scale),
    )
    bottom = min(image_height, round((content_bottom + 10) * scale))
    return left, 0, right, bottom


def detail_image_is_complete(image: Image.Image) -> bool:
    """Reject captures whose lower detail/status sections never rendered."""
    if image.width < 500 or image.height < 500:
        return False
    grayscale = image.convert("L")
    try:
        populated = 0
        band_count = 5
        for band in range(band_count):
            top = round(image.height * band / band_count)
            bottom = round(image.height * (band + 1) / band_count)
            region = grayscale.crop((0, top, image.width, bottom))
            try:
                histogram = region.histogram()
                non_white = sum(histogram[:245])
                if non_white >= max(100, region.width * region.height * 0.002):
                    populated += 1
            finally:
                region.close()
        return populated >= 4
    finally:
        grayscale.close()


def row_boxes_from_metrics(
    metrics: Sequence[Mapping[str, float]],
    *,
    scroll_x: float,
    scroll_y: float,
    scale: float,
) -> list[PixelBox]:
    return [
        PixelBox(
            float(item["x"]) + scroll_x,
            float(item["y"]) + scroll_y,
            float(item["width"]),
            float(item["height"]),
            scale,
        )
        for item in metrics
    ]


def crop_box_for_row(
    rect: Mapping[str, float],
    *,
    image_width: int,
    image_height: int,
    document_width: float,
    document_height: float,
    viewport_width: float,
    viewport_height: float | None = None,
    scroll_x: float,
    scroll_y: float,
) -> tuple[int, int, int, int]:
    """Map a row into either a full-document or renderer-viewport capture."""
    viewport_scale = image_width / max(1, viewport_width)
    full_document = image_height / max(viewport_scale, 0.001) >= document_height - 2
    scale = image_width / max(1, document_width if full_document else viewport_width)
    x = float(rect["x"]) + (scroll_x if full_document else 0)
    y = float(rect["y"]) + (scroll_y if full_document else 0)
    return (
        max(0, round(x * scale)),
        max(0, round(y * scale)),
        min(image_width, round((x + float(rect["width"])) * scale)),
        min(image_height, round((y + float(rect["height"])) * scale)),
    )


class BackgroundPagePort:
    """Same-tab CNIPA page port backed by the already logged-in normal Edge."""

    def __init__(
        self,
        page: Any,
        mark_recognizer: MarkVisionRecognizer | None,
        local_mark_recognizer: LocalMarkRecognizer | None = None,
    ) -> None:
        self.page = page
        self.mark_recognizer = mark_recognizer
        self.local_mark_recognizer = local_mark_recognizer
        self._query_params: str | None = None
        self._page_number = 1
        self._result_image: Image.Image | None = None
        self._result_image_scroll: float | None = None
        self._mark_cache: dict[str, str] = {}
        self.remote_model_call_count = 0

    @classmethod
    def from_context(
        cls,
        context: Any,
        mark_recognizer: MarkVisionRecognizer | None,
        local_mark_recognizer: LocalMarkRecognizer | None = None,
    ):
        pages = [page for page in context.pages if page.url.startswith(CNIPA_ORIGIN)]
        if not pages:
            raise RuntimeError("专用 Edge 中没有商标网站标签页")
        ready = [
            page for page in pages
            if page.url.startswith(f"{CNIPA_ORIGIN}/sgtmi")
            and page.locator('input[type="button"][value="查询"]').count()
        ]
        return cls((ready or pages)[-1], mark_recognizer, local_mark_recognizer)

    def close(self) -> None:
        self._clear_image()

    def require_search_ready(self) -> None:
        self._clear_image()
        if self.page.url == f"{CNIPA_ORIGIN}/sgtmi" and self._search_form_exists():
            return
        # Direct /sgtmi navigation is rejected by CNIPA's WAF. Enter through
        # the public home page and its own "商标综合查询" navigation element.
        expired_refreshes = 0
        other_retries = 0
        while True:
            try:
                self.page.goto(
                    f"{CNIPA_ORIGIN}/",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                self._wait_for(
                    lambda: self.page.get_by_text(
                        "商标综合查询", exact=True
                    ).count() > 0,
                    "商标网站首页未加载",
                    seconds=20,
                )
                self._check_site()
                self.page.get_by_text(
                    "商标综合查询", exact=True
                ).first.evaluate("element => element.click()")
                self._wait_for(
                    self._search_form_exists,
                    "商标综合查询页面未加载",
                    seconds=20,
                )
                self._check_site()
                return
            except NeedsUserAction:
                raise
            except (RuntimeError, PlaywrightTimeoutError) as exc:
                self._clear_image()
                if isinstance(exc, ExpiredPageError):
                    if expired_refreshes >= MAX_EXPIRED_PAGE_REFRESHES:
                        raise RuntimeError(
                            f"商标查询首页刷新{MAX_EXPIRED_PAGE_REFRESHES}次后仍显示页面已过期"
                        ) from exc
                    expired_refreshes += 1
                    self.page.wait_for_timeout(EXPIRED_PAGE_REFRESH_DELAY_MS)
                    continue
                if other_retries == 0:
                    other_retries += 1
                    self.page.wait_for_timeout(500)
                    continue
                raise RuntimeError(f"商标查询首页恢复后仍不可用：{exc}") from exc

    def fill_search(
        self, mark: str, applicant: str | None, applicant_language: str | None
    ) -> None:
        fields = self.page.locator('input[type="text"]')
        if fields.count() < 5:
            raise RuntimeError("商标综合查询输入框不完整")
        fields.nth(0).fill("")
        fields.nth(1).fill("")
        fields.nth(2).fill(mark)
        fields.nth(3).fill(applicant if applicant_language == "zh" else "")
        fields.nth(4).fill(applicant if applicant_language == "en" else "")
        if fields.nth(2).input_value() != mark:
            raise RuntimeError("检索要素写入后校验失败")

    def submit_search(self) -> None:
        query = self.page.locator('input[type="button"][value="查询"]')
        if query.count() != 1:
            raise RuntimeError("商标查询按钮不存在")
        params = query.evaluate(_OPEN_WITHOUT_POPUP)
        if not isinstance(params, str) or not params:
            raise RuntimeError("网站未生成商标检索参数")
        self._query_params = params
        self._load_current_results()

    def require_results_ready(self) -> None:
        for refreshes in range(MAX_EXPIRED_PAGE_REFRESHES + 1):
            try:
                self._wait_for(
                    lambda: self._on_results_page()
                    and self.page.locator("table tr").count() >= 1,
                    "商标检索结果页未加载",
                    seconds=35,
                )
                self._check_site()
                return
            except ExpiredPageError as exc:
                if refreshes >= MAX_EXPIRED_PAGE_REFRESHES:
                    raise RuntimeError(
                        f"商标检索结果页刷新{MAX_EXPIRED_PAGE_REFRESHES}次后仍显示页面已过期"
                    ) from exc
                self._reload_expired_results()

    def _reload_expired_results(self) -> None:
        self._clear_image()
        self.page.wait_for_timeout(EXPIRED_PAGE_REFRESH_DELAY_MS)
        self.page.reload(wait_until="domcontentloaded", timeout=25000)

    def _load_current_results(self) -> None:
        """Load the list and wait for this query's API response and stable DOM."""
        self._clear_image()
        expired_refreshes = 0
        http_403_refreshes = 0
        first_request = True
        while True:
            try:
                with self.page.expect_response(
                    lambda response: RESULTS_API_PATH in response.url,
                    timeout=25000,
                ) as response_info:
                    if first_request:
                        first_request = False
                        self.page.goto(
                            f"{CNIPA_ORIGIN}/list",
                            wait_until="domcontentloaded",
                            timeout=25000,
                        )
                    else:
                        self.page.reload(
                            wait_until="domcontentloaded",
                            timeout=25000,
                        )
                response = response_info.value
                response.finished()
            except PlaywrightTimeoutError as exc:
                try:
                    self._check_site()
                except ExpiredPageError as expired:
                    if expired_refreshes >= MAX_EXPIRED_PAGE_REFRESHES:
                        raise RuntimeError(
                            f"商标检索结果接口刷新{MAX_EXPIRED_PAGE_REFRESHES}次后仍显示页面已过期"
                        ) from expired
                    expired_refreshes += 1
                    self.page.wait_for_timeout(EXPIRED_PAGE_REFRESH_DELAY_MS)
                    continue
                raise RuntimeError("本次商标检索结果接口未完成") from exc
            if response.ok:
                break
            if response.status == 403 and http_403_refreshes == 0:
                http_403_refreshes += 1
                self.page.wait_for_timeout(RESULTS_HTTP_403_REFRESH_DELAY_MS)
                continue
            if response.status == 403:
                raise RuntimeError("商标检索结果接口刷新一次后仍返回 HTTP 403")
            raise RuntimeError(f"本次商标检索结果接口返回 HTTP {response.status}")
        self._page_number = 1
        self.require_results_ready()
        for refreshes in range(MAX_EXPIRED_PAGE_REFRESHES + 1):
            try:
                self._wait_for_results_stable()
                break
            except ExpiredPageError as exc:
                if refreshes >= MAX_EXPIRED_PAGE_REFRESHES:
                    raise RuntimeError(
                        f"商标检索结果页刷新{MAX_EXPIRED_PAGE_REFRESHES}次后仍显示页面已过期"
                    ) from exc
                self._reload_expired_results()
                self.require_results_ready()

    def _wait_for_results_stable(self) -> None:
        deadline = time.monotonic() + 10
        last_fingerprint: str | None = None
        stable_since: float | None = None
        while time.monotonic() < deadline:
            self._check_site()
            try:
                fingerprint = self._results_fingerprint()
                now = time.monotonic()
                if fingerprint == last_fingerprint:
                    if (
                        stable_since is not None
                        and now - stable_since >= RESULTS_STABILITY_SECONDS
                    ):
                        return
                else:
                    last_fingerprint = fingerprint
                    stable_since = now
            except Exception:
                last_fingerprint = None
                stable_since = None
            self.page.wait_for_timeout(100)
        raise RuntimeError("本次商标检索结果尚未稳定")

    def _results_fingerprint(self) -> str:
        table = self.page.locator("table").first
        return table.inner_text() if table.count() else ""

    def read_result_rows(self) -> Sequence[Sequence[str]]:
        return self.page.locator("table").first.evaluate(
            "table => Array.from(table.querySelectorAll('tr'), row => "
            "Array.from(row.children, cell => cell.innerText.trim()))"
        )

    def enrich_result_marks(
        self, records: Sequence[ResultRecord], expected_mark: str
    ) -> Sequence[ResultRecord]:
        records = [
            replace(item, mark=self._mark_cache[item.registration_no])
            if (
                not item.mark.strip()
                and item.registration_no in self._mark_cache
                and self._mark_cache[item.registration_no]
            )
            else item
            for item in records
        ]
        missing = [
            item
            for item in records
            if not item.mark.strip() and item.registration_no not in self._mark_cache
        ]
        if not missing:
            return records
        recognized: dict[int, str] = {}

        if self.local_mark_recognizer is not None:
            for item in missing:
                crop = self._capture_result_mark_image(item.row_index or 0)
                try:
                    assessment = self.local_mark_recognizer.assess_mark(
                        crop, expected_mark
                    )
                except Exception:
                    assessment = None
                finally:
                    crop.close()
                if (
                    assessment is not None
                    and not assessment.needs_remote
                    and assessment.text is not None
                ):
                    recognized[item.sequence] = assessment.text.strip()

        remote_candidates = [
            item for item in missing if item.sequence not in recognized
        ]
        if remote_candidates and self.mark_recognizer is None:
            raise VisionRecognitionError(
                "本地OCR未能识别序号："
                + "、".join(str(item.sequence) for item in remote_candidates[:10])
            )

        def recognize_batches(candidates: Sequence[ResultRecord]) -> None:
            for start in range(0, len(candidates), MARK_OCR_BATCH_SIZE):
                batch = candidates[start:start + MARK_OCR_BATCH_SIZE]
                crops = [
                    self._capture_result_row_image(item.row_index or 0)
                    for item in batch
                ]
                try:
                    encoded = self._stack_images_bytes(crops)
                    self.remote_model_call_count += 1
                    recognized.update(
                        self.mark_recognizer.recognize_marks(
                            encoded, batch, expected_mark
                        )
                    )
                except VisionRecognitionError:
                    raise
                finally:
                    for crop in crops:
                        crop.close()

        recognize_batches(remote_candidates)
        unresolved = [
            item for item in remote_candidates if item.sequence not in recognized
        ]
        if unresolved:
            # One focused batch retry is enough. Never turn a busy result page
            # into dozens of sequential model calls.
            recognize_batches(unresolved)
        still_unresolved = [
            item.sequence for item in missing if item.sequence not in recognized
        ]
        if still_unresolved:
            raise VisionRecognitionError(
                "商标图片文字识别结果不完整，缺少序号："
                + "、".join(str(value) for value in still_unresolved[:10])
            )
        by_sequence = {item.sequence: item for item in missing}
        for sequence, mark in recognized.items():
            item = by_sequence.get(sequence)
            if item is not None:
                self._mark_cache[item.registration_no] = str(mark).strip()
        return [
            replace(item, mark=str(recognized.get(item.sequence, "")).strip())
            if not item.mark.strip() and str(recognized.get(item.sequence, "")).strip()
            else item
            for item in records
        ]

    def total_pages(self) -> int:
        return self._page_counter()[1]

    def go_to_page(self, page_number: int) -> None:
        if page_number < 1:
            raise ValueError("页码必须大于零")
        for refreshes in range(MAX_EXPIRED_PAGE_REFRESHES + 1):
            try:
                self._check_site()
                self._go_to_page_once(page_number)
                return
            except ExpiredPageError as exc:
                if refreshes >= MAX_EXPIRED_PAGE_REFRESHES:
                    raise RuntimeError(
                        f"商标结果翻页刷新{MAX_EXPIRED_PAGE_REFRESHES}次后仍显示页面已过期"
                    ) from exc
                self.page.wait_for_timeout(EXPIRED_PAGE_REFRESH_DELAY_MS)
                self._restore_list()

    def _go_to_page_once(self, page_number: int) -> None:
        if not self._on_results_page():
            self._restore_list()
        current, total = self._page_counter()
        if page_number > total:
            raise ValueError(f"结果页 {page_number} 超出总页数 {total}")
        for _ in range(max(2, total * 2)):
            if current == page_number:
                self._page_number = current
                return
            direction = 1 if page_number > current else -1
            desired = current + direction
            before = self._row_fingerprint()
            for click_attempt in range(2):
                clicked = self.page.evaluate(
                    """({desired, direction}) => {
                    const table = document.querySelector('table');
                    if (!table) return false;
                    const links = [...document.querySelectorAll('a')].filter(a =>
                        a.getBoundingClientRect().top >= table.getBoundingClientRect().bottom - 2);
                    const number = links.find(a => a.innerText.trim() === String(desired));
                    const next = links.find(a => ['>', '›', '>>', '»'].includes(a.innerText.trim()));
                    const previous = links.find(a => ['<', '‹', '<<', '«'].includes(a.innerText.trim()));
                    const target = number || (direction > 0 ? next : previous);
                    if (!target) return false;
                    target.click();
                    return true;
                    }""",
                    {"desired": desired, "direction": direction},
                )
                if not clicked:
                    raise RuntimeError(f"未找到结果第 {desired} 页入口")
                try:
                    self._wait_for(
                        lambda: self._page_counter()[0] == desired
                        and self._row_fingerprint() != before,
                        f"结果第 {desired} 页未加载",
                        seconds=20,
                    )
                    break
                except NeedsUserAction:
                    raise
                except RuntimeError as exc:
                    if isinstance(exc, ExpiredPageError):
                        raise
                    if click_attempt >= 1:
                        raise
                    self.page.wait_for_timeout(500)
            self._clear_image()
            current, total = self._page_counter()
        raise RuntimeError(f"无法到达结果第 {page_number} 页")

    def refresh_results(self, page_number: int) -> None:
        try:
            self._check_site()
        except ExpiredPageError:
            # The old table is stale; _restore_list reissues the saved query.
            self.page.wait_for_timeout(EXPIRED_PAGE_REFRESH_DELAY_MS)
        self._restore_list()
        if page_number > 1:
            self.go_to_page(page_number)

    def click_mark_cell(self, row_index: int, sequence: int) -> None:
        self.require_results_ready()
        rows = self.page.locator("table").first.locator("tr")
        if row_index + 1 >= rows.count():
            raise ValueError(f"序号 {sequence} 的结果行不存在")
        row = rows.nth(row_index + 1)
        cells = row.locator("td")
        if cells.count() < 5 or str(cells.nth(0).inner_text()).strip() != str(sequence):
            raise ValueError(f"序号 {sequence} 的结果行不匹配")
        mark_link = cells.nth(4).locator("a").first
        params = mark_link.evaluate(_OPEN_WITHOUT_POPUP)
        if not isinstance(params, str) or not params:
            raise RuntimeError(f"序号 {sequence} 的详情参数未生成")
        self._clear_image()
        self.page.goto(f"{CNIPA_ORIGIN}/detail", wait_until="domcontentloaded", timeout=25000)
        self.require_detail_ready()

    def require_detail_ready(self) -> None:
        last_error: Exception | None = None
        expired_refreshes = 0
        other_refreshes = 0
        while True:
            try:
                self._wait_for(
                    lambda: self.page.url == f"{CNIPA_ORIGIN}/detail"
                    and "申请/注册号" in self.page.locator("body").inner_text(),
                    "商标详情页未加载",
                    seconds=20,
                )
                self._wait_for(
                    self._detail_decision_fields_ready,
                    "商标详情状态尚未加载",
                    seconds=15,
                )
                self._check_site()
                return
            except TransientSiteError as exc:
                last_error = exc
            except NeedsUserAction:
                raise
            except RuntimeError as exc:
                last_error = exc
            if isinstance(last_error, ExpiredPageError):
                if expired_refreshes >= MAX_EXPIRED_PAGE_REFRESHES:
                    raise RuntimeError(
                        f"商标详情状态刷新{MAX_EXPIRED_PAGE_REFRESHES}次后仍显示页面已过期"
                    ) from last_error
                expired_refreshes += 1
            else:
                if other_refreshes >= 1:
                    raise RuntimeError(
                        f"商标详情状态刷新一次后仍未加载：{last_error}"
                    ) from last_error
                other_refreshes += 1
            self.page.wait_for_timeout(EXPIRED_PAGE_REFRESH_DELAY_MS)
            self.page.reload(wait_until="domcontentloaded", timeout=25000)

    def _detail_decision_fields_ready(self) -> bool:
        labels = self.read_detail_labels()
        status = " ".join(str(labels.get("商标状态", "")).split())
        if not status:
            return False
        normalized = status.casefold()
        # The status widget is populated in stages.  Wait for both its coded
        # English state and Chinese description instead of accepting a label
        # or one half of the final value as a completed decision.
        if "/" not in status or not re.search(r"[\u3400-\u9fff]", status):
            return False
        if "issued and active" in normalized and "注册商标" in status:
            return bool(str(labels.get("专用权期限", "")).strip())
        return True

    def read_detail_labels(self) -> Mapping[str, str]:
        rows = self.page.locator("table").evaluate_all(
            "tables => tables.flatMap(t => Array.from(t.querySelectorAll('tr'), "
            "row => Array.from(row.children, cell => cell.innerText.trim())))"
        )
        return detail_labels_from_rows(rows)

    def capture_result_page(self, output: Path) -> Path:
        return self._save_image(self._result_page_image().copy(), output)

    def capture_detail_page(self, output: Path) -> Path:
        last_error: Exception | None = None
        expired_refreshes = 0
        other_refreshes = 0
        while True:
            try:
                self._wait_for_detail_capture_ready()
                image = self._capture_detail_image()
                if not detail_image_is_complete(image):
                    image.close()
                    raise RuntimeError("商标详情截图内容未完整渲染")
                return self._save_image(image, output)
            except NeedsUserAction:
                raise
            except Exception as exc:
                last_error = exc
                if isinstance(exc, ExpiredPageError):
                    if expired_refreshes >= MAX_EXPIRED_PAGE_REFRESHES:
                        raise RuntimeError(
                            f"商标详情截图刷新{MAX_EXPIRED_PAGE_REFRESHES}次后仍显示页面已过期"
                        ) from exc
                    expired_refreshes += 1
                    self.page.wait_for_timeout(EXPIRED_PAGE_REFRESH_DELAY_MS)
                else:
                    if other_refreshes >= 2:
                        break
                    other_refreshes += 1
                self.page.reload(wait_until="domcontentloaded", timeout=25000)
                self.require_detail_ready()
        raise RuntimeError(f"商标详情截图不完整，刷新重试后仍失败：{last_error}")

    def _wait_for_detail_capture_ready(self) -> None:
        """Wait for required detail fields/images and a stable content height."""
        deadline = time.monotonic() + 35
        last_signature: tuple[str, int] | None = None
        stable_since: float | None = None
        while time.monotonic() < deadline:
            self._check_site()
            state = self.page.evaluate(
                """() => {
                    const body = document.body?.innerText || '';
                    const root = document.querySelector('.xqbox') || document.body;
                    const images = [...root.querySelectorAll('img')].filter(img => {
                        const r = img.getBoundingClientRect();
                        return r.width > 20 && r.height > 20;
                    });
                    const loading = [...document.querySelectorAll(
                        '.loading,.layui-layer-loading,.el-loading-mask,[aria-busy="true"]'
                    )].some(el => {
                        const s = getComputedStyle(el); const r = el.getBoundingClientRect();
                        return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0;
                    });
                    return {
                        ready: body.includes('申请/注册号') && body.includes('申请人名称') &&
                               body.includes('专用权期限') && body.includes('商标状态') &&
                               images.every(img => img.complete && img.naturalWidth > 0) && !loading,
                        text: body.slice(0, 3000),
                        height: Math.round(root?.getBoundingClientRect().height || 0)
                    };
                }"""
            )
            now = time.monotonic()
            signature = (str(state.get("text") or ""), int(state.get("height") or 0))
            if state.get("ready") and signature == last_signature:
                if stable_since is not None and now - stable_since >= 0.6:
                    return
            else:
                last_signature = signature
                stable_since = now if state.get("ready") else None
            self.page.wait_for_timeout(150)
        raise RuntimeError("商标详情必要字段或图片未完整加载")

    def _capture_detail_image(self) -> Image.Image:
        image = capture_full_page_image(self.page)
        try:
            bounds = self.page.locator("table").evaluate_all(
                "tables => tables.map(t => {const r=t.getBoundingClientRect();"
                "return {x:r.x+scrollX,width:r.width}}).filter(r=>r.width>400)"
            )
            if bounds:
                document_width = self._document_width()
                content_bottom = self.page.locator(".xqbox").evaluate(
                    "e => {const r=e.getBoundingClientRect();return r.bottom+scrollY}"
                ) if self.page.locator(".xqbox").count() else image.height * document_width / image.width
                crop = detail_evidence_crop_box(
                    table_bounds=bounds,
                    content_bottom=content_bottom,
                    image_width=image.width,
                    image_height=image.height,
                    document_width=document_width,
                )
                if crop[2] > crop[0] and crop[3] > crop[1]:
                    cropped = image.crop(crop)
                    image.close()
                    image = cropped
            return image
        except Exception:
            image.close()
            raise

    def capture_result_row(self, row_index: int, output: Path) -> Path:
        return self._save_image(self._capture_result_row_image(row_index), output)

    def capture_result_header(self, output: Path) -> Path:
        return self._save_image(self._capture_result_edge("header"), output)

    def capture_result_footer(self, output: Path) -> Path:
        return self._save_image(self._capture_result_edge("footer"), output)

    def result_row_box(self, row_index: int) -> PixelBox:
        metrics = self.page.evaluate(
            """index => {
                const row = document.querySelectorAll('table tr')[index + 1];
                if (!row) return null;
                const rect = row.getBoundingClientRect();
                return {x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                        scrollX, scrollY};
            }""",
            row_index,
        )
        if not metrics:
            raise ValueError(f"结果行 {row_index + 1} 不存在")
        scale = self._result_page_image().width / max(1, self._document_width())
        return row_boxes_from_metrics(
            [metrics], scroll_x=metrics["scrollX"], scroll_y=metrics["scrollY"], scale=scale
        )[0]

    def _restore_list(self) -> None:
        if not self._query_params:
            raise RuntimeError("缺少可恢复的商标查询参数")
        self.page.evaluate("value => sessionStorage.setItem('params', value)", self._query_params)
        self._load_current_results()

    def _search_form_exists(self) -> bool:
        return self.page.locator('input[type="button"][value="查询"]').count() == 1

    def _on_results_page(self) -> bool:
        return self.page.url.split("#", 1)[0] == f"{CNIPA_ORIGIN}/list"

    def _page_counter(self) -> tuple[int, int]:
        return page_counter(self.page.locator("body").inner_text())

    def _row_fingerprint(self) -> str:
        rows = self.page.locator("table tr")
        return rows.nth(1).inner_text() if rows.count() > 1 else ""

    def _check_site(self) -> None:
        url = self.page.url
        if "sso.cnipa.gov.cn" in url:
            raise NeedsUserAction(BrowserState.LOGIN_REQUIRED, "商标网站要求重新登录")
        body_locator = self.page.locator("body")
        if body_locator.count() == 0:
            return
        try:
            body = body_locator.inner_text(timeout=2000)[:500]
        except PlaywrightTimeoutError:
            return
        if "国家知识产权公共服务平台统一身份认证系统" in body:
            raise NeedsUserAction(BrowserState.LOGIN_REQUIRED, "商标网站要求重新登录")
        if "请求被阻断" in body or "403 Forbidden" in body:
            raise NeedsUserAction(BrowserState.WAF_BLOCKED, "商标网站阻断了本次请求")
        if "云安全平台检测到您当前的访问行为存在异常" in body:
            raise TransientSiteError("商标网站云安全平台提示访问行为异常")
        if "页面已过期" in body:
            raise ExpiredPageError("商标网站查询页面已过期")

    def _wait_for(self, predicate, message: str, seconds: float = 90) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._check_site()
            try:
                if predicate():
                    return
            except Exception:
                pass
            self.page.wait_for_timeout(150)
        raise RuntimeError(message)

    def _document_width(self) -> float:
        return float(self.page.evaluate("document.documentElement.scrollWidth"))

    def _result_page_image(self) -> Image.Image:
        scroll = float(self.page.evaluate("scrollY"))
        if self._result_image_scroll != scroll:
            self._clear_image()
        if self._result_image is None:
            self._result_image = capture_page_image(self.page)
            self._result_image_scroll = scroll
        return self._result_image

    def _clear_image(self) -> None:
        if self._result_image is not None:
            self._result_image.close()
            self._result_image = None
        self._result_image_scroll = None

    def _capture_result_row_image(self, row_index: int) -> Image.Image:
        row = self.page.locator("table").first.locator("tr").nth(row_index + 1)
        if row.count() != 1:
            raise ValueError(f"结果行 {row_index + 1} 不存在")
        rect = row.evaluate(
            "el => {const r=el.getBoundingClientRect();"
            "return {x:r.x,y:r.y,width:r.width,height:r.height,viewportHeight:innerHeight}}"
        )
        if rect["y"] < 0 or rect["y"] + rect["height"] > rect["viewportHeight"]:
            row.evaluate("el => el.scrollIntoView({block:'center'})")
            self._clear_image()
            self.page.wait_for_timeout(150)
        image_node = row.locator("td").nth(4).locator("img")
        if image_node.count():
            try:
                image_node.evaluate(
                    "el => el.complete && el.naturalWidth > 0"
                ) or self.page.wait_for_function(
                    "el => el.complete && el.naturalWidth > 0",
                    arg=image_node.element_handle(),
                    timeout=5000,
                )
            except Exception:
                pass
        image = self._result_page_image()
        rect = row.evaluate(
            "el => {const r=el.getBoundingClientRect();"
            "return {x:r.x,y:r.y,width:r.width,height:r.height}}"
        )
        viewport = self.page.evaluate(
            "({width:innerWidth,scrollX,scrollY,docWidth:document.documentElement.scrollWidth,"
            "docHeight:document.documentElement.scrollHeight})"
        )
        left, top, right, bottom = crop_box_for_row(
            rect,
            image_width=image.width,
            image_height=image.height,
            document_width=viewport["docWidth"],
            document_height=viewport["docHeight"],
            viewport_width=viewport["width"],
            scroll_x=viewport["scrollX"],
            scroll_y=viewport["scrollY"],
        )
        if right <= left or bottom <= top:
            raise RuntimeError(f"结果行 {row_index + 1} 不在后台截图范围内")
        return image.crop((left, top, right, bottom)).convert("RGB")

    def _capture_result_mark_image(self, row_index: int) -> Image.Image:
        row_image = self._capture_result_row_image(row_index)
        try:
            row = self.page.locator("table").first.locator("tr").nth(row_index + 1)
            metrics = row.evaluate(
                """row => {
                    const rr = row.getBoundingClientRect();
                    const cell = row.querySelectorAll('td')[4];
                    if (!cell) return null;
                    const cr = cell.getBoundingClientRect();
                    return {rowX:rr.x,rowWidth:rr.width,cellX:cr.x,cellWidth:cr.width};
                }"""
            )
            if not metrics or float(metrics["rowWidth"]) <= 0:
                return row_image.copy()
            scale = row_image.width / float(metrics["rowWidth"])
            left = max(
                0,
                round((float(metrics["cellX"]) - float(metrics["rowX"])) * scale),
            )
            right = min(
                row_image.width,
                round(
                    (
                        float(metrics["cellX"])
                        - float(metrics["rowX"])
                        + float(metrics["cellWidth"])
                    )
                    * scale
                ),
            )
            if right <= left:
                return row_image.copy()
            return row_image.crop((left, 0, right, row_image.height)).convert("RGB")
        finally:
            row_image.close()

    def _capture_result_edge(self, edge: str) -> Image.Image:
        table = self.page.locator("table").first
        rows = table.locator("tr")
        if table.count() != 1 or rows.count() < 1:
            raise RuntimeError("商标结果表不存在，无法生成证据截图")
        anchor = rows.first if edge == "header" else rows.last
        anchor.evaluate(
            "(el, edge) => el.scrollIntoView({block: edge === 'header' ? 'start' : 'center'})",
            edge,
        )
        self._clear_image()
        self.page.wait_for_timeout(150)
        metrics = self.page.evaluate(
            """edge => {
                const table = document.querySelector('table');
                const rows = table ? [...table.querySelectorAll('tr')] : [];
                if (!table || !rows.length) return null;
                const tr = table.getBoundingClientRect();
                const hr = rows[0].getBoundingClientRect();
                const y = edge === 'header' ? Math.max(0, tr.top - 90) :
                    Math.min(innerHeight - 35, tr.bottom);
                const bottom = edge === 'header' ? hr.bottom + 4 :
                    Math.min(innerHeight, y + 120);
                return {rect:{x:tr.x,y,width:tr.width,height:Math.max(35,bottom-y)},
                    viewportWidth:innerWidth,viewportHeight:innerHeight,scrollX,scrollY,
                    docWidth:document.documentElement.scrollWidth,
                    docHeight:document.documentElement.scrollHeight};
            }""",
            edge,
        )
        if not metrics:
            raise RuntimeError("商标结果表上下文未加载")
        image = self._result_page_image()
        left, top, right, bottom = crop_box_for_row(
            metrics["rect"],
            image_width=image.width,
            image_height=image.height,
            document_width=metrics["docWidth"],
            document_height=metrics["docHeight"],
            viewport_width=metrics["viewportWidth"],
            viewport_height=metrics["viewportHeight"],
            scroll_x=metrics["scrollX"],
            scroll_y=metrics["scrollY"],
        )
        if right <= left or bottom <= top:
            raise RuntimeError("商标结果表上下文不在截图范围内")
        return image.crop((left, top, right, bottom)).convert("RGB")

    @staticmethod
    def _stack_images_bytes(images: Sequence[Image.Image]) -> bytes:
        width = max(image.width for image in images)
        height = sum(image.height for image in images) + 4 * (len(images) - 1)
        canvas = Image.new("RGB", (width, height), "white")
        try:
            y = 0
            for image in images:
                canvas.paste(image, (0, y))
                y += image.height + 4
            stream = BytesIO()
            canvas.save(stream, format="PNG", optimize=True)
            return stream.getvalue()
        finally:
            canvas.close()

    @staticmethod
    def _save_image(image: Image.Image, output: Path) -> Path:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            image.save(output, format="PNG", optimize=True)
        finally:
            image.close()
        return output
