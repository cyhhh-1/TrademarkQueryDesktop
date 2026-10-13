"""Exercise a large XLSX through the real task engine without CNIPA traffic."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
from pathlib import Path

from openpyxl import load_workbook
from PIL import Image

from trademark_worker.models import DetailRecord, LookupJob, ResultRecord, TaskRequest
from trademark_worker.task_engine import TaskEngine


class FaultInjectingClient:
    def __init__(self) -> None:
        self.attempts: Counter[int] = Counter()
        self.last_record: ResultRecord | None = None

    def search(self, job: LookupJob, page_progress=None) -> list[ResultRecord]:
        self.attempts[job.row] += 1
        if job.row == 3 and self.attempts[job.row] == 1:
            raise RuntimeError("simulated transient website error")
        if job.row == 11:
            raise RuntimeError("simulated persistent website error")
        if page_progress is not None:
            page_progress(1, 1)
        return [
            ResultRecord(
                1,
                f"QA{job.row:06d}",
                "35",
                "2026年09月13日",
                job.mark,
                job.applicant or "",
                row_index=0,
            )
        ]

    def open_detail(self, record: ResultRecord) -> DetailRecord:
        self.last_record = record
        return DetailRecord(
            record.mark,
            record.registration_no,
            record.applicant,
            "2026年09月13日至2036年09月12日",
        )

    def capture_detail(self, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (640, 900), "white").save(output)
        return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("data_root", type=Path)
    args = parser.parse_args()
    source = args.input.resolve()
    original_hash = sha256(source.read_bytes()).hexdigest()
    client = FaultInjectingClient()
    events: list[dict[str, object]] = []
    summary = TaskEngine(client, args.data_root).run(
        TaskRequest("offline-stress-20", source), events.append
    )

    assert summary.state.value == "completed"
    assert len(summary.items) == 20
    assert len(summary.completed_rows) == 20
    assert sum(item.status == "success" for item in summary.items) == 19
    assert sum(item.status == "failed" for item in summary.items) == 1
    assert client.attempts[3] == 2
    assert client.attempts[11] == 2
    assert sha256(source.read_bytes()).hexdigest() == original_hash

    workbook = load_workbook(summary.output_path)
    try:
        sheet = workbook.active
        assert len(sheet._images) == 19
        anchors = {image.anchor._from.row for image in sheet._images}
        assert anchors == set(range(1, 21)) - {10}
        assert sheet["C2"].value == "2026年09月13日至2036年09月12日"
        assert sheet["C11"].value in (None, "")
    finally:
        workbook.close()

    print(
        f"PASS: 20 rows, 19 success, 1 permanent failure, "
        f"transient retry recovered, 19 embedded screenshots, "
        f"input unchanged; output={summary.output_path}"
    )


if __name__ == "__main__":
    main()
