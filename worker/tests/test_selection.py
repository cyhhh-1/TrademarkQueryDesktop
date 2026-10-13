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
        record(1, "抖音月付", "北京字跳网络技术有限公司"),
        record(4, "抖音月付服务", "北京字跳网络技术有限公司"),
        record(3, " 抖音月付 ", "北京字跳网络技术有限公司"),
        record(2, "抖音月付", "其他公司"),
    ]

    candidates = application_candidates(
        records, "抖音月付", "北京字跳网络技术有限公司"
    )

    assert [item.sequence for item in candidates] == [3, 1]


def test_chinese_applicant_uses_chinese_field() -> None:
    assert applicant_field("北京字跳网络技术有限公司") == "zh"
    assert applicant_field("ByteDance Ltd.") == "en"
    assert applicant_field("ByteDance 北京") == "zh"


def test_normalization_unifies_nfkc_whitespace_and_english_case() -> None:
    assert normalize_mark("  ＭＥＩＴＵＡＮ\n月付 ") == "meituan 月付"
    assert normalize_applicant("  ByteDance   LTD. ") == "bytedanceltd."


def test_applicant_normalization_ignores_spaces_and_parenthesis_width() -> None:
    assert normalize_applicant("来塔科技 （厦门） 有限公司") == normalize_applicant(
        "来塔科技(厦门)有限公司"
    )


def test_multiple_normalized_companies_are_not_unique() -> None:
    records = [
        record(1, "美团月付", "北京三快科技有限公司"),
        record(2, "美团月付", "上海三快科技有限公司"),
    ]

    decision = decide_uniqueness(records, "美团月付")

    assert decision.status == "否"
    assert decision.applicants == (
        "北京三快科技有限公司",
        "上海三快科技有限公司",
    )


def test_same_normalized_company_is_unique_and_preserves_first_original() -> None:
    records = [
        record(1, "美团月付", "北京三快科技有限公司"),
        record(2, " 美团月付 ", " 北京三快科技有限公司 "),
    ]

    decision = decide_uniqueness(records, "美团月付")

    assert decision.status == "是"
    assert decision.applicants == ("北京三快科技有限公司",)


def test_missing_applicant_requires_review() -> None:
    decision = decide_uniqueness([record(1, "美团月付", "")], "美团月付")

    assert decision.status == "需人工复核"
    assert decision.applicants == ()


def test_no_exact_mark_is_reported_as_not_found() -> None:
    decision = decide_uniqueness(
        [record(1, "美团月付服务", "北京三快科技有限公司")], "美团月付"
    )

    assert decision.status == "未检索到同名商标"
    assert decision.records == ()


def test_chinese_mark_ignores_latin_transliteration_but_not_extra_chinese() -> None:
    records = [
        record(1, "扶馨陪诊 FUXIN PEIZHEN", "中发数擎科技（北京）有限公司"),
        record(2, "扶馨陪诊服务 FUXIN PEIZHEN", "其他公司"),
    ]

    decision = decide_uniqueness(records, "扶馨陪诊")

    assert decision.status == "是"
    assert [item.sequence for item in decision.records] == [1]


def test_company_suffixes_are_not_removed() -> None:
    assert normalize_applicant("示例有限公司") != normalize_applicant("示例股份有限公司")
