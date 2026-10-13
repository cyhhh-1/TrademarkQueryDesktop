from __future__ import annotations

import pytest

from trademark_worker.browser.state import PageSnapshot, classify_page
from trademark_worker.models import BrowserState


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (PageSnapshot("about:blank", None, "", False, False), BrowserState.WAITING_SITE),
        (
            PageSnapshot(
                "https://sso.cnipa.gov.cn/am/#/login",
                200,
                "登录 验证码",
                False,
                True,
            ),
            BrowserState.LOGIN_REQUIRED,
        ),
        (
            PageSnapshot(
                "https://wcjs.sbj.cnipa.gov.cn/verify",
                200,
                "请完成验证码",
                False,
                True,
            ),
            BrowserState.CAPTCHA_REQUIRED,
        ),
        (
            PageSnapshot(
                "https://wcjs.sbj.cnipa.gov.cn/",
                200,
                "商标综合查询",
                False,
                False,
            ),
            BrowserState.RETURNED_HOME,
        ),
        (
            PageSnapshot(
                "https://wcjs.sbj.cnipa.gov.cn/sgtmi",
                200,
                "检索要素 查询",
                True,
                False,
                has_logged_in_identity=True,
            ),
            BrowserState.READY,
        ),
        (
            PageSnapshot(
                "https://wcjs.sbj.cnipa.gov.cn/list",
                200,
                "检索到1件商标",
                False,
                False,
            ),
            BrowserState.RESULTS,
        ),
        (
            PageSnapshot(
                "https://wcjs.sbj.cnipa.gov.cn/detail",
                200,
                "商标详情 专用权期限",
                False,
                False,
            ),
            BrowserState.DETAIL,
        ),
        (
            PageSnapshot(
                "https://wcjs.sbj.cnipa.gov.cn/", 403, "Forbidden", False, False
            ),
            BrowserState.WAF_BLOCKED,
        ),
        (
            PageSnapshot(
                "https://wcjs.sbj.cnipa.gov.cn/", 406, "Not Acceptable", False, False
            ),
            BrowserState.WAF_BLOCKED,
        ),
        (
            PageSnapshot(
                "https://wcjs.sbj.cnipa.gov.cn/",
                None,
                "访问异常 403",
                False,
                False,
            ),
            BrowserState.WAF_BLOCKED,
        ),
    ],
)
def test_classifies_browser_state(
    snapshot: PageSnapshot, expected: BrowserState
) -> None:
    assert classify_page(snapshot) is expected


def test_unrelated_site_waits_without_being_treated_as_logged_in() -> None:
    snapshot = PageSnapshot("https://example.com/", 200, "Example", False, False)

    assert classify_page(snapshot) is BrowserState.WAITING_SITE


def test_public_search_form_without_header_identity_requires_login() -> None:
    snapshot = PageSnapshot(
        "https://wcjs.sbj.cnipa.gov.cn/sgtmi",
        200,
        "检索要素 查询",
        True,
        False,
        has_logged_in_identity=False,
    )

    assert classify_page(snapshot) is BrowserState.LOGIN_REQUIRED


def test_403_browser_title_is_blocked_even_when_error_body_is_empty() -> None:
    snapshot = PageSnapshot(
        "https://wcjs.sbj.cnipa.gov.cn/",
        None,
        "",
        False,
        False,
        title="403 Forbidden",
    )

    assert classify_page(snapshot) is BrowserState.WAF_BLOCKED


def test_only_actual_sso_page_is_treated_as_login_required() -> None:
    detail = PageSnapshot(
        "https://wcjs.sbj.cnipa.gov.cn/detail",
        None,
        "",
        False,
        False,
        title="商标详细内容",
    )

    assert classify_page(detail) is BrowserState.DETAIL
