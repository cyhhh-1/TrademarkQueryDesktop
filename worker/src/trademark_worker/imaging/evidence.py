"""Crop result rows and stack them into workbook-ready evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence

from PIL import Image


@dataclass(frozen=True, slots=True)
class PixelBox:
    x: float
    y: float
    width: float
    height: float
    scale: float = 1.0


def crop_rows(
    page_png: Path, boxes: Sequence[PixelBox], output_dir: Path
) -> list[Path]:
    page_png = Path(page_png)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output: list[Path] = []
    with Image.open(page_png) as source:
        image = source.convert("RGB")
        for index, box in enumerate(boxes, start=1):
            if box.width <= 0 or box.height <= 0 or box.scale <= 0:
                raise ValueError("裁剪框尺寸必须为正数")
            left = max(0, round(box.x * box.scale))
            top = max(0, round(box.y * box.scale))
            right = min(image.width, round((box.x + box.width) * box.scale))
            bottom = min(image.height, round((box.y + box.height) * box.scale))
            if right <= left or bottom <= top:
                raise ValueError("裁剪框位于截图范围外")
            target = output_dir / f"row-{index:04d}.png"
            image.crop((left, top, right, bottom)).save(
                target, format="PNG", optimize=True
            )
            output.append(target)
    return output


def stack_rows(
    images: Sequence[Path], output: Path, gap: int = 12
) -> Path:
    if not images:
        raise ValueError("至少一张图片才能拼接")
    if gap < 0:
        raise ValueError("图片间距不能为负数")
    opened: list[Image.Image] = []
    try:
        opened = [Image.open(path).convert("RGB") for path in images]
        width = max(image.width for image in opened)
        height = sum(image.height for image in opened) + gap * (len(opened) - 1)
        canvas = Image.new("RGB", (width, height), (232, 236, 241))
        y = 0
        for image in opened:
            canvas.paste(image, (0, y))
            y += image.height + gap
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output, format="PNG", optimize=True)
        return output
    finally:
        for image in opened:
            image.close()

