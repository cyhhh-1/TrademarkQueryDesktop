"""Persistent worker process entry point."""

from __future__ import annotations

import sys
from typing import TextIO

from PIL import Image
from playwright.sync_api import sync_playwright

from trademark_worker import __version__
from trademark_worker.instance_lock import WorkerInstanceLock
from trademark_worker.local_ocr import build_local_ocr_recognizer
from trademark_worker.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    parse_command,
)
from trademark_worker.runtime import EventChannel, WorkerRuntime


def configure_standard_streams() -> None:
    """Use one explicit wire encoding, including inside a frozen Windows Worker."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def run(input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> int:
    channel = EventChannel(output_stream)
    instance_lock = WorkerInstanceLock.acquire()
    if instance_lock is None:
        channel.emit(
            {
                "type": "error",
                "code": "WORKER_ALREADY_RUNNING",
                "message": "商标查询客户端已在运行，请使用已经打开的客户端窗口",
            }
        )
        return 3
    with instance_lock:
        runtime = WorkerRuntime(channel)
        channel.emit(
            {
                "type": "hello",
                "protocol": PROTOCOL_VERSION,
                "worker_version": __version__,
            }
        )
        for line in input_stream:
            if not line.strip():
                continue
            try:
                command = parse_command(line)
            except ProtocolError as exc:
                channel.emit(
                    {"type": "error", "code": "PROTOCOL_ERROR", "message": str(exc)}
                )
                continue
            try:
                if not runtime.handle(command):
                    return 0
            except Exception as exc:
                runtime.emit_error(exc)
        runtime.close()
        return 0


def main(argv: list[str] | None = None) -> int:
    configure_standard_streams()
    arguments = sys.argv[1:] if argv is None else argv
    if arguments == ["--version"]:
        print(__version__)
        return 0
    if arguments == ["--playwright-smoke"]:
        with sync_playwright() as playwright:
            print(playwright.chromium.name)
        return 0
    if arguments == ["--ocr-smoke"]:
        recognizer = build_local_ocr_recognizer()
        if recognizer is None:
            raise RuntimeError("RapidOCR 未打包")
        image = Image.new("RGB", (64, 32), "white")
        try:
            recognizer.recognize_mark(image, "测试")
        finally:
            image.close()
        print("rapidocr")
        return 0
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
