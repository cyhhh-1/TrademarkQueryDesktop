"""Deterministic application candidate and uniqueness rules."""

from __future__ import annotations

from collections.abc import Sequence

from trademark_worker.models import ResultRecord, UniquenessDecision
from trademark_worker.normalization import marks_equal, normalize_applicant


def exact_mark_records(
    records: Sequence[ResultRecord], mark: str
) -> list[ResultRecord]:
    return [item for item in records if marks_equal(item.mark, mark)]


def application_candidates(
    records: Sequence[ResultRecord], mark: str, applicant: str
) -> list[ResultRecord]:
    expected_applicant = normalize_applicant(applicant)
    matches = [
        item
        for item in exact_mark_records(records, mark)
        if normalize_applicant(item.applicant) == expected_applicant
    ]
    return sorted(matches, key=lambda item: item.sequence, reverse=True)


def decide_uniqueness(
    records: Sequence[ResultRecord], mark: str
) -> UniquenessDecision:
    exact = exact_mark_records(records, mark)
    if not exact:
        return UniquenessDecision("未检索到同名商标", (), ())

    normalized_to_original: dict[str, str] = {}
    for item in exact:
        normalized = normalize_applicant(item.applicant)
        if not normalized:
            return UniquenessDecision("需人工复核", (), tuple(exact))
        normalized_to_original.setdefault(normalized, item.applicant.strip())

    applicants = tuple(normalized_to_original.values())
    status = "是" if len(applicants) == 1 else "否"
    return UniquenessDecision(status, applicants, tuple(exact))
