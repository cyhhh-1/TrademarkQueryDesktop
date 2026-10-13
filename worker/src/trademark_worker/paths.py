"""Local application and task path construction."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class RunPaths:
    root: Path
    input_dir: Path
    output_dir: Path
    evidence_dir: Path
    request_path: Path
    checkpoint_path: Path
    result_path: Path

    def create(self) -> "RunPaths":
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        return self


def app_data_root(local_app_data: str | None = None) -> Path:
    base = local_app_data or os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("无法确定 LOCALAPPDATA")
    return Path(base) / "TrademarkQueryDesktop"


def build_run_paths(run_id: str, root: Path | None = None) -> RunPaths:
    if not _RUN_ID.fullmatch(run_id):
        raise ValueError("run_id 包含不安全字符")
    task_root = (root or app_data_root()) / "tasks" / run_id
    return RunPaths(
        root=task_root,
        input_dir=task_root / "input",
        output_dir=task_root / "output",
        evidence_dir=task_root / "evidence",
        request_path=task_root / "request.json",
        checkpoint_path=task_root / "checkpoint.json",
        result_path=task_root / "result.json",
    )

