from __future__ import annotations

from trademark_worker.models import ResultRecord
from trademark_worker.normalization import (
    applicant_field,
    normalize_applicant,
    normalize_mark,
)
from trademark_worker.selection import application_candidates, decide_uniqueness


def record(
    sequence: int,
    mark: str,
    applicant: str,
    registration_no: str | None = None,
) -> ResultRecord:
    return ResultRecord(
        sequence=sequence,
        registration_no=registration_no or str(90000000 + sequence),
        international_class="35",
        application_date="2026年08月03日",
        mark=mark,
        applicant=applicant,
    )


def test_application_candidates_are_exact_and_sequence_descending() -> None:
    records = [
        record(1, "云杉月付", "北京云杉网络技术有限公司"),
        record(4, "云杉月付服务", "北京云杉网络技术有限公司"),
        record(3, " 云杉月付 ", "北京云杉网络技术有限公司"),
        record(2, "云杉月付", "其他公司"),
    ]

    candidates = application_candidates(
        records, "云杉月付", "北京云杉网络技术有限公司"
    )

    assert [item.sequence for item in candidates] == [3, 1]


def test_chinese_applicant_uses_chinese_field() -> None:
    assert applicant_field("北京云杉网络技术有限公司") == "zh"
    assert applicant_field("ByteDance Ltd.") == "en"
    assert applicant_field("ByteDance 北京") == "zh"


def test_normalization_unifies_nfkc_whitespace_and_english_case() -> None:
    assert normalize_mark("  ＭＥＩＴＵＡＮ\n月付 ") == "meituan 月付"
    assert normalize_applicant("  ByteDance   LTD. ") == "bytedanceltd."


def test_applicant_normalization_ignores_spaces_and_parenthesis_width() -> None:
    assert normalize_applicant("乙巳科技 （厦门） 有限公司") == normalize_applicant(
        "乙巳科技(厦门)有限公司"
    )


def test_multiple_normalized_companies_are_not_unique() -> None:
    records = [
        record(1, "青禾月付", "北京青禾科技有限公司"),
        record(2, "青禾月付", "上海青禾科技有限公司"),
    ]

    decision = decide_uniqueness(records, "青禾月付")

    assert decision.status == "否"
    assert decision.applicants == (
        "北京青禾科技有限公司",
        "上海青禾科技有限公司",
    )


def test_same_normalized_company_is_unique_and_preserves_first_original() -> None:
    records = [
        record(1, "青禾月付", "北京青禾科技有限公司"),
        record(2, " 青禾月付 ", " 北京青禾科技有限公司 "),
    ]

    decision = decide_uniqueness(records, "青禾月付")

    assert decision.status == "是"
    assert decision.applicants == ("北京青禾科技有限公司",)


def test_missing_applicant_requires_review() -> None:
    decision = decide_uniqueness([record(1, "青禾月付", "")], "青禾月付")

    assert decision.status == "需人工复核"
    assert decision.applicants == ()


def test_no_exact_mark_is_reported_as_not_found() -> None:
    decision = decide_uniqueness(
        [record(1, "青禾月付服务", "北京青禾科技有限公司")], "青禾月付"
    )

    assert decision.status == "未检索到同名商标"
    assert decision.records == ()


def test_chinese_mark_ignores_latin_transliteration_but_not_extra_chinese() -> None:
    records = [
        record(1, "安馨陪诊 ANXIN PEIZHEN", "甲辰科技（北京）有限公司"),
        record(2, "安馨陪诊服务 ANXIN PEIZHEN", "其他公司"),
    ]

    decision = decide_uniqueness(records, "安馨陪诊")

    assert decision.status == "是"
    assert [item.sequence for item in decision.records] == [1]


def test_company_suffixes_are_not_removed() -> None:
    assert normalize_applicant("示例有限公司") != normalize_applicant("示例股份有限公司")
