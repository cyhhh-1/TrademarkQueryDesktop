from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from trademark_worker.paths import app_data_root, build_run_paths
from trademark_worker.protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    WorkerEvent,
    emit_event,
    parse_command,
)


def test_hello_event_is_one_utf8_json_line() -> None:
    stream = io.StringIO()

    emit_event(
        WorkerEvent(type="hello", payload={"protocol": PROTOCOL_VERSION}), stream
    )

    assert stream.getvalue() == '{"type":"hello","protocol":1}\n'


def test_rejects_command_without_supported_protocol() -> None:
    with pytest.raises(ProtocolError, match="不支持的协议版本"):
        parse_command('{"type":"start","protocol":2}')


def test_parses_supported_command_and_preserves_payload() -> None:
    command = parse_command(
        '{"type":"inspect_file","protocol":1,"path":"C:/files/input.xlsx"}'
    )

    assert command.type == "inspect_file"
    assert command.payload == {"path": "C:/files/input.xlsx"}


def test_app_data_root_uses_dedicated_folder() -> None:
    assert app_data_root(r"C:\Local") == Path(r"C:\Local\TrademarkQueryDesktop")


def test_run_paths_are_scoped_to_validated_run_id(tmp_path: Path) -> None:
    paths = build_run_paths("run-20260909-a1", root=tmp_path)

    assert paths.root == tmp_path / "tasks" / "run-20260909-a1"
    assert paths.input_dir == paths.root / "input"
    assert paths.output_dir == paths.root / "output"
    assert paths.evidence_dir == paths.root / "evidence"


@pytest.mark.parametrize("run_id", ["../escape", "a/b", "a\\b", "", "."])
def test_run_paths_reject_unsafe_run_ids(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError, match="run_id"):
        build_run_paths(run_id, root=tmp_path)


def test_event_wire_payload_cannot_override_type() -> None:
    event = WorkerEvent(type="error", payload={"type": "hello", "code": "X"})

    assert json.loads(event.to_json_line()) == {"type": "error", "code": "X"}
