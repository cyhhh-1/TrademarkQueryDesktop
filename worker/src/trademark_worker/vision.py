"""Optional Qwen vision fallback for image-only trademark cells."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import base64
import json
import os
from pathlib import Path
import re
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from trademark_worker.models import ResultRecord


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.7-plus"


class MarkVisionRecognizer(Protocol):
    def recognize_marks(
        self,
        image_png: bytes,
        records: Sequence[ResultRecord],
        expected_mark: str,
    ) -> Mapping[int, str]: ...


@dataclass(frozen=True, slots=True)
class VisionSettings:
    base_url: str
    api_key: str
    model: str = DEFAULT_MODEL


class VisionRecognitionError(RuntimeError):
    pass


def load_vision_settings(data_root: Path) -> VisionSettings | None:
    """Load secrets at runtime; never embed them in the Worker executable."""
    config: dict[str, object] = {}
    path = Path(data_root) / "settings.json"
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}
        if isinstance(loaded, dict):
            config = loaded

    def value(name: str, default: str = "") -> str:
        environment = os.environ.get(name, "").strip()
        if environment:
            return environment
        configured = config.get(name)
        return str(configured).strip() if configured is not None else default

    api_key = value("DASHSCOPE_API_KEY")
    if not api_key:
        return None
    model = value("DASHSCOPE_MODEL", DEFAULT_MODEL)
    if model.startswith("openai/"):
        model = model.removeprefix("openai/")
    return VisionSettings(
        base_url=value("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        api_key=api_key,
        model=model,
    )


def parse_mark_response(content: str) -> dict[int, str]:
    text = str(content or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VisionRecognitionError("多模态模型未返回有效 JSON") from exc
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise VisionRecognitionError("多模态模型结果缺少 rows")
    output: dict[int, str] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            sequence = int(item.get("sequence"))
        except (TypeError, ValueError):
            continue
        output[sequence] = str(item.get("mark") or "").strip()
    return output


def apply_recognized_marks(
    records: Sequence[ResultRecord], recognized: Mapping[int, str]
) -> list[ResultRecord]:
    return [
        replace(item, mark=str(recognized.get(item.sequence, "")).strip())
        if not item.mark.strip() and str(recognized.get(item.sequence, "")).strip()
        else item
        for item in records
    ]


class QwenVisionRecognizer:
    def __init__(self, settings: VisionSettings) -> None:
        self.settings = settings

    def recognize_marks(
        self,
        image_png: bytes,
        records: Sequence[ResultRecord],
        expected_mark: str,
    ) -> Mapping[int, str]:
        identities = [
            {
                "sequence": item.sequence,
                "registration_no": item.registration_no,
            }
            for item in records
            if not item.mark.strip()
        ]
        if not identities:
            return {}
        prompt = (
            "你是商标检索结果表的视觉识别器。请识别图片中指定序号行的商标文字。"
            "不要推测申请人或注册号，只抄录商标列中肉眼可见的完整文字；"
            "必须区分完全相同名称和包含检索词的更长名称，例如检索‘风行’时‘风行科技’必须返回‘风行科技’。"
            "字体艺术化也要逐字辨认；图形确实没有文字时填空字符串。"
            "rows 必须包含待识别列表中的每一个 sequence，即使 mark 为空也不能省略。"
            f"本次检索词是 {expected_mark!r}，待识别行是 "
            f"{json.dumps(identities, ensure_ascii=False)}。"
            '只返回 JSON 对象，格式为 {"rows":[{"sequence":1,"mark":"商标文字"}]}。'
        )
        encoded = base64.b64encode(image_png).decode("ascii")
        body = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{encoded}"
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
        request = Request(
            f"{self.settings.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, dict)
                )
            return parse_mark_response(str(content))
        except HTTPError as exc:
            error = f"HTTP {exc.code}"
        except (URLError, TimeoutError) as exc:
            error = type(exc).__name__
        except (KeyError, IndexError, json.JSONDecodeError, VisionRecognitionError) as exc:
            error = type(exc).__name__
        raise VisionRecognitionError(f"多模态商标文字识别失败（{error}）")


def build_vision_recognizer(data_root: Path) -> MarkVisionRecognizer | None:
    settings = load_vision_settings(data_root)
    return QwenVisionRecognizer(settings) if settings else None
