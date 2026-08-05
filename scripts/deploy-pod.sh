#!/usr/bin/env bash
# Deploy a cost-optimized RunPod Pod running baidu/Unlimited-OCR via vLLM's
# official Docker image. No custom Dockerfile/build needed — the image
# already contains everything; we only override the container start command.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./load-env.sh
source "$SCRIPT_DIR/load-env.sh"

if [ -z "${RUNPOD_API_KEY:-}" ]; then
  echo "RUNPOD_API_KEY not set. See scripts/load-env.sh." >&2
  exit 1
fi

STATE_DIR="$SCRIPT_DIR/../.runpod"
mkdir -p "$STATE_DIR"

if [ -s "$STATE_DIR/pod-id" ]; then
  echo "A pod is already tracked at $STATE_DIR/pod-id ($(cat "$STATE_DIR/pod-id"))." >&2
  echo "Run scripts/teardown-pod.sh first, or delete that file if it's stale." >&2
  exit 1
fi

IMAGE="vllm/vllm-openai:unlimited-ocr"
POD_NAME="unlimited-ocr-demo"

# Cheapest-first fallback list on Community Cloud, checked live against
# https://api.runpod.io/v2/catalog/gpus on 2026-08-05:
#   RTX A5000  24GB  ~$0.16/hr  (cheapest AND best VRAM headroom for gundam/multi-page)
#   RTX A4000  16GB  ~$0.17/hr  (meets the model's official >=8GB bf16 floor)
#   RTX 3090   24GB  ~$0.22/hr
#   RTX 4090   24GB  ~$0.34/hr
# Prices and availability fluctuate — re-check with scripts/check-gpu-prices.sh
# before deploying (Community Cloud stock is limited; RunPod stopped onboarding
# new CC hosts, so LOW/NONE availability on any single card is common).
GPU_TYPES='["NVIDIA RTX A5000","NVIDIA RTX A4000","NVIDIA GeForce RTX 3090","NVIDIA GeForce RTX 4090"]'

PAYLOAD=$(jq -n \
  --arg name "$POD_NAME" \
  --arg image "$IMAGE" \
  --argjson gpuTypeIds "$GPU_TYPES" \
  '{
    name: $name,
    imageName: $image,
    cloudType: "COMMUNITY",
    gpuTypeIds: $gpuTypeIds,
    gpuTypePriority: "custom",
    gpuCount: 1,
    containerDiskInGb: 40,
    volumeInGb: 0,
    ports: ["8000/http"],
    dockerStartCmd: [
      "baidu/Unlimited-OCR",
      "--trust-remote-code",
      "--logits_processors", "vllm.model_executor.models.unlimited_ocr:NGramPerReqLogitsProcessor",
      "--no-enable-prefix-caching",
      "--mm-processor-cache-gb", "0",
      "--host", "0.0.0.0",
      "--port", "8000"
    ]
  }')

echo "Requesting pod (Community Cloud, cheapest-first GPU fallback)..."
RESPONSE=$(curl -sS --request POST \
  --url "https://rest.runpod.io/v1/pods" \
  --header "Authorization: Bearer $RUNPOD_API_KEY" \
  --header "Content-Type: application/json" \
  --data "$PAYLOAD")

echo "$RESPONSE" | jq .

POD_ID=$(echo "$RESPONSE" | jq -r '.id // empty')
if [ -z "$POD_ID" ]; then
  echo "Pod creation failed — see response above (common cause: no capacity for any GPU in the fallback list)." >&2
  exit 1
fi

echo "$POD_ID" > "$STATE_DIR/pod-id"
echo ""
echo "Pod created: $POD_ID"
echo "The vLLM server needs a few minutes to pull the image and download model weights."
echo "Track status:  scripts/pod-status.sh"
echo "Test it:       scripts/test-ocr.sh <path-to-image>"
echo "Tear down:     scripts/teardown-pod.sh   (do this as soon as you're done — billing is per-second while RUNNING)"
