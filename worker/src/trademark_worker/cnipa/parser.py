"""Pure HTML/table parsers for CNIPA list and detail pages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
import re

from trademark_worker.cnipa.selectors import DETAIL_LABELS, RESULT_HEADERS, header_index, normalize_label
from trademark_worker.models import DetailRecord, ResultRecord


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._depth = 0
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self._table = []
            return
        if self._depth != 1:
            return
        if tag == "tr":
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []
        elif tag == "img" and self._cell is not None:
            attributes = dict(attrs)
            accessible_text = attributes.get("alt") or attributes.get("title")
            if accessible_text:
                self._cell.append(accessible_text)

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            if self._depth == 1 and self._table is not None:
                self.tables.append(self._table)
                self._table = None
            self._depth = max(0, self._depth - 1)
            return
        if self._depth != 1:
            return
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            self._table.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.tokens.append(value)


def parse_result_table(
    rows: Sequence[Sequence[str]], page_number: int = 1
) -> list[ResultRecord]:
    for header_row_index, source_headers in enumerate(rows):
        headers = [str(item or "").strip() for item in source_headers]
        indexes = {
            name: header_index(headers, candidates)
            for name, candidates in RESULT_HEADERS.items()
        }
        required = ("sequence", "mark", "applicant")
        if any(indexes[name] is None for name in required):
            continue

        output: list[ResultRecord] = []
        for row_index, source_row in enumerate(rows[header_row_index + 1 :]):
            row = [str(item or "").strip() for item in source_row]
            sequence_index = indexes["sequence"]
            assert sequence_index is not None
            if sequence_index >= len(row):
                continue
            match = re.search(r"\d+", row[sequence_index])
            if not match:
                continue
            output.append(
                ResultRecord(
                    sequence=int(match.group()),
                    registration_no=_cell(row, indexes["registration_no"]),
                    international_class=_cell(row, indexes["international_class"]),
                    application_date=_cell(row, indexes["application_date"]),
                    mark=_cell(row, indexes["mark"]),
                    applicant=_cell(row, indexes["applicant"]),
                    page_number=page_number,
                    row_index=row_index,
                )
            )
        return output
    return []


def parse_result_html(html: str, page_number: int = 1) -> list[ResultRecord]:
    parser = _TableParser()
    parser.feed(html)
    for table in parser.tables:
        records = parse_result_table(table, page_number=page_number)
        if records:
            return records
    return []


def parse_detail(labels: Mapping[str, str]) -> DetailRecord:
    values = {normalize_label(key): str(value or "").strip() for key, value in labels.items()}

    def get(*names: str) -> str:
        return next((values.get(normalize_label(name), "") for name in names if values.get(normalize_label(name), "")), "")

    return DetailRecord(
        mark=get("商标"),
        registration_no=get("申请/注册号", "注册号", "申请号"),
        applicant=get(
            "申请人名称（中文）",
            "申请人名称(中文)",
            "申请人名称（英文）",
            "申请人名称(英文)",
        ),
        term=get("专用权期限") or None,
        international_class=get("国际分类"),
        status=get("商标状态"),
    )


def parse_detail_html(html: str) -> DetailRecord:
    parser = _TextParser()
    parser.feed(html)
    labels: dict[str, str] = {}
    known = {normalize_label(label) for label in DETAIL_LABELS}
    tokens = parser.tokens
    for index, token in enumerate(tokens):
        normalized = normalize_label(token)
        inline = _inline_label_value(token)
        if inline is not None:
            label, value = inline
            if normalize_label(label) in known:
                labels[label] = value
                continue
        if normalized in known and index + 1 < len(tokens):
            next_token = tokens[index + 1]
            if normalize_label(next_token) not in known:
                labels[token] = next_token
    return parse_detail(labels)


def _cell(row: list[str], index: int | None) -> str:
    return row[index].strip() if index is not None and index < len(row) else ""


def _inline_label_value(token: str) -> tuple[str, str] | None:
    for separator in ("：", ":"):
        if separator in token:
            label, value = token.split(separator, 1)
            return label.strip(), value.strip()
    return None
