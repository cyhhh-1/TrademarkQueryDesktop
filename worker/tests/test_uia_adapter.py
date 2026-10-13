from __future__ import annotations

from types import SimpleNamespace

from PIL import Image
import pytest

from trademark_worker.browser.capture import BackgroundPageCapture
import trademark_worker.browser.uia as uia_module
from trademark_worker.browser.uia import (
    UIABrowserSession,
    UIAPagePort,
    detail_labels_from_rows,
    is_logged_in_header_name,
    page_counter,
    scroll_offset,
    tab_title,
)
from trademark_worker.models import BrowserState


def test_minimized_dedicated_edge_is_still_attachable_for_restore() -> None:
    class MinimizedWindow:
        handle = 77

        def rectangle(self):
            return SimpleNamespace(width=lambda: 0, height=lambda: 0)

        def window_text(self):
            return "CNIPA - Microsoft Edge"

    session = UIABrowserSession.__new__(UIABrowserSession)
    session.process_id = 4242
    session.desktop = SimpleNamespace(windows=lambda **_kwargs: [MinimizedWindow()])

    assert session._find_window_handle() == 77


def test_hiding_minimized_edge_for_background_keeps_other_window_focused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeUser32:
        foreground = 101
        position = (0, 0)

        def GetSystemMetrics(self, index: int) -> int:
            assert index == 76
            return 0

        def ShowWindow(self, hwnd: int, command: int) -> None:
            assert hwnd == 202
            if command == 9:  # SW_RESTORE activates a minimized window.
                self.foreground = hwnd

        def SetWindowPos(self, hwnd: int, _after: int, x: int, y: int,
                         _width: int, _height: int, flags: int) -> None:
            assert hwnd == 202
            if not flags & 0x0010:  # SWP_NOACTIVATE
                self.foreground = hwnd
            self.position = (x, y)

    user32 = FakeUser32()
    monkeypatch.setattr(uia_module.ctypes, "windll", SimpleNamespace(user32=user32))
    session = UIABrowserSession.__new__(UIABrowserSession)
    session.window_handle = 202

    session.hide_for_background()

    assert user32.foreground == 101
    assert user32.position == (-2300, 100)


@pytest.mark.parametrize(
    ("rect", "should_move"),
    [((-2300, 100, -500, 1100), True), ((100, 100, 1500, 900), False)],
)
def test_restore_for_user_brings_offscreen_edge_back_to_visible_monitor(
    monkeypatch: pytest.MonkeyPatch, rect: tuple[int, int, int, int], should_move: bool
) -> None:
    current_rect = list(rect)

    class FakeUser32:
        calls: list[tuple] = []

        def GetSystemMetrics(self, index: int) -> int:
            return {76: 0, 77: 0, 78: 1920, 79: 1080}[index]

        def ShowWindow(self, hwnd: int, command: int) -> None:
            self.calls.append(("show", hwnd, command))

        def SetWindowPos(self, hwnd: int, after: int, x: int, y: int,
                         width: int, height: int, flags: int) -> None:
            self.calls.append(("move", hwnd, x, y, width, height, flags))
            current_rect[:] = [x, y, x + width, y + height]

        def SetForegroundWindow(self, hwnd: int) -> None:
            self.calls.append(("foreground", hwnd))

    user32 = FakeUser32()
    monkeypatch.setattr(uia_module.ctypes, "windll", SimpleNamespace(user32=user32))
    session = UIABrowserSession.__new__(UIABrowserSession)
    session.window_handle = 202
    window = SimpleNamespace(rectangle=lambda: SimpleNamespace(
        left=current_rect[0], top=current_rect[1], right=current_rect[2], bottom=current_rect[3],
        width=lambda: current_rect[2] - current_rect[0], height=lambda: current_rect[3] - current_rect[1],
    ))
    session.desktop = SimpleNamespace(window=lambda **_kwargs: window)

    session.restore_for_user()

    moves = [call for call in user32.calls if call[0] == "move"]
    assert len(moves) == int(should_move)
    if should_move:
        assert moves[0][2:4] == (100, 100)
    assert user32.calls[-1] == ("foreground", 202)


def test_restore_for_user_rejects_edge_that_stays_offscreen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeUser32:
        def GetSystemMetrics(self, index: int) -> int:
            return {76: 0, 77: 0, 78: 1920, 79: 1080}[index]

        def ShowWindow(self, *_args) -> None:
            pass

        def SetWindowPos(self, *_args) -> None:
            pass

        def SetForegroundWindow(self, *_args) -> None:
            pass

    monkeypatch.setattr(uia_module.ctypes, "windll", SimpleNamespace(user32=FakeUser32()))
    session = UIABrowserSession.__new__(UIABrowserSession)
    session.window_handle = 202
    session.desktop = SimpleNamespace(window=lambda **_kwargs: SimpleNamespace(
        rectangle=lambda: SimpleNamespace(
            left=-2300, top=100, right=-500, bottom=1100,
            width=lambda: 1800, height=lambda: 1000,
        )
    ))

    with pytest.raises(RuntimeError, match="屏幕"):
        session.restore_for_user()


def test_logged_in_header_identity_excludes_public_navigation_text() -> None:
    for value in (
        "",
        "中文",
        "English",
        "帮助",
        "当前数据截至：(2026年09月10日)",
        "WWW.CNIPA.GOV.CN WCJS.SBJ.CNIPA.GOV.CN",
    ):
        assert not is_logged_in_header_name(value)

    assert is_logged_in_header_name("钱家卫")


class _SearchInvoke:
    def __init__(self) -> None:
        self.calls = 0

    def Invoke(self) -> None:
        self.calls += 1


class _SearchButton:
    def __init__(self) -> None:
        self.iface_invoke = _SearchInvoke()

    def window_text(self) -> str:
        return "查询"


class _SearchDocument:
    def __init__(self, button: _SearchButton) -> None:
        self.button = button

    def descendants(self, *, control_type: str):
        return [self.button] if control_type == "Button" else []


class _NewTabSession:
    def __init__(self, document: _SearchDocument) -> None:
        self._document = document
        self.results_selected = False

    def document(self) -> _SearchDocument:
        return self._document

    def state(self) -> BrowserState:
        return BrowserState.RESULTS if self.results_selected else BrowserState.READY


def test_submit_search_selects_the_new_result_tab_before_reading_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    button = _SearchButton()
    session = _NewTabSession(_SearchDocument(button))
    page = UIAPagePort(session)  # type: ignore[arg-type]
    selected: list[str] = []

    def select_tab(prefix: str) -> None:
        selected.append(prefix)
        if prefix == "商标检索结果":
            session.results_selected = True

    ticks = iter([0.0, 0.1, 20.1])
    monkeypatch.setattr(page, "_select_tab", select_tab)
    monkeypatch.setattr(page, "_result_table", lambda: object())
    monkeypatch.setattr(uia_module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(uia_module.time, "sleep", lambda _: None)

    page.submit_search()

    assert button.iface_invoke.calls == 1
    assert selected == ["商标综合检索", "商标检索结果"]


def test_click_mark_selects_new_detail_tab_before_validating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str] = []

    class Invoke:
        def Invoke(self) -> None:
            pass

    class Cell:
        def __init__(self, text: str, link: bool = False) -> None:
            self.text = text
            self.link = link

        def window_text(self) -> str:
            return self.text

        def descendants(self, *, control_type: str):
            return [type("Link", (), {"iface_invoke": Invoke()})()] if self.link else []

    class Row:
        def __init__(self, cells: list[Cell]) -> None:
            self.cells = cells

        def children(self) -> list[Cell]:
            return self.cells

    class Session:
        detail_selected = False

        def state(self) -> BrowserState:
            return BrowserState.DETAIL if self.detail_selected else BrowserState.RESULTS

    session = Session()
    page = UIAPagePort(session)  # type: ignore[arg-type]
    header = Row([Cell("序号"), Cell("商标"), Cell("申请人名称")])
    result = Row([Cell("1"), Cell("目标商标", link=True), Cell("申请人")])
    page._close_tabs = lambda _: None  # type: ignore[method-assign]

    def select_tab(prefix: str) -> None:
        selected.append(prefix)
        if prefix == "商标详细内容":
            session.detail_selected = True

    page._select_tab = select_tab  # type: ignore[method-assign]
    page._require_result_table = lambda: object()  # type: ignore[method-assign]
    page._table_rows = lambda _: [header]  # type: ignore[method-assign]
    page._data_rows = lambda _: [result]  # type: ignore[method-assign]
    page._detail_has_fields = lambda: True  # type: ignore[method-assign]
    ticks = iter([0.0, 0.1, 20.1])
    monkeypatch.setattr(uia_module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(uia_module.time, "sleep", lambda _: None)

    page.click_mark_cell(0, 1)

    assert selected == ["商标检索结果", "商标详细内容"]


def test_tab_title_removes_edge_memory_suffix() -> None:
    assert tab_title("商标检索结果 - 内存使用率 - 50.1 MB") == "商标检索结果"
    assert tab_title("商标综合检索") == "商标综合检索"


def test_scroll_offset_maps_uia_percent_to_document_pixels() -> None:
    assert scroll_offset(1000, 50.0, 0.0) == 0
    assert scroll_offset(1000, 50.0, 50.0) == 500
    assert scroll_offset(1000, 50.0, 100.0) == 1000
    assert scroll_offset(1000, 100.0, 100.0) == 0


def test_detail_labels_from_rows_maps_each_label_to_following_cell() -> None:
    rows = [
        ["申请/注册号", "66227440", "申请日期", "2022年07月27日", "国际分类", "36"],
        ["申请人名称（中文）", "北京字跳网络技术有限公司"],
        ["专用权期限", "2023年08月28日 至 2033年08月27日", "商标形式", ""],
        ["商标状态图标", "", "LIVE/REGISTRATION/Issued and Active", "注册商标"],
    ]

    labels = detail_labels_from_rows(rows)

    assert labels["申请/注册号"] == "66227440"
    assert labels["申请人名称（中文）"] == "北京字跳网络技术有限公司"
    assert labels["专用权期限"] == "2023年08月28日 至 2033年08月27日"
    assert labels["商标状态"] == "LIVE/REGISTRATION/Issued and Active 注册商标"


def test_page_counter_reads_current_and_total_pages() -> None:
    assert page_counter("总记录数：213 | 页数： 1 / 5") == (1, 5)
    assert page_counter("页数:12/18") == (12, 18)
    assert page_counter("当前没有分页") == (1, 1)


class _Rect:
    def __init__(self, top: int, bottom: int = 0) -> None:
        self.top = top
        self.bottom = bottom


class _Control:
    def __init__(self, name: str, top: int = 200) -> None:
        self.name = name
        self._rectangle = _Rect(top)

    def window_text(self) -> str:
        return self.name

    def rectangle(self) -> _Rect:
        return self._rectangle


class _Document:
    def __init__(self, controls: list[_Control]) -> None:
        self.controls = controls

    def descendants(self, control_type: str | None = None) -> list[_Control]:
        return self.controls if control_type in {"Hyperlink", "Button"} else []


class _Session:
    def __init__(self, controls: list[_Control]) -> None:
        self._document = _Document(controls)

    def document(self) -> _Document:
        return self._document


class _Table:
    def rectangle(self) -> _Rect:
        return _Rect(100, 150)


def test_pagination_uses_visible_numeric_page_first() -> None:
    expected = _Control("4")
    page = UIAPagePort(_Session([_Control("3"), expected, _Control(">")]))  # type: ignore[arg-type]

    assert page._pagination_target(4, 1, _Table()) is expected  # type: ignore[arg-type]


def test_pagination_recognizes_symbol_only_next_and_previous_controls() -> None:
    previous = _Control("<")
    next_page = _Control(">")
    page = UIAPagePort(
        _Session([_Control("<<"), previous, next_page, _Control(">>")])  # type: ignore[arg-type]
    )

    assert page._pagination_target(6, 1, _Table()) is next_page  # type: ignore[arg-type]
    assert page._pagination_target(4, -1, _Table()) is previous  # type: ignore[arg-type]


def test_go_to_page_walks_through_a_long_result_set_without_stalling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = [1]
    visited: list[int] = []

    class Session:
        def state(self) -> BrowserState:
            return BrowserState.RESULTS

    class Invoke:
        def __init__(self, desired: int) -> None:
            self.desired = desired

        def Invoke(self) -> None:
            current[0] = self.desired
            visited.append(self.desired)

    class Target:
        def __init__(self, desired: int) -> None:
            self.iface_invoke = Invoke(desired)

    page = UIAPagePort(Session())  # type: ignore[arg-type]
    page._select_tab = lambda _: None  # type: ignore[method-assign]
    page._page_counter = lambda: (current[0], 12)  # type: ignore[method-assign]
    page._require_result_table = lambda: object()  # type: ignore[method-assign]
    page._result_table = lambda: object()  # type: ignore[method-assign]
    page._rows_fingerprint = lambda _: ((str(current[0]),),)  # type: ignore[method-assign]
    page._pagination_target = (  # type: ignore[method-assign]
        lambda desired, _direction, _table: Target(desired)
    )
    monkeypatch.setattr(uia_module.time, "sleep", lambda _: None)

    page.go_to_page(12)

    assert visited == list(range(2, 13))
    assert page.current_page() == 12


class _ScrollItem:
    def ScrollIntoView(self) -> None:
        pass


class _ScreenshotRow(_Control):
    def __init__(self) -> None:
        super().__init__("row")
        self.iface_scroll_item = _ScrollItem()
        self._rectangle = type(
            "ScreenRectangle",
            (),
            {
                "left": 120,
                "top": 230,
                "right": 180,
                "bottom": 270,
                "width": lambda self: 60,
                "height": lambda self: 40,
            },
        )()


class _PageCapture:
    def __init__(self, image: Image.Image) -> None:
        self.image = image
        self.hints: list[str] = []

    def capture(self, url_hint: str) -> Image.Image:
        self.hints.append(url_hint)
        return self.image.copy()


def test_result_row_capture_uses_background_page_pixels_without_focusing() -> None:
    row = _ScreenshotRow()
    document = type(
        "Document",
        (),
        {
            "rectangle": lambda _self: type(
                "Rectangle", (), {"width": lambda _self: 300}
            )()
        },
    )()
    session = type(
        "Session", (), {"window_handle": 42, "document": lambda _self: document}
    )()
    source = Image.new("RGB", (300, 300), "navy")
    capture = _PageCapture(source)
    page = UIAPagePort(
        session,  # type: ignore[arg-type]
        page_capture=capture,
    )
    page._select_tab = lambda _: None  # type: ignore[method-assign]
    page._require_result_table = lambda: object()  # type: ignore[method-assign]
    page._data_rows = lambda _: [row]  # type: ignore[method-assign]
    page.result_row_box = lambda _: type(  # type: ignore[method-assign]
        "Box", (), {"x": 20, "y": 30, "width": 60, "height": 40, "scale": 1.0}
    )()

    captured = page._capture_result_row_image(0)

    assert captured.size == (60, 40)
    assert captured.getpixel((0, 0)) == (0, 0, 128)
    assert capture.hints == ["/list"]


class _FakeWebSocket:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []
        self.responses: list[str] = []

    def send(self, payload: str) -> None:
        import base64
        import json
        from io import BytesIO

        command = json.loads(payload)
        self.commands.append(command)
        response: dict[str, object]
        if command["method"] == "Page.getLayoutMetrics":
            response = {
                "id": command["id"],
                "result": {"cssContentSize": {"width": 300, "height": 450}},
            }
        else:
            buffer = BytesIO()
            Image.new("RGB", (300, 450), "white").save(buffer, format="PNG")
            response = {
                "id": command["id"],
                "result": {"data": base64.b64encode(buffer.getvalue()).decode("ascii")},
            }
        self.responses.append(json.dumps(response))

    def recv(self) -> str:
        return self.responses.pop(0)

    def close(self) -> None:
        pass


def test_cdp_capture_selects_cnipa_target_and_captures_full_page() -> None:
    socket = _FakeWebSocket()
    capture = BackgroundPageCapture(
        "http://127.0.0.1:43127",
        target_loader=lambda _: [
            {"type": "page", "url": "https://wcjs.sbj.cnipa.gov.cn/home", "webSocketDebuggerUrl": "ws://home"},
            {"type": "page", "url": "https://wcjs.sbj.cnipa.gov.cn/list", "webSocketDebuggerUrl": "ws://list"},
        ],
        websocket_factory=lambda *_args, **_kwargs: socket,
    )

    image = capture.capture("/list")

    assert image.size == (300, 450)
    assert [item["method"] for item in socket.commands] == [
        "Page.getLayoutMetrics",
        "Page.captureScreenshot",
    ]
    assert socket.commands[1]["params"]["captureBeyondViewport"] is True  # type: ignore[index]
