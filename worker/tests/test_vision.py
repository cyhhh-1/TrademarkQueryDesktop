from __future__ import annotations

from pathlib import Path
import json
from urllib.error import HTTPError

from trademark_worker.models import ResultRecord
from trademark_worker.vision import (
    VisionSettings,
    apply_recognized_marks,
    load_vision_settings,
    parse_mark_response,
    QwenVisionRecognizer,
    VisionRecognitionError,
)


def record(sequence: int, mark: str = "") -> ResultRecord:
    return ResultRecord(
        sequence=sequence,
        registration_no=f"9000{sequence}",
        international_class="35",
        application_date="2026年08月03日",
        mark=mark,
        applicant="甲公司",
        row_index=sequence - 1,
    )


def test_parse_mark_response_accepts_json_and_markdown_fence() -> None:
    content = '```json\n{"rows":[{"sequence":2,"mark":"青禾"}]}\n```'

    assert parse_mark_response(content) == {2: "青禾"}


def test_parse_mark_response_keeps_explicit_blank_rows() -> None:
    content = '{"rows":[{"sequence":1,"mark":"青猫"},{"sequence":2,"mark":""}]}'

    assert parse_mark_response(content) == {1: "青猫", 2: ""}


def test_apply_recognized_marks_only_fills_blank_values() -> None:
    records = [record(1, "青禾月付"), record(2)]

    enriched = apply_recognized_marks(records, {1: "错误覆盖", 2: "青禾"})

    assert [item.mark for item in enriched] == ["青禾月付", "青禾"]
    assert records[1].mark == ""


def test_load_vision_settings_prefers_environment(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "secret-from-env")
    monkeypatch.setenv("DASHSCOPE_MODEL", "qwen-test")

    settings = load_vision_settings(tmp_path)

    assert settings == VisionSettings(
        base_url="https://example.invalid/v1",
        api_key="secret-from-env",
        model="qwen-test",
    )


def test_load_vision_settings_reads_local_settings_file(
    monkeypatch, tmp_path: Path
) -> None:
    for name in ("DASHSCOPE_BASE_URL", "DASHSCOPE_API_KEY", "DASHSCOPE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "settings.json").write_text(
        '{"DASHSCOPE_BASE_URL":"https://example.invalid/v1",'
        '"DASHSCOPE_API_KEY":"local-secret","DASHSCOPE_MODEL":"qwen-local"}',
        encoding="utf-8",
    )

    settings = load_vision_settings(tmp_path)

    assert settings is not None
    assert settings.api_key == "local-secret"
    assert settings.model == "qwen-local"


def test_load_vision_settings_removes_openai_provider_prefix(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "secret")
    monkeypatch.setenv("DASHSCOPE_MODEL", "openai/qwen3.7-plus")

    settings = load_vision_settings(tmp_path)

    assert settings is not None
    assert settings.model == "qwen3.7-plus"


def test_qwen_defers_transient_rate_limit_without_blocking_retry(monkeypatch) -> None:
    calls = []

    def fake_urlopen(_request, timeout):
        calls.append(timeout)
        raise HTTPError("https://example.invalid", 429, "rate limit", {}, None)

    monkeypatch.setattr("trademark_worker.vision.urlopen", fake_urlopen)
    recognizer = QwenVisionRecognizer(VisionSettings("https://example.invalid", "secret"))

    try:
        recognizer.recognize_marks(b"png", [record(1)], "青禾")
    except VisionRecognitionError as exc:
        assert "HTTP 429" in str(exc)
    else:
        raise AssertionError("rate limiting must defer to the task-level retry")
    assert calls == [45]


def test_qwen_does_not_retry_bad_credentials(monkeypatch) -> None:
    calls = []

    def fake_urlopen(_request, timeout):
        calls.append(timeout)
        raise HTTPError("https://example.invalid", 401, "unauthorized", {}, None)

    monkeypatch.setattr("trademark_worker.vision.urlopen", fake_urlopen)
    recognizer = QwenVisionRecognizer(VisionSettings("https://example.invalid", "secret"))

    try:
        recognizer.recognize_marks(b"png", [record(1)], "青禾")
    except VisionRecognitionError as exc:
        assert "HTTP 401" in str(exc)
    else:
        raise AssertionError("invalid credentials must surface as an error")
    assert calls == [45]
