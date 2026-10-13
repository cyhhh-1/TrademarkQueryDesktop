from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_checksums(path: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        checksum, name = line.split("  ", 1)
        checksums[name] = checksum
    return checksums


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the final desktop release bundle.")
    parser.add_argument("release_dir", type=Path)
    parser.add_argument("--version", default="0.1.29")
    parser.add_argument("--include-manual", action="store_true")
    args = parser.parse_args()

    release_dir = args.release_dir.resolve()
    installer = release_dir / f"商标查询助手_{args.version}_x64-setup.exe"
    bundle = release_dir / f"商标查询助手_{args.version}_release.zip"
    manual_docx = release_dir / f"商标查询助手安装与使用说明_{args.version}.docx"
    manual_pdf = release_dir / f"商标查询助手安装与使用说明_{args.version}.pdf"
    checksum_file = release_dir / "SHA256.txt"
    required = [installer, bundle, checksum_file]
    if args.include_manual:
        required.extend([manual_docx, manual_pdf])
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise AssertionError(f"missing release files: {missing}")

    pdf_page_count = None
    if args.include_manual:
        try:
            import pymupdf as fitz
        except ImportError:  # pragma: no cover - environments without PyMuPDF
            fitz = None
            from pypdf import PdfReader

        with zipfile.ZipFile(manual_docx) as archive:
            if archive.testzip() is not None:
                raise AssertionError("DOCX archive is corrupt")
            document_xml = archive.read("word/document.xml")
            if args.version.encode() not in document_xml:
                raise AssertionError("DOCX version text is missing")

        if fitz is not None:
            with fitz.open(manual_pdf) as document:
                pdf_page_count = document.page_count
                pdf_text = "\n".join(page.get_text() for page in document)
        else:
            document = PdfReader(manual_pdf)
            pdf_page_count = len(document.pages)
            pdf_text = "\n".join(page.extract_text() or "" for page in document.pages)
        if pdf_page_count != 9:
            raise AssertionError(f"unexpected PDF page count: {pdf_page_count}")
        if args.version not in pdf_text:
            raise AssertionError("PDF version text is missing")

    checksums = parse_checksums(checksum_file)
    checksum_targets = [installer]
    if args.include_manual:
        checksum_targets.extend([manual_docx, manual_pdf])
    for path in checksum_targets:
        if checksums.get(path.name) != sha256(path):
            raise AssertionError(f"checksum mismatch: {path.name}")

    expected_files = [installer, checksum_file]
    if args.include_manual:
        expected_files.extend([manual_docx, manual_pdf])
    expected_members = {path.name for path in expected_files}
    with zipfile.ZipFile(bundle) as archive:
        if archive.testzip() is not None:
            raise AssertionError("release ZIP is corrupt")
        members = {Path(name).name for name in archive.namelist() if not name.endswith("/")}
    if members != expected_members:
        raise AssertionError(f"unexpected release ZIP members: {sorted(members)}")

    print(
        json.dumps(
            {
                "version": args.version,
                "installer_sha256": checksums[installer.name],
                "pdf_pages": pdf_page_count,
                "bundle_members": sorted(members),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
