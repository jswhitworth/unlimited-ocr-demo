# unlimited-ocr-demo

**PDFs to HTML and beyond...**

A one-command document pipeline: fetch public PDFs, OCR them on a rented GPU with
[baidu/Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR), convert the model's layout
markers into real HTML, score the result against ground truth, and publish the lot as a Hugging
Face dataset.

The GPU is rented for the ~15 minutes it takes to boot and infer, then terminated automatically.
A full run over the three bundled court opinions costs about **$0.04**.

---

## Before your first run

**1. Tools**

```bash
# macOS
brew install jq                                  # curl and python3 you already have
pip install pymupdf markdown huggingface_hub[cli]
```

**2. RunPod API key** — stored in the macOS Keychain, never in a file:

```bash
security add-generic-password -a "$USER" -s "runpod-api-key" -w
# paste your key from https://console.runpod.io/user/settings when prompted
```

Every script sources `scripts/load-env.sh`, which reads it back out. If a script exits with
`RUNPOD_API_KEY not set`, this step is what's missing.

**3. Hugging Face login** — only needed if you intend to publish (`--upload`):

```bash
hf auth login
```

**4. Credit** — add funds at [console.runpod.io/billing](https://console.runpod.io/billing).
$10 is roughly 60 hours of A5000 time, or a few hundred pipeline runs.

---

## Run it

```bash
./scripts/run-pipeline.sh
```

That does the whole thing: downloads the PDFs in `pdf-targets.md`, renders their pages,
deploys a pod, OCRs every document, **tears the pod down**, and writes HTML to `work/html/`.
Nothing is published unless you ask.

Expect roughly:

```
=== Stage A: fetch source PDFs ===          ~5s
=== Stage A: rasterize pages ===            ~10s
=== Stage B: deploying pod ===              ~30s to place
=== Stage B: OCR (billing window) ===       5-10 min boot, then ~1 min/doc
=== Tearing down pod (billing stops) ===
=== Stage C: raw -> HTML, manifest ===      ~2s
```

The long silence is the pod pulling an 8.8 GB image and 6.67 GB of weights. `ocr-pdfs.py` polls
`/v1/models` and prints `waiting for model to load...` until it answers — that's normal, not a hang.

Then open `work/html/2968s18.html` and, if it looks right:

```bash
./scripts/run-pipeline.sh --upload
```

---

## The flags you'll actually use

| Command | What it does | GPU? |
|---|---|---|
| `./scripts/run-pipeline.sh --prep-only` | Download + rasterize, then stop | no |
| `./scripts/run-pipeline.sh --local-only` | Rebuild HTML from raw output already on disk | no |
| `./scripts/run-pipeline.sh --only 2968s18` | Just one document from the table | yes |
| `./scripts/run-pipeline.sh --url <pdf>` | Any PDF, no table edit needed | yes |
| `./scripts/run-pipeline.sh --upload` | ...and publish to Hugging Face | yes |
| `./scripts/run-pipeline.sh --keep-pod` | Leave the pod running for the next run | yes |

They compose. `--only 2968s18 --upload` re-does one document and publishes.

### Processing a document that isn't in the table

`--url` takes a URL **or a local path**, is repeatable, and accepts metadata that flows into the
HTML provenance header and the manifest:

```bash
./scripts/run-pipeline.sh --url https://example.gov/opinion.pdf
./scripts/run-pipeline.sh --url ~/Downloads/contract.pdf --case "Acme v. Roe"
```

The document gets an id from its filename and is remembered in `work/adhoc-targets.json`, so
later runs can refer to it by that id. When you pass `--url`, only those documents are
processed — the table is left alone for that run.

Use `pdf-targets.md` for the standing demo set you want reproducible and version controlled;
use `--url` for one-offs.

### Iterating on the HTML

Raw model output is checkpointed to `work/raw/` the moment it arrives. Tweak `det-to-html.py`
and replay as often as you like:

```bash
./scripts/run-pipeline.sh --local-only
```

No pod, no cost. A rendering bug never means paying for a second boot.

---

## Watching the money

Billing is **per second while the pod is RUNNING**. The pipeline terminates the pod it created
even if OCR crashes or you Ctrl-C — that's on a `trap`, not on the happy path. But two cases
leave a pod alive, and both are on you:

- you passed `--keep-pod`
- you ran `deploy-pod.sh` yourself instead of through the pipeline

Check and clean up:

```bash
./scripts/pod-status.sh      # status, $/hr, proxy URL
./scripts/teardown-pod.sh    # terminate
```

`.runpod/pod-id` tracks the current pod. If that file exists, **assume something is billing**
until `pod-status.sh` says otherwise. The pipeline treats an existing pod as "someone else's" —
it reuses it and won't tear it down.

Before deploying, you can check what the market is doing:

```bash
./scripts/check-gpu-prices.sh COMMUNITY
```

Deploy asks for an RTX A5000 first and falls back through A4000 → 3090 → 4090, because
Community Cloud stock fluctuates and any single card is often unavailable.

---

## Manual / single-image operation

Useful for probing a live pod without running the pipeline:

```bash
./scripts/deploy-pod.sh
./scripts/pod-status.sh                              # poll until RUNNING and the model loads
./scripts/test-ocr.sh work/pages/2968s18/page_0001.png
./scripts/teardown-pod.sh
```

The pod exposes an OpenAI-compatible API at `https://<POD_ID>-8000.proxy.runpod.net/v1`.

---

## What you get

```
work/
├── pdf/        source PDFs, exactly as downloaded
├── pages/      page images sent to the model (PNG, 300 dpi)
├── text/       the PDF's own text layer — ground truth for scoring
├── raw/        unmodified model output, <|det|> markers intact, + per-doc meta.json
├── html/       the deliverable
├── manifest.jsonl   one row per document: provenance, parameters, accuracy
└── README.md   generated dataset card
```

All of `work/` is derived and gitignored — deleting it costs you one re-run of the free stages
plus, if you delete `raw/`, one GPU boot.

Each HTML file opens with a provenance box: source URL, page count, model, prompt, mode, and a
**text-layer match** percentage. That last number is a real character-level comparison against
the PDF's embedded text, not an estimate — the bundled opinions are born-digital, so ground truth
is free. It also means they're an *easy* OCR target: they prove the pipeline works end to end,
not that the model handles degraded scans. Add a scanned PDF to `pdf-targets.md` for that; the
score will simply report `—` where no text layer exists.

---

## When something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `RUNPOD_API_KEY not set` | Key not in Keychain | See setup step 2 |
| `Pod creation failed` | No capacity for any GPU in the fallback list | `./scripts/check-gpu-prices.sh COMMUNITY`, widen the list in `deploy-pod.sh` |
| `A pod is already tracked at .runpod/pod-id` | Previous pod never torn down | `./scripts/pod-status.sh`, then `teardown-pod.sh` — or delete the file if it's stale |
| Proxy URL 404s or 502s | Model still loading | Wait; check the pod's Logs tab in the console if it exceeds ~10 min |
| `empty output — check the '<image>' prompt prefix` | Prompt lost its literal `<image>` prefix | Restore it; the model returns nothing without it |
| `** TRUNCATED — raise --max-tokens **` | Document exceeded the output budget | `python3 scripts/ocr-pdfs.py --only <id> --force --max-tokens 32768` |
| `** unrecognised categories: ... **` | Model emitted a block type `det-to-html.py` doesn't map | Harmless — renders as a paragraph. Add it to the category sets to style it |
| `no page images — run rasterize.py first` | Stage A skipped or `work/` deleted | `./scripts/run-pipeline.sh --prep-only` |

---

## Going deeper

- **[DEPLOYMENT.md](DEPLOYMENT.md)** — model sizing, GPU price analysis, why no Dockerfile is
  needed, the SGLang batch alternative, and the reasoning behind every request parameter.
- **[CLAUDE.md](CLAUDE.md)** — architecture notes and the API/model gotchas, for working on the
  code rather than running it.
- **[pdf-targets.md](pdf-targets.md)** — the curated document table. Add a row to extend the
  standing demo set.
