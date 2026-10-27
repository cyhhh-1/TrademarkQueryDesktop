"""Fast staged OCR for CNIPA trademark cells.

RapidOCR stays local and handles clear results. Only close mismatches, weak
reads, or conflicting reads are escalated to the configured multimodal model.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import re
import sys
from typing import Any, Protocol

from PIL import Image, ImageEnhance, ImageOps

from trademark_worker.normalization import chinese_mark_text, marks_equal, normalize_mark


def _load_frozen_cv2_extension() -> None:
    """Load OpenCV's native extension without its external source bootstrap."""

    if not getattr(sys, "frozen", False) or "cv2" in sys.modules:
        return
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    cv2_dir = bundle_root / "cv2"
    candidates = sorted(cv2_dir.glob("cv2*.pyd"))
    if not candidates:
        raise ImportError(f"Frozen OpenCV extension not found under {cv2_dir}")
    spec = importlib.util.spec_from_file_location("cv2", candidates[0])
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create OpenCV loader for {candidates[0]}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["cv2"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("cv2", None)
        raise


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    confidence: float


@dataclass(frozen=True, slots=True)
class LocalMarkAssessment:
    """A local OCR result and whether a remote model must verify it."""

    text: str | None
    needs_remote: bool


class OcrEngine(Protocol):
    def __call__(self, image: Any) -> Any: ...


class LocalMarkRecognizer:
    def __init__(
        self,
        engine: OcrEngine | None = None,
        *,
        accepted_confidence: float = 0.88,
    ) -> None:
        self._engine = engine
        self.accepted_confidence = accepted_confidence

    def _require_engine(self) -> OcrEngine:
        if self._engine is None:
            _load_frozen_cv2_extension()
            from rapidocr import RapidOCR

            # RapidOCR 3.9 ships lightweight PP-OCRv6 small models. Explicit
            # thread limits keep long overnight jobs responsive on office PCs.
            self._engine = RapidOCR(
                params={
                    "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                    "Global.log_level": "error",
                }
            )
        return self._engine

    def recognize_mark(self, image: Image.Image, expected_mark: str) -> str | None:
        """Compatibility wrapper for callers that only need trusted OCR text."""

        assessment = self.assess_mark(image, expected_mark)
        return assessment.text if not assessment.needs_remote else None

    def assess_mark(
        self, image: Image.Image, expected_mark: str
    ) -> LocalMarkAssessment:
        """Read locally and decide whether a remote model must verify the row."""

        first = self._read(image, expected_mark)
        if first is not None and marks_equal(first.text, expected_mark):
            return LocalMarkAssessment(expected_mark.strip(), False)

        enhanced = self._enhance(image)
        try:
            second = self._read(enhanced, expected_mark)
        finally:
            enhanced.close()
        if second is not None and marks_equal(second.text, expected_mark):
            return LocalMarkAssessment(expected_mark.strip(), False)

        if first is None or second is None:
            candidate = second or first
            return LocalMarkAssessment(candidate.text if candidate else None, True)

        first_key = self._comparison_text(first.text, expected_mark)
        second_key = self._comparison_text(second.text, expected_mark)
        expected_key = self._comparison_text(expected_mark, expected_mark)
        if not first_key or first_key != second_key:
            return LocalMarkAssessment(second.text, True)

        if self._clearly_different(first_key, expected_key):
            return LocalMarkAssessment(second.text, False)
        return LocalMarkAssessment(second.text, True)

    def _read(self, image: Image.Image, expected_mark: str) -> OcrLine | None:
        import numpy as np

        result = self._require_engine()(np.asarray(image.convert("RGB"))[:, :, ::-1])
        accepted = [
            line
            for line in self._lines(result)
            if line.confidence >= self.accepted_confidence
        ]
        for line in accepted:
            if marks_equal(line.text, expected_mark):
                return OcrLine(expected_mark.strip(), line.confidence)

        expected_han = chinese_mark_text(expected_mark)
        if expected_han:
            chinese = [
                OcrLine(chinese_mark_text(line.text), line.confidence)
                for line in accepted
                if chinese_mark_text(line.text)
            ]
            return max(chinese, key=lambda line: (len(line.text), line.confidence), default=None)

        textual = [line for line in accepted if line.text.strip()]
        return max(
            textual,
            key=lambda line: (len(line.text.strip()), line.confidence),
            default=None,
        )

    @staticmethod
    def _enhance(image: Image.Image) -> Image.Image:
        width = max(1, image.width * 2)
        height = max(1, image.height * 2)
        grayscale = ImageOps.grayscale(image)
        try:
            contrasted = ImageOps.autocontrast(grayscale, cutoff=1)
            try:
                boosted = ImageEnhance.Contrast(contrasted).enhance(1.5)
                try:
                    return boosted.resize((width, height), Image.Resampling.LANCZOS)
                finally:
                    boosted.close()
            finally:
                contrasted.close()
        finally:
            grayscale.close()

    @staticmethod
    def _comparison_text(observed: str, expected: str) -> str:
        normalized_expected = normalize_mark(expected)
        if chinese_mark_text(expected) and not re.search(
            r"[a-z0-9]", normalized_expected, re.IGNORECASE
        ):
            return chinese_mark_text(observed)
        return re.sub(r"\s+", "", normalize_mark(observed))

    @staticmethod
    def _clearly_different(observed: str, expected: str) -> bool:
        if not observed or not expected or observed == expected:
            return False
        # Fuzzy searches commonly return extensions such as 云杉商城 or
        # 云杉月付. A stable strict extension is unambiguously another mark.
        if expected in observed and len(observed) > len(expected):
            return True
        return LocalMarkRecognizer._edit_distance(observed, expected) >= 2

    @staticmethod
    def _edit_distance(left: str, right: str) -> int:
        previous = list(range(len(right) + 1))
        for row, left_char in enumerate(left, start=1):
            current = [row]
            for column, right_char in enumerate(right, start=1):
                current.append(
                    min(
                        current[-1] + 1,
                        previous[column] + 1,
                        previous[column - 1] + (left_char != right_char),
                    )
                )
            previous = current
        return previous[-1]

    @staticmethod
    def _lines(result: Any) -> list[OcrLine]:
        # RapidOCR 3.x returns RapidOCROutput. The tuple branch keeps injected
        # test engines and older cached environments compatible.
        if hasattr(result, "txts") and hasattr(result, "scores"):
            texts = list(result.txts or [])
            scores = list(result.scores or [])
            return [
                OcrLine(str(text).strip(), float(score))
                for text, score in zip(texts, scores, strict=False)
                if str(text or "").strip()
            ]

        rows = result[0] if isinstance(result, tuple) and result else result
        output: list[OcrLine] = []
        for row in rows or []:
            if len(row) < 3:
                continue
            try:
                confidence = float(row[2])
            except (TypeError, ValueError):
                continue
            text = str(row[1] or "").strip()
            if text:
                output.append(OcrLine(text, confidence))
        return output


def build_local_ocr_recognizer() -> LocalMarkRecognizer | None:
    if importlib.util.find_spec("rapidocr") is None:
        return None
    return LocalMarkRecognizer()
