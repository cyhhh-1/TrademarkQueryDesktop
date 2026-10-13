from __future__ import annotations

from pathlib import Path

import pytest

from trademark_worker.checkpoint import Checkpoint, CheckpointStore, ResumeError


def test_checkpoint_round_trip_and_no_partial_file(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.json")

    store.save(
        Checkpoint(run_id="r1", input_sha256="abc", completed_rows=(2,))
    )

    loaded = store.load()
    assert loaded is not None
    assert loaded.completed_rows == (2,)
    assert not (tmp_path / "checkpoint.json.writing").exists()


def test_load_missing_checkpoint_returns_none(tmp_path: Path) -> None:
    assert CheckpointStore(tmp_path / "missing.json").load() is None


def test_resume_rejects_different_input_hash(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.json")
    store.save(Checkpoint(run_id="r1", input_sha256="abc"))

    with pytest.raises(ResumeError, match="输入文件与检查点不一致"):
        store.require_input("def")


def test_corrupted_checkpoint_has_stable_error(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.json"
    path.write_text("{bad", encoding="utf-8")

    with pytest.raises(ResumeError) as captured:
        CheckpointStore(path).load()

    assert captured.value.code == "CHECKPOINT_DAMAGED"
