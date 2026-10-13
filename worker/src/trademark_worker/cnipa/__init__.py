"""CNIPA trademark search page adapters."""

from trademark_worker.cnipa.client import CnipaClient, PagePort
from trademark_worker.cnipa.parser import (
    parse_detail,
    parse_detail_html,
    parse_result_html,
    parse_result_table,
)

__all__ = [
    "CnipaClient",
    "PagePort",
    "parse_detail",
    "parse_detail_html",
    "parse_result_html",
    "parse_result_table",
]
