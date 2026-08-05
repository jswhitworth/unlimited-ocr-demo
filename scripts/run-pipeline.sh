#!/usr/bin/env bash
# End-to-end: pdf-targets.md -> RunPod/Unlimited-OCR -> HTML -> Hugging Face.
#
# Structured so that the GPU billing window contains inference and nothing
# else. Everything before it (download, rasterize) and after it (HTML,
# manifest, upload) is local CPU work.
#
# Tear-down is wired to a trap: if OCR crashes, times out, or you Ctrl-C,
# the pod this run created is still terminated. A pod that was already
# running before this script started is left alone.
#
#   ./scripts/run-pipeline.sh                        # everything in pdf-targets.md
#   ./scripts/run-pipeline.sh --url https://.../x.pdf # any PDF, no table edit needed
#   ./scripts/run-pipeline.sh --only 2968s18         # one document from the table
#   ./scripts/run-pipeline.sh --upload               # ...and publish to Hugging Face
#   ./scripts/run-pipeline.sh --prep-only            # Stage A only, no GPU spend
#   ./scripts/run-pipeline.sh --local-only           # rebuild HTML from existing raw
#
# --url takes a URL or a local path, is repeatable, and accepts optional
# --case/--court/--date metadata that ends up in the HTML provenance header.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="$SCRIPT_DIR/../.runpod"
POD_ID_FILE="$STATE_DIR/pod-id"
PY="${PYTHON:-python3}"

UPLOAD=0
PREP_ONLY=0
LOCAL_ONLY=0
KEEP_POD=0
DPI=300
ONLY_ARGS=()
URL_ARGS=()
META_ARGS=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --upload)     UPLOAD=1; shift ;;
    --prep-only)  PREP_ONLY=1; shift ;;
    --local-only) LOCAL_ONLY=1; shift ;;
    --keep-pod)   KEEP_POD=1; shift ;;
    --dpi)        DPI="$2"; shift 2 ;;
    --url)        URL_ARGS+=(--url "$2"); shift 2 ;;
    --case)       META_ARGS+=(--case "$2"); shift 2 ;;
    --court)      META_ARGS+=(--court "$2"); shift 2 ;;
    --date)       META_ARGS+=(--date "$2"); shift 2 ;;
    --only)
      shift
      while [ "$#" -gt 0 ] && [[ "$1" != --* ]]; do ONLY_ARGS+=("$1"); shift; done ;;
    -h|--help)
      # Print the header comment block (line 2 until the first non-comment).
      awk 'NR>1 && /^#/ { sub(/^# ?/, ""); print; next } NR>1 { exit }' "$0"
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

ONLY_FLAG=()
if [ "${#ONLY_ARGS[@]}" -gt 0 ]; then
  ONLY_FLAG=(--only "${ONLY_ARGS[@]}")
fi

DEPLOYED_HERE=0
CLEANED_UP=0

cleanup() {
  [ "$CLEANED_UP" = "1" ] && return 0
  CLEANED_UP=1
  if [ "$DEPLOYED_HERE" = "1" ] && [ "$KEEP_POD" = "0" ] && [ -s "$POD_ID_FILE" ]; then
    echo ""
    echo "=== Tearing down pod (billing stops) ==="
    "$SCRIPT_DIR/teardown-pod.sh" \
      || echo "!! TEARDOWN FAILED — terminate it manually at https://console.runpod.io/pods" >&2
  elif [ "$DEPLOYED_HERE" = "1" ] && [ "$KEEP_POD" = "1" ]; then
    echo ""
    echo "Pod left running (--keep-pod). Terminate with: ./scripts/teardown-pod.sh"
  fi
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------- Stage A
if [ "$LOCAL_ONLY" = "0" ]; then
  echo "=== Stage A: fetch source PDFs ==="
  if [ "${#URL_ARGS[@]}" -gt 0 ]; then
    # Register the ad-hoc targets, then pin every later stage to exactly the
    # document ids they resolved to.
    ONLY_ARGS=()
    while IFS= read -r doc_id; do
      [ -n "$doc_id" ] && ONLY_ARGS+=("$doc_id")
    done < <("$PY" "$SCRIPT_DIR/fetch-pdfs.py" "${URL_ARGS[@]}" "${META_ARGS[@]}" --print-ids)
    if [ "${#ONLY_ARGS[@]}" -eq 0 ]; then
      echo "No PDFs could be fetched from the given --url argument(s)." >&2
      exit 1
    fi
    ONLY_FLAG=(--only "${ONLY_ARGS[@]}")
    echo "Processing: ${ONLY_ARGS[*]}"
  else
    "$PY" "$SCRIPT_DIR/fetch-pdfs.py" "${ONLY_FLAG[@]}"
  fi

  echo ""
  echo "=== Stage A: rasterize pages + extract text layer ==="
  "$PY" "$SCRIPT_DIR/rasterize.py" --dpi "$DPI" "${ONLY_FLAG[@]}"

  if [ "$PREP_ONLY" = "1" ]; then
    echo ""
    echo "Prep complete (--prep-only). No GPU time spent."
    exit 0
  fi

  # ------------------------------------------------------------- Stage B
  echo ""
  if [ -s "$POD_ID_FILE" ]; then
    echo "=== Stage B: reusing existing pod $(cat "$POD_ID_FILE") ==="
    echo "(not created by this run — it will be left running)"
  else
    echo "=== Stage B: deploying pod ==="
    "$SCRIPT_DIR/deploy-pod.sh"
    DEPLOYED_HERE=1
  fi

  echo ""
  echo "=== Stage B: OCR (billing window) ==="
  "$PY" "$SCRIPT_DIR/ocr-pdfs.py" "${ONLY_FLAG[@]}"

  # Stop billing before spending time on local post-processing.
  cleanup
fi

# ---------------------------------------------------------------- Stage C
echo ""
echo "=== Stage C: raw -> HTML, manifest, dataset card ==="
"$PY" "$SCRIPT_DIR/det-to-html.py" --dpi "$DPI" "${ONLY_FLAG[@]}"

# ---------------------------------------------------------------- Stage D
if [ "$UPLOAD" = "1" ]; then
  echo ""
  echo "=== Stage D: publish to Hugging Face ==="
  "$SCRIPT_DIR/upload-dataset.sh"
else
  echo ""
  echo "Not uploaded. Review work/html/, then publish with:"
  echo "  ./scripts/upload-dataset.sh"
fi
