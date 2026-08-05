#!/usr/bin/env python3
"""Stage A — collect source PDFs into work/pdf/.

By default this fetches everything listed in pdf-targets.md. Pass --url to
point the pipeline at an arbitrary PDF instead, without editing the table:

    ./scripts/fetch-pdfs.py --url https://example.gov/opinion.pdf
    ./scripts/fetch-pdfs.py --url ~/Downloads/contract.pdf --case "Acme v. Roe"

A --url target is remembered in work/adhoc-targets.json, so every later
stage can find it by id and keep its provenance. Local paths work too.

Network/CPU only: run this before any GPU pod exists. Re-running is free —
files already on disk are skipped unless --force is passed.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from targets import (PDF_DIR, is_remote, load_targets, register_adhoc, select,
                     unregister_adhoc)

# Court and SEC servers both expect a real User-Agent identifying the caller.
USER_AGENT = "unlimited-ocr-demo/1.0 (document OCR pipeline)"


def collect(source: str, dest: Path, timeout: int) -> int:
    """Copy or download source to dest, refusing anything that isn't a PDF."""
    if is_remote(source):
        req = Request(source, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    else:
        body = Path(source).read_bytes()

    if not body.startswith(b"%PDF"):
        raise ValueError(f"not a PDF (starts with {body[:16]!r})")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return len(body)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", action="append", metavar="URL_OR_PATH", default=[],
                        help="PDF URL or local path to process (repeatable); "
                             "replaces the pdf-targets.md list for this run")
    parser.add_argument("--case", default="", help="metadata for --url targets")
    parser.add_argument("--court", default="", help="metadata for --url targets")
    parser.add_argument("--date", default="", help="metadata for --url targets")
    parser.add_argument("--only", nargs="*", metavar="DOC_ID",
                        help="restrict to these document ids")
    parser.add_argument("--force", action="store_true",
                        help="re-download PDFs that are already present")
    parser.add_argument("--timeout", type=int, default=60,
                        help="per-request timeout in seconds (default: 60)")
    parser.add_argument("--print-ids", action="store_true",
                        help="print resolved doc ids on stdout (progress goes to stderr)")
    args = parser.parse_args()

    # With --print-ids the caller is parsing stdout, so chatter goes to stderr.
    stream = sys.stderr if args.print_ids else sys.stdout
    def log(message: str) -> None:
        print(message, file=stream)

    if args.url:
        targets = [register_adhoc(u, args.case, args.court, args.date) for u in args.url]
    else:
        targets = select(load_targets(), args.only)

    failures = 0
    for target in targets:
        dest = PDF_DIR / f"{target['doc_id']}.pdf"
        if dest.is_file() and not args.force:
            log(f"  skip  {target['doc_id']:<28} ({dest.stat().st_size:,} bytes, already fetched)")
        else:
            try:
                size = collect(target["url"], dest, args.timeout)
            except (HTTPError, URLError, ValueError, OSError) as exc:
                print(f"  FAIL  {target['doc_id']:<28} {exc}", file=sys.stderr)
                failures += 1
                # Don't leave a target behind for a --url that never produced
                # a PDF; but keep one whose file is already on disk from an
                # earlier run and merely failed to re-download now.
                if target.get("origin") == "--url" and not dest.is_file():
                    unregister_adhoc(target["doc_id"])
                continue
            log(f"  got   {target['doc_id']:<28} ({size:,} bytes)")

        if args.print_ids:
            print(target["doc_id"])

    log(f"\n{len(targets) - failures}/{len(targets)} PDFs available in {PDF_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
