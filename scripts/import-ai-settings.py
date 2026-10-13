"""One-time import of DashScope settings into the independent desktop app."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


KEYS = ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY", "DASHSCOPE_MODEL")


def find_value(source: object, name: str) -> str | None:
    if isinstance(source, dict):
        direct = source.get(name)
        if direct is not None and str(direct).strip():
            return str(direct).strip()
        for value in source.values():
            found = find_value(value, name)
            if found:
                return found
    elif isinstance(source, list):
        for value in source:
            found = find_value(value, name)
            if found:
                return found
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    destination = args.destination or (
        Path(os.environ["LOCALAPPDATA"])
        / "TrademarkQueryDesktop"
        / "settings.json"
    )
    payload = json.loads(args.source.read_text(encoding="utf-8"))
    values = {key: find_value(payload, key) for key in KEYS}
    values = {key: value for key, value in values.items() if value}
    if "DASHSCOPE_API_KEY" not in values:
        raise SystemExit("source config does not contain DASHSCOPE_API_KEY")
    values.setdefault(
        "DASHSCOPE_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    values.setdefault("DASHSCOPE_MODEL", "qwen3.7-plus")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".writing.json")
    temporary.write_text(
        json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, destination)
    print(f"AI settings imported: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
