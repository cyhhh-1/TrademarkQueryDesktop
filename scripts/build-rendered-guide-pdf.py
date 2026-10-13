from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas


def page_number(path: Path) -> int:
    return int(path.stem.rsplit("-", 1)[1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output_pdf", type=Path)
    parser.add_argument("--prefix", default="guide-page")
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    pages = sorted(input_dir.glob(f"{args.prefix}-*.emf"), key=page_number)
    if not pages:
        raise RuntimeError(f"No EMF pages found in {input_dir}")

    png_pages: list[Path] = []
    for emf_path in pages:
        png_path = emf_path.with_suffix(".png")
        with Image.open(emf_path) as image:
            image.load(dpi=200)
            image.convert("RGB").save(png_path, format="PNG", optimize=True)
        png_pages.append(png_path)

    args.output_pdf.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(args.output_pdf), pagesize=A4)
    page_width, page_height = A4
    for png_path in png_pages:
        canvas.drawImage(str(png_path), 0, 0, width=page_width, height=page_height)
        # Keep the version machine-readable without changing the rendered page.
        text = canvas.beginText(0, 0)
        text.setTextRenderMode(3)
        text.setFont("Helvetica", 1)
        text.textLine(args.version)
        canvas.drawText(text)
        canvas.showPage()
    canvas.save()
    print(f"Rendered {len(png_pages)} pages to {args.output_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
