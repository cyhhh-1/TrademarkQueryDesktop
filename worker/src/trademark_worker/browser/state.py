"""Classify browser pages without interacting with login or captcha controls."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from trademark_worker.models import BrowserState


@dataclass(frozen=True, slots=True)
class PageSnapshot:
    url: str
    http_status: int | None
    text: str
    has_search_form: bool
    has_captcha: bool
    title: str = ""
    has_logged_in_identity: bool = False


def classify_page(snapshot: PageSnapshot) -> BrowserState:
    url = snapshot.url.strip()
    text = f"{snapshot.title}\n{snapshot.text}".casefold()
    # Edge can expose about:blank briefly while a tab is being created or
    # switched. It is not evidence that the employee's login expired.
    if not url or url == "about:blank":
        return BrowserState.WAITING_SITE
    if snapshot.http_status in {403, 406} or _looks_blocked(text):
        return BrowserState.WAF_BLOCKED

    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path.rstrip("/") or "/"
    if host == "sso.cnipa.gov.cn":
        return BrowserState.LOGIN_REQUIRED
    if snapshot.has_captcha:
        return BrowserState.CAPTCHA_REQUIRED
    if host != "wcjs.sbj.cnipa.gov.cn":
        return BrowserState.WAITING_SITE
    if path == "/sgtmi" and snapshot.has_search_form:
        # The public site can paint the complete search form before its delayed
        # authentication check redirects to SSO. The logged-in header identity
        # is the deterministic signal that the form is actually usable.
        return (
            BrowserState.READY
            if snapshot.has_logged_in_identity
            else BrowserState.LOGIN_REQUIRED
        )
    if path == "/list":
        return BrowserState.RESULTS
    if path == "/detail":
        return BrowserState.DETAIL
    return BrowserState.RETURNED_HOME


def _looks_blocked(text: str) -> bool:
    markers = (
        "403 forbidden",
        "406 not acceptable",
        "访问异常 403",
        "访问异常 406",
        "request rejected",
    )
    return any(marker in text for marker in markers)
