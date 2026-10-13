from __future__ import annotations

import builtins

from PIL import Image

import trademark_worker.local_ocr as local_ocr_module
from trademark_worker.local_ocr import LocalMarkRecognizer


class FakeEngine:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def __call__(self, _image):
        self.calls += 1
        return self.rows, None


class SequencedEngine:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, _image):
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return result, None


def ocr_row(text: str, confidence: float):
    return [[[0, 0], [20, 0], [20, 10], [0, 10]], text, confidence]


def test_local_ocr_accepts_chinese_name_with_separate_pinyin() -> None:
    engine = FakeEngine(
        [ocr_row("扶馨陪诊", 0.994), ocr_row("FUXIN PEIZHEN", 0.999)]
    )
    recognizer = LocalMarkRecognizer(engine)
    image = Image.new("RGB", (240, 100), "white")

    assert recognizer.recognize_mark(image, "扶馨陪诊") == "扶馨陪诊"
    assert engine.calls == 1
    image.close()


def test_local_ocr_preserves_additional_chinese_characters() -> None:
    engine = FakeEngine([ocr_row("快手科技", 0.98)])
    recognizer = LocalMarkRecognizer(engine)
    image = Image.new("RGB", (240, 100), "white")

    assert recognizer.recognize_mark(image, "快手") == "快手科技"
    image.close()


def test_local_ocr_defers_low_confidence_text_to_remote_model() -> None:
    engine = FakeEngine([ocr_row("扶馨陪诊", 0.60)])
    recognizer = LocalMarkRecognizer(engine)
    image = Image.new("RGB", (240, 100), "white")

    assert recognizer.recognize_mark(image, "扶馨陪诊") is None
    image.close()


def test_stable_fuzzy_search_extension_is_resolved_locally() -> None:
    engine = FakeEngine([ocr_row("抖音月付", 0.99)])
    recognizer = LocalMarkRecognizer(engine)
    image = Image.new("RGB", (240, 100), "white")

    assessment = recognizer.assess_mark(image, "抖音")

    assert assessment.text == "抖音月付"
    assert assessment.needs_remote is False
    assert engine.calls == 2
    image.close()


def test_single_character_near_match_is_sent_to_remote_model() -> None:
    engine = FakeEngine([ocr_row("票务免", 0.99)])
    recognizer = LocalMarkRecognizer(engine)
    image = Image.new("RGB", (240, 100), "white")

    assessment = recognizer.assess_mark(image, "票务兔")

    assert assessment.text == "票务免"
    assert assessment.needs_remote is True
    image.close()


def test_conflicting_original_and_enhanced_reads_are_sent_to_remote_model() -> None:
    engine = SequencedEngine(
        [ocr_row("装千里", 0.99)],
        [ocr_row("装干里", 0.99)],
    )
    recognizer = LocalMarkRecognizer(engine)
    image = Image.new("RGB", (240, 100), "white")

    assessment = recognizer.assess_mark(image, "别的商标")

    assert assessment.text == "装干里"
    assert assessment.needs_remote is True
    assert engine.calls == 2
    image.close()


def test_rapidocr_three_output_shape_is_supported() -> None:
    class Output:
        txts = ["itotii"]
        scores = [0.98]

    class OutputEngine:
        calls = 0

        def __call__(self, _image):
            self.calls += 1
            return Output()

    engine = OutputEngine()
    recognizer = LocalMarkRecognizer(engine)
    image = Image.new("RGB", (240, 100), "white")

    assessment = recognizer.assess_mark(image, "itotii")

    assert assessment.text == "itotii"
    assert assessment.needs_remote is False
    assert engine.calls == 1
    image.close()


def test_build_recognizer_does_not_import_rapidocr(monkeypatch) -> None:
    monkeypatch.setattr(local_ocr_module.importlib.util, "find_spec", lambda _name: object())
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "rapidocr":
            raise AssertionError("RapidOCR must remain lazy during worker startup")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    recognizer = local_ocr_module.build_local_ocr_recognizer()

    assert isinstance(recognizer, LocalMarkRecognizer)
    assert recognizer._engine is None
