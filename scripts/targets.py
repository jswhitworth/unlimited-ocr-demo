#!/usr/bin/env python3
"""The pipeline's list of source documents.

Targets come from two places, and every stage reads both:

  * pdf-targets.md — the curated table, for the standing demo set;
  * work/adhoc-targets.json — documents registered on the fly by passing
    --url to fetch-pdfs.py or run-pipeline.sh, so you can point the pipeline
    at any PDF without editing the table.

Ad-hoc entries are remembered after registration, which is what lets the
later stages (rasterize, OCR, HTML, manifest) find them by id and keeps
provenance attached to the output.

Run this file directly to print the current target list.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGETS_MD = REPO_ROOT / "pdf-targets.md"
WORK = REPO_ROOT / "work"

PDF_DIR = WORK / "pdf"
PAGES_DIR = WORK / "pages"
TEXT_DIR = WORK / "text"
RAW_DIR = WORK / "raw"
HTML_DIR = WORK / "html"

ADHOC_JSON = WORK / "adhoc-targets.json"

PDF_URL_RE = re.compile(r"https?://\S+?\.pdf", re.IGNORECASE)
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(name: str) -> str:
    """Make a string safe to use as a filename and a repo path segment."""
    return _SLUG_RE.sub("-", name).strip("-.") or "document"


def derive_doc_id(source: str, taken: dict[str, str]) -> str:
    """Stable id for a source, disambiguated when basenames collide.

    The PDF's own basename is readable and traceable, but two different
    sources can easily both be named opinion.pdf — so a short hash of the
    source is appended only when that actually happens.
    """
    base = slugify(Path(urlparse(source).path or source).stem)
    if taken.get(base) in (None, source):
        return base
    return f"{base}-{hashlib.sha1(source.encode()).hexdigest()[:6]}"


def is_remote(source: str) -> bool:
    return urlparse(source).scheme in ("http", "https")


def _table_rows(md: str):
    """Yield {header: cell} dicts for the data rows of every table in md."""
    header: list[str] | None = None
    for line in md.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            header = None  # any non-table line ends the current table
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and set("".join(cells)) <= set("-: "):
            continue  # |---|---| separator row
        if header is None:
            header = [c.lower() for c in cells]
            continue
        yield dict(zip(header, cells))


def load_md_targets(path: Path = TARGETS_MD) -> list[dict]:
    """Return one dict per PDF listed in the curated targets table."""
    if not path.is_file():
        return []

    targets: list[dict] = []
    seen: set[str] = set()
    for row in _table_rows(path.read_text(encoding="utf-8")):
        match = PDF_URL_RE.search(row.get("url", ""))
        if not match:
            continue
        url = match.group(0)
        # The PDF's own basename is already unique and traceable back to the
        # source, which makes it a better id than anything we'd invent.
        doc_id = Path(urlparse(url).path).stem
        if doc_id in seen:
            raise SystemExit(f"Duplicate document id {doc_id!r} in {path.name}")
        seen.add(doc_id)
        targets.append(
            {
                "doc_id": doc_id,
                "url": url,
                "court": row.get("court", ""),
                "case": row.get("case", ""),
                "date": row.get("date", ""),
                "listed_pages": row.get("pages", ""),
                "origin": "pdf-targets.md",
            }
        )

    return targets


def load_adhoc() -> list[dict]:
    """Documents registered via --url on a previous run."""
    if not ADHOC_JSON.is_file():
        return []
    try:
        return json.loads(ADHOC_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{ADHOC_JSON} is corrupt ({exc}). Delete it to reset.")


def register_adhoc(source: str, case: str = "", court: str = "", date: str = "") -> dict:
    """Add (or update) an ad-hoc target and return it.

    `source` may be an http(s) URL or a local file path.
    """
    if not is_remote(source):
        resolved = Path(source).expanduser().resolve()
        if not resolved.is_file():
            raise SystemExit(f"Not a URL and not an existing file: {source}")
        source = str(resolved)

    # A URL that's already in the curated table is that target, not a new one.
    md_targets = load_md_targets()
    for target in md_targets:
        if target["url"] == source:
            return target

    entries = load_adhoc()
    for entry in entries:
        if entry["url"] == source:
            # Re-registering is how you attach or correct metadata.
            for key, value in (("case", case), ("court", court), ("date", date)):
                if value:
                    entry[key] = value
            _save_adhoc(entries)
            return entry

    taken = {t["doc_id"]: t["url"] for t in load_md_targets() + entries}
    entry = {
        "doc_id": derive_doc_id(source, taken),
        "url": source,
        "court": court,
        "case": case,
        "date": date,
        "listed_pages": "",
        "origin": "--url",
    }
    entries.append(entry)
    _save_adhoc(entries)
    return entry


def unregister_adhoc(doc_id: str) -> None:
    """Drop an ad-hoc target again.

    Used to roll back a registration whose download turned out to fail, so a
    bad --url doesn't leave a permanent target behind.
    """
    entries = load_adhoc()
    remaining = [e for e in entries if e["doc_id"] != doc_id]
    if len(remaining) != len(entries):
        _save_adhoc(remaining)


def _save_adhoc(entries: list[dict]) -> None:
    ADHOC_JSON.parent.mkdir(parents=True, exist_ok=True)
    ADHOC_JSON.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def load_targets(path: Path = TARGETS_MD) -> list[dict]:
    """Curated table plus everything registered via --url."""
    targets = load_md_targets(path)
    known = {t["doc_id"] for t in targets}
    for entry in load_adhoc():
        if entry["doc_id"] not in known:
            targets.append(entry)
            known.add(entry["doc_id"])

    if not targets:
        raise SystemExit(
            f"No targets. Add a row to {path.name}, or pass --url <pdf-url>."
        )
    return targets


def select(targets: list[dict], only: list[str] | None) -> list[dict]:
    """Filter targets down to an explicit --only list of doc ids."""
    if not only:
        return targets
    by_id = {t["doc_id"]: t for t in targets}
    unknown = [d for d in only if d not in by_id]
    if unknown:
        raise SystemExit(
            f"Unknown doc id(s): {', '.join(unknown)}\n"
            f"Known: {', '.join(by_id)}"
        )
    return [by_id[d] for d in only]


if __name__ == "__main__":
    json.dump(load_targets(), sys.stdout, indent=2)
    print()
