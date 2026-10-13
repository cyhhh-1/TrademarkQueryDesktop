from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from trademark_worker.imaging.evidence import PixelBox, crop_rows, stack_rows


def solid_png(path: Path, size: tuple[int, int], color: str) -> Path:
    Image.new("RGB", size, color).save(path)
    return path


def coordinate_png(path: Path, width: int, height: int) -> Path:
    image = Image.new("RGB", (width, height))
    for y in range(height):
        for x in range(width):
            image.putpixel((x, y), (x % 256, y % 256, 0))
    image.save(path)
    return path


def test_stack_rows_preserves_order_and_adds_gap(tmp_path: Path) -> None:
    red = solid_png(tmp_path / "red.png", (100, 20), "red")
    blue = solid_png(tmp_path / "blue.png", (80, 30), "blue")

    output = stack_rows([red, blue], tmp_path / "stack.png", gap=12)

    image = Image.open(output)
    assert image.size == (100, 62)
    assert image.getpixel((2, 2))[:3] == (255, 0, 0)
    assert image.getpixel((2, 40))[:3] == (0, 0, 255)
    assert image.getpixel((2, 25))[:3] == (232, 236, 241)


def test_crop_rows_uses_css_to_pixel_scale(tmp_path: Path) -> None:
    source = coordinate_png(tmp_path / "page.png", 200, 100)
    boxes = [PixelBox(x=10, y=10, width=50, height=20, scale=2)]

    cropped_path = crop_rows(source, boxes, tmp_path / "rows")[0]

    cropped = Image.open(cropped_path)
    assert cropped.size == (100, 40)
    assert cropped.getpixel((0, 0))[:2] == (20, 20)


def test_crop_rows_clamps_box_to_image_bounds(tmp_path: Path) -> None:
    source = solid_png(tmp_path / "page.png", (100, 50), "white")
    boxes = [PixelBox(x=80, y=40, width=40, height=20, scale=1)]

    cropped = Image.open(crop_rows(source, boxes, tmp_path / "rows")[0])

    assert cropped.size == (20, 10)


def test_stack_rows_rejects_empty_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="至少一张"):
        stack_rows([], tmp_path / "stack.png")

