"""Atomic task checkpoints used for pause and resume."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class ResumeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    run_id: str
    input_sha256: str
    source_path: str = ""
    working_path: str = ""
    completed_rows: tuple[int, ...] = ()
    paused_row: int | None = None
    state: str = "running"


class CheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> Checkpoint | None:
        if not self.path.exists():
            return None
        try:
            return Checkpoint.model_validate_json(
                self.path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise ResumeError(
                "CHECKPOINT_DAMAGED", "任务检查点损坏，无法安全恢复"
            ) from exc

    def save(self, checkpoint: Checkpoint) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.writing")
        try:
            temporary.write_text(
                checkpoint.model_dump_json(indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def require_input(self, input_sha256: str) -> Checkpoint:
        checkpoint = self.load()
        if checkpoint is None:
            raise ResumeError("CHECKPOINT_MISSING", "找不到可恢复的任务检查点")
        if checkpoint.input_sha256 != input_sha256:
            raise ResumeError(
                "CHECKPOINT_INPUT_MISMATCH", "输入文件与检查点不一致"
            )
        return checkpoint
