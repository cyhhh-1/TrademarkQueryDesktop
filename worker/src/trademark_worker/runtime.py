"""Command router for the persistent desktop worker process."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import sys
from threading import Lock, Thread
import time
from typing import TextIO
from uuid import uuid4

from playwright.sync_api import sync_playwright

from trademark_worker.browser.edge import (
    EdgeLaunchError,
    EdgeProcess,
    active_edge_process_id,
    launch_login_edge,
    stop_edge_process,
)
from trademark_worker.browser.background import CNIPA_ORIGIN, BackgroundPagePort
from trademark_worker.browser.uia import UIABrowserSession
from trademark_worker.checkpoint import CheckpointStore, ResumeError
from trademark_worker.cnipa.client import CnipaClient
from trademark_worker.models import BrowserState, NeedsUserAction, TaskRequest
from trademark_worker.local_ocr import build_local_ocr_recognizer
from trademark_worker.paths import app_data_root, build_run_paths
from trademark_worker.protocol import WorkerCommand, WorkerEvent, emit_event
from trademark_worker.task_engine import TaskEngine
from trademark_worker.workbook.inspector import (
    WorkbookInputError,
    inspect_workbook,
    load_jobs,
)
from trademark_worker.workbook.writer import WorkbookOutputError
from trademark_worker.vision import build_vision_recognizer


READY_STABILITY_POLLS = 20
READY_STABILITY_INTERVAL_SECONDS = 0.5


class CommandError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EventChannel:
    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self._lock = Lock()

    def emit(self, payload: dict[str, object]) -> None:
        event_type = str(payload.get("type", "error"))
        event_payload = {key: value for key, value in payload.items() if key != "type"}
        with self._lock:
            emit_event(WorkerEvent(event_type, event_payload), self.stream)


class WorkerRuntime:
    def __init__(
        self,
        channel: EventChannel,
        data_root: Path | None = None,
        error_stream: TextIO = sys.stderr,
    ) -> None:
        self.channel = channel
        self.data_root = Path(data_root or app_data_root())
        self.error_stream = error_stream
        self._edge: EdgeProcess | None = None
        self._engine: TaskEngine | None = None
        self._task_thread: Thread | None = None
        self._confirmed_process_id: int | None = None
        self._lock = Lock()

    def handle(self, command: WorkerCommand) -> bool:
        handlers: dict[str, Callable[[dict[str, object]], None]] = {
            "inspect_file": self._inspect_file,
            "open_browser": self._open_browser,
            "show_browser": self._show_browser,
            "connect_browser": self._connect_browser,
            "browser_status": self._browser_status,
            "start": self._start,
            "pause": self._pause,
            "resume": self._resume,
            "cancel": self._cancel,
        }
        if command.type == "shutdown":
            self.close()
            return False
        handler = handlers.get(command.type)
        if handler is None:
            raise CommandError(
                "UNKNOWN_COMMAND", f"不支持的命令：{command.type}"
            )
        handler(command.payload)
        return True

    def close(self) -> None:
        engine = self._engine
        if engine is not None:
            engine.cancel()
        thread = self._task_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=20)
        if self._edge is not None:
            stop_edge_process(self._edge)
            self._edge = None
        self._confirmed_process_id = None

    def _inspect_file(self, payload: dict[str, object]) -> None:
        path = _required_path(payload, "path", "inspect_file")
        inspection = inspect_workbook(path)
        jobs = load_jobs(path, inspection)
        self.channel.emit(
            {
                "type": "file_inspected",
                "path": str(path),
                "size": path.stat().st_size,
                "rows": [
                    {
                        "row": job.row,
                        "mark": job.mark,
                        "applicant": job.applicant,
                    }
                    for job in jobs
                ],
                **inspection.to_wire(),
            }
        )

    def _open_browser(self, payload: dict[str, object]) -> None:
        del payload
        self._confirmed_process_id = None
        profile = self.data_root / "edge-profile"
        if self._edge is not None:
            process_id = active_edge_process_id(self._edge)
            if process_id is not None:
                self._restore_edge_for_user(process_id)
                try:
                    state = self._browser_state_for(process_id)
                    self.channel.emit({"type": "browser_state", "state": state})
                    return
                except Exception:
                    pass
        self._edge = launch_login_edge(profile)
        process_id = active_edge_process_id(self._edge)
        if process_id is None:
            raise EdgeLaunchError("EDGE_WINDOW_UNAVAILABLE", "专用 Edge 窗口尚未就绪，请重新打开商标网站")
        self._restore_edge_for_user(process_id)
        self.channel.emit({"type": "browser_state", "state": "LOGIN_REQUIRED"})

    def _show_browser(self, payload: dict[str, object]) -> None:
        """Restore the dedicated Edge without disturbing an active query."""
        del payload
        try:
            process_id = self._require_process_id()
        except CommandError as exc:
            if exc.code != "BROWSER_NOT_OPEN" or (
                self._task_thread is not None and self._task_thread.is_alive()
            ):
                raise
            self._open_browser({})
            return
        self._restore_edge_for_user(process_id)
        self.channel.emit({"type": "browser_shown"})

    @staticmethod
    def _restore_edge_for_user(process_id: int) -> None:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                session = UIABrowserSession.attach(process_id)
                try:
                    session.restore_for_user()
                finally:
                    session.disconnect()
                return
            except Exception:
                time.sleep(0.2)
        raise EdgeLaunchError("EDGE_WINDOW_UNAVAILABLE", "未能显示专用 Edge，请重新点击打开商标网站")

    def _connect_browser(self, payload: dict[str, object]) -> None:
        del payload
        process_id = self._require_process_id()
        self._enter_search_after_confirmation(process_id)
        self._confirmed_process_id = process_id
        # require_search_ready() has already verified the real CNIPA search URL
        # and query form through CDP. A second UIA probe here can fail or stall
        # even though that verified page is usable, leaving the employee with a
        # button that appears to do nothing. Keep UIA preflight for task start,
        # but acknowledge this explicit login confirmation from the verified DOM.
        self.channel.emit(
            {"type": "browser_state", "state": BrowserState.READY.value}
        )

    def _enter_search_after_confirmation(self, process_id: int) -> None:
        # The login button is an explicit user confirmation. Do not read the
        # Edge address bar through UI Automation here: accessibility probing can
        # block for tens of seconds even while the CNIPA page is fully usable.
        del process_id
        endpoint = self._edge.cdp_endpoint if self._edge is not None else None
        if not endpoint:
            raise CommandError("EDGE_CAPTURE_UNAVAILABLE", "专用 Edge 缺少网页连接，无法进入商标综合查询")
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=5000)
            pages = [
                page for page in browser.contexts[0].pages
                if page.url.startswith(CNIPA_ORIGIN)
                or page.url.startswith("https://sso.cnipa.gov.cn/")
                or page.url == "about:blank"
            ]
            if not pages:
                raise CommandError("CNIPA_TAB_MISSING", "专用 Edge 中没有商标网站或登录页面")
            visible = []
            for page in pages:
                try:
                    if page.evaluate("document.visibilityState === 'visible'"):
                        visible.append(page)
                except Exception:
                    continue
            # Chromium reports only the active tab as visible. If a test or
            # an unusual target cannot expose visibility, preserve the first
            # CNIPA candidate instead of guessing from stale background tabs.
            selected = (visible or pages)[0]
            port = BackgroundPagePort(selected, None)
            try:
                port.require_search_ready()
            finally:
                port.close()
            # Leaving sync_playwright disconnects the driver. Calling
            # browser.close() here would close the employee's existing Edge.

    def _browser_status(self, payload: dict[str, object]) -> None:
        del payload
        state = self._browser_state_for(self._require_process_id())
        self.channel.emit({"type": "browser_state", "state": state})

    def _start(self, payload: dict[str, object]) -> None:
        path = _required_path(payload, "path", "start")
        run_id = _optional_text(payload, "run_id") or uuid4().hex
        overwrite = bool(payload.get("overwrite", False))
        self._begin_task(
            lambda engine: engine.run(
                TaskRequest(run_id, path, overwrite), self.channel.emit
            ),
            run_id,
        )

    def _resume(self, payload: dict[str, object]) -> None:
        run_id = _required_text(payload, "run_id", "resume")
        self._begin_task(
            lambda engine: engine.resume(run_id, self.channel.emit), run_id
        )

    def _cancel(self, payload: dict[str, object]) -> None:
        engine = self._engine
        if engine is not None:
            engine.cancel()
            self.channel.emit({"type": "task_state", "state": "cancelling"})
            return

        # A paused engine thread has already exited, but its checkpoint and
        # partial workbook are deliberately kept.  Cancelling that task must
        # therefore finish the checkpoint directly instead of waiting for an
        # engine that no longer exists.
        run_id = _required_text(payload, "run_id", "cancel")
        store = CheckpointStore(
            build_run_paths(run_id, self.data_root).checkpoint_path
        )
        checkpoint = store.load()
        if checkpoint is None or checkpoint.state not in {
            "paused_user_action",
            "cancelled",
        }:
            raise CommandError("TASK_NOT_RUNNING", "当前没有可停止的任务")
        output = Path(checkpoint.working_path)
        if not checkpoint.working_path or not output.is_file():
            raise CommandError("WORKING_COPY_MISSING", "任务结果工作副本不存在")
        if checkpoint.state != "cancelled":
            checkpoint = checkpoint.model_copy(update={"state": "cancelled"})
            store.save(checkpoint)
        self.channel.emit(
            {
                "type": "task_state",
                "state": "cancelled",
                "run_id": checkpoint.run_id,
                "current": len(checkpoint.completed_rows),
                "output_path": checkpoint.working_path,
            }
        )

    def _pause(self, payload: dict[str, object]) -> None:
        del payload
        engine = self._engine
        if engine is None:
            raise CommandError("TASK_NOT_RUNNING", "当前没有正在执行的任务")
        engine.pause()
        self.channel.emit({"type": "task_state", "state": "pausing"})

    def _begin_task(
        self, operation: Callable[[TaskEngine], object], run_id: str
    ) -> None:
        process_id = self._require_process_id()
        endpoint = self._edge.cdp_endpoint if self._edge is not None else None
        if not endpoint:
            raise CommandError(
                "EDGE_CAPTURE_UNAVAILABLE",
                "专用 Edge 缺少后台截图通道，请关闭后重新打开商标网站",
            )
        browser_state = self._browser_state_for(process_id)
        if browser_state != BrowserState.READY.value:
            self.channel.emit({"type": "browser_state", "state": browser_state})
            raise CommandError(
                "BROWSER_NOT_READY",
                "查询开始前未确认稳定的登录状态，请完成登录后重新检查",
            )
        with self._lock:
            if self._task_thread is not None and self._task_thread.is_alive():
                raise CommandError("TASK_ALREADY_RUNNING", "已有商标任务正在执行")

            def target() -> None:
                session: UIABrowserSession | None = None
                try:
                    session = UIABrowserSession.attach(process_id)
                    session.hide_for_background()
                    session.disconnect()
                    session = None
                    with sync_playwright() as playwright:
                        browser = playwright.chromium.connect_over_cdp(endpoint, timeout=5000)
                        port = BackgroundPagePort.from_context(
                            browser.contexts[0],
                            build_vision_recognizer(self.data_root),
                            build_local_ocr_recognizer(),
                        )
                        try:
                            engine = TaskEngine(CnipaClient(port), self.data_root)
                            self._engine = engine
                            operation(engine)
                        finally:
                            port.close()
                        # The Playwright context manager disconnects from CDP;
                        # never close the browser that the employee is using.
                except Exception as exc:
                    self.emit_error(exc)
                finally:
                    self._engine = None
                    if session is not None:
                        session.disconnect()

            self._task_thread = Thread(
                target=target,
                name=f"trademark-task-{run_id}",
                daemon=True,
            )
            self._task_thread.start()

    def _require_process_id(self) -> int:
        if self._edge is None:
            raise CommandError("BROWSER_NOT_OPEN", "请先点击“打开商标网站”")
        process_id = active_edge_process_id(self._edge)
        if process_id is None:
            raise CommandError("BROWSER_NOT_OPEN", "请先点击“打开商标网站”")
        return process_id

    def _browser_state_for(self, process_id: int) -> str:
        session = UIABrowserSession.attach(process_id)
        try:
            def read_state() -> BrowserState:
                state = session.state()
                if (
                    state is BrowserState.LOGIN_REQUIRED
                    and self._confirmed_process_id == process_id
                    and session.url().split("#", 1)[0].rstrip("/") == f"{CNIPA_ORIGIN}/sgtmi"
                ):
                    # The employee explicitly confirmed login, and the site's
                    # own navigation reached the search page. Its username
                    # header is not reliable even when queries work.
                    return BrowserState.READY
                if state in {BrowserState.RESULTS, BrowserState.DETAIL} and not (
                    self._task_thread is not None and self._task_thread.is_alive()
                ):
                    self._return_to_home()
                    state = session.state()
                if state is BrowserState.RETURNED_HOME:
                    state = session.enter_comprehensive_query()
                    if (
                        state is BrowserState.LOGIN_REQUIRED
                        and session.url().startswith(f"{CNIPA_ORIGIN}/sgtmi")
                    ):
                        # CNIPA paints the search form before its account name.
                        # Do not mistake that short rendering interval for an
                        # expired login after a user-requested home navigation.
                        for _ in range(20):
                            time.sleep(READY_STABILITY_INTERVAL_SECONDS)
                            state = session.state()
                            if state is not BrowserState.LOGIN_REQUIRED:
                                break
                return state

            state = read_state()
            if state is BrowserState.RETURNED_HOME:
                # SSO may land on the home page once more after the first
                # navigation. Re-enter through CNIPA's own link one time.
                state = read_state()
            if (
                state is BrowserState.WAITING_SITE
                and self._confirmed_process_id == process_id
            ):
                # Current Edge builds can expose the rendered document while
                # hiding the address bar and tab strip from Windows UIA.  In
                # that case UIA cannot classify the page even though the CDP
                # page channel remains healthy. Revalidate the already
                # confirmed tab through that channel before rejecting start.
                try:
                    self._enter_search_after_confirmation(process_id)
                    state = BrowserState.READY
                except NeedsUserAction as exc:
                    state = exc.state
            if state is BrowserState.READY and self._confirmed_process_id != process_id:
                # CNIPA can briefly render the search form before its asynchronous
                # session check redirects an expired user to SSO. Require ten
                # continuous seconds of READY before reporting a usable login.
                for _ in range(READY_STABILITY_POLLS):
                    time.sleep(READY_STABILITY_INTERVAL_SECONDS)
                    state = read_state()
                    if state is not BrowserState.READY:
                        break
            return state.value
        finally:
            session.disconnect()

    def _return_to_home(self) -> None:
        endpoint = self._edge.cdp_endpoint if self._edge is not None else None
        if not endpoint:
            raise CommandError(
                "EDGE_CAPTURE_UNAVAILABLE",
                "专用 Edge 缺少页面连接，无法回到商标网首页",
            )
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint, timeout=5000)
            pages = [
                page for page in browser.contexts[0].pages
                if page.url.startswith(CNIPA_ORIGIN)
            ]
            if not pages:
                raise CommandError("CNIPA_TAB_MISSING", "专用 Edge 中没有商标网站标签页")
            pages[-1].goto(
                f"{CNIPA_ORIGIN}/", wait_until="domcontentloaded", timeout=25000
            )

    def emit_error(self, exc: Exception) -> None:
        if isinstance(exc, CommandError):
            code = exc.code
            message = str(exc)
        elif isinstance(exc, WorkbookInputError):
            code = exc.code
            message = str(exc)
        elif isinstance(exc, EdgeLaunchError):
            code = exc.code
            message = str(exc)
        elif isinstance(exc, ResumeError):
            code = exc.code
            message = str(exc)
        elif isinstance(exc, WorkbookOutputError):
            code = "WORKBOOK_OUTPUT_ERROR"
            message = str(exc)
        else:
            code = "WORKER_INTERNAL_ERROR"
            message = "Worker 内部错误，请查看本地日志"
            self.error_stream.write(f"worker_internal_error: {type(exc).__name__}\n")
            self.error_stream.flush()
        self.channel.emit({"type": "error", "code": code, "message": message})


def _required_path(
    payload: dict[str, object], key: str, command: str
) -> Path:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CommandError(
            "INVALID_COMMAND", f"{command} 命令缺少 {key}"
        )
    return Path(value)


def _required_text(
    payload: dict[str, object], key: str, command: str
) -> str:
    value = _optional_text(payload, key)
    if value is None:
        raise CommandError(
            "INVALID_COMMAND", f"{command} 命令缺少 {key}"
        )
    return value


def _optional_text(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None
