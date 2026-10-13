"""Windows UI Automation adapter for the employee-visible Edge session.

All navigation and form interaction uses Windows UI Automation.  The dedicated
Edge exposes a loopback-only renderer endpoint solely for background evidence
screenshots; no WebDriver or automation browser flags are used, and this module
never touches fields on the CNIPA SSO page.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import ctypes
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import re
import time

from PIL import Image
from pywinauto import Desktop
from pywinauto.controls.uiawrapper import UIAWrapper
from comtypes import CoInitializeEx, CoUninitialize

from trademark_worker.browser.capture import BackgroundPageCapture
from trademark_worker.browser.state import PageSnapshot, classify_page
from trademark_worker.cnipa.selectors import RESULT_HEADERS, header_index
from trademark_worker.imaging.evidence import PixelBox
from trademark_worker.models import BrowserState, NeedsUserAction, ResultRecord
from trademark_worker.vision import MarkVisionRecognizer, VisionRecognitionError


_TAB_MEMORY_SUFFIX = re.compile(r"\s+-\s+内存使用率.*$")
_PAGE_RE = re.compile(r"页数\s*[：:]?\s*(\d+)\s*/\s*(\d+)")
def tab_title(value: str) -> str:
    """Return the page title without Edge's optional memory-usage suffix."""
    return _TAB_MEMORY_SUFFIX.sub("", str(value or "")).strip()


def scroll_offset(
    viewport_height: int, vertical_view_size: float, vertical_scroll_percent: float
) -> int:
    """Convert UIA scroll percentages into a full-document pixel offset."""
    if viewport_height <= 0 or vertical_view_size <= 0 or vertical_view_size >= 100:
        return 0
    total_height = viewport_height * 100.0 / vertical_view_size
    maximum = max(0.0, total_height - viewport_height)
    percent = min(100.0, max(0.0, vertical_scroll_percent))
    return round(maximum * percent / 100.0)


def page_counter(text: str) -> tuple[int, int]:
    """Return the current and total result pages from CNIPA footer text."""
    match = _PAGE_RE.search(str(text or ""))
    if match is None:
        return 1, 1
    current = max(1, int(match.group(1)))
    total = max(current, int(match.group(2)))
    return current, total


def _rectangle_is_empty(rectangle) -> bool:
    return rectangle.width() <= 0 or rectangle.height() <= 0


def is_logged_in_header_name(value: str) -> bool:
    """Return True for the account name shown in CNIPA's top header."""
    name = str(value or "").strip()
    if not name:
        return False
    public_names = {
        "中文",
        "English",
        "帮助",
        "登录",
        "注册",
        "退出登录",
        "WWW.CNIPA.GOV.CN WCJS.SBJ.CNIPA.GOV.CN",
    }
    return name not in public_names and not name.startswith("当前数据截至")


def detail_labels_from_rows(rows: Sequence[Sequence[str]]) -> dict[str, str]:
    """Map alternating label/value cells from the CNIPA detail table."""
    known = {
        "商标",
        "申请/注册号",
        "注册号",
        "申请号",
        "国际分类",
        "申请人名称（中文）",
        "申请人名称(中文)",
        "申请人名称（英文）",
        "申请人名称(英文)",
        "专用权期限",
        "商标状态",
        "商标状态图标",
    }
    labels: dict[str, str] = {}
    for source in rows:
        cells = [str(item or "").strip().replace("\xa0", " ").strip() for item in source]
        for index in range(0, len(cells) - 1, 2):
            label = cells[index].rstrip("：:")
            if label not in known:
                continue
            key = "商标状态" if label == "商标状态图标" else label
            if key == "商标状态":
                # CNIPA renders the status icon and its text in separate cells
                # on some detail pages.  The adjacent icon cell has no text,
                # while the following cell contains the actual status value.
                value = " ".join(
                    value for value in cells[index + 1 :] if value
                ).strip()
                if value or key not in labels:
                    labels[key] = value
            else:
                value = cells[index + 1]
                if value or key not in labels:
                    labels[key] = value
    return labels


class UIABrowserSession:
    """Attach to one application-owned Edge process through Windows UIA."""

    def __init__(self, process_id: int) -> None:
        CoInitializeEx(0)
        self._com_initialized = True
        self.process_id = int(process_id)
        self.desktop = Desktop(backend="uia")
        self.window_handle = self._find_window_handle()

    @classmethod
    def attach(cls, process_id: int) -> "UIABrowserSession":
        return cls(process_id)

    @property
    def window(self):
        return self.desktop.window(handle=self.window_handle)

    def _find_window_handle(self) -> int:
        candidates = []
        for window in self.desktop.windows(process=self.process_id, visible_only=True):
            rectangle = window.rectangle()
            title = window.window_text()
            if "Edge" not in title and "Microsoft" not in title:
                continue
            # Windows UIA reports a zero-sized rectangle for a minimized Edge.
            # Keep its top-level handle so the employee can restore it.
            area = max(0, rectangle.width()) * max(0, rectangle.height())
            candidates.append((area, window.handle))
        if not candidates:
            raise RuntimeError("未找到本应用打开的 Microsoft Edge 窗口")
        return max(candidates)[1]

    def document(self) -> UIAWrapper:
        documents = [
            item
            for item in self.window.descendants(control_type="Document")
            if item.element_info.automation_id == "RootWebArea"
        ]
        if not documents:
            raise RuntimeError("Edge 当前标签页没有可读取的网页内容")
        return documents[-1]

    def url(self) -> str:
        edits = [
            item
            for item in self.window.descendants(control_type="Edit")
            if item.element_info.automation_id == "view_1021"
        ]
        return str(edits[-1].get_value()).strip() if edits else ""

    def snapshot(self) -> PageSnapshot:
        try:
            document = self.document()
            names = [
                item.window_text().strip()
                for item in document.descendants()
                if item.window_text().strip()
            ]
            title = document.window_text()
            document_top = document.rectangle().top
            header_names = [
                item.window_text().strip()
                for item in document.descendants()
                if item.window_text().strip()
                and not _rectangle_is_empty(item.rectangle())
                and item.rectangle().top <= document_top + 35
            ]
        except Exception:
            names = []
            title = self.window.window_text()
            header_names = []
        text = "\n".join(names[:600])
        return PageSnapshot(
            url=self.url(),
            http_status=None,
            text=text[:30_000],
            has_search_form="检索要素" in names and "查询" in names,
            has_captcha="验证码" in text,
            title=title,
            has_logged_in_identity=any(
                is_logged_in_header_name(name) for name in header_names
            ),
        )

    def state(self) -> BrowserState:
        return classify_page(self.snapshot())

    def enter_comprehensive_query(self) -> BrowserState:
        state = self.state()
        if state is not BrowserState.RETURNED_HOME:
            return state
        document = self.document()
        targets = [
            item
            for item in document.descendants(control_type="ListItem")
            if item.element_info.automation_id == "txnS02"
            or item.window_text().strip() == "商标综合查询"
        ]
        if not targets:
            return BrowserState.RETURNED_HOME
        targets[0].iface_invoke.Invoke()
        return self.wait_for_states(
            {
                BrowserState.READY,
                BrowserState.LOGIN_REQUIRED,
                BrowserState.AUTH_BLOCKED,
                BrowserState.WAF_BLOCKED,
            },
            timeout=15.0,
        )

    def wait_for_states(
        self, states: set[BrowserState], timeout: float = 15.0
    ) -> BrowserState:
        deadline = time.monotonic() + timeout
        last = BrowserState.WAITING_SITE
        while time.monotonic() < deadline:
            last = self.state()
            if last in states:
                return last
            time.sleep(0.15)
        return last

    def disconnect(self) -> None:
        if self._com_initialized:
            self._com_initialized = False
            CoUninitialize()

    def minimize(self) -> None:
        """Move the dedicated browser to the taskbar without activating it."""
        ctypes.windll.user32.ShowWindow(self.window_handle, 6)

    def hide_for_background(self) -> None:
        """Keep Edge rendered off-screen so CDP can capture without focus theft."""
        user32 = ctypes.windll.user32
        virtual_left = user32.GetSystemMetrics(76)
        # A minimized renderer stops painting; an off-screen visible window
        # continues to render while the employee uses any other application.
        user32.SetWindowPos(
            self.window_handle,
            0,
            virtual_left - 2300,
            100,
            1800,
            1000,
            0x0010 | 0x0004,  # SWP_NOACTIVATE | SWP_NOZORDER
        )
        user32.ShowWindow(self.window_handle, 4)  # SW_SHOWNOACTIVATE

    def restore_for_user(self) -> None:
        """Restore Edge only after the employee explicitly asks to open it."""
        user32 = ctypes.windll.user32
        user32.ShowWindow(self.window_handle, 9)  # SW_RESTORE
        rect = self.window.rectangle()
        virtual_left = user32.GetSystemMetrics(76)
        virtual_top = user32.GetSystemMetrics(77)
        virtual_width = max(1, user32.GetSystemMetrics(78))
        virtual_height = max(1, user32.GetSystemMetrics(79))
        visible_width = max(
            0, min(rect.right, virtual_left + virtual_width) - max(rect.left, virtual_left)
        )
        visible_height = max(
            0, min(rect.bottom, virtual_top + virtual_height) - max(rect.top, virtual_top)
        )
        if (visible_width < min(320, max(1, rect.width() // 3))
                or visible_height < min(180, max(1, rect.height() // 3))):
            margin_x = min(100, virtual_width // 10)
            margin_y = min(100, virtual_height // 10)
            user32.SetWindowPos(
                self.window_handle,
                0,
                virtual_left + margin_x,
                virtual_top + margin_y,
                min(max(800, rect.width()), max(1, virtual_width - 2 * margin_x)),
                min(max(600, rect.height()), max(1, virtual_height - 2 * margin_y)),
                0x0010 | 0x0004,  # SWP_NOACTIVATE | SWP_NOZORDER
            )
        user32.ShowWindow(self.window_handle, 3)  # SW_MAXIMIZE
        user32.SetForegroundWindow(self.window_handle)
        restored = self.window.rectangle()
        visible_width = max(
            0,
            min(restored.right, virtual_left + virtual_width)
            - max(restored.left, virtual_left),
        )
        visible_height = max(
            0,
            min(restored.bottom, virtual_top + virtual_height)
            - max(restored.top, virtual_top),
        )
        if visible_width < 320 or visible_height < 180:
            raise RuntimeError("专用 Edge 窗口仍在屏幕外")


class UIAPagePort:
    """CNIPA page operations implemented with accessible Edge controls."""

    def __init__(
        self,
        session: UIABrowserSession,
        mark_recognizer: MarkVisionRecognizer | None = None,
        page_capture: BackgroundPageCapture | None = None,
    ) -> None:
        self.session = session
        self.mark_recognizer = mark_recognizer
        self.page_capture = page_capture
        self._page_number = 1
        self.remote_model_call_count = 0

    def require_search_ready(self) -> None:
        self._select_tab("商标综合检索")
        self._require_state(BrowserState.READY, "请先在 Edge 中打开商标综合查询页面")

    def fill_search(
        self, mark: str, applicant: str | None, applicant_language: str | None
    ) -> None:
        self._close_tabs(("商标详细内容", "商标检索结果"))
        self._select_tab("商标综合检索")
        self._set_field("检索要素", mark)
        self._set_field("申请人名称（中文）", "")
        self._set_field("申请人名称（英文）", "")
        if applicant and applicant_language in {"zh", "en"}:
            label = (
                "申请人名称（中文）"
                if applicant_language == "zh"
                else "申请人名称（英文）"
            )
            self._set_field(label, applicant)

    def submit_search(self) -> None:
        self._select_tab("商标综合检索")
        document = self.session.document()
        buttons = [
            item
            for item in document.descendants(control_type="Button")
            if item.window_text().strip() == "查询"
        ]
        if not buttons:
            raise RuntimeError("商标综合查询页面缺少查询按钮")
        buttons[-1].iface_invoke.Invoke()
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            try:
                self._select_tab("商标检索结果")
            except (NeedsUserAction, RuntimeError):
                pass
            state = self.session.state()
            self._raise_if_user_action(state)
            if state is BrowserState.RESULTS and self._result_table() is not None:
                self._page_number = 1
                return
            time.sleep(0.15)
        raise NeedsUserAction(BrowserState.WAITING_SITE, "提交查询后未出现商标检索结果")

    def require_results_ready(self) -> None:
        deadline = time.monotonic() + 10.0
        last = BrowserState.WAITING_SITE
        while time.monotonic() < deadline:
            try:
                self._select_tab("商标检索结果")
                last = self.session.state()
                if last is BrowserState.RESULTS and self._result_table() is not None:
                    return
                self._raise_if_user_action(last)
            except (RuntimeError, NeedsUserAction) as exc:
                if isinstance(exc, NeedsUserAction) and exc.state is not BrowserState.WAITING_SITE:
                    raise
            time.sleep(0.15)
        self._raise_unexpected(last, "商标检索结果页尚未就绪")

    def read_result_rows(self) -> Sequence[Sequence[str]]:
        table = self._require_result_table()
        rows = self._table_rows(table)
        if not rows:
            return []
        output = [[cell.window_text().strip() for cell in row.children()] for row in rows]
        mark_index = header_index(output[0], RESULT_HEADERS["mark"])
        if mark_index is not None:
            for row in output[1:]:
                if mark_index < len(row) and row[mark_index] in {"点击查看", "查看"}:
                    row[mark_index] = ""
        return output

    def enrich_result_marks(
        self, records: Sequence[ResultRecord], expected_mark: str
    ) -> Sequence[ResultRecord]:
        missing = [item for item in records if not item.mark.strip()]
        if not missing or self.mark_recognizer is None:
            return records
        images = [self._capture_result_row_image(item.row_index or 0) for item in missing]
        image_png = self._stack_images_bytes(images)
        try:
            self.remote_model_call_count += 1
            recognized = self.mark_recognizer.recognize_marks(
                image_png, missing, expected_mark
            )
        except VisionRecognitionError as exc:
            raise NeedsUserAction(
                BrowserState.WAITING_SITE,
                "商标图片文字识别失败，请检查本机大模型配置后继续",
            ) from exc
        return [
            replace(item, mark=str(recognized.get(item.sequence, "")).strip())
            if not item.mark.strip() and str(recognized.get(item.sequence, "")).strip()
            else item
            for item in records
        ]

    def total_pages(self) -> int:
        self._select_tab("商标检索结果")
        return self._page_counter()[1]

    def current_page(self) -> int:
        self._select_tab("商标检索结果")
        return self._page_counter()[0]

    def _page_counter(self) -> tuple[int, int]:
        document = self.session.document()
        text = "\n".join(item.window_text() for item in document.descendants())
        return page_counter(text)

    def go_to_page(self, page_number: int) -> None:
        if page_number < 1:
            raise ValueError("页码必须大于零")
        self._select_tab("商标检索结果")
        current, total = self._page_counter()
        if page_number > total:
            raise ValueError(f"结果页 {page_number} 超出总页数 {total}")
        for _ in range(max(2, total * 2)):
            if current == page_number:
                self._page_number = current
                return
            direction = 1 if page_number > current else -1
            desired = current + direction
            table = self._require_result_table()
            before = self._rows_fingerprint(table)
            target = self._pagination_target(desired, direction, table)
            target.iface_invoke.Invoke()
            deadline = time.monotonic() + 12.0
            while time.monotonic() < deadline:
                time.sleep(0.15)
                candidate = self._result_table()
                state = self.session.state()
                self._raise_if_user_action(state)
                next_current, next_total = self._page_counter()
                changed = (
                    candidate is not None
                    and self._rows_fingerprint(candidate) != before
                )
                progressed = (
                    next_current > current if direction > 0 else next_current < current
                )
                if changed and progressed:
                    current, total = next_current, next_total
                    self._page_number = current
                    break
            else:
                raise NeedsUserAction(
                    BrowserState.WAITING_SITE, f"结果第 {desired} 页未加载"
                )
        raise NeedsUserAction(
            BrowserState.WAITING_SITE, f"无法到达结果第 {page_number} 页"
        )

    def click_mark_cell(self, row_index: int, sequence: int) -> None:
        self._close_tabs(("商标详细内容",))
        self._select_tab("商标检索结果")
        table = self._require_result_table()
        headers = [cell.window_text().strip() for cell in self._table_rows(table)[0].children()]
        mark_index = header_index(headers, RESULT_HEADERS["mark"])
        if mark_index is None:
            raise ValueError("结果表缺少商标列")
        rows = self._data_rows(table)
        if row_index >= len(rows):
            raise ValueError(f"序号 {sequence} 的商标结果行不存在")
        cells = rows[row_index].children()
        if mark_index >= len(cells):
            raise ValueError(f"序号 {sequence} 的商标结果行不完整")
        links = cells[mark_index].descendants(control_type="Hyperlink")
        target = links[-1] if links else None
        if target is None:
            raise ValueError(f"序号 {sequence} 的商标图片不可点击")
        target.iface_invoke.Invoke()
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            try:
                self._select_tab("商标详细内容")
            except (NeedsUserAction, RuntimeError):
                pass
            state = self.session.state()
            self._raise_if_user_action(state)
            if state is BrowserState.DETAIL and self._detail_has_fields():
                return
            time.sleep(0.15)
        raise NeedsUserAction(BrowserState.WAITING_SITE, "点击商标后未进入详情页")

    def require_detail_ready(self) -> None:
        deadline = time.monotonic() + 10.0
        last = BrowserState.WAITING_SITE
        while time.monotonic() < deadline:
            try:
                self._select_tab("商标详细内容")
                last = self.session.state()
                if last is BrowserState.DETAIL and self._detail_has_fields():
                    return
                self._raise_if_user_action(last)
            except (RuntimeError, NeedsUserAction) as exc:
                if isinstance(exc, NeedsUserAction) and exc.state is not BrowserState.WAITING_SITE:
                    raise
            time.sleep(0.15)
        self._raise_unexpected(last, "商标详情页尚未就绪")

    def read_detail_labels(self) -> Mapping[str, str]:
        self._select_tab("商标详细内容")
        rows: list[list[str]] = []
        for table in self.session.document().descendants(control_type="Table"):
            for row in table.children():
                cells = [cell.window_text() for cell in row.children()]
                if cells:
                    rows.append(cells)
        return detail_labels_from_rows(rows)

    def capture_result_page(self, output: Path) -> Path:
        self._select_tab("商标检索结果")
        return self._capture_full_document(Path(output), crop_horizontal=False)

    def capture_detail_page(self, output: Path) -> Path:
        self._select_tab("商标详细内容")
        return self._capture_full_document(Path(output), crop_horizontal=True)

    def capture_result_row(self, row_index: int, output: Path) -> Path:
        image = self._capture_result_row_image(row_index)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="PNG", optimize=True)
        return output

    def result_row_box(self, row_index: int) -> PixelBox:
        self._select_tab("商标检索结果")
        document = self.session.document()
        row = self._data_rows(self._require_result_table())[row_index]
        row.iface_scroll_item.ScrollIntoView()
        time.sleep(0.1)
        doc_rect = document.rectangle()
        row_rect = row.rectangle()
        scroll = document.iface_scroll
        offset = scroll_offset(
            doc_rect.height(),
            float(scroll.CurrentVerticalViewSize),
            float(scroll.CurrentVerticalScrollPercent),
        )
        return PixelBox(
            row_rect.left - doc_rect.left,
            offset + row_rect.top - doc_rect.top,
            row_rect.width(),
            row_rect.height(),
        )

    def _select_tab(self, prefix: str) -> None:
        tabs = [
            item
            for item in self.session.window.descendants(control_type="TabItem")
            if tab_title(item.window_text()).startswith(prefix)
        ]
        if not tabs:
            if tab_title(self.session.document().window_text()).startswith(prefix):
                return
            raise NeedsUserAction(BrowserState.WAITING_SITE, f"Edge 中缺少“{prefix}”标签页")
        tabs[-1].select()
        time.sleep(0.1)

    def _close_tabs(self, prefixes: Sequence[str]) -> None:
        while True:
            tabs = [
                item
                for item in self.session.window.descendants(control_type="TabItem")
                if any(tab_title(item.window_text()).startswith(prefix) for prefix in prefixes)
            ]
            if not tabs:
                return
            tabs[-1].select()
            time.sleep(0.05)
            close_buttons = [
                item
                for item in self.session.window.descendants(control_type="Button")
                if item.window_text().strip() == "关闭标签页"
            ]
            if not close_buttons:
                raise RuntimeError("无法关闭上一次商标查询标签页")
            close_buttons[-1].iface_invoke.Invoke()
            time.sleep(0.15)

    def _edit_near_label(self, document: UIAWrapper, label: str) -> UIAWrapper:
        labels = [
            item
            for item in document.descendants()
            if item.window_text().strip() == label and not _rectangle_is_empty(item.rectangle())
        ]
        edits = [
            item
            for item in document.descendants(control_type="Edit")
            if not _rectangle_is_empty(item.rectangle())
        ]
        if not labels or not edits:
            raise RuntimeError(f"未找到输入框：{label}")
        target_y = (labels[-1].rectangle().top + labels[-1].rectangle().bottom) / 2
        candidates = [item for item in edits if item.rectangle().left >= labels[-1].rectangle().left]
        if not candidates:
            raise RuntimeError(f"未找到输入框：{label}")
        return min(
            candidates,
            key=lambda item: abs(
                (item.rectangle().top + item.rectangle().bottom) / 2 - target_y
            ),
        )

    def _set_field(self, label: str, value: str) -> None:
        # The CNIPA form can re-render after each value change, invalidating
        # wrappers for adjacent inputs. Resolve the field immediately before use.
        document = self.session.document()
        self._edit_near_label(document, label).iface_value.SetValue(value)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            current = self._edit_near_label(self.session.document(), label)
            if str(current.get_value()) == value:
                return
            time.sleep(0.05)
        raise RuntimeError("商标查询输入框写入后校验失败")

    def _result_table(self) -> UIAWrapper | None:
        try:
            document = self.session.document()
        except Exception:
            return None
        for table in document.descendants(control_type="Table"):
            rows = self._table_rows(table)
            if not rows:
                continue
            headers = [cell.window_text().strip() for cell in rows[0].children()]
            if (
                header_index(headers, RESULT_HEADERS["mark"]) is not None
                and header_index(headers, RESULT_HEADERS["applicant"]) is not None
            ):
                return table
        return None

    def _detail_has_fields(self) -> bool:
        try:
            document = self.session.document()
        except Exception:
            return False
        return any(
            item.window_text().strip() == "申请/注册号"
            for item in document.descendants(control_type="DataItem")
        )

    def _require_result_table(self) -> UIAWrapper:
        table = self._result_table()
        if table is None:
            raise RuntimeError("页面未找到商标结果表")
        return table

    @staticmethod
    def _table_rows(table: UIAWrapper) -> list[UIAWrapper]:
        return [item for item in table.children() if item.element_info.control_type == "DataItem"]

    def _data_rows(self, table: UIAWrapper) -> list[UIAWrapper]:
        rows = self._table_rows(table)
        return rows[1:] if rows else []

    def _rows_fingerprint(self, table: UIAWrapper) -> tuple[tuple[str, ...], ...]:
        return tuple(
            tuple(cell.window_text().strip() for cell in row.children())
            for row in self._data_rows(table)
        )

    def _pagination_target(
        self, desired: int, direction: int, table: UIAWrapper
    ) -> UIAWrapper:
        document = self.session.document()
        table_bottom = table.rectangle().bottom
        direct = [
            item
            for item in document.descendants(control_type="Hyperlink")
            if item.window_text().strip() == str(desired)
            and item.rectangle().top >= table_bottom
        ]
        if direct:
            return direct[-1]
        labels = (
            ("下一页", ">", "›", "next", ">>", "»")
            if direction > 0
            else ("上一页", "<", "‹", "previous", "<<", "«")
        )
        fallbacks: list[UIAWrapper] = []
        for control_type in ("Hyperlink", "Button"):
            fallbacks.extend(
                item
                for item in document.descendants(control_type=control_type)
                if item.rectangle().top >= table_bottom
                and item.window_text().strip().casefold() in labels
            )
        if fallbacks:
            return min(
                fallbacks,
                key=lambda item: labels.index(item.window_text().strip().casefold()),
            )
        raise NeedsUserAction(BrowserState.WAITING_SITE, f"未找到结果第 {desired} 页入口")

    def _capture_result_row_image(self, row_index: int) -> Image.Image:
        page = self._background_capture("/list")
        try:
            box = self.result_row_box(row_index)
            document_width = max(1, self.session.document().rectangle().width())
            scale = page.width / document_width
            left = max(0, round(box.x * scale))
            top = max(0, round(box.y * scale))
            right = min(page.width, round((box.x + box.width) * scale))
            bottom = min(page.height, round((box.y + box.height) * scale))
            if right <= left or bottom <= top:
                raise RuntimeError("结果行不在后台截图范围内")
            return page.crop((left, top, right, bottom)).convert("RGB")
        finally:
            page.close()

    @staticmethod
    def _stack_images_bytes(images: Sequence[Image.Image]) -> bytes:
        width = max(image.width for image in images)
        height = sum(image.height for image in images) + 4 * (len(images) - 1)
        canvas = Image.new("RGB", (width, height), "white")
        y = 0
        for image in images:
            canvas.paste(image, (0, y))
            y += image.height + 4
        output = BytesIO()
        canvas.save(output, format="PNG", optimize=True)
        return output.getvalue()

    def _capture_full_document(self, output: Path, crop_horizontal: bool) -> Path:
        url_hint = "/detail" if crop_horizontal else "/list"
        canvas = self._background_capture(url_hint)
        document = self.session.document()
        rectangle = document.rectangle()
        if crop_horizontal:
            coordinate_scale = canvas.width / max(1, rectangle.width())
            cropped = self._crop_detail_horizontal(
                canvas, rectangle.left, coordinate_scale
            )
            if cropped is not canvas:
                canvas.close()
                canvas = cropped
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output, format="PNG", optimize=True)
        canvas.close()
        return output

    def _background_capture(self, url_hint: str) -> Image.Image:
        if self.page_capture is None:
            raise RuntimeError("Edge 后台截图服务未连接")
        return self.page_capture.capture(url_hint)

    def _crop_detail_horizontal(
        self, image: Image.Image, document_left: int, coordinate_scale: float = 1.0
    ) -> Image.Image:
        document = self.session.document()
        useful: list[tuple[int, int]] = []
        for item in document.descendants():
            name = item.window_text().strip()
            if (
                name.startswith("WWW.CNIPA.GOV.CN")
                or name.startswith("当前数据截至")
                or item.element_info.control_type == "Table"
            ):
                rectangle = item.rectangle()
                if not _rectangle_is_empty(rectangle):
                    useful.append((rectangle.left, rectangle.right))
        if not useful:
            return image
        left = max(
            0,
            round((min(item[0] for item in useful) - document_left - 12) * coordinate_scale),
        )
        right = min(
            image.width,
            round((max(item[1] for item in useful) - document_left + 12) * coordinate_scale),
        )
        return image.crop((left, 0, right, image.height)) if right > left else image

    def _require_state(self, expected: BrowserState, fallback: str) -> None:
        state = self.session.state()
        if state is expected:
            return
        self._raise_unexpected(state, fallback)

    def _raise_if_user_action(self, state: BrowserState) -> None:
        if state in {
            BrowserState.LOGIN_REQUIRED,
            BrowserState.CAPTCHA_REQUIRED,
            BrowserState.AUTH_BLOCKED,
            BrowserState.WAF_BLOCKED,
            BrowserState.RETURNED_HOME,
        }:
            self._raise_unexpected(state, "商标网站需要人工处理")

    @staticmethod
    def _raise_unexpected(state: BrowserState, fallback: str) -> None:
        messages = {
            BrowserState.LOGIN_REQUIRED: "请在 Edge 中完成登录",
            BrowserState.CAPTCHA_REQUIRED: "请在 Edge 中完成人工验证",
            BrowserState.AUTH_BLOCKED: "登录页面已失效，请从商标网站首页重新进入",
            BrowserState.WAF_BLOCKED: "商标网站拒绝访问，请稍后从首页重试",
            BrowserState.RETURNED_HOME: "请在 Edge 中进入商标综合查询",
        }
        raise NeedsUserAction(state, messages.get(state, fallback))
