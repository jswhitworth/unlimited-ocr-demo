#!/usr/bin/env python3
"""Stage C — turn raw model output into HTML, a manifest, and a dataset card.

The model emits each document block wrapped in layout markers:

    <|det|>title [[x1,y1,x2,y2]]<|/det|>Some heading text

The README's remove_det() throws those away to get flat text for
benchmarking. For HTML they are worth keeping: the category tells us what
each block *is* (so titles become real headings and tables stay tables) and
the bbox is retained as a data-bbox attribute, preserving the model's layout
grounding in the output.

Category vocabulary is discovered from the output rather than assumed —
anything unrecognised falls back to a paragraph and is reported in the
manifest under det_categories so the first real run tells you what exists.

Pure CPU: re-run this as often as you like without touching a GPU.
"""
from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

import markdown as md

from targets import HTML_DIR, RAW_DIR, TEXT_DIR, WORK, load_targets, select

# category, optional bbox, then the block's first line of content.
#
# Deliberately more tolerant than the regex in the model's README: that one
# uses \[[^\]]*\] for the bbox, which silently fails to match the nested
# [[x1,y1,x2,y2]] form that DeepSeek-OCR-derived models emit (it stops at the
# first ]). Matching "anything up to <|/det|>" handles both conventions, and a
# missing bbox as well.
DET_RE = re.compile(r"<\|det\|>([^<\s]+)\s*([^<]*?)\s*<\|/det\|>(.*)", re.DOTALL)
# Any other special token that survived skip_special_tokens=False.
SPECIAL_TOKEN_RE = re.compile(r"<\|[^|]*\|>")
# The model prefixes the first block of each page with a bare <PAGE> token.
# It sits *in front of* the <|det|> marker, so it has to be stripped before
# matching or the page's opening block silently fails to parse.
PAGE_BREAK_RE = re.compile(r"^\s*<PAGE>\s*")

HEADINGS = {
    "title": "h1", "doc_title": "h1",
    "sec_title": "h2", "section_title": "h2",
    "sub_title": "h3", "subsec_title": "h3", "subsection_title": "h3",
    "para_title": "h4", "paragraph_title": "h4",
}
DROPPED = {"image", "figure", "picture"}          # no pixels in an HTML dump
FURNITURE = {"header", "footer", "page_header", "page_footer",
             "page_number", "page-number"}         # running heads, folios
FOOTNOTES = {"footnote", "foot_note", "page_footnote"}
TABLES = {"table"}
FORMULAS = {"formula", "equation", "isolate_formula"}


def clean(text: str) -> str:
    return SPECIAL_TOKEN_RE.sub("", text).replace("<PAGE>", "").strip()


def parse_blocks(raw: str) -> list[dict]:
    """Split raw model output into {category, bbox, body} blocks."""
    blocks: list[dict] = []
    current: dict | None = None
    page = 0
    starts_page = False

    for line in raw.splitlines():
        line = line.rstrip()
        if PAGE_BREAK_RE.match(line):
            line = PAGE_BREAK_RE.sub("", line, count=1)
            page += 1
            starts_page = True
        if not line.strip():
            continue
        match = DET_RE.match(line)
        if match:
            if current is not None:
                blocks.append(current)
            category = match.group(1).strip()
            bbox = (match.group(2) or "").strip() or None
            content = match.group(3).strip()
            current = {"category": category, "bbox": bbox, "page": max(page, 1),
                       "starts_page": starts_page, "lines": [content] if content else []}
            starts_page = False
            continue
        if current is None:
            # Content before the first marker — treat as untagged body text.
            current = {"category": None, "bbox": None, "page": max(page, 1),
                       "starts_page": starts_page, "lines": []}
            starts_page = False
        current["lines"].append(line)

    if current is not None:
        blocks.append(current)

    for block in blocks:
        block["body"] = clean("\n".join(block.pop("lines")))
    return [b for b in blocks if b["body"]]


def render_block(block: dict, drop_furniture: bool) -> str:
    category = (block["category"] or "text").lower()
    body = block["body"]
    attr = f' data-bbox="{html.escape(block["bbox"])}"' if block["bbox"] else ""
    attr += f' data-page="{block.get("page", 1)}"'

    if category in DROPPED:
        return ""
    if category in FURNITURE:
        if drop_furniture:
            return ""
        return f'<div class="furniture" data-category="{html.escape(category)}"{attr}>{html.escape(body)}</div>'
    if category in HEADINGS:
        tag = HEADINGS[category]
        return f'<{tag}{attr}>{html.escape(body)}</{tag}>'
    if category in FORMULAS:
        return f'<div class="formula"{attr}>{html.escape(body)}</div>'
    if category in TABLES:
        inner = body if body.lstrip().startswith("<table") else md.markdown(body, extensions=["tables"])
        return f'<div class="table-wrap" data-category="table"{attr}>{inner}</div>'

    rendered = md.markdown(body, extensions=["tables", "sane_lists"])
    wrapper_class = "footnote" if category in FOOTNOTES else "block"
    tag = "aside" if category in FOOTNOTES else "section"
    return (f'<{tag} class="{wrapper_class}" data-category="{html.escape(category)}"{attr}>'
            f'{rendered}</{tag}>')


def plain_text(blocks: list[dict]) -> str:
    """Reading-order text, used for scoring against the PDF's text layer.

    Running heads, folios and footnotes are kept here even though the HTML
    renders them as furniture: the PDF's text layer contains them, so
    dropping them from this side of the comparison would understate accuracy
    by penalising the model for text it actually read correctly.
    """
    keep = [b["body"] for b in blocks
            if (b["category"] or "text").lower() not in DROPPED]
    return "\n\n".join(keep)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def similarity(ocr_text: str, truth: str) -> float | None:
    """Character-level similarity against the born-digital text layer."""
    a, b = normalise(ocr_text), normalise(truth)
    if not a or not b:
        return None
    # autojunk must be off: on sequences longer than 200 it treats any
    # element appearing in >1% of positions as junk, which means spaces —
    # and the resulting ratio badly understates similarity for prose.
    return round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 4)


CSS = """
:root { color-scheme: light dark; }
body { max-width: 46rem; margin: 2rem auto; padding: 0 1.25rem;
       font: 16px/1.65 Georgia, "Times New Roman", serif; }
h1 { font-size: 1.6rem; } h2 { font-size: 1.3rem; } h3 { font-size: 1.1rem; }
.provenance { font-family: system-ui, sans-serif; font-size: .8rem;
              border: 1px solid currentColor; border-radius: 6px;
              padding: .75rem 1rem; margin-bottom: 2.5rem; opacity: .75; }
.provenance dl { display: grid; grid-template-columns: max-content 1fr;
                 gap: .15rem .75rem; margin: 0; }
.provenance dt { font-weight: 600; } .provenance dd { margin: 0; }
.furniture { font-family: system-ui, sans-serif; font-size: .75rem;
             opacity: .5; margin: .5rem 0; }
.formula { font-family: ui-monospace, monospace; overflow-x: auto;
           padding: .5rem 0; }
.footnote { font-size: .85rem; opacity: .85;
            border-left: 2px solid currentColor; padding-left: .75rem; }
.table-wrap { overflow-x: auto; }
.page-break { border: 0; border-top: 1px dashed currentColor; opacity: .25;
              margin: 2rem 0; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { border: 1px solid currentColor; padding: .35rem .5rem; text-align: left; }
"""


def build_html(target: dict, meta: dict, body_html: str, score: float | None) -> str:
    source = target.get("url", "")
    escaped = html.escape(source)
    # Ad-hoc targets may be local files, which shouldn't render as links.
    source_html = (f'<a href="{escaped}">{escaped}</a>'
                   if source.startswith(("http://", "https://")) else f"<code>{escaped}</code>")

    rows = {
        "Document": target.get("case", ""),
        "Court": target.get("court", ""),
        "Decided": target.get("date", ""),
        "Source PDF": source_html,
        "Pages": meta.get("pages", ""),
        "OCR model": meta.get("model", "baidu/Unlimited-OCR"),
        "Mode": meta.get("image_mode", "base (multi-image input)"),
        "Prompt": html.escape(str(meta.get("prompt", ""))),
        "Generated": date.today().isoformat(),
    }
    if score is not None:
        rows["Text-layer match"] = f"{score:.2%}"

    # Ad-hoc documents often have no case/court metadata — omit empty rows
    # rather than printing blank labels.
    dl = "\n".join(f"    <dt>{k}</dt><dd>{v}</dd>"
                   for k, v in rows.items() if str(v).strip())
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(target.get("case") or target["doc_id"])}</title>
<style>{CSS}</style>
</head>
<body>
<div class="provenance">
  <dl>
{dl}
  </dl>
</div>
{body_html}
</body>
</html>
"""


DATASET_CARD = """---
license: other
license_name: us-government-edict-public-domain
task_categories:
- image-to-text
language:
- en
tags:
- ocr
- document-parsing
- legal
- unlimited-ocr
---

# OCR Demo Documents — state court opinions

Public state appellate opinions parsed with
[baidu/Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR), served on
vLLM. Each document is published as the original PDF, the raw model output,
the converted HTML, and the PDF's own text layer.

## Layout

| Path | Contents |
|---|---|
| `pdf/` | Source PDFs, exactly as downloaded from the court |
| `pages/` | Page images fed to the model (PNG, {dpi} dpi) |
| `raw/` | Unmodified model output, including `<\\|det\\|>` layout markers |
| `html/` | Converted HTML — the deliverable |
| `text/` | The PDF's embedded text layer, used as ground truth |
| `manifest.jsonl` | One row per document: provenance, parameters, scores |

## Documents

{table}

## Method

Pages are rendered at {dpi} dpi with PyMuPDF, then all pages of a document
are sent in a **single** `Multi page parsing.` request — the long-horizon
capability the model is named for. Multi-image input puts the model in base
mode; `window_size` is 1024 (rather than the single-image 128) and
`skip_special_tokens` is false so the layout markers survive.

The `<|det|>` markers are parsed rather than stripped: block categories drive
the HTML structure (titles become headings, tables stay tables) and bounding
boxes are preserved as `data-bbox` attributes.

## On the accuracy numbers

These opinions are **born-digital** PDFs with real embedded text, which is
why a text-layer match score is available at all — it is a genuine
character-level comparison, not an estimate. It also means they are an easy
OCR target: they validate the pipeline end to end, but they do not
demonstrate performance on scanned or degraded documents.

## Licensing

US state court opinions are government edicts and are not subject to
copyright. The pipeline that produced the derived files lives in the
accompanying demo repository.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", metavar="DOC_ID",
                        help="restrict to these document ids")
    parser.add_argument("--drop-furniture", action="store_true",
                        help="omit running heads, footers and page numbers")
    parser.add_argument("--dpi", type=int, default=300,
                        help="dpi to record in the dataset card (default: 300)")
    args = parser.parse_args()

    targets = select(load_targets(), args.only)
    HTML_DIR.mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    all_categories: set[str] = set()
    missing = 0

    for target in targets:
        doc_id = target["doc_id"]
        raw_path = RAW_DIR / f"{doc_id}.txt"
        if not raw_path.is_file():
            print(f"  skip  {doc_id:<28} no raw output — run ocr-pdfs.py first")
            missing += 1
            continue

        meta_path = RAW_DIR / f"{doc_id}.meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}

        blocks = parse_blocks(raw_path.read_text(encoding="utf-8"))
        categories = sorted({(b["category"] or "untagged").lower() for b in blocks})
        all_categories.update(categories)
        unknown = [c for c in categories if c not in
                   HEADINGS.keys() | DROPPED | FURNITURE | FOOTNOTES | TABLES | FORMULAS
                   | {"text", "para", "paragraph", "list", "untagged"}]

        rendered: list[str] = []
        for block in blocks:
            fragment = render_block(block, args.drop_furniture)
            if not fragment:
                continue
            # Keep the model's page boundaries visible in the output.
            if block.get("starts_page") and rendered:
                rendered.append(f'<hr class="page-break" data-page="{block.get("page")}">')
            rendered.append(fragment)
        body_html = "\n".join(rendered)

        truth_path = TEXT_DIR / f"{doc_id}.txt"
        truth = truth_path.read_text(encoding="utf-8") if truth_path.is_file() else ""
        score = similarity(plain_text(blocks), truth)

        html_path = HTML_DIR / f"{doc_id}.html"
        html_path.write_text(build_html(target, meta, body_html, score), encoding="utf-8")

        manifest.append({
            **target,
            "pages": meta.get("pages"),
            "model": meta.get("model", "baidu/Unlimited-OCR"),
            "prompt": meta.get("prompt"),
            "image_mode": meta.get("image_mode"),
            "window_size": meta.get("window_size"),
            "ngram_size": meta.get("ngram_size"),
            "finish_reason": meta.get("finish_reason"),
            "ocr_seconds": meta.get("elapsed_seconds"),
            "raw_chars": len(raw_path.read_text(encoding="utf-8")),
            "blocks": len(blocks),
            "det_categories": categories,
            "unknown_categories": unknown,
            "text_layer_match": score,
            "html": f"html/{doc_id}.html",
            "pdf": f"pdf/{doc_id}.pdf",
        })

        note = f"  ** unrecognised categories: {', '.join(unknown)} **" if unknown else ""
        score_text = f"{score:.2%} vs text layer" if score is not None else "no ground truth"
        print(f"  html  {doc_id:<28} {len(blocks)} blocks, {score_text}{note}")

    if manifest:
        with (WORK / "manifest.jsonl").open("w", encoding="utf-8") as fh:
            for row in manifest:
                fh.write(json.dumps(row) + "\n")

        table = "\n".join(
            f"| `{r['doc_id']}` | {r['case']} | {r['court']} | {r['pages'] or '?'} | "
            + (f"{r['text_layer_match']:.2%}" if r["text_layer_match"] is not None else "—")
            + " |"
            for r in manifest
        )
        header = "| id | Case | Court | Pages | Text-layer match |\n|---|---|---|---|---|\n"
        (WORK / "README.md").write_text(
            DATASET_CARD.format(dpi=args.dpi, table=header + table), encoding="utf-8"
        )
        print(f"\n{len(manifest)} documents → {HTML_DIR}")
        print(f"Manifest and dataset card written to {WORK}")
        if all_categories:
            print(f"Block categories seen: {', '.join(sorted(all_categories))}")

    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
