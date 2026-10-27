from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
from PIL import ImageDraw
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
import pytest

import trademark_worker.browser.background as background_module
from trademark_worker.browser.background import (
    BackgroundPagePort,
    _OPEN_WITHOUT_POPUP,
    capture_page_image,
    crop_box_for_row,
    row_boxes_from_metrics,
)
from trademark_worker.local_ocr import LocalMarkAssessment
from trademark_worker.browser.edge import resolve_edge_executable


def test_site_check_tolerates_result_page_before_body_exists() -> None:
    class MissingBody:
        def count(self) -> int:
            return 0

        def inner_text(self, **_kwargs):
            raise AssertionError("must not wait for a missing body")

    page = SimpleNamespace(
        url="https://wcjs.sbj.cnipa.gov.cn/list",
        locator=lambda selector: MissingBody(),
    )

    BackgroundPagePort(page, None)._check_site()


def test_site_check_detects_cloud_security_transient_page() -> None:
    class Body:
        def count(self):
            return 1

        def inner_text(self, **_kwargs):
            return "云安全平台检测到您当前的访问行为存在异常，请稍后重试"

    page = SimpleNamespace(
        url="https://wcjs.sbj.cnipa.gov.cn/detail",
        locator=lambda _selector: Body(),
    )

    try:
        BackgroundPagePort(page, None)._check_site()
    except background_module.TransientSiteError as exc:
        assert "访问行为异常" in str(exc)
    else:
        raise AssertionError("transient cloud security page must be detected")


def test_results_page_has_bounded_wait_before_next_row() -> None:
    port = BackgroundPagePort(SimpleNamespace(), None)
    observed: list[float] = []
    port._wait_for = lambda _predicate, _message, seconds=90: observed.append(seconds)
    port._check_site = lambda: None

    port.require_results_ready()

    assert observed == [35]


def test_current_cnipa_comprehensive_form_is_recognized_as_ready(monkeypatch) -> None:
    """The live site no longer gives the applicant inputs useful placeholders."""
    html = """
    <main>
      <label>国际分类</label><input id="intCls" type="text">
      <label>申请/注册号</label><input type="text">
      <label>检索要素</label><input type="text">
      <label>申请人名称（中文）</label><input type="text">
      <label>申请人名称（英文）</label><input type="text">
      <input type="button" value="查询">
    </main>
    """
    origin = "https://trademark-ready.invalid"
    monkeypatch.setattr(background_module, "CNIPA_ORIGIN", origin)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(resolve_edge_executable()), headless=True
        )
        try:
            page = browser.new_page()
            page.route(
                f"{origin}/**",
                lambda route: route.fulfill(
                    status=200, body=html, content_type="text/html; charset=utf-8"
                ),
            )
            page.goto(f"{origin}/sgtmi")

            BackgroundPagePort(page, None).require_search_ready()

            assert page.url == f"{origin}/sgtmi"
        finally:
            browser.close()


def test_expired_home_page_is_reloaded_in_place(monkeypatch) -> None:
    origin = "https://trademark-expired.invalid"
    monkeypatch.setattr(background_module, "CNIPA_ORIGIN", origin)
    home_requests = 0
    home_html = '<a href="/sgtmi">商标综合查询</a>'
    search_html = """
    <input type="text"><input type="text"><input type="text">
    <input type="text"><input type="text">
    <input type="button" value="查询">
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(resolve_edge_executable()), headless=True
        )
        try:
            page = browser.new_page()

            def route_request(route) -> None:
                nonlocal home_requests
                if route.request.url.rstrip("/") == origin:
                    home_requests += 1
                    body = "<body>页面已过期</body>" if home_requests == 1 else home_html
                else:
                    body = search_html
                route.fulfill(
                    status=200, body=body, content_type="text/html; charset=utf-8"
                )

            page.route(f"{origin}/**", route_request)
            page.goto(f"{origin}/list")

            BackgroundPagePort(page, None).require_search_ready()

            assert page.url == f"{origin}/sgtmi"
            assert home_requests == 2
        finally:
            browser.close()


def test_expired_result_page_recovers_before_deferring_row() -> None:
    """An expired result page should retry locally while query state remains."""
    class ResultPage:
        url = "https://wcjs.sbj.cnipa.gov.cn/list"
        expired = True
        reloads = 0
        waits = []

        def locator(self, selector):
            page = self

            class Locator:
                def count(self):
                    if selector == "body":
                        return 1
                    if selector == "table tr":
                        return 0 if page.expired else 2
                    raise AssertionError(selector)

                def inner_text(self, **_kwargs):
                    return "页面已过期" if page.expired else "查询结果"

            return Locator()

        def reload(self, **_kwargs):
            self.reloads += 1
            self.expired = False

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    page = ResultPage()
    BackgroundPagePort(page, None).require_results_ready()
    assert page.reloads == 1
    assert page.waits == [1000]


@pytest.mark.parametrize("recover_after, succeeds", [(10, True), (11, False)])
def test_expired_result_page_refreshes_at_most_ten_times(
    recover_after: int, succeeds: bool
) -> None:
    class ResultPage:
        url = "https://wcjs.sbj.cnipa.gov.cn/list"

        def __init__(self) -> None:
            self.reloads = 0
            self.waits: list[int] = []

        def locator(self, selector):
            page = self

            class Locator:
                def count(self):
                    if selector == "body":
                        return 1
                    if selector == "table tr":
                        return 0 if page.reloads < recover_after else 2
                    raise AssertionError(selector)

                def inner_text(self, **_kwargs):
                    return "页面已过期" if page.reloads < recover_after else "查询结果"

            return Locator()

        def reload(self, **_kwargs):
            self.reloads += 1

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    page = ResultPage()
    port = BackgroundPagePort(page, None)
    if succeeds:
        port.require_results_ready()
    else:
        with pytest.raises(RuntimeError, match="刷新10次后仍显示页面已过期"):
            port.require_results_ready()
    assert page.reloads == 10
    assert page.waits == [1000] * 10


@pytest.mark.parametrize("recover_after, succeeds", [(10, True), (11, False)])
def test_expired_result_pagination_restores_query_at_most_ten_times(
    recover_after: int, succeeds: bool
) -> None:
    waits: list[int] = []
    page = SimpleNamespace(wait_for_timeout=waits.append)
    port = BackgroundPagePort(page, None)
    restored: list[bool] = []
    port._check_site = lambda: None
    port._restore_list = lambda: restored.append(True)

    def navigate(_page_number: int):
        if len(restored) < recover_after:
            raise background_module.ExpiredPageError("页面已过期")

    port._go_to_page_once = navigate
    if succeeds:
        port.go_to_page(2)
    else:
        with pytest.raises(RuntimeError, match="刷新10次后仍显示页面已过期"):
            port.go_to_page(2)
    assert len(restored) == 10
    assert waits == [1000] * 10


def test_expired_result_page_can_restore_saved_query() -> None:
    """A stale table must not block restoring its saved search parameters."""
    class Body:
        def count(self):
            return 1

        def inner_text(self, **_kwargs):
            return "页面已过期"

    waits = []
    page = SimpleNamespace(
        url="https://wcjs.sbj.cnipa.gov.cn/list",
        locator=lambda _selector: Body(),
        wait_for_timeout=waits.append,
    )
    port = BackgroundPagePort(page, None)
    restored = []
    port._restore_list = lambda: restored.append(True)

    port.refresh_results(1)

    assert restored == [True]
    assert waits == [1000]


def test_expired_result_api_timeout_reloads_before_deferring_row() -> None:
    class ResultPage:
        url = "https://wcjs.sbj.cnipa.gov.cn/list"
        expired = False
        requests = 0
        reloads = 0

        def locator(self, _selector):
            page = self

            class Body:
                def count(self):
                    return 1

                def inner_text(self, **_kwargs):
                    return "页面已过期" if page.expired else "查询结果"

            return Body()

        def expect_response(self, _predicate, **_kwargs):
            self.requests += 1
            first_request = self.requests == 1

            class Response:
                ok = True

                def finished(self):
                    pass

            class ResponseInfo:
                value = Response()

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    if first_request:
                        raise PlaywrightTimeoutError("result API did not respond")

            return ResponseInfo()

        def goto(self, *_args, **_kwargs):
            self.expired = True

        def reload(self, **_kwargs):
            self.reloads += 1
            self.expired = False

        def wait_for_timeout(self, _milliseconds):
            pass

    page = ResultPage()
    port = BackgroundPagePort(page, None)
    port.require_results_ready = lambda: None
    port._wait_for_results_stable = lambda: None

    port._load_current_results()

    assert (page.requests, page.reloads) == (2, 1)


def test_expired_detail_capture_waits_one_second_before_refresh(
    monkeypatch, tmp_path
) -> None:
    waits = []
    reloads = []
    page = SimpleNamespace(
        wait_for_timeout=waits.append,
        reload=lambda **_kwargs: reloads.append(True),
    )
    port = BackgroundPagePort(page, None)
    attempts = []

    def wait_for_capture():
        attempts.append(True)
        if len(attempts) == 1:
            raise background_module.ExpiredPageError("页面已过期")

    port._wait_for_detail_capture_ready = wait_for_capture
    port._capture_detail_image = lambda: Image.new("RGB", (100, 100), "white")
    port._save_image = lambda _image, output: output
    port.require_detail_ready = lambda: None
    monkeypatch.setattr(background_module, "detail_image_is_complete", lambda _image: True)

    output = tmp_path / "detail.png"
    assert port.capture_detail_page(output) == output
    assert waits == [1000]
    assert reloads == [True]


@pytest.mark.parametrize("recover_after, succeeds", [(10, True), (11, False)])
def test_expired_detail_status_refreshes_at_most_ten_times(
    recover_after: int, succeeds: bool
) -> None:
    class DetailPage:
        url = "https://wcjs.sbj.cnipa.gov.cn/detail"

        def __init__(self) -> None:
            self.reloads = 0
            self.waits: list[int] = []

        def reload(self, **_kwargs):
            self.reloads += 1

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    page = DetailPage()
    port = BackgroundPagePort(page, None)

    def wait_for_ready(_predicate, _message, seconds):
        if page.reloads < recover_after:
            raise background_module.ExpiredPageError("页面已过期")

    port._wait_for = wait_for_ready
    port._check_site = lambda: None
    if succeeds:
        port.require_detail_ready()
    else:
        with pytest.raises(RuntimeError, match="刷新10次后仍显示页面已过期"):
            port.require_detail_ready()
    assert page.reloads == 10
    assert page.waits == [1000] * 10


def test_staged_local_ocr_sends_only_ambiguous_rows_to_remote(monkeypatch) -> None:
    records = [
        background_module.ResultRecord(1, "1", "35", "", "", "甲公司", row_index=0),
        background_module.ResultRecord(2, "2", "35", "", "", "甲公司", row_index=1),
    ]

    class LocalRecognizer:
        calls = 0

        def assess_mark(self, _image, _expected):
            self.calls += 1
            if self.calls == 1:
                return LocalMarkAssessment("云杉月付", False)
            return LocalMarkAssessment("云衫", True)

    class RemoteRecognizer:
        calls: list[list[int]] = []

        def recognize_marks(self, _image, batch, _expected):
            self.calls.append([item.sequence for item in batch])
            return {2: "云杉"}

    remote = RemoteRecognizer()
    port = BackgroundPagePort(SimpleNamespace(), remote, LocalRecognizer())
    monkeypatch.setattr(
        port,
        "_capture_result_mark_image",
        lambda _index: Image.new("RGB", (120, 60), "white"),
    )
    monkeypatch.setattr(
        port,
        "_capture_result_row_image",
        lambda _index: Image.new("RGB", (300, 80), "white"),
    )

    enriched = port.enrich_result_marks(records, "云杉")

    assert [item.mark for item in enriched] == ["云杉月付", "云杉"]
    assert remote.calls == [[2]]
    assert port.remote_model_call_count == 1


def test_missing_mark_ocr_retries_unresolved_rows_individually(monkeypatch) -> None:
    records = [
        background_module.ResultRecord(1, "1", "35", "", "", "甲公司", row_index=0),
        background_module.ResultRecord(2, "2", "35", "", "", "乙公司", row_index=1),
    ]

    class Recognizer:
        calls: list[list[int]] = []

        def recognize_marks(self, _image, batch, _expected):
            self.calls.append([item.sequence for item in batch])
            return {1: "风行"} if len(batch) > 1 else {2: "风行"}

    recognizer = Recognizer()
    port = BackgroundPagePort(SimpleNamespace(), recognizer)
    monkeypatch.setattr(
        port,
        "_capture_result_row_image",
        lambda _index: Image.new("RGB", (300, 80), "white"),
    )

    enriched = port.enrich_result_marks(records, "风行")

    assert [item.mark for item in enriched] == ["风行", "风行"]
    assert recognizer.calls == [[1, 2], [2]]


def test_explicit_graphic_only_mark_is_not_retried(monkeypatch) -> None:
    records = [
        background_module.ResultRecord(1, "1", "35", "", "", "甲公司", row_index=0),
        background_module.ResultRecord(2, "2", "35", "", "", "乙公司", row_index=1),
    ]

    class Recognizer:
        calls: list[list[int]] = []

        def recognize_marks(self, _image, batch, _expected):
            self.calls.append([item.sequence for item in batch])
            return {1: "青猫", 2: ""}

    recognizer = Recognizer()
    port = BackgroundPagePort(SimpleNamespace(), recognizer)
    monkeypatch.setattr(
        port,
        "_capture_result_row_image",
        lambda _index: Image.new("RGB", (300, 80), "white"),
    )

    enriched = port.enrich_result_marks(records, "青猫")

    assert [item.mark for item in enriched] == ["青猫", ""]
    assert recognizer.calls == [[1, 2]]


def test_thirty_two_result_rows_use_bounded_model_batches(monkeypatch) -> None:
    records = [
        background_module.ResultRecord(
            index,
            str(index),
            "35",
            "",
            "",
            "甲公司",
            row_index=index - 1,
        )
        for index in range(1, 33)
    ]

    class Recognizer:
        calls = 0

        def recognize_marks(self, _image, batch, _expected):
            self.calls += 1
            return {item.sequence: "星河闪购" for item in batch}

    recognizer = Recognizer()
    port = BackgroundPagePort(SimpleNamespace(), recognizer)
    monkeypatch.setattr(
        port,
        "_capture_result_row_image",
        lambda _index: Image.new("RGB", (300, 40), "white"),
    )

    enriched = port.enrich_result_marks(records, "星河闪购")

    assert len(enriched) == 32
    assert recognizer.calls == 3
    assert port.remote_model_call_count == 3

    cached = port.enrich_result_marks(records, "星河闪购")
    assert [item.mark for item in cached] == ["星河闪购"] * 32
    assert recognizer.calls == 3
    assert port.remote_model_call_count == 3


def test_scaled_viewport_capture_is_not_mistaken_for_full_document() -> None:
    rect = {"x": 10, "y": 600, "width": 900, "height": 100}
    crop = crop_box_for_row(
        rect,
        image_width=2400,
        image_height=1800,
        document_width=1200,
        document_height=1600,
        viewport_width=1200,
        viewport_height=900,
        scroll_x=0,
        scroll_y=700,
    )

    assert crop == (20, 1200, 1820, 1400)


def test_detail_ready_refreshes_transient_cloud_security_page(monkeypatch) -> None:
    class Page:
        url = background_module.CNIPA_ORIGIN + "/detail"
        reloads = 0

        def wait_for_timeout(self, _milliseconds):
            pass

        def reload(self, **_kwargs):
            self.reloads += 1

    page = Page()
    port = BackgroundPagePort(page, None)
    calls = 0

    def wait(_predicate, _message, seconds=90):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise background_module.TransientSiteError("访问行为存在异常")

    monkeypatch.setattr(port, "_wait_for", wait)
    monkeypatch.setattr(port, "_check_site", lambda: None)

    port.require_detail_ready()

    assert calls == 3
    assert page.reloads == 1


def test_detail_ready_requires_status_and_active_term(monkeypatch) -> None:
    port = BackgroundPagePort(SimpleNamespace(), None)

    monkeypatch.setattr(port, "read_detail_labels", lambda: {})
    assert not port._detail_decision_fields_ready()

    monkeypatch.setattr(
        port,
        "read_detail_labels",
        lambda: {"商标状态": "LIVE/REGISTRATION/Issued and Active 注册商标"},
    )
    assert not port._detail_decision_fields_ready()

    monkeypatch.setattr(
        port,
        "read_detail_labels",
        lambda: {"商标状态": "注册商标", "专用权期限": "2021年至2031年"},
    )
    assert not port._detail_decision_fields_ready()

    monkeypatch.setattr(
        port,
        "read_detail_labels",
        lambda: {
            "商标状态": "LIVE/REGISTRATION/Issued and Active 注册商标",
            "专用权期限": "2021年06月21日 至 2031年06月20日",
        },
    )
    assert port._detail_decision_fields_ready()

    monkeypatch.setattr(
        port,
        "read_detail_labels",
        lambda: {"商标状态": "DEAD/REGISTRATION/Cancelled 注册商标被撤销"},
    )
    assert port._detail_decision_fields_ready()


def test_submit_search_waits_for_current_query_results_instead_of_stale_table(
    monkeypatch,
) -> None:
    origin = "https://trademark-results.invalid"
    monkeypatch.setattr(background_module, "CNIPA_ORIGIN", origin)
    search_html = """
    <input type="button" value="查询"
      onclick="sessionStorage.setItem('params', 'current-query')">
    """
    results_html = """
    <table><tr><th>序号</th></tr><tr><td>OLD-RESULT</td></tr></table>
    <script>
      setTimeout(() => {
        fetch('/api/so/com').then(() => {
          document.querySelector('table').innerHTML =
            '<tr><th>序号</th></tr><tr><td>NEW-RESULT</td></tr>';
        });
      }, 350);
    </script>
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(resolve_edge_executable()), headless=True
        )
        try:
            page = browser.new_page()

            def route_request(route) -> None:
                if route.request.url.endswith("/api/so/com"):
                    route.fulfill(status=200, body="{}", content_type="application/json")
                elif route.request.url.endswith("/list"):
                    route.fulfill(
                        status=200,
                        body=results_html,
                        content_type="text/html; charset=utf-8",
                    )
                else:
                    route.fulfill(
                        status=200,
                        body=search_html,
                        content_type="text/html; charset=utf-8",
                    )

            page.route(f"{origin}/**", route_request)
            page.goto(f"{origin}/sgtmi")
            port = BackgroundPagePort(page, None)

            port.submit_search()

            assert "NEW-RESULT" in port._row_fingerprint()
        finally:
            browser.close()


def test_submit_search_refreshes_once_when_results_api_returns_403(monkeypatch) -> None:
    origin = "https://trademark-results-refresh.invalid"
    monkeypatch.setattr(background_module, "CNIPA_ORIGIN", origin)
    monkeypatch.setattr(background_module, "RESULTS_HTTP_403_REFRESH_DELAY_MS", 1)
    api_requests = 0
    search_html = """
    <input type="button" value="查询"
      onclick="sessionStorage.setItem('params', 'current-query')">
    """
    results_html = """
    <table><tr><th>序号</th></tr><tr><td>RESULT</td></tr></table>
    <script>fetch('/api/so/com')</script>
    """

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(resolve_edge_executable()), headless=True
        )
        try:
            page = browser.new_page()

            def route_request(route) -> None:
                nonlocal api_requests
                if route.request.url.endswith("/api/so/com"):
                    api_requests += 1
                    route.fulfill(
                        status=403 if api_requests == 1 else 200,
                        body="{}",
                        content_type="application/json",
                    )
                elif route.request.url.endswith("/list"):
                    route.fulfill(
                        status=200,
                        body=results_html,
                        content_type="text/html; charset=utf-8",
                    )
                else:
                    route.fulfill(
                        status=200,
                        body=search_html,
                        content_type="text/html; charset=utf-8",
                    )

            page.route(f"{origin}/**", route_request)
            page.goto(f"{origin}/sgtmi")

            BackgroundPagePort(page, None).submit_search()

            assert api_requests == 2
        finally:
            browser.close()


def test_capture_does_not_activate_edge_tab_and_uses_view_screenshot() -> None:
    image = Image.new("RGB", (20, 30), "white")
    stream = BytesIO()
    image.save(stream, format="PNG")
    commands: list[tuple[str, dict | None]] = []

    class FakeSession:
        def send(self, method: str, params: dict | None = None):
            commands.append((method, params))
            if method == "Page.captureScreenshot":
                return {"data": base64.b64encode(stream.getvalue()).decode("ascii")}
            return {}

        def detach(self):
            commands.append(("detach", None))

    page = SimpleNamespace(context=SimpleNamespace(new_cdp_session=lambda _: FakeSession()))

    result = capture_page_image(page)

    assert result.size == (20, 30)
    assert commands == [
        (
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": False, "captureBeyondViewport": True},
        ),
        ("detach", None),
    ]


def test_clicking_mark_anchor_stores_query_params_without_opening_a_tab() -> None:
    html = (
        '<a id="mark" href="about:blank" target="_blank" '
        'onclick="sessionStorage.setItem(\'params\',\'ok\')">商标</a>'
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(resolve_edge_executable()), headless=True
        )
        try:
            context = browser.new_context()
            page = context.new_page()
            page.route(
                "https://trademark-test.invalid/**",
                lambda route: route.fulfill(status=200, body=html, content_type="text/html"),
            )
            page.goto("https://trademark-test.invalid/")

            params = page.locator("#mark").evaluate(_OPEN_WITHOUT_POPUP)
            page.wait_for_timeout(200)

            assert params == "ok"
            assert len(context.pages) == 1
        finally:
            browser.close()


def test_row_boxes_use_document_coordinates_after_scroll() -> None:
    boxes = row_boxes_from_metrics(
        [{"x": 20, "y": 10, "width": 300, "height": 100}],
        scroll_x=0,
        scroll_y=600,
        scale=1.5,
    )

    assert boxes[0].x == 20
    assert boxes[0].y == 610
    assert boxes[0].scale == 1.5


def test_tall_result_page_crops_visible_viewport_after_scrolling() -> None:
    rect = {"x": 10, "y": 180, "width": 1000, "height": 100}
    crop = crop_box_for_row(
        rect, image_width=1200, image_height=900,
        document_width=1200, document_height=3200,
        viewport_width=1200, scroll_x=0, scroll_y=1100,
    )
    assert crop == (10, 180, 1010, 280)


def test_full_document_capture_uses_document_y() -> None:
    rect = {"x": 10, "y": 180, "width": 1000, "height": 100}
    crop = crop_box_for_row(
        rect, image_width=1200, image_height=3200,
        document_width=1200, document_height=3200,
        viewport_width=1200, scroll_x=0, scroll_y=1100,
    )
    assert crop == (10, 1280, 1010, 1380)


def test_full_page_capture_includes_detail_status_below_the_viewport(monkeypatch) -> None:
    class FakePage:
        scroll = 0

        def evaluate(self, script: str, argument=None):
            if "scrollTo" in script:
                self.scroll = min(int(argument), 3)
                return None
            return {
                "viewportWidth": 4,
                "viewportHeight": 4,
                "documentHeight": 7,
                "scrollY": self.scroll,
            }

        def wait_for_timeout(self, _milliseconds: int) -> None:
            pass

    page = FakePage()
    monkeypatch.setattr(
        background_module,
        "capture_page_image",
        lambda current: Image.new("RGB", (4, 4), "red" if current.scroll == 0 else "blue"),
    )

    image = background_module.capture_full_page_image(page)

    assert image.size == (4, 7)
    assert image.getpixel((0, 0)) == (255, 0, 0)
    assert image.getpixel((0, 6)) == (0, 0, 255)
    assert page.scroll == 0
    image.close()


def test_detail_evidence_crop_keeps_full_record_but_excludes_site_footer() -> None:
    crop = background_module.detail_evidence_crop_box(
        table_bounds=[{"x": 286, "width": 830}],
        header_bounds={"x": 48, "y": 12, "width": 1260},
        content_bottom=976,
        image_width=1773,
        image_height=1599,
        document_width=1403,
    )

    scale = 1773 / 1403
    assert crop[0] == round(48 * scale)
    assert crop[2] == round((48 + 1260) * scale)
    assert crop[1] == round(12 * scale)
    assert crop[3] == round(976 * scale)


def test_detail_capture_waits_for_goods_services_text(monkeypatch) -> None:
    class Page:
        url = "https://wcjs.sbj.cnipa.gov.cn/detail"

        def evaluate(self, _script):
            return {
                "ready": True,
                "serviceReady": False,
                "serviceText": "",
                "text": "申请/注册号 申请人名称 专用权期限 商标状态",
                "height": 1000,
            }

        def wait_for_timeout(self, _milliseconds):
            pass

    clock = 0.0

    def monotonic():
        nonlocal clock
        clock += 0.4
        return clock

    port = BackgroundPagePort(Page(), None)
    monkeypatch.setattr(port, "_check_site", lambda: None)
    monkeypatch.setattr(background_module.time, "monotonic", monotonic)

    with pytest.raises(RuntimeError, match="商品/服务"):
        port._wait_for_detail_capture_ready()


def test_detail_capture_waits_until_goods_services_ajax_text_arrives() -> None:
    html = """
    <div class="xqbox">
      <div>商品/服务</div><div id="services"><span class="spinner"></span></div>
      <div>类似群</div><div>4502;</div>
      <div>申请/注册号</div><div>67195611</div>
      <div>申请人名称（中文）</div><div>甲辰科技（北京）有限公司</div>
      <div>专用权期限</div><div>2023年03月28日 至 2033年03月27日</div>
      <div>商标状态</div><div>注册商标</div>
    </div>
    <script>
      setTimeout(() => {
        document.querySelector('#services').textContent = '医疗陪护服务；健康咨询服务；';
      }, 900);
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(resolve_edge_executable()), headless=True
        )
        try:
            page = browser.new_page()
            page.set_content(html)

            BackgroundPagePort(page, None)._wait_for_detail_capture_ready()

            assert "医疗陪护服务" in page.locator("#services").inner_text()
        finally:
            browser.close()


def test_detail_evidence_rejects_a_partially_rendered_capture() -> None:
    partial = Image.new("RGB", (900, 1000), "white")
    draw = ImageDraw.Draw(partial)
    draw.rectangle((40, 20, 860, 380), fill=(210, 220, 235))
    for y in range(40, 360, 35):
        draw.line((80, y, 700, y), fill="black", width=3)

    assert not background_module.detail_image_is_complete(partial)
    partial.close()


def test_detail_evidence_accepts_content_across_the_full_record() -> None:
    complete = Image.new("RGB", (900, 1000), "white")
    draw = ImageDraw.Draw(complete)
    for band in range(5):
        top = band * 200 + 25
        draw.rectangle((45, top, 855, top + 130), outline=(100, 120, 150), width=3)
        for y in range(top + 25, top + 110, 25):
            draw.line((80, y, 700, y), fill="black", width=3)

    assert background_module.detail_image_is_complete(complete)
    complete.close()


def test_detail_capture_refreshes_and_retries_incomplete_render(
    monkeypatch, tmp_path
) -> None:
    class Page:
        reloads = 0

        def reload(self, **_kwargs):
            self.reloads += 1

    page = Page()
    port = BackgroundPagePort(page, None)
    monkeypatch.setattr(port, "_wait_for_detail_capture_ready", lambda: None)
    monkeypatch.setattr(port, "require_detail_ready", lambda: None)
    calls = 0

    def capture():
        nonlocal calls
        calls += 1
        image = Image.new("RGB", (900, 1000), "white")
        draw = ImageDraw.Draw(image)
        band_limit = 2 if calls == 1 else 5
        for band in range(band_limit):
            top = band * 200 + 25
            draw.rectangle((45, top, 855, top + 130), outline="black", width=4)
            draw.line((80, top + 60, 700, top + 60), fill="black", width=3)
        return image

    monkeypatch.setattr(port, "_capture_detail_image", capture)

    output = port.capture_detail_page(tmp_path / "detail.png")

    assert output.is_file()
    assert calls == 2
    assert page.reloads == 1


def test_full_page_stitch_avoids_viewport_border_at_tile_top(monkeypatch) -> None:
    class FakePage:
        scroll = 0

        def evaluate(self, script: str, argument=None):
            if "scrollTo" in script:
                self.scroll = min(int(argument), 3)
                return None
            return {"viewportWidth": 4, "viewportHeight": 4,
                    "documentHeight": 7, "scrollY": self.scroll}

        def wait_for_timeout(self, _milliseconds: int) -> None:
            pass

    def capture(page):
        color = "red" if page.scroll == 0 else "blue"
        tile = Image.new("RGB", (4, 4), color)
        if page.scroll:
            for x in range(4):
                tile.putpixel((x, 0), (0, 0, 0))
        return tile

    monkeypatch.setattr(background_module, "capture_page_image", capture)
    image = background_module.capture_full_page_image(FakePage())

    assert image.getpixel((0, 3)) == (255, 0, 0)
    assert image.getpixel((0, 4)) == (0, 0, 255)
    image.close()
