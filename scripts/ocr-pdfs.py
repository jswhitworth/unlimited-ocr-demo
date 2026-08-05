#!/usr/bin/env python3
"""Stage B — OCR every prepared document against the running vLLM pod.

This is the only stage that costs money, so it is built to keep the billing
window short and to never repeat itself:

  * one request per document (all pages in a single "Multi page parsing."
    call — the long-horizon capability the model is named for),
  * raw model output is written to work/raw/<doc_id>.txt the moment it
    arrives, before any post-processing, so a bug in HTML generation never
    costs another GPU boot,
  * documents that already have raw output are skipped unless --force.

Request format follows the official vLLM recipe
(https://recipes.vllm.ai/baidu/Unlimited-OCR):
  * the literal "<image>" prefix on the prompt is mandatory — omitting it
    produces empty output,
  * passing multiple images automatically switches the model from gundam
    (crop) mode to base mode, so there is no image_size flag to set here,
  * window_size is 128 for a single image but 1024 for multi-page input,
  * skip_special_tokens must be false so the <|det|> layout markers survive.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from rasterize import page_paths
from targets import RAW_DIR, REPO_ROOT, load_targets, select

POD_ID_FILE = REPO_ROOT / ".runpod" / "pod-id"
MODEL = "baidu/Unlimited-OCR"
MULTIPAGE_PROMPT = "<image>Multi page parsing."


def base_url() -> str:
    if not POD_ID_FILE.is_file() or not POD_ID_FILE.read_text().strip():
        raise SystemExit(
            f"No tracked pod at {POD_ID_FILE}. Run scripts/deploy-pod.sh first."
        )
    return f"https://{POD_ID_FILE.read_text().strip()}-8000.proxy.runpod.net"


def wait_for_server(url: str, timeout: int) -> None:
    """Poll /v1/models until the model has finished loading.

    First boot pulls an 8.8GB image and 6.67GB of weights, so the proxy
    404s/502s for several minutes before answering.
    """
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            with urlopen(f"{url}/v1/models", timeout=15) as resp:
                if resp.status == 200:
                    print(f"  server ready after {attempt} probe(s)")
                    return
        except (HTTPError, URLError, OSError):
            pass
        print(f"  waiting for model to load... ({attempt})", end="\r", flush=True)
        time.sleep(10)
    raise SystemExit(
        f"\nServer at {url} not ready within {timeout}s — check the pod's Logs tab."
    )


def image_part(path: Path) -> dict:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


def build_payload(prompt: str, pages: list[Path], max_tokens: int, window_size: int) -> dict:
    return {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}] + [image_part(p) for p in pages],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0,
        "skip_special_tokens": False,
        "stream": True,
        "vllm_xargs": {"ngram_size": 35, "window_size": window_size},
    }


def stream_completion(url: str, payload: dict, timeout: int) -> tuple[str, str | None]:
    """POST a streaming chat completion. Returns (text, finish_reason).

    Streaming keeps the RunPod proxy connection alive during a long
    multi-page parse and gives live progress instead of a silent wait.
    """
    req = Request(
        f"{url}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    chunks: list[str] = []
    finish_reason: str | None = None
    last_report = time.time()

    with urlopen(req, timeout=timeout) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):]
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            choice = (event.get("choices") or [{}])[0]
            delta = choice.get("delta", {}).get("content") or ""
            if delta:
                chunks.append(delta)
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
            if time.time() - last_report > 5:
                print(f"    ...{sum(len(c) for c in chunks):,} chars", end="\r", flush=True)
                last_report = time.time()

    return "".join(chunks), finish_reason


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", metavar="DOC_ID",
                        help="restrict to these document ids")
    parser.add_argument("--force", action="store_true",
                        help="re-run OCR even if raw output already exists (costs GPU time)")
    parser.add_argument("--prompt", default=MULTIPAGE_PROMPT,
                        help=f"model prompt (default: {MULTIPAGE_PROMPT!r})")
    parser.add_argument("--max-tokens", type=int, default=16384,
                        help="output token budget per document (default: 16384)")
    parser.add_argument("--window-size", type=int, default=1024,
                        help="no-repeat ngram window; 1024 for multi-page (default: 1024)")
    parser.add_argument("--boot-timeout", type=int, default=1800,
                        help="seconds to wait for the model to load (default: 1800)")
    parser.add_argument("--request-timeout", type=int, default=1800,
                        help="per-document request timeout in seconds (default: 1800)")
    args = parser.parse_args()

    url = base_url()
    targets = select(load_targets(), args.only)

    pending = []
    for target in targets:
        doc_id = target["doc_id"]
        raw_path = RAW_DIR / f"{doc_id}.txt"
        if raw_path.is_file() and not args.force:
            print(f"  skip  {doc_id:<28} raw output already on disk")
            continue
        pages = page_paths(doc_id)
        if not pages:
            print(f"  FAIL  {doc_id:<28} no page images — run rasterize.py first",
                  file=sys.stderr)
            continue
        pending.append((target, pages))

    if not pending:
        print("\nNothing to OCR — all documents already have raw output.")
        return 0

    print(f"\nEndpoint: {url}")
    wait_for_server(url, args.boot_timeout)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    failures = 0
    for target, pages in pending:
        doc_id = target["doc_id"]
        print(f"\n  OCR   {doc_id} — {len(pages)} pages in one request")
        started = time.time()
        try:
            text, finish_reason = stream_completion(
                url, build_payload(args.prompt, pages, args.max_tokens, args.window_size),
                args.request_timeout,
            )
        except (HTTPError, URLError, OSError) as exc:
            print(f"  FAIL  {doc_id:<28} {exc}", file=sys.stderr)
            failures += 1
            continue

        elapsed = time.time() - started
        if not text.strip():
            print(f"  FAIL  {doc_id:<28} empty output — check the '<image>' prompt prefix",
                  file=sys.stderr)
            failures += 1
            continue

        # Checkpoint before anything else touches it.
        (RAW_DIR / f"{doc_id}.txt").write_text(text, encoding="utf-8")
        (RAW_DIR / f"{doc_id}.meta.json").write_text(
            json.dumps(
                {
                    "doc_id": doc_id,
                    "model": MODEL,
                    "prompt": args.prompt,
                    "image_mode": "base (multi-image input)",
                    "pages": len(pages),
                    "max_tokens": args.max_tokens,
                    "temperature": 0,
                    "ngram_size": 35,
                    "window_size": args.window_size,
                    "finish_reason": finish_reason,
                    "output_chars": len(text),
                    "elapsed_seconds": round(elapsed, 1),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        note = ""
        if finish_reason == "length":
            note = "  ** TRUNCATED — raise --max-tokens **"
        print(f"  done  {doc_id:<28} {len(text):,} chars in {elapsed:.0f}s "
              f"(finish: {finish_reason}){note}")

    print(f"\n{len(pending) - failures}/{len(pending)} documents OCR'd into {RAW_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
