#!/usr/bin/env python3
"""Stage A — render each source PDF to page PNGs and extract its text layer.

The page images (work/pages/<doc_id>/page_NNNN.png) are what actually gets
sent to the model; 300 dpi matches the pdf_to_images() helper in the
Unlimited-OCR README.

The extracted text layer (work/text/<doc_id>.txt) is free ground truth:
these court opinions are born-digital, so the OCR output can be scored
against the PDF's own text rather than eyeballed. That also means they are
an *easy* OCR target — good for validating the pipeline, not for proving
the model handles degraded scans.

Still CPU-only: run before deploying a pod.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fitz  # PyMuPDF

from targets import PAGES_DIR, PDF_DIR, TEXT_DIR, load_targets, select


def page_paths(doc_id: str) -> list[Path]:
    """Rendered page images for doc_id, in page order."""
    return sorted((PAGES_DIR / doc_id).glob("page_*.png"))


def rasterize(pdf_path: Path, doc_id: str, dpi: int, force: bool) -> tuple[int, bool]:
    """Render pages + extract text. Returns (page_count, did_work)."""
    out_dir = PAGES_DIR / doc_id
    text_path = TEXT_DIR / f"{doc_id}.txt"

    with fitz.open(pdf_path) as doc:
        page_count = doc.page_count
        already = len(page_paths(doc_id)) == page_count and text_path.is_file()
        if already and not force:
            return page_count, False

        out_dir.mkdir(parents=True, exist_ok=True)
        text_path.parent.mkdir(parents=True, exist_ok=True)

        # Rendering at dpi/72 scale; the model downsamples to its own base
        # resolution, so oversampling here is deliberate — downscaling from
        # 300 dpi beats rendering straight to the model's input size.
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        chunks = []
        for index, page in enumerate(doc, start=1):
            page.get_pixmap(matrix=matrix).save(out_dir / f"page_{index:04d}.png")
            chunks.append(page.get_text())

    # Stale pages from a previous run at a different page count would corrupt
    # the multi-page request, so drop anything beyond the current count.
    for extra in page_paths(doc_id)[page_count:]:
        extra.unlink()

    text_path.write_text("\n\f\n".join(chunks), encoding="utf-8")
    return page_count, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", metavar="DOC_ID",
                        help="restrict to these document ids")
    parser.add_argument("--dpi", type=int, default=300,
                        help="render resolution (default: 300, per the model's README)")
    parser.add_argument("--force", action="store_true",
                        help="re-render even if page images already exist")
    args = parser.parse_args()

    targets = select(load_targets(), args.only)
    missing = 0
    total_pages = 0

    for target in targets:
        doc_id = target["doc_id"]
        pdf_path = PDF_DIR / f"{doc_id}.pdf"
        if not pdf_path.is_file():
            print(f"  FAIL  {doc_id:<28} no PDF at {pdf_path} — run fetch-pdfs.py first",
                  file=sys.stderr)
            missing += 1
            continue

        pages, did_work = rasterize(pdf_path, doc_id, args.dpi, args.force)
        total_pages += pages
        verb = "render" if did_work else "skip"
        chars = len((TEXT_DIR / f"{doc_id}.txt").read_text(encoding="utf-8"))
        print(f"  {verb:<6}{doc_id:<28} {pages} pages @ {args.dpi}dpi, "
              f"{chars:,} chars of text-layer ground truth")

    print(f"\n{total_pages} page images ready in {PAGES_DIR}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
