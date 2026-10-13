from __future__ import annotations

import argparse
import json

from trademark_worker.browser.uia import UIABrowserSession, tab_title


def main() -> int:
    parser = argparse.ArgumentParser(description="Print UIA diagnostics for the dedicated Edge.")
    parser.add_argument("process_id", type=int)
    args = parser.parse_args()

    session = UIABrowserSession.attach(args.process_id)
    try:
        tabs = []
        for item in session.window.descendants(control_type="TabItem"):
            try:
                selected = bool(item.is_selected())
            except Exception:
                selected = None
            tabs.append(
                {
                    "raw": item.window_text(),
                    "title": tab_title(item.window_text()),
                    "selected": selected,
                    "rect": str(item.rectangle()),
                }
            )
        documents = [
            {
                "name": item.window_text(),
                "automation_id": item.element_info.automation_id,
                "rect": str(item.rectangle()),
            }
            for item in session.window.descendants(control_type="Document")
        ]
        print(
            json.dumps(
                {
                    "window_handle": session.window_handle,
                    "window_title": session.window.window_text(),
                    "url": session.url(),
                    "state": session.state().value,
                    "tabs": tabs,
                    "documents": documents,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0
    finally:
        session.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
