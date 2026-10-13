"""Domain models shared by workbook, browser and task layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal


class LookupMode(str, Enum):
    APPLICATION = "application"
    UNIQUENESS = "uniqueness"


class BrowserState(str, Enum):
    EDGE_NOT_RUNNING = "EDGE_NOT_RUNNING"
    WAITING_SITE = "WAITING_SITE"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    RETURNED_HOME = "RETURNED_HOME"
    READY = "READY"
    RESULTS = "RESULTS"
    DETAIL = "DETAIL"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    AUTH_BLOCKED = "AUTH_BLOCKED"
    WAF_BLOCKED = "WAF_BLOCKED"


class TaskState(str, Enum):
    DRAFT = "draft"
    WAITING_BROWSER = "waiting_browser"
    READY = "ready"
    RUNNING = "running"
    PAUSED_USER_ACTION = "paused_user_action"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class LookupJob:
    worksheet: str
    row: int
    mode: LookupMode
    mark: str
    applicant: str | None = None

    @classmethod
    def application(
        cls, row: int, mark: str, applicant: str, worksheet: str = "Sheet1"
    ) -> "LookupJob":
        return cls(worksheet, row, LookupMode.APPLICATION, mark, applicant)

    @classmethod
    def uniqueness(
        cls, row: int, mark: str, worksheet: str = "Sheet1"
    ) -> "LookupJob":
        return cls(worksheet, row, LookupMode.UNIQUENESS, mark, None)


@dataclass(frozen=True, slots=True)
class ResultRecord:
    sequence: int
    registration_no: str
    international_class: str
    application_date: str
    mark: str
    applicant: str
    page_number: int = 1
    row_index: int | None = None


@dataclass(frozen=True, slots=True)
class DetailRecord:
    mark: str
    registration_no: str
    applicant: str
    term: str | None
    international_class: str = ""
    status: str = ""


UniquenessStatus = Literal[
    "是",
    "否",
    "需人工复核",
    "未检索到同名商标",
    "未检索到有效注册商标，需人工复核",
]


@dataclass(frozen=True, slots=True)
class UniquenessDecision:
    status: UniquenessStatus
    applicants: tuple[str, ...]
    records: tuple[ResultRecord, ...]


@dataclass(frozen=True, slots=True)
class RowResult:
    status: str
    applicant: str | None = None
    term: str | None = None
    uniqueness: UniquenessStatus | None = None
    registration_no: str | None = None
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def application_success(
        cls, applicant: str, term: str, registration_no: str | None = None
    ) -> "RowResult":
        return cls("success", applicant, term, None, registration_no)

    @classmethod
    def uniqueness_result(
        cls,
        status: UniquenessStatus,
        applicant: str | None = None,
        term: str | None = None,
        registration_no: str | None = None,
    ) -> "RowResult":
        if status != "是":
            applicant = None
            term = None
            registration_no = None
        return cls("success", applicant, term, status, registration_no)


class NeedsUserAction(RuntimeError):
    def __init__(self, state: BrowserState, message: str) -> None:
        super().__init__(message)
        self.state = state


@dataclass(frozen=True, slots=True)
class TaskRequest:
    run_id: str
    input_path: Path
    overwrite: bool = False
