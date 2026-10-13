"""Capture CNIPA pages from Edge's renderer without activating its window."""

from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
from io import BytesIO
import json
from typing import Any, Protocol
from urllib.request import urlopen

from PIL import Image


class _WebSocket(Protocol):
    def send(self, payload: str) -> None: ...

    def recv(self) -> str: ...

    def close(self) -> None: ...


TargetLoader = Callable[[str], Sequence[dict[str, Any]]]
WebSocketFactory = Callable[..., _WebSocket]


def _load_targets(endpoint: str) -> Sequence[dict[str, Any]]:
    with urlopen(f"{endpoint.rstrip('/')}/json/list", timeout=3.0) as response:
        payload = json.load(response)
    if not isinstance(payload, list):
        raise RuntimeError("Edge 后台截图接口返回了无效标签页列表")
    return [item for item in payload if isinstance(item, dict)]


def _open_websocket(url: str, *, timeout: float, origin: str) -> _WebSocket:
    from websocket import create_connection

    return create_connection(url, timeout=timeout, origin=origin)


class BackgroundPageCapture:
    """Capture a full page over loopback CDP without using WebDriver."""

    def __init__(
        self,
        endpoint: str,
        *,
        target_loader: TargetLoader = _load_targets,
        websocket_factory: WebSocketFactory = _open_websocket,
        timeout: float = 15.0,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.target_loader = target_loader
        self.websocket_factory = websocket_factory
        self.timeout = timeout

    def capture(self, url_hint: str) -> Image.Image:
        websocket_url = self._websocket_url(url_hint)
        socket = self.websocket_factory(
            websocket_url,
            timeout=self.timeout,
            origin=self.endpoint,
        )
        try:
            self._command(socket, 1, "Page.getLayoutMetrics")
            screenshot = self._command(
                socket,
                2,
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "fromSurface": True,
                    "captureBeyondViewport": True,
                },
            )
        finally:
            socket.close()
        encoded = screenshot.get("data")
        if not isinstance(encoded, str) or not encoded:
            raise RuntimeError("Edge 后台截图未返回图片数据")
        try:
            with Image.open(BytesIO(base64.b64decode(encoded))) as source:
                image = source.convert("RGB")
        except Exception as exc:
            raise RuntimeError("Edge 后台截图图片无效") from exc
        if image.width <= 1 or image.height <= 1:
            image.close()
            raise RuntimeError("Edge 后台截图为空")
        extrema = image.getextrema()
        if all(high <= 4 for _low, high in extrema):
            image.close()
            raise RuntimeError("Edge 后台截图为黑屏")
        return image

    def _websocket_url(self, url_hint: str) -> str:
        candidates = []
        for target in self.target_loader(self.endpoint):
            if target.get("type") != "page":
                continue
            url = str(target.get("url") or "")
            websocket_url = str(target.get("webSocketDebuggerUrl") or "")
            if url_hint in url and websocket_url:
                candidates.append(websocket_url)
        if not candidates:
            raise RuntimeError(f"未找到可后台截图的商标网页：{url_hint}")
        return candidates[-1]

    @staticmethod
    def _command(
        socket: _WebSocket,
        command_id: int,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": command_id, "method": method}
        if params is not None:
            payload["params"] = params
        socket.send(json.dumps(payload, ensure_ascii=False))
        while True:
            response = json.loads(socket.recv())
            if response.get("id") != command_id:
                continue
            if "error" in response:
                message = response["error"].get("message", "未知错误")
                raise RuntimeError(f"Edge 后台截图失败：{message}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Edge 后台截图接口返回无效结果")
            return result
