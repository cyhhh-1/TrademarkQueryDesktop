"""Versioned newline-delimited JSON protocol for the desktop worker."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, TextIO


PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    """Raised when a desktop command violates the wire contract."""


@dataclass(frozen=True, slots=True)
class WorkerCommand:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> dict[str, Any]:
        return {"type": self.type, **{k: v for k, v in self.payload.items() if k != "type"}}

    def to_json_line(self) -> str:
        return json.dumps(
            self.to_wire(), ensure_ascii=False, separators=(",", ":")
        ) + "\n"


def parse_command(line: str) -> WorkerCommand:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError("命令不是有效的 JSON") from exc
    if not isinstance(raw, dict):
        raise ProtocolError("命令必须是 JSON 对象")
    if raw.get("protocol") != PROTOCOL_VERSION:
        raise ProtocolError("不支持的协议版本")
    command_type = raw.get("type")
    if not isinstance(command_type, str) or not command_type.strip():
        raise ProtocolError("命令缺少 type")
    payload = {key: value for key, value in raw.items() if key not in {"type", "protocol"}}
    return WorkerCommand(type=command_type, payload=payload)


def emit_event(event: WorkerEvent, stream: TextIO = sys.stdout) -> None:
    stream.write(event.to_json_line())
    stream.flush()

