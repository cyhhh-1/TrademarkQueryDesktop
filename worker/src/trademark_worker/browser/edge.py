"""Launch the visible, dedicated Microsoft Edge used by the local client."""

from __future__ import annotations

import ctypes
import os
import re
import socket
import subprocess
import time
from urllib.request import urlopen
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence

from trademark_worker.paths import app_data_root


CNIPA_HOME = "https://wcjs.sbj.cnipa.gov.cn/"
WM_CLOSE = 0x0010


class EdgeLaunchError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class EdgeProcess:
    process: subprocess.Popen[bytes] | None
    profile_dir: Path
    cdp_endpoint: str | None = None


def _edge_process_candidates() -> list[tuple[int, str]]:
    if os.name != "nt":
        return []
    try:
        import win32com.client

        service = win32com.client.GetObject("winmgmts:")
        processes = service.ExecQuery(
            "SELECT ProcessId, CommandLine FROM Win32_Process "
            "WHERE Name='msedge.exe'"
        )
        return [
            (int(process.ProcessId), str(process.CommandLine or ""))
            for process in processes
        ]
    except Exception:
        return []


def _matching_edge_roots(
    profile_dir: Path, candidates: Sequence[tuple[int, str]] | None = None
) -> list[tuple[int, str]]:
    profile = str(profile_dir.resolve()).replace("/", "\\").casefold()
    marker = f"--user-data-dir={profile}"
    matches: list[tuple[int, str]] = []
    for process_id, command_line in (
        _edge_process_candidates() if candidates is None else candidates
    ):
        normalized = str(command_line).replace('"', "").replace("/", "\\").casefold()
        marker_at = normalized.find(marker)
        marker_end = marker_at + len(marker)
        if (
            marker_at >= 0
            and (marker_end == len(normalized) or normalized[marker_end].isspace())
            and "--type=" not in normalized
        ):
            matches.append((int(process_id), str(command_line)))
    return matches


def active_edge_process_id(
    edge: EdgeProcess,
    candidates: Sequence[tuple[int, str]] | None = None,
) -> int | None:
    """Return the live root PID even when Edge relaunches its bootstrap process."""
    matches = _matching_edge_roots(edge.profile_dir, candidates)
    if matches:
        return max(process_id for process_id, _command_line in matches)
    if edge.process is not None and edge.process.poll() is None:
        return int(edge.process.pid)
    return None


def default_edge_candidates() -> tuple[Path, ...]:
    return (
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    )


def resolve_edge_executable(candidates: Sequence[Path] | None = None) -> Path:
    for candidate in candidates or default_edge_candidates():
        path = Path(candidate)
        if path.is_file():
            return path
    raise EdgeLaunchError("EDGE_NOT_INSTALLED", "未找到 Microsoft Edge")


def build_login_edge_command(
    edge_exe: Path, profile_dir: Path, cdp_port: int
) -> list[str]:
    """Build normal Edge with a loopback capture endpoint and no WebDriver."""
    endpoint = f"http://127.0.0.1:{cdp_port}"
    return [
        str(edge_exe),
        f"--user-data-dir={profile_dir}",
        "--remote-debugging-address=127.0.0.1",
        f"--remote-debugging-port={cdp_port}",
        f"--remote-allow-origins={endpoint}",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-background-timer-throttling",
        "--lang=zh-CN",
        "--accept-lang=zh-CN,zh,en",
        "--force-renderer-accessibility",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        "--start-maximized",
        CNIPA_HOME,
    ]


def launch_login_edge(profile_dir: Path | None = None) -> EdgeProcess:
    """Open CNIPA in a normal, visible Edge session for employee login."""
    profile = Path(profile_dir or (app_data_root() / "edge-profile"))
    profile.mkdir(parents=True, exist_ok=True)
    existing_roots = _matching_edge_roots(profile)
    if existing_roots:
        for _process_id, command_line in sorted(existing_roots, reverse=True):
            port_match = re.search(r"--remote-debugging-port=(\d+)\b", command_line)
            if not port_match:
                continue
            endpoint = f"http://127.0.0.1:{int(port_match.group(1))}"
            if _wait_for_capture_endpoint(endpoint, 12.0):
                return EdgeProcess(None, profile, endpoint)
        raise EdgeLaunchError(
            "EDGE_CAPTURE_UNAVAILABLE",
            "专用 Edge 已打开但后台连接不可用，请关闭该 Edge 窗口后重新打开商标网站",
        )
    port = _available_loopback_port()
    endpoint = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        build_login_edge_command(resolve_edge_executable(), profile, port),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if _wait_for_capture_endpoint(endpoint, 12.0):
        return EdgeProcess(process, profile, endpoint)
    if process.poll() is None:
        process.terminate()
    raise EdgeLaunchError("EDGE_CAPTURE_UNAVAILABLE", "Edge 后台截图服务未能启动")


def _wait_for_capture_endpoint(endpoint: str, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{endpoint}/json/version", timeout=0.5):
                return True
        except Exception:
            time.sleep(0.15)
    return False


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request_graceful_close(process_id: int) -> bool:
    """Post WM_CLOSE to top-level windows owned by the dedicated Edge process."""
    user32 = ctypes.windll.user32
    closed = False
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):
        nonlocal closed
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process_id and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            closed = True
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return closed


def stop_edge_process(edge: EdgeProcess, timeout_seconds: float = 10.0) -> None:
    """Close only the Edge root process launched by this Worker."""
    process_id = active_edge_process_id(edge)
    if process_id is None:
        return
    _request_graceful_close(process_id)
    if (
        edge.process is None
        or process_id != edge.process.pid
        or edge.process.poll() is not None
    ):
        return
    try:
        edge.process.wait(timeout=timeout_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    edge.process.terminate()
    try:
        edge.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        edge.process.kill()
        edge.process.wait(timeout=5)
