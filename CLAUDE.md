# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A cost-capped demo pipeline that OCRs public PDFs with [baidu/Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR) served by vLLM on an ephemeral RunPod GPU pod, converts the model's layout markers to HTML, and publishes the result as a Hugging Face dataset. There is no application to build or test — the repo is a set of shell + Python stage scripts orchestrated by `scripts/run-pipeline.sh`.

`DEPLOYMENT.md` is the full writeup (model sizing, GPU price analysis, request-format rationale). Read it before changing deploy or request parameters.

## Commands

```bash
./scripts/run-pipeline.sh --prep-only          # Stage A only — fetch + rasterize, no GPU spend
./scripts/run-pipeline.sh --local-only         # rebuild HTML from existing work/raw/, no pod
./scripts/run-pipeline.sh                      # full run: prep → deploy → OCR → teardown → HTML
./scripts/run-pipeline.sh --only 2968s18       # restrict to document ids from pdf-targets.md
./scripts/run-pipeline.sh --url <pdf-or-path>  # ad-hoc document; repeatable, no table edit
./scripts/run-pipeline.sh --upload             # ...and publish to Hugging Face

./scripts/check-gpu-prices.sh COMMUNITY        # live $/hr + availability, cheapest first
./scripts/deploy-pod.sh                        # create pod, write id to .runpod/pod-id
./scripts/pod-status.sh                        # status, cost/hr, proxy URL
./scripts/test-ocr.sh <image> [prompt]         # single-image smoke test against the pod
./scripts/teardown-pod.sh                      # terminate — run the moment you're done
```

Individual stages are runnable directly (`python3 scripts/rasterize.py --only <id>`, etc.); each takes `--only` and most take `--force`. `python3 scripts/targets.py` prints the resolved target list.

**Iterating on Stage C never needs a GPU** — `--local-only` (or `det-to-html.py` directly) replays `work/raw/`.

Requirements: `curl`, `jq`, `python3` with `pymupdf` + `markdown`, and the `hf` CLI (`hf auth login`) for upload. There is no requirements file or virtualenv — deps are expected on the ambient `python3`.

## Cost discipline — the central constraint

The project is capped at **$10 of RunPod credit**, and boot (~5–10 min pulling an 8.8 GB image + 6.67 GB of weights) dominates inference (~2–3 min). Every design choice follows from that, and changes should preserve these properties:

- **All non-GPU work stays outside the billing window.** Fetch/rasterize run before deploy; HTML/manifest/upload run after teardown. `run-pipeline.sh` calls `cleanup` explicitly before Stage C rather than waiting for the trap.
- **Teardown is on a `trap EXIT INT TERM`.** A pod *this run created* is terminated even on crash or Ctrl-C; a pod that was already running when the script started is reused and left alone. `--keep-pod` opts out.
- **Raw output is checkpointed before post-processing.** `ocr-pdfs.py` writes `work/raw/<doc_id>.txt` as soon as the response lands and skips documents that already have one, so a Stage C bug never costs a second boot.
- **Nothing publishes implicitly** — upload requires an explicit `--upload`.

## Architecture

Stages, all keyed on a **document id** (`doc_id`) that flows through every directory:

| Stage | Script | GPU | Output |
|---|---|---|---|
| — | `targets.py` | no | Target list (module, not a stage) |
| A | `fetch-pdfs.py` | no | `work/pdf/<id>.pdf` |
| A | `rasterize.py` | no | `work/pages/<id>/page_NNNN.png` @300dpi, `work/text/<id>.txt` |
| B | `ocr-pdfs.py` | **yes** | `work/raw/<id>.txt`, `<id>.meta.json` |
| C | `det-to-html.py` | no | `work/html/<id>.html`, `work/manifest.jsonl`, `work/README.md` |
| D | `upload-dataset.sh` | no | HF dataset `jswhitworth/ocr-demo-documents` |

`targets.py` is the single source of truth for what gets processed and where it lives — every path constant (`PDF_DIR`, `PAGES_DIR`, `RAW_DIR`, …) is defined there and imported by the other scripts. Targets come from two merged sources: the curated `pdf-targets.md` table (parsed generically by markdown table headers) and `work/adhoc-targets.json` (documents registered by `--url`, remembered so later stages can resolve them by id). A `--url` matching a table entry resolves to that target instead of duplicating it; a `--url` whose download fails is unregistered rather than left as a broken target.

`work/` is entirely derived and gitignored. `.runpod/pod-id` is local state, also gitignored.

## Model / API specifics that are easy to get wrong

- **RunPod's REST API is split across two hosts.** Pod CRUD → `https://rest.runpod.io/v1/pods`. GPU catalog/pricing → `https://api.runpod.io/v2/catalog/gpus`. Hitting `rest.runpod.io/v2/catalog/...` returns a silent 301 to the docs site, not an error.
- **`RUNPOD_API_KEY` comes from the macOS Keychain** via `source scripts/load-env.sh` (`security find-generic-password -s runpod-api-key`). Never introduce a `.env` with real key material.
- **The literal `<image>` prompt prefix is mandatory** — omitting it produces empty output. Single-image prompt is `<image>document parsing.`; multi-page is `<image>Multi page parsing.`
- **`skip_special_tokens: false`** must stay set, or the `<|det|>` layout markers the HTML stage depends on are stripped.
- **`window_size` differs by mode**: 128 for a single image (`test-ocr.sh`), **1024** for multi-page (`ocr-pdfs.py`). Sending multiple images auto-switches the model from gundam to base mode — there is no image-mode flag.
- **The README's bbox regex is wrong.** `\[[^\]]*\]` stops at the first `]` and misses nested `[[x1,y1,x2,y2]]`. `det-to-html.py` matches up to `<|/det|>` instead.
- **`SequenceMatcher(..., autojunk=False)`** in the scoring code is deliberate: the default treats spaces as junk in long strings and badly understates prose similarity.
- **`det-to-html.py` discovers block categories rather than assuming them** — unrecognised ones render as paragraphs and are surfaced under `unknown_categories` in the manifest. Extend the category sets there rather than hardcoding assumptions.
- **Deploy uses a priority-ordered GPU fallback list** (A5000 → A4000 → 3090 → 4090) on Community Cloud, because single-card stock frequently reads LOW/NONE. If placement fails, re-check `check-gpu-prices.sh` before editing the list.
