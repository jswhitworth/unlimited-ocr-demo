#!/usr/bin/env bash
# Stage D — publish work/ to the Hugging Face dataset repo.
#
# This pushes to a PUBLIC repo, so it is never run implicitly: run-pipeline.sh
# only calls it when you pass --upload.
#
# Page images are excluded by default — they are large and re-derivable from
# the source PDFs. Pass --with-pages to include them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="$SCRIPT_DIR/../work"
REPO_ID="${HF_DATASET_REPO:-jswhitworth/ocr-demo-documents}"

WITH_PAGES=0
COMMIT_MSG="Add OCR'd state court opinions (Unlimited-OCR via vLLM)"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --with-pages) WITH_PAGES=1; shift ;;
    --repo) REPO_ID="$2"; shift 2 ;;
    --message) COMMIT_MSG="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: $0 [--repo OWNER/NAME] [--with-pages] [--message MSG]"
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if ! command -v hf >/dev/null 2>&1; then
  echo "hf CLI not found. Install with: pip install -U huggingface_hub[cli]" >&2
  exit 1
fi

if ! hf auth whoami >/dev/null 2>&1; then
  echo "Not logged in to Hugging Face. Run: hf auth login" >&2
  exit 1
fi

if [ ! -d "$WORK_DIR/html" ] || [ -z "$(ls -A "$WORK_DIR/html" 2>/dev/null)" ]; then
  echo "No HTML in $WORK_DIR/html — run the pipeline through det-to-html.py first." >&2
  exit 1
fi

echo "Uploading $WORK_DIR -> https://huggingface.co/datasets/$REPO_ID"
echo "Contents:"
find "$WORK_DIR" -type f -not -path "*/pages/*" | sed "s|$WORK_DIR/|  |" | sort
if [ "$WITH_PAGES" = "1" ]; then
  echo "  pages/ ($(find "$WORK_DIR/pages" -type f 2>/dev/null | wc -l | tr -d ' ') page images)"
fi
echo ""

EXCLUDE_ARGS=()
if [ "$WITH_PAGES" = "0" ]; then
  EXCLUDE_ARGS=(--exclude "pages/*")
fi

hf upload "$REPO_ID" "$WORK_DIR" . \
  --repo-type dataset \
  --commit-message "$COMMIT_MSG" \
  "${EXCLUDE_ARGS[@]}"

echo ""
echo "Done: https://huggingface.co/datasets/$REPO_ID"
