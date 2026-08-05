#!/usr/bin/env bash
# Send a local image to the deployed Unlimited-OCR vLLM endpoint and print
# the parsed markdown/text result.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/load-env.sh"

STATE_DIR="$SCRIPT_DIR/../.runpod"
POD_ID_FILE="$STATE_DIR/pod-id"

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 <path-to-image> [prompt]" >&2
  exit 1
fi

IMAGE_PATH="$1"
PROMPT="${2:-<image>document parsing.}"

if [ ! -f "$IMAGE_PATH" ]; then
  echo "Image not found: $IMAGE_PATH" >&2
  exit 1
fi

if [ ! -s "$POD_ID_FILE" ]; then
  echo "No tracked pod found at $POD_ID_FILE. Run scripts/deploy-pod.sh first." >&2
  exit 1
fi

POD_ID=$(cat "$POD_ID_FILE")
BASE_URL="https://$POD_ID-8000.proxy.runpod.net"

EXT="${IMAGE_PATH##*.}"
case "${EXT,,}" in
  jpg|jpeg) MIME="image/jpeg" ;;
  png) MIME="image/png" ;;
  webp) MIME="image/webp" ;;
  *) MIME="image/png" ;;
esac

B64=$(base64 < "$IMAGE_PATH" | tr -d '\n')

PAYLOAD=$(jq -n \
  --arg model "baidu/Unlimited-OCR" \
  --arg prompt "$PROMPT" \
  --arg data_url "data:$MIME;base64,$B64" \
  '{
    model: $model,
    messages: [
      {
        role: "user",
        content: [
          {type: "text", text: $prompt},
          {type: "image_url", image_url: {url: $data_url}}
        ]
      }
    ],
    max_tokens: 8192,
    temperature: 0,
    skip_special_tokens: false,
    vllm_xargs: {ngram_size: 35, window_size: 128}
  }')

echo "POST $BASE_URL/v1/chat/completions"
curl -sS --max-time 300 --request POST \
  --url "$BASE_URL/v1/chat/completions" \
  --header "Content-Type: application/json" \
  --data "$PAYLOAD" | jq -r '.choices[0].message.content // .'
