"""Resumable row-by-row orchestration for both supported XLSX workflows."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import Event
from typing import Protocol

from trademark_worker.checkpoint import Checkpoint, CheckpointStore, ResumeError
from trademark_worker.models import (
    BrowserState,
    DetailRecord,
    LookupJob,
    LookupMode,
    NeedsUserAction,
    ResultRecord,
    RowResult,
    TaskRequest,
    TaskState,
)
from trademark_worker.normalization import marks_equal, normalize_applicant
from trademark_worker.paths import RunPaths, build_run_paths
from trademark_worker.selection import (
    application_candidates,
    exact_mark_records,
)
from trademark_worker.workbook.inspector import (
    WorkbookInspection,
    inspect_workbook,
    load_jobs,
    prepare_working_copy,
)
from trademark_worker.workbook.writer import (
    apply_row_result,
    record_execution_status,
    validate_output,
    write_row_error,
)


EventSink = Callable[[dict[str, object]], None]
StageSink = Callable[..., None]


class TrademarkClient(Protocol):
    def search(
        self,
        job: LookupJob,
        page_progress: Callable[[int, int], None] | None = None,
    ) -> list[ResultRecord]: ...
    def search_pages(
        self,
        job: LookupJob,
        page_progress: Callable[[int, int], None] | None = None,
    ) -> Iterable[list[ResultRecord]]: ...
    def open_detail(self, record: ResultRecord) -> DetailRecord: ...
    def capture_exact_rows(
        self, records: Sequence[ResultRecord], output_dir: Path
    ) -> Path: ...
    def capture_detail(self, output: Path) -> Path: ...


@dataclass(frozen=True, slots=True)
class TaskItemResult:
    row: int
    mark: str
    status: str
    applicant: str | None = None
    term: str | None = None
    uniqueness: str | None = None
    registration_no: str | None = None
    message: str = ""
    attempts: int = 1

    @classmethod
    def from_row(
        cls, job: LookupJob, result: RowResult, attempts: int = 1
    ) -> "TaskItemResult":
        return cls(
            row=job.row,
            mark=job.mark,
            status=result.status,
            applicant=result.applicant,
            term=result.term,
            uniqueness=result.uniqueness,
            registration_no=result.registration_no,
            message=result.message,
            attempts=attempts,
        )


@dataclass(frozen=True, slots=True)
class TaskSummary:
    run_id: str
    state: TaskState
    output_path: Path
    items: tuple[TaskItemResult, ...]
    completed_rows: tuple[int, ...]

    def to_wire(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "output_path": str(self.output_path),
            "items": [asdict(item) for item in self.items],
            "completed_rows": list(self.completed_rows),
        }


class TaskEngine:
    def __init__(self, client: TrademarkClient, data_root: Path) -> None:
        self.client = client
        self.data_root = Path(data_root)
        self._cancelled = Event()
        self._pause_requested = Event()

    def run(self, request: TaskRequest, events: EventSink) -> TaskSummary:
        self._cancelled.clear()
        self._pause_requested.clear()
        paths = build_run_paths(request.run_id, self.data_root).create()
        source = Path(request.input_path).resolve()
        input_sha256 = _file_sha256(source)
        inspection = inspect_workbook(source)
        working = prepare_working_copy(source, paths.root)
        _write_json_atomic(
            paths.request_path,
            {
                "run_id": request.run_id,
                "input_path": str(source),
                "overwrite": request.overwrite,
            },
        )
        checkpoint = Checkpoint(
            run_id=request.run_id,
            input_sha256=input_sha256,
            source_path=str(source),
            working_path=str(working),
        )
        CheckpointStore(paths.checkpoint_path).save(checkpoint)
        jobs = load_jobs(source, inspection, overwrite=request.overwrite)
        return self._execute(paths, inspection, working, jobs, checkpoint, events)

    def resume(self, run_id: str, events: EventSink) -> TaskSummary:
        self._cancelled.clear()
        self._pause_requested.clear()
        paths = build_run_paths(run_id, self.data_root).create()
        store = CheckpointStore(paths.checkpoint_path)
        checkpoint = store.load()
        if checkpoint is None:
            raise ResumeError("CHECKPOINT_MISSING", "找不到可恢复的任务检查点")
        source = Path(checkpoint.source_path)
        checkpoint = store.require_input(_file_sha256(source))
        request = _read_request(paths.request_path)
        inspection = inspect_workbook(source)
        working = Path(checkpoint.working_path)
        if not working.is_file():
            raise ResumeError("WORKING_COPY_MISSING", "任务结果工作副本不存在")
        jobs = [
            job
            for job in load_jobs(
                source, inspection, overwrite=bool(request.get("overwrite", False))
            )
            if job.row not in checkpoint.completed_rows
        ]
        resumed = checkpoint.model_copy(update={"state": "running", "paused_row": None})
        store.save(resumed)
        return self._execute(paths, inspection, working, jobs, resumed, events)

    def cancel(self) -> None:
        self._cancelled.set()

    def pause(self) -> None:
        self._pause_requested.set()

    def _execute(
        self,
        paths: RunPaths,
        inspection: WorkbookInspection,
        working: Path,
        jobs: Sequence[LookupJob],
        checkpoint: Checkpoint,
        events: EventSink,
    ) -> TaskSummary:
        store = CheckpointStore(paths.checkpoint_path)
        completed = list(checkpoint.completed_rows)
        total = len(completed) + len(jobs)
        items: list[TaskItemResult] = []
        retry_jobs: list[LookupJob] = []
        events(
            {
                "type": "task_state",
                "state": TaskState.RUNNING.value,
                "run_id": checkpoint.run_id,
                "current": len(completed),
                "total": total,
            }
        )

        def cancelled_summary(next_job: LookupJob | None) -> TaskSummary:
            nonlocal checkpoint
            cancelled = checkpoint.model_copy(
                update={
                    "completed_rows": tuple(completed),
                    "paused_row": next_job.row if next_job is not None else None,
                    "state": TaskState.CANCELLED.value,
                }
            )
            store.save(cancelled)
            summary = TaskSummary(
                checkpoint.run_id,
                TaskState.CANCELLED,
                working,
                tuple(items),
                tuple(completed),
            )
            events(
                {
                    "type": "task_state",
                    "state": TaskState.CANCELLED.value,
                    "run_id": checkpoint.run_id,
                    "current": len(completed),
                    "total": total,
                    "output_path": str(working),
                }
            )
            return summary

        def paused_summary(
            next_job: LookupJob | None,
            *,
            reason: str = "USER_REQUESTED",
            message: str = "任务已由用户暂停",
        ) -> TaskSummary:
            nonlocal checkpoint
            paused = checkpoint.model_copy(
                update={
                    "completed_rows": tuple(completed),
                    "paused_row": next_job.row if next_job is not None else None,
                    "state": TaskState.PAUSED_USER_ACTION.value,
                }
            )
            store.save(paused)
            summary = TaskSummary(
                checkpoint.run_id,
                TaskState.PAUSED_USER_ACTION,
                working,
                tuple(items),
                tuple(completed),
            )
            events(
                {
                    "type": "paused",
                    "run_id": checkpoint.run_id,
                    "row": next_job.row if next_job is not None else 0,
                    "reason": reason,
                    "message": message,
                    "recoverable": True,
                    "current": len(completed),
                    "total": total,
                    "output_path": str(working),
                }
            )
            return summary

        def finalize(
            job: LookupJob,
            result: RowResult,
            evidence: Path | None,
            attempts: int,
            position: int,
            emit_stage: StageSink,
            model_used: bool,
        ) -> None:
            nonlocal checkpoint
            if result.status == "success":
                emit_stage("writing_workbook")
                apply_row_result(working, inspection, job, result, evidence)
            else:
                emit_stage("writing_workbook")
                write_row_error(working, inspection, job, result.message)
            item = TaskItemResult.from_row(job, result, attempts=attempts)
            record_execution_status(
                working,
                job,
                item.status,
                item.attempts,
                item.message,
                inspection=inspection,
                model_used=model_used,
            )
            validate_output(working)
            items.append(item)
            completed.append(job.row)
            checkpoint = checkpoint.model_copy(
                update={
                    "completed_rows": tuple(completed),
                    "paused_row": None,
                    "state": TaskState.RUNNING.value,
                }
            )
            store.save(checkpoint)
            events(
                {
                    "type": "row_progress",
                    "phase": "completed",
                    **asdict(item),
                    "current": position,
                    "completed_count": len(completed),
                    "total": total,
                }
            )

        def run_attempt(job: LookupJob, position: int, attempt: int) -> bool:
            model_calls_before = int(
                getattr(self.client, "remote_model_call_count", 0)
            )

            def model_used() -> bool:
                return int(
                    getattr(self.client, "remote_model_call_count", 0)
                ) > model_calls_before

            events(
                {
                    "type": "row_progress",
                    "phase": "started",
                    "row": job.row,
                    "mark": job.mark,
                    "attempt": attempt,
                    "pass_number": attempt,
                    "current": position,
                    "completed_count": len(completed),
                    "total": total,
                }
            )

            def emit_stage(stage: str, **details: object) -> None:
                events(
                    {
                        "type": "row_progress",
                        "phase": "stage",
                        "stage": stage,
                        "row": job.row,
                        "mark": job.mark,
                        "attempt": attempt,
                        "pass_number": attempt,
                        "current": position,
                        "completed_count": len(completed),
                        "total": total,
                        **details,
                    }
                )

            try:
                result, evidence = self._run_row(
                    job, paths.evidence_dir, emit_stage
                )
            except Exception as exc:
                if isinstance(exc, NeedsUserAction) and exc.state in {
                    BrowserState.LOGIN_REQUIRED,
                    BrowserState.CAPTCHA_REQUIRED,
                    BrowserState.AUTH_BLOCKED,
                }:
                    record_execution_status(
                        working, job, "paused", attempt, str(exc),
                        inspection=inspection, model_used=model_used(),
                    )
                    raise
                message = _row_error_message(exc)
                if attempt == 1:
                    record_execution_status(
                        working,
                        job,
                        "retry_pending",
                        1,
                        message,
                        inspection=inspection,
                        model_used=model_used(),
                    )
                    events(
                        {
                            "type": "row_deferred",
                            "row": job.row,
                            "mark": job.mark,
                            "attempt": 1,
                            "pass_number": 1,
                            "will_retry": True,
                            "message": message,
                            "current": position,
                            "completed_count": len(completed),
                            "total": total,
                        }
                    )
                    return False
                finalize(
                    job,
                    RowResult(status="failed", message=message),
                    None,
                    attempts=2,
                    position=position,
                    emit_stage=emit_stage,
                    model_used=model_used(),
                )
                return True
            finalize(
                job, result, evidence, attempt, position, emit_stage,
                model_used(),
            )
            return True

        for position, job in enumerate(jobs, start=len(completed) + 1):
            if self._cancelled.is_set():
                return cancelled_summary(job)
            if self._pause_requested.is_set():
                return paused_summary(job)
            try:
                if not run_attempt(job, position, attempt=1):
                    retry_jobs.append(job)
            except NeedsUserAction as exc:
                return paused_summary(
                    job,
                    reason=exc.state.value,
                    message=str(exc),
                )

        for job in retry_jobs:
            if self._cancelled.is_set():
                return cancelled_summary(job)
            if self._pause_requested.is_set():
                return paused_summary(job)
            try:
                run_attempt(job, len(completed) + 1, attempt=2)
            except NeedsUserAction as exc:
                return paused_summary(
                    job,
                    reason=exc.state.value,
                    message=str(exc),
                )

        validate_output(working)
        completed_checkpoint = checkpoint.model_copy(
            update={
                "completed_rows": tuple(completed),
                "paused_row": None,
                "state": TaskState.COMPLETED.value,
            }
        )
        store.save(completed_checkpoint)
        summary = TaskSummary(
            checkpoint.run_id,
            TaskState.COMPLETED,
            working,
            tuple(items),
            tuple(completed),
        )
        _write_json_atomic(paths.result_path, summary.to_wire())
        events(
            {
                "type": "completed",
                **summary.to_wire(),
                "current": total,
                "total": total,
            }
        )
        return summary

    def _run_row(
        self, job: LookupJob, evidence_root: Path, stages: StageSink
    ) -> tuple[RowResult, Path | None]:
        row_dir = evidence_root / f"row-{job.row}"
        row_dir.mkdir(parents=True, exist_ok=True)
        stages("searching")
        if job.mode is LookupMode.APPLICATION:
            pages = self.client.search_pages(
                job,
                page_progress=lambda current, total: stages(
                    "reading_page", page=current, pages=total
                ),
            )
            return self._run_application(job, pages, row_dir, stages)

        records = self.client.search(
            job,
            page_progress=lambda current, total: stages(
                "reading_page", page=current, pages=total
            ),
        )
        stages("checking_results")
        return self._run_uniqueness(job, records, row_dir, stages)

    def _run_application(
        self,
        job: LookupJob,
        pages: Iterable[Sequence[ResultRecord]],
        row_dir: Path,
        stages: StageSink,
    ) -> tuple[RowResult, Path | None]:
        assert job.applicant is not None
        found_candidates = False
        matched_detail = False
        active_detail = False
        for records in pages:
            stages("checking_results")
            candidates = application_candidates(records, job.mark, job.applicant)
            if candidates:
                found_candidates = True
            for candidate in candidates:
                stages("opening_detail")
                detail = self.client.open_detail(candidate)
                if not _detail_matches(detail, candidate):
                    continue
                matched_detail = True
                if not _is_active_registration(detail):
                    continue
                active_detail = True
                if not detail.term:
                    continue
                stages("capturing_evidence")
                evidence = self.client.capture_detail(row_dir / "detail.png")
                return (
                    RowResult.application_success(
                        detail.applicant, detail.term, detail.registration_no
                    ),
                    evidence,
                )

        if not found_candidates:
            message = "未找到商标名称及申请人一致的检索记录"
        elif not matched_detail:
            message = "检索到候选记录，但详情申请人或注册号不一致"
        elif not active_detail:
            message = "检索到申请人一致的商标，但状态不是有效注册商标"
        else:
            message = "检索到有效注册商标，但专用权期限为空"
        return RowResult(
            status="not_found",
            message=message,
        ), None

    def _run_uniqueness(
        self,
        job: LookupJob,
        records: Sequence[ResultRecord],
        row_dir: Path,
        stages: StageSink,
    ) -> tuple[RowResult, Path | None]:
        exact = exact_mark_records(records, job.mark)
        if not exact:
            return RowResult.uniqueness_result("未检索到同名商标"), None

        grouped: dict[str, list[ResultRecord]] = {}
        for candidate in sorted(exact, key=lambda item: item.sequence, reverse=True):
            key = normalize_applicant(candidate.applicant)
            if not key:
                stages("capturing_evidence")
                evidence = self.client.capture_exact_rows(exact[:4], row_dir)
                return RowResult.uniqueness_result("需人工复核"), evidence
            grouped.setdefault(key, []).append(candidate)

        active: list[tuple[ResultRecord, DetailRecord]] = []
        for candidates in grouped.values():
            for candidate in candidates:
                stages("opening_detail")
                detail = self.client.open_detail(candidate)
                if not _detail_matches(detail, candidate):
                    continue
                if _is_active_registration(detail):
                    active.append((candidate, detail))
                    break
            if len(active) >= 2:
                break

        if not active:
            stages("capturing_evidence")
            evidence = self.client.capture_exact_rows(exact[:4], row_dir)
            return (
                RowResult.uniqueness_result(
                    "未检索到有效注册商标，需人工复核"
                ),
                evidence,
            )

        if len(active) >= 2:
            active_keys = {
                normalize_applicant(detail.applicant) for _, detail in active[:2]
            }
            evidence_records = [candidate for candidate, _ in active[:2]]
            for candidate in exact:
                if len(evidence_records) >= 4:
                    break
                if (
                    normalize_applicant(candidate.applicant) in active_keys
                    and candidate not in evidence_records
                ):
                    evidence_records.append(candidate)
            stages("capturing_evidence")
            evidence = self.client.capture_exact_rows(evidence_records, row_dir)
            return RowResult.uniqueness_result("否"), evidence

        active_record, active_detail = active[0]
        active_key = normalize_applicant(active_detail.applicant)
        evidence_records = [
            candidate
            for candidate in exact
            if normalize_applicant(candidate.applicant) == active_key
        ][:4]
        if active_record not in evidence_records:
            evidence_records.insert(0, active_record)
            evidence_records = evidence_records[:4]
        stages("capturing_evidence")
        evidence = self.client.capture_exact_rows(evidence_records, row_dir)
        return (
            RowResult.uniqueness_result(
                "是",
                applicant=active_detail.applicant,
                term=active_detail.term,
                registration_no=active_detail.registration_no,
            ),
            evidence,
        )


def _detail_matches(detail: DetailRecord, record: ResultRecord) -> bool:
    identity_matches = (
        bool(detail.registration_no)
        and detail.registration_no == record.registration_no
    ) or marks_equal(detail.mark, record.mark)
    return identity_matches and normalize_applicant(detail.applicant) == normalize_applicant(
        record.applicant
    )


def _is_active_registration(detail: DetailRecord) -> bool:
    status = " ".join(str(detail.status or "").split()).casefold()
    return "issued and active" in status and "注册商标" in status


def _row_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    return message or type(exc).__name__


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_request(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResumeError("REQUEST_DAMAGED", "任务请求记录损坏，无法恢复") from exc
