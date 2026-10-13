"""Stable, text-first selectors for the CNIPA search site."""

from __future__ import annotations


SEARCH_LABELS = {
    "mark": ("检索要素",),
    "applicant_zh": ("申请人名称（中文）", "申请人名称(中文)"),
    "applicant_en": ("申请人名称（英文）", "申请人名称(英文)"),
}

RESULT_HEADERS = {
    "sequence": ("序号",),
    "registration_no": ("申请/注册号", "注册号", "申请号"),
    "international_class": ("国际分类",),
    "application_date": ("申请日期",),
    "mark": ("商标",),
    "applicant": ("申请人名称", "申请人"),
}

DETAIL_LABELS = (
    "商标",
    "申请/注册号",
    "国际分类",
    "申请人名称（中文）",
    "申请人名称(中文)",
    "申请人名称（英文）",
    "申请人名称(英文)",
    "专用权期限",
    "商标状态",
)


def normalize_label(value: str) -> str:
    return "".join(str(value).replace("\n", " ").split()).replace("：", "").replace(":", "")


def header_index(headers: list[str], candidates: tuple[str, ...]) -> int | None:
    normalized = [normalize_label(item) for item in headers]
    wanted = [normalize_label(item) for item in candidates]
    for candidate in wanted:
        if candidate in normalized:
            return normalized.index(candidate)
    for index, header in enumerate(normalized):
        if any(candidate in header for candidate in wanted):
            return index
    return None
