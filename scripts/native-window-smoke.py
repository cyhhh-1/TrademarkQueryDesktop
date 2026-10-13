from __future__ import annotations

import argparse
import ctypes
import subprocess
import time
from pathlib import Path

import win32api
import win32gui
from pywinauto import Application, mouse


SW_RESTORE = 9


def wait_until(predicate, *, timeout: float = 10.0, interval: float = 0.1) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition did not become true before timeout")


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the packaged Tauri window controls.")
    parser.add_argument("exe", type=Path)
    args = parser.parse_args()

    exe = args.exe.resolve()
    if not exe.is_file():
        raise FileNotFoundError(exe)

    process = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    try:
        app = Application(backend="uia").connect(process=process.pid, timeout=20)
        window = app.window(title_re=".*商标查询助手.*")
        window.wait("visible", timeout=20)
        hwnd = window.handle
        user32 = ctypes.windll.user32

        minimize_buttons = [
            button
            for button in window.descendants(control_type="Button")
            if button.window_text() == "最小化"
        ]
        if len(minimize_buttons) != 1:
            print(
                "button inventory:",
                [
                    (button.window_text(), str(button.rectangle()), button.is_visible())
                    for button in window.descendants(control_type="Button")
                ],
            )
            raise AssertionError(f"expected one minimize button, got {len(minimize_buttons)}")
        minimize_button = minimize_buttons[0]
        print(
            "window diagnostics:",
            {
                "pid": process.pid,
                "hwnd": hwnd,
                "window_rect": str(window.rectangle()),
                "win32_rect": win32gui.GetWindowRect(hwnd),
                "dpi": user32.GetDpiForWindow(hwnd),
                "screen": (win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)),
                "minimize_rect": str(minimize_button.rectangle()),
                "minimize_enabled": minimize_button.is_enabled(),
            },
        )
        minimize_button.invoke()
        time.sleep(1.0)
        print(
            "after minimize click:",
            {
                "iconic": bool(user32.IsIconic(hwnd)),
                "visible": bool(user32.IsWindowVisible(hwnd)),
                "foreground": user32.GetForegroundWindow() == hwnd,
            },
        )
        wait_until(lambda: bool(user32.IsIconic(hwnd)))

        user32.ShowWindow(hwnd, SW_RESTORE)
        wait_until(lambda: not bool(user32.IsIconic(hwnd)))
        window.wait("visible", timeout=10)
        window.set_focus()
        wait_until(lambda: user32.GetForegroundWindow() == hwnd)

        before = window.rectangle()
        start = (before.left + min(520, before.width() // 2), before.top + 34)
        end = (start[0] + 70, start[1] + 45)
        mouse.move(coords=start)
        mouse.press(button="left", coords=start)
        time.sleep(0.4)
        for step in range(1, 8):
            point = (
                start[0] + (end[0] - start[0]) * step // 7,
                start[1] + (end[1] - start[1]) * step // 7,
            )
            mouse.move(coords=point)
            time.sleep(0.08)
        mouse.release(button="left", coords=end)
        time.sleep(0.8)
        after = window.rectangle()
        moved = abs(after.left - before.left) >= 30 or abs(after.top - before.top) >= 30
        if not moved:
            raise AssertionError(f"window did not move: before={before}, after={after}")

        window.child_window(title="最大化", control_type="Button", found_index=0).invoke()
        wait_until(lambda: bool(user32.IsZoomed(hwnd)))
        window.child_window(title="最大化", control_type="Button", found_index=0).invoke()
        wait_until(lambda: not bool(user32.IsZoomed(hwnd)))

        window.child_window(title="关闭", control_type="Button", found_index=0).invoke()
        process.wait(timeout=15)
        print("native window smoke passed: minimize, drag, maximize/restore, close")
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
