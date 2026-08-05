# Unlimited-OCR — RunPod Deployment Guide

Source: [baidu/Unlimited-OCR](https://github.com/baidu/Unlimited-OCR) · [baidu/Unlimited-OCR on Hugging Face](https://huggingface.co/baidu/Unlimited-OCR)

Budget target: **$10 in RunPod credits.** Everything below is chosen to stretch that as far as possible.

## 1. Model size & hardware sizing

| Property | Value |
|---|---|
| Total parameters | **3.34B** (3336.1M) |
| Weight file | Single safetensors shard, **6.67 GB** in bf16 (`model.safetensors.index.json` → `total_size: 6,672,212,480`) |
| Architecture | `UnlimitedOCRForCausalLM` — DeepSeek-OCR-style MoE decoder + hybrid vision encoder ("DeepEncoder") |
| Decoder | 12 layers, hidden size 1280, **64 routed experts + 2 shared experts, 6 active per token** (sparse MoE — active compute per token is far below the 3.34B total, similar to DeepSeek-OCR's ~570M-active design) |
| Vision encoder | SAM-B (global view) + CLIP-L (local tiles) hybrid, 1024 px base resolution; supports `base` (single view) and `gundam` (tiled crops for dense/long documents) modes |
| Context length | 32,768 tokens |
| Precision | bf16 |
| Official minimum | vLLM recipe states **"a single GPU with ≥8 GB VRAM is enough for BF16 inference"** |
| vLLM server image | `vllm/vllm-openai:unlimited-ocr` — **8.8 GB compressed** on Docker Hub (checked directly against the registry manifest) |

**Why the practical target is higher than 8 GB VRAM:** that figure covers the weights alone. A real demo also needs headroom for KV cache at up to 32K context, `gundam` tiling (more vision tokens per page), multi-page/PDF batches, and vLLM's own activation overhead.

## 2. GPU choice — optimized for $10 of credit

RunPod Pod pricing is **live/market-based**, not fixed — the table below is a real pull from RunPod's pricing API on 2026-08-05, filtered to GPUs with enough VRAM. `scripts/check-gpu-prices.sh` re-runs this same query so you can confirm current rates before deploying.

| GPU | VRAM | Community Cloud $/hr | Hours on $10 |
|---|---|---|---|
| **RTX A5000** | 24 GB | **~$0.16** | **~62 hrs** |
| RTX A4000 | 16 GB | ~$0.17 | ~58 hrs |
| RTX 3090 | 24 GB | ~$0.22 | ~45 hrs |
| RTX 4090 | 24 GB | ~$0.34 | ~29 hrs |

**Community Cloud is roughly 30–50% cheaper than Secure Cloud for the same card** (e.g. A4000 is $0.17/hr on Community vs $0.25/hr on Secure). The tradeoff is variable reliability and fluctuating stock — RunPod stopped onboarding new Community Cloud hosts, so any single GPU model can show low/no availability at a given moment. That's why `deploy-pod.sh` requests a **priority-ordered fallback list** (A5000 → A4000 → 3090 → 4090) instead of one fixed GPU: RunPod tries each in order and uses the first one with capacity.

Any of these comfortably clears the model's 8 GB floor with room for `gundam` tiling and a few concurrent requests. **A5000 (24 GB) is the pick** — it's currently the cheapest option on Community Cloud *and* has the most VRAM headroom of the bunch, so there's no real tradeoff.

At $0.16–0.22/hr, a couple of hours of demoing costs well under a dollar — the real risk to your $10 isn't the hourly rate, it's **forgetting to terminate the pod**. `scripts/teardown-pod.sh` exists specifically so tear-down is a single command, not a trip through the console.

## 3. Do you need a Dockerfile?

**No — not for the recommended path.** Baidu/vLLM already publish a ready-to-run image, `vllm/vllm-openai:unlimited-ocr`, containing vLLM + CUDA + the model's custom logits processor. Deploying it on RunPod means:

- **`imageName`**: the public image, pulled directly by RunPod — no build, no push, no registry account.
- **`dockerStartCmd`**: an override of the container's default `CMD` that supplies the model name and vLLM flags (this is exactly what you'd type after the image name in a plain `docker run` command).

Building a custom image would only add value if you wanted to *bake in* extra Python deps (e.g. for the SGLang path in Section 5) so the Pod doesn't have to `pip install` on every boot — and even then, that costs local build time + a place to push it (Docker Hub), for a benefit (faster boot) that matters more for repeated deploys than a one-off $10 demo. Skipped here; ask if you want one for the SGLang path specifically.

## 4. Deploy — scripted (recommended)

The repo now includes scripts that call the RunPod REST API directly, using the `RUNPOD_API_KEY` your `scripts/load-env.sh` already pulls from Keychain. This avoids console point-and-click entirely and keeps GPU-billed idle time to a minimum.

| Script | Purpose |
|---|---|
| `scripts/check-gpu-prices.sh [SECURE\|COMMUNITY]` | Live $/hr for GPUs ≤32GB, cheapest first |
| `scripts/deploy-pod.sh` | Creates the Pod (Community Cloud, A5000→A4000→3090→4090 fallback, vLLM image, port 8000) |
| `scripts/pod-status.sh` | Shows pod status, cost/hr, and the proxy URL |
| `scripts/test-ocr.sh <image> [prompt]` | Sends a local image to the running endpoint, prints the parsed result |
| `scripts/teardown-pod.sh` | **Terminates the pod — run this as soon as you're done** |

For the full document pipeline (PDF → OCR → HTML → Hugging Face) see Section 7.

```bash
./scripts/check-gpu-prices.sh COMMUNITY   # optional sanity check
./scripts/deploy-pod.sh                   # creates the pod, saves its ID to .runpod/pod-id
./scripts/pod-status.sh                   # poll until it's ready (first boot pulls the 8.8GB image + 6.67GB weights)
./scripts/test-ocr.sh path/to/document.png
./scripts/teardown-pod.sh                 # stop billing
```

`deploy-pod.sh` sets `containerDiskInGb: 40` (image ~8.8 GB compressed / larger uncompressed + ~7 GB weights, plus buffer) and `volumeInGb: 0` since a one-off demo doesn't need weights to persist across restarts.

## 5. Manual path (RunPod console)

If you'd rather click through the UI:

1. Go to [console.runpod.io/pods](https://console.runpod.io/pods) → **Deploy**.
2. Skip the template gallery — you're running a custom image directly.
3. **GPU**: select **RTX A5000 (24 GB)**, Community Cloud tab (fall back to A4000/3090/4090 if out of capacity).
4. **Container image**: `vllm/vllm-openai:unlimited-ocr`
   - On a Hopper-class GPU (H100/H200) only, use `vllm/vllm-openai:unlimited-ocr-cu129` instead.
5. **Container start command / overrides**:
   ```
   baidu/Unlimited-OCR \
   --trust-remote-code \
   --logits_processors vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor \
   --no-enable-prefix-caching \
   --mm-processor-cache-gb 0 \
   --host 0.0.0.0 --port 8000
   ```
6. **Expose HTTP Ports**: add `8000`.
7. **Container disk**: 40 GB.
8. Click **Deploy On-Demand**.

Once running, the OpenAI-compatible API is reachable at `https://<POD_ID>-8000.proxy.runpod.net`. Exact request format (image passed as `image_url` with a base64 data URI or remote URL, plus required `skip_special_tokens: false` and `vllm_xargs: {ngram_size: 35, window_size: 128}` fields) is what `test-ocr.sh` sends — see that script for a working example, or the [vLLM recipe](https://recipes.vllm.ai/baidu/Unlimited-OCR) for more.

## 6. Alternative: SGLang batch script (best for the "long-document" demo)

If the point of the demo is showing off **multi-page PDF / long-document parsing** — the headline feature of Unlimited-OCR — the repo's own `infer.py` is a better showpiece than a single-image API call: it spins up an SGLang server and runs concurrent batch inference over a folder of images or a PDF.

1. Deploy a Pod with the **RunPod PyTorch** template on the same RTX A5000 (24 GB), with SSH/web terminal enabled.
2. In the Pod terminal:
   ```bash
   git clone https://github.com/baidu/Unlimited-OCR.git
   cd Unlimited-OCR
   uv venv --python 3.12 && source .venv/bin/activate
   uv pip install wheel/sglang-0.0.0.dev11416+g92e8bb79e-py3-none-any.whl
   uv pip install kernels==0.11.7 pymupdf==1.27.2.2
   ```
3. Run batch inference against a demo PDF:
   ```bash
   python infer.py \
     --pdf ./examples/document.pdf \
     --output_dir ./outputs \
     --concurrency 8 \
     --image_mode gundam \
     --model_dir baidu/Unlimited-OCR \
     --gpu 0
   ```
4. Pull parsed output from `./outputs`.

This demonstrates the "one-shot long-horizon parsing" capability the model is named for, at the cost of a more manual (and slightly slower-to-bill-start) setup than the vLLM image.

## 7. Document pipeline — PDF → OCR → HTML → Hugging Face

`scripts/run-pipeline.sh` takes the PDFs listed in `pdf-targets.md` all the way to
`jswhitworth/ocr-demo-documents`. It is built around one constraint: **boot dominates
inference**. Pulling the image and weights takes ~5–10 min; OCR'ing all 10 pages of the
three court opinions takes ~2–3 min. So the pod-running window is ~15 min ≈ **$0.04 on an
A5000**, and everything that doesn't need a GPU is kept outside it.

| Stage | Script | GPU? | Purpose |
|---|---|---|---|
| — | `targets.py` | no | Resolves the target list: `pdf-targets.md` + anything registered via `--url` |
| A | `fetch-pdfs.py` | no | Downloads source PDFs → `work/pdf/` |
| A | `rasterize.py` | no | Renders pages to PNG @300dpi → `work/pages/`, extracts text layer → `work/text/` |
| B | `ocr-pdfs.py` | **yes** | One multi-page request per document → `work/raw/` |
| C | `det-to-html.py` | no | Parses `<\|det\|>` markers → `work/html/`, `manifest.jsonl`, dataset card |
| D | `upload-dataset.sh` | no | Publishes `work/` to the HF dataset repo |

```bash
./scripts/run-pipeline.sh --prep-only    # Stage A only — no GPU spend
./scripts/run-pipeline.sh                # prep, deploy, OCR, tear down, build HTML
./scripts/run-pipeline.sh --local-only   # rebuild HTML from existing raw output, no pod
./scripts/run-pipeline.sh --upload       # ...and publish
```

### Choosing what to process

There are three ways, and they compose with every other flag:

```bash
# 1. Everything in the curated table (default)
./scripts/run-pipeline.sh

# 2. One or more documents from the table, by id
./scripts/run-pipeline.sh --only 2968s18 0260s20

# 3. Any PDF at all — no table edit required
./scripts/run-pipeline.sh --url https://example.gov/opinion.pdf
./scripts/run-pipeline.sh --url ~/Downloads/contract.pdf --case "Acme v. Roe"
```

`--url` accepts an http(s) URL **or a local file path**, is repeatable, and takes optional
`--case` / `--court` / `--date` metadata that flows into the HTML provenance header and
the manifest. When you pass it, only those documents are processed — the table is left
alone for that run.

Each `--url` target is assigned a document id from the PDF's basename (slugified; a short
hash is appended only if two different sources share a filename) and recorded in
`work/adhoc-targets.json`, which is what lets the later stages find it by id. Passing a
URL that's already in `pdf-targets.md` resolves to that existing target rather than
creating a duplicate, and a `--url` whose download fails is rolled back out of the
registry instead of lingering as a broken target.

Use `pdf-targets.md` for the standing demo set you want reproducible and version
controlled; use `--url` for one-off documents.

**Three properties worth knowing:**

1. **Tear-down is on a `trap`.** If OCR crashes, times out, or you Ctrl-C, the pod *this
   run created* is still terminated. A pod that was already running when the script
   started is reused and left alone. `--keep-pod` opts out.
2. **Raw output is checkpointed before post-processing.** `ocr-pdfs.py` writes
   `work/raw/<doc_id>.txt` the moment a response arrives and skips documents that already
   have one. A bug in HTML generation therefore never costs a second GPU boot — iterate
   with `--local-only` as much as you like.
3. **Nothing publishes implicitly.** Upload only happens with an explicit `--upload`.

### Request parameters

Per the [vLLM recipe](https://recipes.vllm.ai/baidu/Unlimited-OCR), multi-page differs
from the single-image call in `test-ocr.sh`:

| | `test-ocr.sh` (single image) | `ocr-pdfs.py` (multi-page) |
|---|---|---|
| prompt | `<image>document parsing.` | `<image>Multi page parsing.` |
| images | one | all pages in one request |
| mode | gundam (tiled crops) | base — automatic once >1 image is sent |
| `window_size` | 128 | **1024** |

The literal `<image>` prefix is **mandatory** — omitting it produces empty output.
`skip_special_tokens` must stay `false` so the `<|det|>` layout markers survive.

### Why the markers are parsed, not stripped

The model wraps each block as `<|det|>category [bbox]<|/det|>content`. The README's
`remove_det()` discards that to get flat text for benchmarking; `det-to-html.py` instead
uses the category to drive real HTML structure (titles → headings, tables → `<table>`,
images dropped, running heads muted) and keeps the bbox as a `data-bbox` attribute. The
category vocabulary is *discovered*, not assumed — anything unrecognised renders as a
paragraph and is reported under `unknown_categories` in the manifest.

> **Note on the bbox regex:** the pattern published in the model's README
> (`\[[^\]]*\]`) silently fails to match nested `[[x1,y1,x2,y2]]` coordinates — it stops
> at the first `]`. `det-to-html.py` matches anything up to `<|/det|>` instead, which
> handles both bracket conventions.

### Accuracy scoring

All three opinions are **born-digital** PDFs with real embedded text, so `rasterize.py`
extracts that text layer as ground truth and `det-to-html.py` scores the OCR against it
(character-level `SequenceMatcher`, `autojunk=False` — the default treats spaces as junk
in long strings and badly understates prose similarity). The score lands in
`manifest.jsonl` and in each HTML file's provenance header.

That makes the demo measurable rather than impressionistic — but it also means these
documents are an *easy* OCR target. They validate the pipeline end to end; they do not
demonstrate performance on scanned or degraded input. Add a scanned document to
`pdf-targets.md` for that (the score will simply report as `—` where no text layer exists).

## 8. Decision summary

- **Cheapest, fastest to a working demo:** `scripts/deploy-pod.sh` → RTX A5000 (24 GB) on Community Cloud, ~$0.16/hr, ~62 hours of runtime on $10.
- **Best showcase of the "unlimited"/long-document capability:** SGLang `infer.py` batch script (Section 6), same GPU.
- **No Dockerfile needed** for the vLLM path — the public image + a start-command override does everything.
- **Guard your credits:** run `teardown-pod.sh` the moment you're done. Community Cloud availability fluctuates — if `deploy-pod.sh` fails to place any GPU in the fallback list, re-run `check-gpu-prices.sh` to see what's actually in stock right now.
