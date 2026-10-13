from __future__ import annotations

import argparse
import time
from pathlib import Path

from pywinauto import Application, Desktop


def wait_until(predicate, *, timeout: float = 20.0, interval: float = 0.2) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition did not become true before timeout")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load an XLSX into an already running installed desktop client."
    )
    parser.add_argument("process_id", type=int)
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--expected-mark", action="append")
    args = parser.parse_args()

    xlsx = args.xlsx.resolve()
    if not xlsx.is_file():
        raise FileNotFoundError(xlsx)

    app = Application(backend="uia").connect(process=args.process_id, timeout=20)
    window = app.window(title_re=".*商标查询助手.*")
    window.wait("visible", timeout=20)
    window.set_focus()
    window.child_window(title="选择 XLSX", control_type="Button").invoke()

    desktop = Desktop(backend="uia")
    wait_until(
        lambda: any(
            item.class_name() == "#32770" and item.is_visible()
            for item in desktop.windows(process=args.process_id)
        ),
        timeout=20,
    )
    dialogs = [
        item
        for item in desktop.windows(process=args.process_id)
        if item.class_name() == "#32770" and item.is_visible()
    ]
    dialog = dialogs[-1]
    edits = [
        item
        for item in dialog.descendants(control_type="Edit")
        if item.element_info.automation_id == "1148"
    ]
    if not edits:
        raise AssertionError("file picker has no editable file-name field")
    file_name = edits[-1]
    file_name.set_edit_text(str(xlsx))
    candidates = [
        button
        for button in dialog.descendants(control_type="Button")
        if button.element_info.automation_id == "1"
        and button.is_visible()
        and button.is_enabled()
    ]
    if not candidates:
        raise AssertionError("file picker has no enabled Open button")
    open_button = candidates[-1]
    open_button.invoke()

    wait_until(
        lambda: xlsx.name in "\n".join(
            item.window_text() for item in window.descendants()
        ),
        timeout=30,
    )
    visible_text = "\n".join(item.window_text() for item in window.descendants())
    expected_marks = args.expected_mark or ["陶玻网", "花洛莉亚"]
    if any(mark not in visible_text for mark in expected_marks):
        raise AssertionError(f"expected trademark rows were not loaded: {expected_marks}")

    if args.open_browser:
        window.child_window(title="打开商标网站", control_type="Button").invoke()

    print(f"installed app loaded workbook: {xlsx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
