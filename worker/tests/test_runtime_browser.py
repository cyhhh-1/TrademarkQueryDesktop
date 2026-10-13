from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import pytest

from trademark_worker.models import BrowserState
from trademark_worker.browser.state import PageSnapshot
from trademark_worker.checkpoint import Checkpoint, CheckpointStore
from trademark_worker.paths import build_run_paths
from trademark_worker.protocol import WorkerCommand
import trademark_worker.browser.edge as edge_module
import trademark_worker.runtime as runtime_module
from trademark_worker.browser.edge import EdgeProcess
from trademark_worker.runtime import CommandError, WorkerRuntime


class CapturingChannel:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(self, event: dict[str, object]) -> None:
        self.events.append(event)


class RunningProcess:
    pid = 4242

    def poll(self) -> None:
        return None


def test_cancel_finishes_a_paused_task_and_keeps_its_partial_output(
    tmp_path: Path,
) -> None:
    channel = CapturingChannel()
    run_id = "paused-run"
    paths = build_run_paths(run_id, tmp_path).create()
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    output = paths.output_dir / "partial.xlsx"
    output.write_bytes(b"PK partial result")
    CheckpointStore(paths.checkpoint_path).save(
        Checkpoint(
            run_id=run_id,
            input_sha256="abc",
            working_path=str(output),
            completed_rows=(2, 3),
            paused_row=4,
            state="paused_user_action",
        )
    )
    worker = WorkerRuntime(channel, data_root=tmp_path)

    worker.handle(WorkerCommand("cancel", {"run_id": run_id}))

    saved = CheckpointStore(paths.checkpoint_path).load()
    assert saved is not None
    assert saved.state == "cancelled"
    assert saved.completed_rows == (2, 3)
    assert channel.events[-1] == {
        "type": "task_state",
        "state": "cancelled",
        "run_id": run_id,
        "current": 2,
        "output_path": str(output),
    }


def test_open_browser_starts_normal_accessible_edge(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    launched: list[Path] = []

    def fake_launch(profile: Path):
        launched.append(profile)
        return SimpleNamespace(
            process=RunningProcess(), profile_dir=profile, cdp_endpoint=None
        )

    monkeypatch.setattr(runtime_module, "launch_login_edge", fake_launch)
    monkeypatch.setattr(worker := WorkerRuntime(channel, data_root=tmp_path), "_restore_edge_for_user", lambda _pid: None)

    worker.handle(WorkerCommand("open_browser"))

    assert launched == [tmp_path / "edge-profile"]
    assert channel.events[-1] == {
        "type": "browser_state",
        "state": BrowserState.LOGIN_REQUIRED.value,
    }


def test_open_browser_restores_new_edge_even_if_profile_was_last_minimized(
    monkeypatch, tmp_path: Path
) -> None:
    restored: list[int] = []

    class FakeSession:
        def restore_for_user(self) -> None:
            restored.append(4242)

        def disconnect(self) -> None:
            pass

    monkeypatch.setattr(
        runtime_module,
        "launch_login_edge",
        lambda profile: SimpleNamespace(
            process=RunningProcess(), profile_dir=profile, cdp_endpoint=None
        ),
    )
    monkeypatch.setattr(runtime_module, "active_edge_process_id", lambda _edge: 4242)
    monkeypatch.setattr(
        runtime_module.UIABrowserSession,
        "attach",
        lambda _process_id: FakeSession(),
    )
    worker = WorkerRuntime(CapturingChannel(), data_root=tmp_path)

    worker.handle(WorkerCommand("open_browser"))

    assert restored == [4242]


def test_show_browser_restores_edge_without_resetting_active_query(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    worker = WorkerRuntime(channel, data_root=tmp_path)
    worker._edge = SimpleNamespace(
        process=RunningProcess(),
        profile_dir=tmp_path / "edge-profile",
        cdp_endpoint="http://127.0.0.1:43127",
    )
    worker._confirmed_process_id = 4242
    restored: list[int] = []
    monkeypatch.setattr(runtime_module, "active_edge_process_id", lambda _edge: 4242)
    monkeypatch.setattr(
        worker, "_restore_edge_for_user", lambda process_id: restored.append(process_id)
    )

    worker.handle(WorkerCommand("show_browser"))

    assert restored == [4242]
    assert worker._confirmed_process_id == 4242
    assert channel.events == [{"type": "browser_shown"}]


def test_show_browser_reopens_dedicated_edge_after_paused_browser_exits(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    worker = WorkerRuntime(channel, data_root=tmp_path)
    worker._edge = SimpleNamespace(
        process=RunningProcess(),
        profile_dir=tmp_path / "edge-profile",
        cdp_endpoint="http://127.0.0.1:43127",
    )
    monkeypatch.setattr(runtime_module, "active_edge_process_id", lambda _edge: None)
    launched: list[Path] = []

    def launch(profile: Path):
        launched.append(profile)
        return SimpleNamespace(process=RunningProcess(), profile_dir=profile)

    monkeypatch.setattr(runtime_module, "launch_login_edge", launch)
    monkeypatch.setattr(worker, "_restore_edge_for_user", lambda _pid: None)
    monkeypatch.setattr(runtime_module, "active_edge_process_id", lambda _edge: 4242 if launched else None)

    worker.handle(WorkerCommand("show_browser"))

    assert launched == [tmp_path / "edge-profile"]
    assert channel.events[-1] == {
        "type": "browser_state",
        "state": BrowserState.LOGIN_REQUIRED.value,
    }


def test_open_browser_does_not_report_ready_when_existing_window_cannot_be_restored(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    worker = WorkerRuntime(channel, data_root=tmp_path)
    worker._edge = SimpleNamespace(
        process=RunningProcess(),
        profile_dir=tmp_path / "edge-profile",
        cdp_endpoint="http://127.0.0.1:43127",
    )
    monkeypatch.setattr(runtime_module, "active_edge_process_id", lambda _edge: 4242)
    monkeypatch.setattr(
        worker,
        "_restore_edge_for_user",
        lambda _pid: (_ for _ in ()).throw(
            runtime_module.EdgeLaunchError("EDGE_WINDOW_UNAVAILABLE", "未能显示专用 Edge")
        ),
    )
    monkeypatch.setattr(worker, "_browser_state_for", lambda _pid: BrowserState.READY.value)

    with pytest.raises(runtime_module.EdgeLaunchError, match="未能显示专用 Edge"):
        worker.handle(WorkerCommand("open_browser"))

    assert channel.events == []


def test_connect_browser_attaches_without_restarting_edge(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    edge = SimpleNamespace(
        process=RunningProcess(),
        profile_dir=tmp_path / "edge-profile",
        cdp_endpoint="http://127.0.0.1:43127",
    )
    stopped: list[object] = []
    launched: list[Path] = []
    monkeypatch.setattr(
        runtime_module,
        "stop_edge_process",
        lambda process: stopped.append(process),
    )
    monkeypatch.setattr(
        runtime_module,
        "launch_login_edge",
        lambda profile: launched.append(profile),
    )
    worker = WorkerRuntime(channel, data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = edge
    monkeypatch.setattr(worker, "_enter_search_after_confirmation", lambda _process_id: None)
    worker.handle(WorkerCommand("connect_browser"))

    assert stopped == []
    assert launched == []
    assert channel.events[-1] == {
        "type": "browser_state",
        "state": BrowserState.READY.value,
    }


def test_connect_browser_trusts_the_verified_search_page_without_a_second_uia_probe(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    worker = WorkerRuntime(channel, data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = SimpleNamespace(
        process=RunningProcess(),
        profile_dir=tmp_path / "edge-profile",
        cdp_endpoint="http://127.0.0.1:43127",
    )
    monkeypatch.setattr(runtime_module, "active_edge_process_id", lambda _: 4242)
    monkeypatch.setattr(worker, "_enter_search_after_confirmation", lambda _: None)

    def unexpected_second_probe(_process_id: int) -> str:
        raise AssertionError("verified search page must not be discarded by a second UIA probe")

    monkeypatch.setattr(worker, "_browser_state_for", unexpected_second_probe)

    worker.handle(WorkerCommand("connect_browser"))

    assert worker._confirmed_process_id == 4242
    assert channel.events[-1] == {
        "type": "browser_state",
        "state": BrowserState.READY.value,
    }


def test_connect_browser_uses_cdp_without_blocking_on_uia_address_bar(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    worker = WorkerRuntime(channel, data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = SimpleNamespace(
        process=RunningProcess(),
        profile_dir=tmp_path / "edge-profile",
        cdp_endpoint="http://127.0.0.1:43127",
    )
    monkeypatch.setattr(runtime_module, "active_edge_process_id", lambda _: 4242)

    page = SimpleNamespace(url="https://wcjs.sbj.cnipa.gov.cn/home")
    browser = SimpleNamespace(
        contexts=[SimpleNamespace(pages=[page])],
        close=lambda: None,
    )

    class FakePlaywright:
        def __enter__(self):
            return SimpleNamespace(
                chromium=SimpleNamespace(connect_over_cdp=lambda _, **_kwargs: browser)
            )

        def __exit__(self, *_args):
            return None

    class Port:
        def __init__(self, selected, _recognizer):
            assert selected is page

        def require_search_ready(self) -> None:
            page.url = "https://wcjs.sbj.cnipa.gov.cn/sgtmi"

        def close(self) -> None:
            pass

    monkeypatch.setattr(runtime_module, "sync_playwright", FakePlaywright)
    monkeypatch.setattr(runtime_module, "BackgroundPagePort", Port)
    monkeypatch.setattr(
        runtime_module.UIABrowserSession,
        "attach",
        lambda _process_id: (_ for _ in ()).throw(
            AssertionError("login confirmation must not wait for UIA")
        ),
    )

    worker.handle(WorkerCommand("connect_browser"))

    assert page.url.endswith("/sgtmi")
    assert channel.events[-1] == {
        "type": "browser_state",
        "state": BrowserState.READY.value,
    }


def test_connect_browser_does_not_close_the_existing_dedicated_edge(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    worker = WorkerRuntime(channel, data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = SimpleNamespace(
        process=RunningProcess(),
        profile_dir=tmp_path / "edge-profile",
        cdp_endpoint="http://127.0.0.1:43127",
    )
    monkeypatch.setattr(runtime_module, "active_edge_process_id", lambda _: 4242)

    page = SimpleNamespace(
        url="https://wcjs.sbj.cnipa.gov.cn/sgtmi",
        evaluate=lambda _script: True,
    )

    class RemoteBrowser:
        contexts = [SimpleNamespace(pages=[page])]

        def close(self) -> None:
            raise AssertionError("disconnecting CDP must not close the employee's Edge")

    class FakePlaywright:
        def __enter__(self):
            return SimpleNamespace(
                chromium=SimpleNamespace(connect_over_cdp=lambda _, **_kwargs: RemoteBrowser())
            )

        def __exit__(self, *_args):
            return None

    class Port:
        def __init__(self, selected, _recognizer):
            assert selected is page

        def require_search_ready(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(runtime_module, "sync_playwright", FakePlaywright)
    monkeypatch.setattr(runtime_module, "BackgroundPagePort", Port)

    worker.handle(WorkerCommand("connect_browser"))

    assert channel.events[-1] == {
        "type": "browser_state",
        "state": BrowserState.READY.value,
    }


def test_connect_browser_uses_relaunched_edge_root_process(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    profile = tmp_path / "edge-profile"
    class ExitedBootstrap:
        pid = 111

        @staticmethod
        def poll() -> int:
            return 0

    worker = WorkerRuntime(channel, data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = EdgeProcess(ExitedBootstrap(), profile, "http://127.0.0.1:43127")  # type: ignore[arg-type]
    monkeypatch.setattr(worker, "_enter_search_after_confirmation", lambda _process_id: None)
    monkeypatch.setattr(
        edge_module,
        "_edge_process_candidates",
        lambda: [
            (
                222,
                f'"msedge.exe" --user-data-dir="{profile}" --new-window',
            )
        ],
    )
    worker.handle(WorkerCommand("connect_browser"))

    assert worker._confirmed_process_id == 222
    assert channel.events[-1] == {
        "type": "browser_state",
        "state": BrowserState.READY.value,
    }


@pytest.mark.parametrize(
    ("starting_url", "stale_tab_last"),
    [
        ("https://sso.cnipa.gov.cn/am/#/user-info-center", False),
        ("about:blank", False),
        ("https://sso.cnipa.gov.cn/am/#/user-info-center", True),
    ],
)
def test_user_confirmed_login_enters_search_from_non_search_page_without_header(
    monkeypatch, tmp_path: Path, starting_url: str, stale_tab_last: bool
) -> None:
    class SearchLink:
        @property
        def first(self):
            return self

        def count(self) -> int:
            return 1

        def evaluate(self, _script: str) -> None:
            page.url = "https://wcjs.sbj.cnipa.gov.cn/sgtmi"

    class SearchButton:
        def count(self) -> int:
            return int(page.url.endswith("/sgtmi"))

    class Body:
        def count(self) -> int:
            return 1

        def inner_text(self, **_kwargs) -> str:
            return "商标综合查询"

    class Page:
        url = "https://sso.cnipa.gov.cn/am/#/user-info-center"
        visited: list[str] = []

        def goto(self, url: str, **_kwargs) -> None:
            self.visited.append(url)
            self.url = "https://wcjs.sbj.cnipa.gov.cn/home"

        def get_by_text(self, name: str, *, exact: bool):
            assert (name, exact) == ("商标综合查询", True)
            return SearchLink()

        def locator(self, selector: str):
            if selector == 'input[type="button"][value="查询"]':
                return SearchButton()
            if selector == "body":
                return Body()
            raise AssertionError(selector)

    page = Page()
    page.url = starting_url

    class StalePage:
        url = "https://wcjs.sbj.cnipa.gov.cn/sgtmi"

        def locator(self, _selector: str):
            return SimpleNamespace(count=lambda: 1)

    browser = SimpleNamespace(
        contexts=[SimpleNamespace(pages=[page, StalePage()] if stale_tab_last else [page])],
        close=lambda: None,
    )

    class FakePlaywright:
        def __enter__(self):
            return SimpleNamespace(
                chromium=SimpleNamespace(connect_over_cdp=lambda _, **_kwargs: browser)
            )

        def __exit__(self, *_args):
            return None

    class Session:
        def state(self) -> BrowserState:
            return BrowserState.LOGIN_REQUIRED

        def url(self) -> str:
            return page.url

        def snapshot(self) -> PageSnapshot:
            return PageSnapshot(
                page.url, None, "商标综合查询", page.url.endswith("/sgtmi"), False
            )

        def disconnect(self) -> None:
            pass

    channel = CapturingChannel()
    worker = WorkerRuntime(channel, data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = SimpleNamespace(
        process=RunningProcess(),
        profile_dir=tmp_path / "edge-profile",
        cdp_endpoint="http://127.0.0.1:43127",
    )
    monkeypatch.setattr(runtime_module, "active_edge_process_id", lambda _: 4242)
    monkeypatch.setattr(runtime_module, "sync_playwright", FakePlaywright)
    monkeypatch.setattr(runtime_module.UIABrowserSession, "attach", lambda _: Session())

    worker.handle(WorkerCommand("connect_browser"))

    assert page.visited == ["https://wcjs.sbj.cnipa.gov.cn/"]
    assert page.url == "https://wcjs.sbj.cnipa.gov.cn/sgtmi"
    assert channel.events[-1] == {
        "type": "browser_state",
        "state": BrowserState.READY.value,
    }


def test_open_browser_reuses_relaunched_edge_instead_of_opening_another_window(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    profile = tmp_path / "edge-profile"
    attached: list[int] = []

    class ExitedBootstrap:
        pid = 111

        @staticmethod
        def poll() -> int:
            return 0

    worker = WorkerRuntime(channel, data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = EdgeProcess(ExitedBootstrap(), profile)  # type: ignore[arg-type]
    monkeypatch.setattr(
        edge_module,
        "_edge_process_candidates",
        lambda: [
            (
                222,
                f'"msedge.exe" --user-data-dir="{profile}" --new-window',
            )
        ],
    )
    monkeypatch.setattr(
        worker,
        "_browser_state_for",
        lambda process_id: attached.append(process_id) or BrowserState.READY.value,
    )
    monkeypatch.setattr(worker, "_restore_edge_for_user", lambda _pid: None)

    def unexpected_launch(_profile: Path):
        raise AssertionError("an active dedicated Edge must be reused")

    monkeypatch.setattr(runtime_module, "launch_login_edge", unexpected_launch)

    worker.handle(WorkerCommand("open_browser"))

    assert attached == [222]
    assert channel.events[-1] == {
        "type": "browser_state",
        "state": BrowserState.READY.value,
    }


def test_browser_state_enters_comprehensive_query_from_home(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[object] = []

    class FakeSession:
        first_state = True

        def state(self) -> BrowserState:
            if self.first_state:
                self.first_state = False
                return BrowserState.RETURNED_HOME
            return BrowserState.READY

        def enter_comprehensive_query(self) -> BrowserState:
            calls.append("clicked")
            return BrowserState.READY

        def disconnect(self) -> None:
            calls.append("disconnected")

    monkeypatch.setattr(
        runtime_module.UIABrowserSession,
        "attach",
        lambda process_id: calls.append(process_id) or FakeSession(),
    )
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _: None)
    worker = WorkerRuntime(CapturingChannel(), data_root=tmp_path)  # type: ignore[arg-type]

    state = worker._browser_state_for(4242)

    assert state == BrowserState.READY.value
    assert calls == [4242, "clicked", "disconnected"]


def test_browser_preflight_waits_for_identity_after_search_form_appears(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeSession:
        entered = False

        def state(self) -> BrowserState:
            return BrowserState.READY if self.entered else BrowserState.RETURNED_HOME

        def enter_comprehensive_query(self) -> BrowserState:
            self.entered = True
            return BrowserState.LOGIN_REQUIRED

        def url(self) -> str:
            return "https://wcjs.sbj.cnipa.gov.cn/sgtmi"

        def disconnect(self) -> None:
            pass

    monkeypatch.setattr(runtime_module.UIABrowserSession, "attach", lambda _: FakeSession())
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _: None)
    worker = WorkerRuntime(CapturingChannel(), data_root=tmp_path)  # type: ignore[arg-type]

    assert worker._browser_state_for(4242) == BrowserState.READY.value


def test_browser_preflight_uses_confirmed_cdp_page_when_edge_hides_uia_chrome(
    monkeypatch, tmp_path: Path
) -> None:
    """Newer Edge builds can hide the address bar and tabs from UI Automation."""

    class ChromeHiddenSession:
        def state(self) -> BrowserState:
            return BrowserState.WAITING_SITE

        def disconnect(self) -> None:
            pass

    worker = WorkerRuntime(CapturingChannel(), data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = EdgeProcess(
        RunningProcess(),
        tmp_path / "edge-profile",
        "http://127.0.0.1:43127",
    )
    worker._confirmed_process_id = 4242
    cdp_checks: list[int] = []
    monkeypatch.setattr(
        runtime_module.UIABrowserSession,
        "attach",
        lambda _process_id: ChromeHiddenSession(),
    )
    monkeypatch.setattr(
        worker,
        "_enter_search_after_confirmation",
        lambda process_id: cdp_checks.append(process_id),
    )

    assert worker._browser_state_for(4242) == BrowserState.READY.value
    assert cdp_checks == [4242]


def test_browser_preflight_reenters_home_once_after_sso_return(
    monkeypatch, tmp_path: Path
) -> None:
    class FakeSession:
        entries = 0

        def state(self) -> BrowserState:
            if self.entries <= 1:
                return BrowserState.RETURNED_HOME
            return BrowserState.READY

        def enter_comprehensive_query(self) -> BrowserState:
            self.entries += 1
            return BrowserState.LOGIN_REQUIRED

        def url(self) -> str:
            return "https://wcjs.sbj.cnipa.gov.cn/sgtmi"

        def disconnect(self) -> None:
            pass

    session = FakeSession()
    monkeypatch.setattr(runtime_module.UIABrowserSession, "attach", lambda _: session)
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _: None)
    worker = WorkerRuntime(CapturingChannel(), data_root=tmp_path)  # type: ignore[arg-type]

    assert worker._browser_state_for(4242) == BrowserState.READY.value
    assert session.entries == 2


def test_browser_preflight_returns_from_previous_detail_before_next_workbook(
    monkeypatch, tmp_path: Path
) -> None:
    current = [BrowserState.DETAIL]

    class FakeSession:
        def state(self) -> BrowserState:
            return current[0]

        def enter_comprehensive_query(self) -> BrowserState:
            current[0] = BrowserState.READY
            return current[0]

        def disconnect(self) -> None:
            pass

    worker = WorkerRuntime(CapturingChannel(), data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = SimpleNamespace(cdp_endpoint="http://127.0.0.1:43127")
    monkeypatch.setattr(runtime_module.UIABrowserSession, "attach", lambda _: FakeSession())
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        worker, "_return_to_home", lambda: current.__setitem__(0, BrowserState.RETURNED_HOME),
        raising=False,
    )

    assert worker._browser_state_for(4242) == BrowserState.READY.value


def test_return_to_home_navigates_existing_cnipa_tab_without_new_browser(
    monkeypatch, tmp_path: Path
) -> None:
    page = SimpleNamespace(url="https://wcjs.sbj.cnipa.gov.cn/detail")
    page.goto = lambda url, **_: setattr(page, "url", url)
    browser = SimpleNamespace(
        contexts=[SimpleNamespace(pages=[page])], close=lambda: None
    )

    class FakePlaywright:
        def __enter__(self):
            return SimpleNamespace(
                chromium=SimpleNamespace(connect_over_cdp=lambda _, **_kwargs: browser)
            )

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(runtime_module, "sync_playwright", FakePlaywright)
    worker = WorkerRuntime(CapturingChannel(), data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = SimpleNamespace(cdp_endpoint="http://127.0.0.1:43127")

    worker._return_to_home()

    assert page.url == "https://wcjs.sbj.cnipa.gov.cn/"


def test_browser_ready_must_remain_stable_during_preflight(
    monkeypatch, tmp_path: Path
) -> None:
    states = iter(
        [
            BrowserState.READY,
            BrowserState.READY,
            BrowserState.LOGIN_REQUIRED,
        ]
    )
    sleeps: list[float] = []

    class FakeSession:
        def state(self) -> BrowserState:
            return next(states)

        def disconnect(self) -> None:
            pass

    monkeypatch.setattr(runtime_module.UIABrowserSession, "attach", lambda _: FakeSession())
    monkeypatch.setattr(runtime_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    worker = WorkerRuntime(CapturingChannel(), data_root=tmp_path)  # type: ignore[arg-type]

    state = worker._browser_state_for(4242)

    assert state == BrowserState.LOGIN_REQUIRED.value
    assert sleeps == [0.5, 0.5]


def test_browser_ready_rejects_a_delayed_sso_redirect_after_three_seconds(
    monkeypatch, tmp_path: Path
) -> None:
    states = iter([BrowserState.READY] * 15 + [BrowserState.LOGIN_REQUIRED])
    sleeps: list[float] = []

    class FakeSession:
        def state(self) -> BrowserState:
            return next(states)

        def disconnect(self) -> None:
            pass

    monkeypatch.setattr(runtime_module.UIABrowserSession, "attach", lambda _: FakeSession())
    monkeypatch.setattr(runtime_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    worker = WorkerRuntime(CapturingChannel(), data_root=tmp_path)  # type: ignore[arg-type]

    state = worker._browser_state_for(4242)

    assert state == BrowserState.LOGIN_REQUIRED.value
    assert sleeps == [0.5] * 15


def test_close_stops_only_the_edge_started_by_this_worker(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    edge = SimpleNamespace(
        process=RunningProcess(),
        profile_dir=tmp_path / "edge-profile",
        cdp_endpoint=None,
    )
    stopped: list[object] = []
    monkeypatch.setattr(
        runtime_module,
        "stop_edge_process",
        lambda process: stopped.append(process),
    )
    worker = WorkerRuntime(channel, data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = edge

    worker.close()

    assert stopped == [edge]
    assert worker._edge is None


def test_pause_command_only_requests_cooperative_user_pause(tmp_path: Path) -> None:
    channel = CapturingChannel()
    calls: list[str] = []
    worker = WorkerRuntime(channel, data_root=tmp_path)  # type: ignore[arg-type]
    worker._engine = SimpleNamespace(pause=lambda: calls.append("pause"))

    worker.handle(WorkerCommand("pause"))

    assert calls == ["pause"]
    assert channel.events[-1] == {
        "type": "task_state",
        "state": "pausing",
    }


def test_task_moves_dedicated_edge_offscreen_before_background_work(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    actions: list[str] = []

    class FakeSession:
        def hide_for_background(self) -> None:
            actions.append("offscreen")

        def disconnect(self) -> None:
            actions.append("disconnected")

    class FakeEngine:
        def __init__(self, _client, _root: Path) -> None:
            pass

    class FakePort:
        def close(self) -> None:
            actions.append("port_closed")

    class FakeBrowser:
        contexts = [SimpleNamespace()]

        def close(self) -> None:
            raise AssertionError("background task must not close the dedicated Edge")

    class FakePlaywright:
        def __enter__(self):
            return SimpleNamespace(
                chromium=SimpleNamespace(connect_over_cdp=lambda _, **_kwargs: FakeBrowser())
            )

        def __exit__(self, *_):
            return None

    edge = SimpleNamespace(
        process=RunningProcess(),
        profile_dir=tmp_path / "edge-profile",
        cdp_endpoint="http://127.0.0.1:43127",
    )
    worker = WorkerRuntime(channel, data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = edge
    monkeypatch.setattr(runtime_module, "active_edge_process_id", lambda _: 4242)
    monkeypatch.setattr(worker, "_browser_state_for", lambda _: BrowserState.READY.value)
    monkeypatch.setattr(runtime_module.UIABrowserSession, "attach", lambda _: FakeSession())
    monkeypatch.setattr(runtime_module, "TaskEngine", FakeEngine)
    monkeypatch.setattr(runtime_module, "CnipaClient", lambda page: page)
    monkeypatch.setattr(runtime_module, "build_vision_recognizer", lambda _: None)
    monkeypatch.setattr(runtime_module, "build_local_ocr_recognizer", lambda: None)
    monkeypatch.setattr(runtime_module, "sync_playwright", FakePlaywright)
    monkeypatch.setattr(
        runtime_module.BackgroundPagePort,
        "from_context",
        lambda _context, _recognizer, _local_recognizer: FakePort(),
    )

    worker._begin_task(lambda _engine: actions.append("executed"), "background")
    worker._task_thread.join(timeout=2)  # type: ignore[union-attr]

    assert actions == ["offscreen", "disconnected", "executed", "port_closed"]


def test_task_refuses_to_start_when_login_is_not_stably_ready(
    monkeypatch, tmp_path: Path
) -> None:
    channel = CapturingChannel()
    edge = SimpleNamespace(
        process=RunningProcess(),
        profile_dir=tmp_path / "edge-profile",
        cdp_endpoint="http://127.0.0.1:43127",
    )
    worker = WorkerRuntime(channel, data_root=tmp_path)  # type: ignore[arg-type]
    worker._edge = edge
    monkeypatch.setattr(runtime_module, "active_edge_process_id", lambda _: 4242)
    monkeypatch.setattr(
        worker,
        "_browser_state_for",
        lambda _: BrowserState.LOGIN_REQUIRED.value,
    )

    with pytest.raises(CommandError, match="查询开始前"):
        worker._begin_task(lambda _engine: None, "not-ready")

    assert worker._task_thread is None
    assert channel.events[-1] == {
        "type": "browser_state",
        "state": BrowserState.LOGIN_REQUIRED.value,
    }
