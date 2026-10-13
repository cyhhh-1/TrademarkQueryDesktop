from __future__ import annotations

from pathlib import Path
from urllib.error import URLError

import pytest

from trademark_worker.browser.edge import (
    EdgeProcess,
    EdgeLaunchError,
    active_edge_process_id,
    build_login_edge_command,
    resolve_edge_executable,
    stop_edge_process,
    launch_login_edge,
)
import trademark_worker.browser.edge as edge_module


def test_login_edge_is_a_normal_chinese_browser_with_local_background_capture(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "profile"

    command = build_login_edge_command(Path("msedge.exe"), profile, 43127)

    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=43127" in command
    assert "--remote-allow-origins=http://127.0.0.1:43127" in command
    assert not any("enable-automation" in argument for argument in command)
    assert "--disable-backgrounding-occluded-windows" in command
    assert "--disable-renderer-backgrounding" in command
    assert "--disable-background-timer-throttling" in command
    assert "--lang=zh-CN" in command
    assert "--accept-lang=zh-CN,zh,en" in command
    assert "--force-renderer-accessibility" in command
    assert "--start-maximized" in command
    assert command[-1] == "https://wcjs.sbj.cnipa.gov.cn/"

def test_resolve_edge_uses_existing_candidate(tmp_path: Path) -> None:
    edge = tmp_path / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")

    assert resolve_edge_executable([edge]) == edge


def test_resolve_edge_reports_missing_browser(tmp_path: Path) -> None:
    with pytest.raises(EdgeLaunchError) as captured:
        resolve_edge_executable([tmp_path / "missing.exe"])

    assert captured.value.code == "EDGE_NOT_INSTALLED"


def test_relaunched_edge_is_found_by_profile_after_bootstrap_exits(
    tmp_path: Path,
) -> None:
    profile = tmp_path / "edge-profile"

    class ExitedBootstrap:
        pid = 111

        @staticmethod
        def poll() -> int:
            return 0

    edge = EdgeProcess(ExitedBootstrap(), profile)  # type: ignore[arg-type]
    candidates = [
        (
            222,
            f'"msedge.exe" --user-data-dir="{profile}" --new-window',
        ),
        (
            223,
            f'"msedge.exe" --type=renderer --user-data-dir="{profile}"',
        ),
    ]

    assert active_edge_process_id(edge, candidates) == 222


def test_stopping_relaunched_edge_closes_the_live_root_window(
    monkeypatch, tmp_path: Path
) -> None:
    profile = tmp_path / "edge-profile"
    closed: list[int] = []

    class ExitedBootstrap:
        pid = 111

        @staticmethod
        def poll() -> int:
            return 0

    edge = EdgeProcess(ExitedBootstrap(), profile)  # type: ignore[arg-type]
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
        edge_module,
        "_request_graceful_close",
        lambda process_id: closed.append(process_id) or True,
    )

    stop_edge_process(edge)

    assert closed == [222]


def test_launch_waits_for_capture_endpoint_after_edge_bootstrap_exits(
    monkeypatch, tmp_path: Path
) -> None:
    profile = tmp_path / "edge-profile"

    class ExitedBootstrap:
        pid = 111

        @staticmethod
        def poll() -> int:
            return 0

        @staticmethod
        def terminate() -> None:
            pass

    attempts = 0

    def fake_urlopen(_url: str, timeout: float):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise URLError("Edge is relaunching")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        return Response()

    monkeypatch.setattr(edge_module, "_edge_process_candidates", lambda: [])
    monkeypatch.setattr(edge_module, "_available_loopback_port", lambda: 43127)
    monkeypatch.setattr(edge_module, "resolve_edge_executable", lambda: Path("msedge.exe"))
    monkeypatch.setattr(edge_module.subprocess, "Popen", lambda *_args, **_kwargs: ExitedBootstrap())
    monkeypatch.setattr(edge_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(edge_module.time, "sleep", lambda _seconds: None)

    edge = launch_login_edge(profile)

    assert edge.cdp_endpoint == "http://127.0.0.1:43127"
    assert attempts == 2


def test_launch_reuses_existing_debuggable_edge_with_same_profile(
    monkeypatch, tmp_path: Path
) -> None:
    profile = tmp_path / "edge-profile"
    monkeypatch.setattr(
        edge_module,
        "_edge_process_candidates",
        lambda: [
            (222, f'"msedge.exe" --user-data-dir="{profile}" --remote-debugging-address=127.0.0.1 --remote-debugging-port=62438'),
        ],
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(edge_module, "urlopen", lambda _url, timeout: Response())
    monkeypatch.setattr(
        edge_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("must attach instead of opening another Edge"),
    )

    edge = launch_login_edge(profile)

    assert edge.cdp_endpoint == "http://127.0.0.1:62438"
    assert active_edge_process_id(edge) == 222


def test_stopping_attached_edge_closes_only_its_dedicated_root(
    monkeypatch, tmp_path: Path
) -> None:
    profile = tmp_path / "edge-profile"
    closed: list[int] = []
    monkeypatch.setattr(
        edge_module,
        "_edge_process_candidates",
        lambda: [
            (222, f'"msedge.exe" --user-data-dir="{profile}" --remote-debugging-port=62438'),
            (333, '"msedge.exe" --user-data-dir="C:\\Other\\Edge"'),
        ],
    )
    monkeypatch.setattr(
        edge_module,
        "_request_graceful_close",
        lambda process_id: closed.append(process_id) or True,
    )

    stop_edge_process(EdgeProcess(None, profile, "http://127.0.0.1:62438"))

    assert closed == [222]
