"""Conservative text normalization used for exact trademark decisions."""

from __future__ import annotations

import re
import unicodedata
from typing import Literal


_HAN = re.compile(r"[\u3400-\u9fff]")
_NON_HAN = re.compile(r"[^\u3400-\u9fff]+")
_LATIN_OR_DIGIT = re.compile(r"[a-z0-9]", re.IGNORECASE)


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def normalize_mark(value: str) -> str:
    return _normalize(value)


def chinese_mark_text(value: str) -> str:
    """Return only Han characters, preserving their visible order."""
    return _NON_HAN.sub("", unicodedata.normalize("NFKC", value))


def marks_equal(observed: str, expected: str) -> bool:
    """Compare trademarks while ignoring a Latin subtitle on Han-only names.

    CNIPA logo cells often render a Chinese name with its pinyin below it.  OCR
    correctly returns both lines, but the pinyin is not an additional Chinese
    trademark name.  Mixed Latin/Han query terms remain strict so that distinct
    marks such as ``ABC中文`` and ``XYZ中文`` are never collapsed.
    """
    normalized_expected = normalize_mark(expected)
    if _HAN.search(normalized_expected) and not _LATIN_OR_DIGIT.search(
        normalized_expected
    ):
        return chinese_mark_text(observed) == chinese_mark_text(expected)
    return normalize_mark(observed) == normalized_expected


def normalize_applicant(value: str) -> str:
    # CNIPA renders the same applicant with inconsistent full/half-width
    # punctuation and may insert spaces around a parenthesized place name.
    # Applicant matching is identity matching, so formatting whitespace must
    # not split otherwise identical legal names into different candidates.
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", "", normalized)


def applicant_field(value: str) -> Literal["zh", "en"]:
    return "zh" if _HAN.search(value) else "en"
